#!/usr/bin/env python3
"""Command-line entrypoint for the photo frame (stdlib argparse).

Subcommands:
  index         walk $CONVERTED, (re)index images, prune missing rows
  select        pick a fresh rotation now and print it (debug/inspection)
  serve         run the web app (waitress)
  export-marks  write rel_paths flagged for deletion to the marks file
"""

import argparse
import logging
from datetime import date

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
    rows = selector.select_rotation(conn, args.n, args.window_days, today=today)
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


def cmd_export_marks(args):
    conn = _open()
    rel_paths = db.marked_rel_paths(conn)
    out = config.MARKS_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(p + "\n" for p in rel_paths))
    print(f"wrote {len(rel_paths)} marked path(s) to {out}")


def build_parser():
    p = argparse.ArgumentParser(prog="photoframe", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("index", help="index $CONVERTED and prune missing rows")
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("select", help="pick a rotation now and print it")
    sp.add_argument("--n", type=int, default=config.SUBSET_SIZE)
    sp.add_argument("--window-days", type=int, default=config.WINDOW_DAYS)
    sp.add_argument("--date", help="pretend today is this YYYY-MM-DD (for testing)")
    sp.set_defaults(func=cmd_select)

    sp = sub.add_parser("serve", help="run the web app (waitress)")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("export-marks", help="write marked rel_paths to the marks file")
    sp.set_defaults(func=cmd_export_marks)

    return p


def main():
    logging_setup.setup_logging(config.LOG_FILE)
    args = build_parser().parse_args()
    log.info("cli: %s", args.command)
    args.func(args)


if __name__ == "__main__":
    main()
