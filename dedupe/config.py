"""Configuration for the duplicate-review tool, read from the environment with
defaults (mirrors ``viewer/photoframe/config.py``).

Runs entirely on M1: unlike the viewer, this needs both ``$CONVERTED`` (to
hash) and ``$SOURCE`` (to resolve/delete originals), so it has no M2 half.
"""

import os
from pathlib import Path


def _expand(value: str) -> Path:
    return Path(value).expanduser()


# Root of the downsized images this tool hashes and displays thumbnails from.
CONVERTED = _expand(os.environ.get("CONVERTED", "~/data00/footage_converted"))
if not CONVERTED.is_dir():
    raise NotADirectoryError(f"CONVERTED is not a valid directory: {CONVERTED}")

# Root of the original images -- resolved via the same stem-glob delete_marked.py
# and apply_rotations.py use, and the only thing purge_duplicates.py deletes from.
SOURCE = _expand(os.environ.get("SOURCE", "~/data00/footage"))

# This tool's own SQLite DB. Unlike photoframe.sqlite, it is produced directly
# by find_duplicates.py on M1 -- it is never pulled from M2.
DB_PATH = _expand(os.environ.get("CHRONICLE_DEDUPE_DB", "~/.cache/chronicle/dedupe.sqlite"))

# Log of source files purge_duplicates.py has actually deleted.
DELETE_LOG = _expand(
    os.environ.get("CHRONICLE_DEDUPE_DELETE_LOG", "~/chronicle-dedupe-deleted.log")
)

# Application log (rotating).
LOG_FILE = _expand(os.environ.get("CHRONICLE_DEDUPE_LOG", "~/chronicle-dedupe.log"))

# Web server bind. Distinct port from the viewer's 5000 (different machine
# anyway, but keep them distinguishable in shared docs/shell history).
HOST = os.environ.get("DEDUPE_HOST", "0.0.0.0")
PORT = int(os.environ.get("DEDUPE_PORT", "5050"))

# Grouping defaults for find_duplicates.py (overridable via CLI flags).
HASH_SIZE = int(os.environ.get("CHRONICLE_DEDUPE_HASH_SIZE", "8"))       # -> 64-bit phash
THRESHOLD = int(os.environ.get("CHRONICLE_DEDUPE_THRESHOLD", "10"))      # max Hamming distance
