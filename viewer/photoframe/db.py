"""SQLite layer (stdlib ``sqlite3``, no ORM).

The only persistent state is per-photo display history plus delete marks. Paths
are stored *relative to* ``$CONVERTED`` so the same database resolves on the Pi
and on the desktop even though they mount the converted tree at different points.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id                INTEGER PRIMARY KEY,
    rel_path          TEXT UNIQUE NOT NULL,
    photo_date        TEXT NOT NULL,          -- 'YYYY-MM-DD'
    added_ts          TEXT NOT NULL,
    last_displayed    TEXT,                   -- NULL = never shown
    display_count     INTEGER NOT NULL DEFAULT 0,
    marked_for_delete INTEGER NOT NULL DEFAULT 0,
    marked_ts         TEXT
);

CREATE TABLE IF NOT EXISTS subset (
    photo_id    INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    selected_at TEXT NOT NULL
);

-- Single-row (id = 1) summary of the active subset's selection window.
CREATE TABLE IF NOT EXISTS subset_meta (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    window_days INTEGER NOT NULL,
    available   INTEGER NOT NULL,   -- non-deleted photos in the window
    viewed      INTEGER NOT NULL,   -- of those, how many have been shown
    selected_at TEXT NOT NULL
);

-- Operations queued by the viewer (e.g. rotate fixes) for M1 to apply to the
-- SOURCE original later. AUTOINCREMENT so ids are never reused: M1 references
-- specific row ids from a stale pulled-DB snapshot to clear them afterwards.
CREATE TABLE IF NOT EXISTS pending_operations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id   INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    op         TEXT NOT NULL,          -- 'rotate_90' | 'rotate_180' | 'rotate_270'
    created_ts TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_photos_date ON photos(photo_date);
CREATE INDEX IF NOT EXISTS idx_photos_last_displayed ON photos(last_displayed);
CREATE INDEX IF NOT EXISTS idx_pending_operations_photo ON pending_operations(photo_id);
"""

_ROTATE_DEGREES = (90, 180, 270)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the database in WAL mode with row access by name."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def get_photo(conn: sqlite3.Connection, photo_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()


def toggle_mark(conn: sqlite3.Connection, photo_id: int) -> bool:
    """Flip ``marked_for_delete`` for a photo. Returns the new state."""
    row = get_photo(conn, photo_id)
    if row is None:
        raise KeyError(f"no photo with id {photo_id}")
    new_state = 0 if row["marked_for_delete"] else 1
    conn.execute(
        "UPDATE photos SET marked_for_delete = ?, marked_ts = ? WHERE id = ?",
        (new_state, now_iso() if new_state else None, photo_id),
    )
    conn.commit()
    return bool(new_state)


def queue_rotate_op(conn: sqlite3.Connection, photo_id: int, degrees: int) -> int:
    """Queue a pending clockwise-rotation op. Returns the photo's new net
    pending rotation (0/90/180/270), i.e. the sum of all its pending rotate
    ops mod 360, for immediate UI feedback."""
    if degrees not in _ROTATE_DEGREES:
        raise ValueError(f"degrees must be one of {_ROTATE_DEGREES}, got {degrees}")
    if get_photo(conn, photo_id) is None:
        raise KeyError(f"no photo with id {photo_id}")
    conn.execute(
        "INSERT INTO pending_operations (photo_id, op, created_ts) VALUES (?, ?, ?)",
        (photo_id, f"rotate_{degrees}", now_iso()),
    )
    conn.commit()
    return pending_rotate_degrees(conn, [photo_id]).get(photo_id, 0)


def pending_rotate_degrees(conn: sqlite3.Connection, photo_ids: list[int]) -> dict[int, int]:
    """Bulk net pending clockwise rotation (mod 360) per photo id. A photo id
    absent from the returned dict has no pending rotation (net 0)."""
    if not photo_ids:
        return {}
    placeholders = ",".join("?" * len(photo_ids))
    rows = conn.execute(
        f"SELECT photo_id, op FROM pending_operations "
        f"WHERE photo_id IN ({placeholders}) AND op LIKE 'rotate_%'",
        photo_ids,
    ).fetchall()
    totals: dict[int, int] = {}
    for r in rows:
        deg = int(r["op"].removeprefix("rotate_"))
        totals[r["photo_id"]] = (totals.get(r["photo_id"], 0) + deg) % 360
    return totals


def clear_pending_operations(conn: sqlite3.Connection, op_ids: list[int]) -> int:
    """Delete specific pending_operations rows by id. Returns rows actually
    deleted -- fewer than requested is not an error (a row may already be gone)."""
    if not op_ids:
        return 0
    placeholders = ",".join("?" * len(op_ids))
    cur = conn.execute(f"DELETE FROM pending_operations WHERE id IN ({placeholders})", op_ids)
    conn.commit()
    return cur.rowcount


def record_displayed(conn: sqlite3.Connection, photo_ids: list[int]) -> None:
    """Mark a set of photos as shown now (advances them to the back of the queue)."""
    if not photo_ids:
        return
    ts = now_iso()
    conn.executemany(
        "UPDATE photos SET last_displayed = ?, display_count = display_count + 1 "
        "WHERE id = ?",
        [(ts, pid) for pid in photo_ids],
    )
    conn.commit()


def marked_rel_paths(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT rel_path FROM photos WHERE marked_for_delete = 1 ORDER BY rel_path"
    ).fetchall()
    return [r["rel_path"] for r in rows]


def get_stats(conn: sqlite3.Connection) -> dict:
    """Aggregate library/viewing/pending-action counts for the stats page."""
    total = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    earliest, latest = conn.execute(
        "SELECT MIN(photo_date), MAX(photo_date) FROM photos"
    ).fetchone()
    by_year = conn.execute(
        "SELECT substr(photo_date, 1, 4) AS year, COUNT(*) AS n FROM photos "
        "GROUP BY year ORDER BY year"
    ).fetchall()
    never_shown = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE last_displayed IS NULL"
    ).fetchone()[0]
    total_displays = conn.execute(
        "SELECT COALESCE(SUM(display_count), 0) FROM photos"
    ).fetchone()[0]
    marked_for_delete = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE marked_for_delete = 1"
    ).fetchone()[0]
    pending_rotate_ops = conn.execute("SELECT COUNT(*) FROM pending_operations").fetchone()[0]
    pending_rotate_photos = conn.execute(
        "SELECT COUNT(DISTINCT photo_id) FROM pending_operations"
    ).fetchone()[0]
    return {
        "total": total,
        "earliest": earliest,
        "latest": latest,
        "by_year": [(r["year"], r["n"]) for r in by_year],
        "never_shown": never_shown,
        "shown": total - never_shown,
        "total_displays": total_displays,
        "marked_for_delete": marked_for_delete,
        "pending_rotate_ops": pending_rotate_ops,
        "pending_rotate_photos": pending_rotate_photos,
    }
