#!/usr/bin/env python3
"""Command-line entrypoint for the photo frame (stdlib argparse).

Subcommands:
  index      walk $CONVERTED, (re)index images, prune missing rows
  select     pick a fresh subset now and print it (debug/inspection)
  serve      run the web app (waitress)
  clear-ops  delete applied pending_operations rows by id (M1-invoked, remote)

Delete-marks and pending operations (e.g. queued rotations) live in the DB;
the desktop reads them straight from a pulled copy (see ../prune/), so there
is no marks-export step here. After M1 applies a pending operation to the
SOURCE original, it calls `clear-ops` here (over ssh, via
../sync/sync-converted clear-ops) to drop the applied rows from this DB.
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from photoframe import config, db, indexer, logging_setup, selector

log = logging.getLogger(__name__)


def _open():
    conn = db.connect(config.DB_PATH)
    db.init_schema(conn)
    return conn


def cmd_index(args):
    conn = _open()
    stats = indexer.index(conn, config.CONVERTED, config.INDEX_ERROR_LOG)
    print(
        f"index: added={stats['added']} existing={stats['existing']} "
        f"skipped={stats['skipped']} pruned={stats['pruned']} total={stats['total']}"
    )
    if stats["skipped"]:
        print(f"  non-canonical paths logged to {config.INDEX_ERROR_LOG}")


def cmd_select(args):
    conn = _open()
    today = date.fromisoformat(args.date) if args.date else None
    rows = selector.select_subset(conn, args.n, args.window_days, today=today)
    print(f"selected {len(rows)} photos for {today or date.today()}:")
    today_year = (today or date.today()).year
    for i, r in enumerate(rows, 1):
        years_ago = today_year - int(r["photo_date"][:4])
        shown = r["last_displayed"] or "never"
        print(
            f"  {i:2d}. {r['photo_date']} ({years_ago}y ago)  "
            f"shown={shown} count={r['display_count']}  {r['rel_path']}"
        )


def cmd_serve(args):
    import app

    app.main()


def cmd_clear_ops(args):
    conn = _open()
    if args.ids_file:
        ids = [int(x) for x in Path(args.ids_file).read_text().split() if x.strip()]
    else:
        ids = [int(x) for x in sys.stdin.read().split() if x.strip()]
    n = db.clear_pending_operations(conn, ids)
    print(f"cleared {n} of {len(ids)} requested pending_operations row(s)")


def build_parser():
    p = argparse.ArgumentParser(prog="photoframe", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("index", help="index $CONVERTED and prune missing rows")
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("select", help="pick a subset now and print it")
    sp.add_argument("--n", type=int, default=config.SUBSET_SIZE)
    sp.add_argument("--window-days", type=int, default=config.WINDOW_DAYS)
    sp.add_argument("--date", help="pretend today is this YYYY-MM-DD (for testing)")
    sp.set_defaults(func=cmd_select)

    sp = sub.add_parser("serve", help="run the web app (waitress)")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser(
        "clear-ops",
        help="delete applied pending_operations rows by id (invoked remotely by sync-converted clear-ops)",
    )
    sp.add_argument("--ids-file", help="read ids from this file instead of stdin")
    sp.set_defaults(func=cmd_clear_ops)

    return p


def main():
    logging_setup.setup_logging(config.LOG_FILE)
    args = build_parser().parse_args()
    log.info("cli: %s", args.command)
    args.func(args)


if __name__ == "__main__":
    main()
