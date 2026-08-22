"""Choose which photos to show and persist the active subset.

Selection priority (per the requirements):
  1. photos taken on *this day in prior years* (widened by +/- window_days),
  2. preferring those never shown,
  3. then the least-recently-shown.
If every photo inside the base +/- window_days band has already been shown, the
window is widened (by doubling) until it reaches the oldest waiting photo, so the
frame surfaces long-unseen photos instead of recycling recently-shown ones.
If that still yields fewer than ``n``, top up with the globally
least-recently-shown photos from any date.
"""

import logging
import sqlite3
from datetime import date, datetime, timedelta

from . import db

log = logging.getLogger(__name__)

# Portable "NULLS FIRST then ascending": NULL last_displayed (never shown) sorts
# first, then oldest last_displayed, then oldest photo first as a stable tiebreak.
_ORDER = (
    "ORDER BY (last_displayed IS NULL) DESC, last_displayed ASC, photo_date ASC, id ASC"
)


def _target_dates(today: date, window_days: int, years: list[int]) -> list[str]:
    """ISO date strings for this day (+/- window) across the given prior years."""
    month_days = set()
    for offset in range(-window_days, window_days + 1):
        d = today + timedelta(days=offset)
        month_days.add((d.month, d.day))
    out = []
    for year in years:
        for month, day in month_days:
            try:
                out.append(date(year, month, day).isoformat())
            except ValueError:
                continue  # e.g. Feb 29 in a non-leap prior year
    return out


def _prior_years(conn: sqlite3.Connection, today: date) -> list[int]:
    rows = conn.execute(
        "SELECT DISTINCT CAST(substr(photo_date, 1, 4) AS INTEGER) AS y FROM photos"
    ).fetchall()
    return sorted(r["y"] for r in rows if r["y"] is not None and r["y"] < today.year)


# +/- 183 days spans every month/day, i.e. the full prior-year library.
_MAX_WINDOW = 183


def _oldest_key(conn: sqlite3.Connection, targets: list[str]) -> tuple[int, str] | None:
    """Sort key of the oldest candidate among ``targets`` (smaller = older).

    ``(0, "")`` for a never-shown photo (oldest possible), ``(1, last_displayed)``
    otherwise, or ``None`` when nothing matches.
    """
    if not targets:
        return None
    placeholders = ",".join("?" * len(targets))
    row = conn.execute(
        f"SELECT last_displayed FROM photos "
        f"WHERE marked_for_delete = 0 AND is_private = 0 "
        f"AND photo_date IN ({placeholders}) "
        f"{_ORDER} LIMIT 1",
        targets,
    ).fetchone()
    if row is None:
        return None
    ld = row["last_displayed"]
    return (0, "") if ld is None else (1, ld)


def _widen_to_oldest(
    conn: sqlite3.Connection, today: date, window_days: int, years: list[int]
) -> tuple[list[str], int]:
    """Smallest doubling window whose oldest candidate is the globally oldest.

    Returns ``(target_dates, window_days_used)``. Each larger window is a superset,
    so ``_oldest_key`` is monotonic as the window grows: compute the global oldest
    once (full year), then double the window until its oldest matches. A never-shown
    (or already-oldest) photo inside the base band matches immediately, so the band
    is not widened needlessly.
    """
    full_targets = _target_dates(today, _MAX_WINDOW, years)
    global_key = _oldest_key(conn, full_targets)
    if global_key is None:  # no prior-year candidates at all
        return _target_dates(today, window_days, years), window_days
    window = window_days
    while window < _MAX_WINDOW:
        targets = _target_dates(today, window, years)
        if _oldest_key(conn, targets) == global_key:
            if window > window_days:
                log.info(
                    "widened window from +/-%d to +/-%d days to reach oldest photo",
                    window_days, window,
                )
            return targets, window  # smallest window holding the oldest photo
        window = min(window * 2, _MAX_WINDOW)
    log.info("widened window to the full year (+/-%d days)", _MAX_WINDOW)
    return full_targets, _MAX_WINDOW


