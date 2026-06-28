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

CREATE TABLE IF NOT EXISTS rotation (
    photo_id    INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    selected_at TEXT NOT NULL
);

-- Single-row (id = 1) summary of the active rotation's selection window.
CREATE TABLE IF NOT EXISTS rotation_meta (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    window_days INTEGER NOT NULL,
    available   INTEGER NOT NULL,   -- non-deleted photos in the window
    viewed      INTEGER NOT NULL,   -- of those, how many have been shown
    selected_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_photos_date ON photos(photo_date);
CREATE INDEX IF NOT EXISTS idx_photos_last_displayed ON photos(last_displayed);
"""


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
