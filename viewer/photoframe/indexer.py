"""Index the converted-images tree into the database.

Walks ``$CONVERTED`` and records every image whose path matches the canonical
layout ``YYYY/YYYY_MM_DD/[<model-subdir>/]<filename>``. Anything else is logged
and skipped, so every indexed row carries a valid ``photo_date``. A prune pass
removes rows whose file has since disappeared (e.g. after a purge on the desktop).
"""

import logging
import re
import sqlite3
from datetime import date
from pathlib import Path

from . import db

log = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}

# Path is matched *relative to* $CONVERTED. The model sub-directory is optional.
CANONICAL_RE = re.compile(
    r"^(?P<year>\d{4})/"
    r"(?P<y>\d{4})_(?P<m>\d{2})_(?P<d>\d{2})/"
    r"(?:[^/]+/)?"
    r"[^/]+\.(?:jpe?g|png)$",
    re.IGNORECASE,
)


def parse_photo_date(rel_path: str) -> date | None:
    """Return the photo date from a canonical rel_path, or None if it doesn't match."""
    match = CANONICAL_RE.match(rel_path)
    if not match:
        return None
    # The YYYY top dir and the YYYY in YYYY_MM_DD should agree; trust the dated dir.
    try:
        return date(int(match["y"]), int(match["m"]), int(match["d"]))
    except ValueError:
        return None


def _iter_images(converted_root: Path):
    for path in converted_root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if name == ".DS_Store" or name.startswith("._"):
            continue
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        yield path


def index(conn: sqlite3.Connection, converted_root: Path, error_log: Path) -> dict:
    """Index new images and prune missing ones. Returns a stats dict."""
    converted_root = Path(converted_root)
    log.info("indexing %s", converted_root)
    added = skipped = existing = 0
    error_log = Path(error_log)
    error_log.parent.mkdir(parents=True, exist_ok=True)
    seen_rel: set[str] = set()

    with error_log.open("a") as errf:
        for path in _iter_images(converted_root):
            rel_path = path.relative_to(converted_root).as_posix()
            photo_date = parse_photo_date(rel_path)
            if photo_date is None:
                errf.write(f"{db.now_iso()}\tNON_CANONICAL\t{rel_path}\n")
                skipped += 1
                continue
            seen_rel.add(rel_path)
            cur = conn.execute(
                "INSERT INTO photos (rel_path, photo_date, added_ts) "
                "VALUES (?, ?, ?) ON CONFLICT(rel_path) DO NOTHING",
                (rel_path, photo_date.isoformat(), db.now_iso()),
            )
            if cur.rowcount:
                added += 1
            else:
                existing += 1
    conn.commit()

    pruned = _prune_missing(conn, converted_root)
    total = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    log.info(
        "indexed: added=%d existing=%d skipped=%d pruned=%d total=%d",
        added, existing, skipped, pruned, total,
    )
    if skipped:
        log.warning("%d non-canonical path(s) skipped; see %s", skipped, error_log)
    return {
        "added": added,
        "existing": existing,
        "skipped": skipped,
        "pruned": pruned,
        "total": total,
    }


def _prune_missing(conn: sqlite3.Connection, converted_root: Path) -> int:
    """Delete rows whose file no longer exists under the converted root."""
    rows = conn.execute("SELECT id, rel_path FROM photos").fetchall()
    gone = [
        r["id"] for r in rows if not (converted_root / r["rel_path"]).exists()
    ]
    if gone:
        conn.executemany("DELETE FROM photos WHERE id = ?", [(i,) for i in gone])
        conn.commit()
    return len(gone)