def _window_counts(conn: sqlite3.Connection, targets: list[str]) -> tuple[int, int]:
    """``(available, viewed)`` for non-deleted photos whose date is in ``targets``."""
    if not targets:
        return 0, 0
    placeholders = ",".join("?" * len(targets))
    row = conn.execute(
        f"SELECT COUNT(*) AS available, COUNT(last_displayed) AS viewed "
        f"FROM photos WHERE marked_for_delete = 0 AND is_private = 0 "
        f"AND photo_date IN ({placeholders})",
        targets,
    ).fetchone()
    return row["available"], row["viewed"]


def select_subset(
    conn: sqlite3.Connection,
    n: int,
    window_days: int,
    today: date | None = None,
) -> list[sqlite3.Row]:
    """Pick ``n`` photos, persist them as the active subset, and mark them shown."""
    today = today or date.today()
    years = _prior_years(conn, today)
    targets, window = _widen_to_oldest(conn, today, window_days, years)

    chosen: list[sqlite3.Row] = []
    if targets:
        placeholders = ",".join("?" * len(targets))
        chosen = conn.execute(
            f"SELECT * FROM photos "
            f"WHERE marked_for_delete = 0 AND is_private = 0 "
            f"AND photo_date IN ({placeholders}) "
            f"{_ORDER} LIMIT ?",
            (*targets, n),
        ).fetchall()

    if len(chosen) < n:
        chosen_ids = [r["id"] for r in chosen]
        excl = ",".join("?" * len(chosen_ids)) if chosen_ids else ""
        not_in = f"AND id NOT IN ({excl})" if chosen_ids else ""
        topup = conn.execute(
            f"SELECT * FROM photos WHERE marked_for_delete = 0 AND is_private = 0 {not_in} "
            f"{_ORDER} LIMIT ?",
            (*chosen_ids, n - len(chosen)),
        ).fetchall()
        chosen = chosen + topup

    ids = [r["id"] for r in chosen]
    _persist_subset(conn, ids)
    db.record_displayed(conn, ids)
    available, viewed = _window_counts(conn, targets)  # after marking the chosen shown
    _persist_subset_meta(conn, window, available, viewed)
    log.info(
        "selected %d photo(s) for %s (n=%d, window=+/-%dd, viewed %d/%d in window): %s",
        len(chosen), today.isoformat(), n, window, viewed, available, ids,
    )
    return chosen


def _persist_subset(conn: sqlite3.Connection, photo_ids: list[int]) -> None:
    selected_at = db.now_iso()
    conn.execute("DELETE FROM subset")
    conn.executemany(
        "INSERT INTO subset (photo_id, position, selected_at) VALUES (?, ?, ?)",
        [(pid, pos, selected_at) for pos, pid in enumerate(photo_ids)],
    )
    conn.commit()


def _persist_subset_meta(
    conn: sqlite3.Connection, window_days: int, available: int, viewed: int
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO subset_meta "
        "(id, window_days, available, viewed, selected_at) VALUES (1, ?, ?, ?, ?)",
        (window_days, available, viewed, db.now_iso()),
    )
    conn.commit()


def subset_meta(conn: sqlite3.Connection) -> dict:
    """Selection-window summary of the active subset (zeros if none yet)."""
    row = conn.execute(
        "SELECT window_days, available, viewed FROM subset_meta WHERE id = 1"
    ).fetchone()
    if row is None:
        return {"window_days": 0, "available": 0, "viewed": 0}
    return {
        "window_days": row["window_days"],
        "available": row["available"],
        "viewed": row["viewed"],
    }


def current_subset(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Photos in the active subset, in display order (empty if none)."""
    return conn.execute(
        "SELECT p.* FROM subset s JOIN photos p ON p.id = s.photo_id "
        "ORDER BY s.position"
    ).fetchall()


def _selected_at(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute("SELECT selected_at FROM subset LIMIT 1").fetchone()
    return datetime.fromisoformat(row["selected_at"]) if row else None


def ensure_subset(
    conn: sqlite3.Connection,
    n: int,
    window_days: int,
    refresh_mins: float,
) -> list[sqlite3.Row]:
    """Return the active subset, reselecting if it is empty or expired."""
    selected_at = _selected_at(conn)
    expired = (
        selected_at is None
        or datetime.now() - selected_at >= timedelta(minutes=refresh_mins)
    )
    if expired:
        log.info(
            "subset %s; reselecting",
            "empty" if selected_at is None else "expired",
        )
        return select_subset(conn, n, window_days)
    return current_subset(conn)
