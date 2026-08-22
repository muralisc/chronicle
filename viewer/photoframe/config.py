"""Configuration for the photo frame, read from the environment with defaults.

Everything is overridable via environment variables so the same code runs on the
Pi (only ``CONVERTED`` available, mounted somewhere local) and on the desktop
(where ``SOURCE`` also exists and the offline purge tool runs).
"""

import os
from pathlib import Path


def _expand(value: str) -> Path:
    return Path(value).expanduser()


# Root of the downsized images the slideshow reads. Required for index/serve.
CONVERTED = _expand(os.environ.get("CONVERTED", "~/data00/footage_converted"))

# Root of the original images. Only needed by the desktop purge tool.
SOURCE = _expand(os.environ.get("SOURCE", "~/data00/footage"))

# SQLite database holding display history + delete marks. The desktop reads
# delete-marks straight from a pulled copy of this DB (see ../prune/).
DB_PATH = _expand(os.environ.get("PHOTOFRAME_DB", "~/photoframe.sqlite"))

# Log of files that did not match the canonical layout during indexing.
INDEX_ERROR_LOG = _expand(
    os.environ.get("PHOTOFRAME_INDEX_ERRORS", "~/photoframe-index-errors.log")
)

# Application log (rotating): indexing, subset selection, marks, serving.
LOG_FILE = _expand(os.environ.get("PHOTOFRAME_LOG", "~/photoframe.log"))

# Web server bind.
HOST = os.environ.get("PHOTOFRAME_HOST", "0.0.0.0")
PORT = int(os.environ.get("PHOTOFRAME_PORT", "5000"))

# Slideshow behaviour.
SUBSET_SIZE = int(os.environ.get("PHOTOFRAME_N", "10"))          # n photos per subset
SUBSET_REFRESH_MINS = float(os.environ.get("PHOTOFRAME_X_MINS", "180"))  # new subset every x minutes
WINDOW_DAYS = int(os.environ.get("PHOTOFRAME_WINDOW_DAYS", "3"))   # +/- days around today
SLIDE_SECONDS = int(os.environ.get("PHOTOFRAME_SLIDE_SECONDS", "60"))  # per-photo dwell
