"""SQLite layer (stdlib ``sqlite3``, no ORM) for the duplicate-review tool.

Mirrors ``viewer/photoframe/db.py``'s shape (row_factory, WAL, foreign_keys
pragma, ``now_iso``/``connect``/``init_schema`` helpers) but is a wholly
separate, M1-local database: ``find_duplicates.py`` writes it directly (there
is no pull step from M2, unlike ``photoframe.sqlite``).
"""

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id          INTEGER PRIMARY KEY,
    rel_path    TEXT UNIQUE NOT NULL,   -- relative to $CONVERTED
    file_size   INTEGER NOT NULL,
    mtime       REAL NOT NULL,          -- converted file's mtime; skip rehash if unchanged
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    phash       TEXT NOT NULL,          -- hex 64-bit perceptual hash
    sha256      TEXT NOT NULL,
    photo_date  TEXT,                   -- 'YYYY-MM-DD'
    source_ext  TEXT,                   -- resolved source extension, NULL if unresolved/ambiguous
    source_size INTEGER,
    is_raw      INTEGER NOT NULL DEFAULT 0,
    scanned_ts  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS duplicate_groups (
    id         INTEGER PRIMARY KEY,
    method     TEXT NOT NULL,           -- 'exact' | 'phash'
    created_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS duplicate_members (
    group_id         INTEGER NOT NULL REFERENCES duplicate_groups(id) ON DELETE CASCADE,
    image_id         INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    distance         INTEGER NOT NULL DEFAULT 0,  -- hamming distance to the group's recommended-keep
    recommended_keep INTEGER NOT NULL DEFAULT 0,
    decision         TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'keep' | 'delete'
    decided_ts       TEXT,
    purged_ts        TEXT,              -- set once purge_duplicates.py actually deletes the source
    PRIMARY KEY (group_id, image_id)
);

CREATE INDEX IF NOT EXISTS idx_images_photo_date ON images(photo_date);
CREATE INDEX IF NOT EXISTS idx_dup_members_decision ON duplicate_members(decision);
CREATE INDEX IF NOT EXISTS idx_dup_members_image ON duplicate_members(image_id);
"""

_DECISIONS = ("pending", "keep", "delete")


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


def get_image(conn: sqlite3.Connection, image_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()


def get_image_by_relpath(conn: sqlite3.Connection, rel_path: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM images WHERE rel_path = ?", (rel_path,)).fetchone()


def upsert_image(
    conn: sqlite3.Connection,
    rel_path: str,
    file_size: int,
    mtime: float,
    width: int,
    height: int,
    phash: str,
    sha256: str,
    photo_date: str | None,
    source_ext: str | None,
    source_size: int | None,
    is_raw: bool,
) -> int:
    """Insert or fully replace the row for ``rel_path``. Returns the image id."""
    conn.execute(
        """
        INSERT INTO images (
            rel_path, file_size, mtime, width, height, phash, sha256,
            photo_date, source_ext, source_size, is_raw, scanned_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(rel_path) DO UPDATE SET
            file_size = excluded.file_size,
            mtime = excluded.mtime,
            width = excluded.width,
            height = excluded.height,
            phash = excluded.phash,
            sha256 = excluded.sha256,
            photo_date = excluded.photo_date,
            source_ext = excluded.source_ext,
            source_size = excluded.source_size,
            is_raw = excluded.is_raw,
            scanned_ts = excluded.scanned_ts
        """,
        (
            rel_path, file_size, mtime, width, height, phash, sha256,
            photo_date, source_ext, source_size, int(is_raw), now_iso(),
        ),
    )
    conn.commit()
    return get_image_by_relpath(conn, rel_path)["id"]


def all_images(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM images").fetchall()


def replace_groups(conn: sqlite3.Connection, groups: list[dict]) -> None:
    """Recompute duplicate_groups/duplicate_members from scratch.

    ``groups`` is a list of {"method": "exact"|"phash", "members": [
        {"image_id": int, "distance": int, "recommended_keep": bool}, ...
    ]}. Any non-'pending' decision on an image_id that still appears in a new
    group is preserved across the rebuild (so a rescan doesn't discard a
    user's earlier keep/delete calls), including its purged_ts.
    """
    prior = {
        row["image_id"]: (row["decision"], row["decided_ts"], row["purged_ts"])
        for row in conn.execute(
            "SELECT image_id, decision, decided_ts, purged_ts FROM duplicate_members "
            "WHERE decision != 'pending'"
        ).fetchall()
    }

    conn.execute("DELETE FROM duplicate_members")
    conn.execute("DELETE FROM duplicate_groups")

    ts = now_iso()
    for group in groups:
        cur = conn.execute(
            "INSERT INTO duplicate_groups (method, created_ts) VALUES (?, ?)",
            (group["method"], ts),
        )
        group_id = cur.lastrowid
        for member in group["members"]:
            image_id = member["image_id"]
            decision, decided_ts, purged_ts = prior.get(image_id, ("pending", None, None))
            conn.execute(
                """
                INSERT INTO duplicate_members (
                    group_id, image_id, distance, recommended_keep,
                    decision, decided_ts, purged_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group_id, image_id, member["distance"], int(member["recommended_keep"]),
                    decision, decided_ts, purged_ts,
                ),
            )
    conn.commit()


def list_groups(conn: sqlite3.Connection, pending_only: bool = True) -> list[dict]:
    """Groups joined with their member images, ordered by group id then
    recommended-keep-first. When ``pending_only``, only groups that still
    have at least one 'pending' member are returned (i.e. still need review)."""
    rows = conn.execute(
        """
        SELECT dm.group_id, dm.image_id, dm.distance, dm.recommended_keep,
               dm.decision, dm.decided_ts, dm.purged_ts,
               dg.method,
               im.rel_path, im.width, im.height, im.file_size,
               im.source_ext, im.source_size, im.is_raw, im.photo_date
        FROM duplicate_members dm
        JOIN duplicate_groups dg ON dg.id = dm.group_id
        JOIN images im ON im.id = dm.image_id
        ORDER BY dm.group_id, dm.recommended_keep DESC, im.rel_path
        """
    ).fetchall()

    groups: dict[int, dict] = {}
    for r in rows:
        g = groups.setdefault(
            r["group_id"], {"group_id": r["group_id"], "method": r["method"], "members": []}
        )
        g["members"].append(
            {
                "image_id": r["image_id"],
                "rel_path": r["rel_path"],
                "width": r["width"],
                "height": r["height"],
                "file_size": r["file_size"],
                "source_ext": r["source_ext"],
                "source_size": r["source_size"],
                "is_raw": bool(r["is_raw"]),
                "photo_date": r["photo_date"],
                "distance": r["distance"],
                "recommended_keep": bool(r["recommended_keep"]),
                "decision": r["decision"],
                "decided_ts": r["decided_ts"],
                "purged_ts": r["purged_ts"],
            }
        )

    result = list(groups.values())
    if pending_only:
        result = [g for g in result if any(m["decision"] == "pending" for m in g["members"])]
    return result


def set_decision(conn: sqlite3.Connection, group_id: int, image_id: int, decision: str) -> None:
    if decision not in _DECISIONS:
        raise ValueError(f"decision must be one of {_DECISIONS}, got {decision!r}")
    cur = conn.execute(
        """
        UPDATE duplicate_members
        SET decision = ?, decided_ts = ?
        WHERE group_id = ? AND image_id = ?
        """,
        (decision, now_iso() if decision != "pending" else None, group_id, image_id),
    )
    if cur.rowcount == 0:
        raise KeyError(f"no duplicate_members row for group {group_id}, image {image_id}")
    conn.commit()


def mark_purged(conn: sqlite3.Connection, group_id: int, image_id: int) -> None:
    conn.execute(
        "UPDATE duplicate_members SET purged_ts = ? WHERE group_id = ? AND image_id = ?",
        (now_iso(), group_id, image_id),
    )
    conn.commit()


def get_stats(conn: sqlite3.Connection) -> dict:
    total_images = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    total_groups = conn.execute("SELECT COUNT(*) FROM duplicate_groups").fetchone()[0]
    by_decision = dict(
        conn.execute(
            "SELECT decision, COUNT(*) FROM duplicate_members GROUP BY decision"
        ).fetchall()
    )
    purged = conn.execute(
        "SELECT COUNT(*) FROM duplicate_members WHERE purged_ts IS NOT NULL"
    ).fetchone()[0]
    return {
        "total_images": total_images,
        "total_groups": total_groups,
        "pending": by_decision.get("pending", 0),
        "kept": by_decision.get("keep", 0),
        "marked_delete": by_decision.get("delete", 0),
        "purged": purged,
    }
