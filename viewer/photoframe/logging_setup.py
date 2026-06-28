"""Central logging configuration.

One call to :func:`setup_logging` from each entry point (the web app and the CLI)
wires the root logger to a rotating log file; modules elsewhere just use
``logging.getLogger(__name__)``. The full record (INFO and up) goes to the file,
while the console only sees warnings/errors so it does not clutter the CLI's own
``print`` output or waitress's startup banner.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(log_file: Path, level: int = logging.INFO) -> None:
    """Idempotently configure root logging to ``log_file`` (rotating) + console."""
    global _configured
    if _configured:
        return

    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    root = logging.getLogger()
    root.setLevel(level)

    file_handler = RotatingFileHandler(
        str(log_file), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    root.addHandler(console)

    _configured = True
    logging.getLogger(__name__).info("logging started → %s", log_file)
