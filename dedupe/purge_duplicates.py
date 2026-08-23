#!/usr/bin/env python3
"""Offline deletion tool -- runs on the desktop (M1), where $SOURCE exists.

Reads duplicate_members rows with decision='delete' from this tool's own
dedupe.sqlite (written by find_duplicates.py, decided via app.py's review
UI) and deletes, for each one, the resolved SOURCE original -- the single
source of truth. The converted .jpg is left alone; reconcile it afterwards
with ../prune/3prune-orphaned-converted.py and propagate the removal to the
Pi with ../sync/sync-converted push (which mirrors with --delete).

This intentionally mirrors ../prune/delete_marked.py almost exactly (same
stem-glob source resolution, same exiftool-based sidecar/ambiguity check,
same --dry-run/--yes/interactive-prompt posture) -- decision='delete' in the
review UI is not by itself sufficient to delete anything; this script
re-resolves and re-verifies the source before touching it. On a successful
delete the corresponding duplicate_members row is stamped with purged_ts (it
is not removed, for audit purposes).

Nothing is deleted without --yes (or an interactive confirmation); use
--dry-run to preview.
"""

import argparse
import glob as globmod
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import config
import db


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _resolve(rel_path, converted_root, source_root):
    """Return (converted_path, [source_matches]) for one rel_path."""
    rel = Path(rel_path)
    converted = converted_root / rel
    src_dir = source_root / rel.parent
    matches = []
    if src_dir.is_dir():
        pattern = globmod.escape(rel.stem) + "*"
        matches = sorted(p for p in src_dir.glob(pattern) if p.is_file())
    return converted, matches


def _is_image_file(path):
    """True if exiftool identifies path's content as an image -- used to tell
    a real source apart from a same-stem sidecar (e.g. RawTherapee's .pp3),
    which the naive <stem>* glob also matches."""
    proc = subprocess.run(
        ["exiftool", "-s3", "-MIMEType", str(path)],
        capture_output=True, text=True,
    )
    return proc.stdout.strip().startswith("image/")


def _image_matches(matches):
    if len(matches) <= 1:
        return matches
    return [m for m in matches if _is_image_file(m)]


def _open_db(args):
    conn = db.connect(args.db)
    db.init_schema(conn)
    return conn


def _pending_deletes(conn):
    """(group_id, image_id, rel_path) for every decision='delete' member not
    yet purged."""
    rows = conn.execute(
        """
        SELECT dm.group_id, dm.image_id, im.rel_path
        FROM duplicate_members dm
        JOIN images im ON im.id = dm.image_id
        WHERE dm.decision = 'delete' AND dm.purged_ts IS NULL
        ORDER BY im.rel_path
        """
    ).fetchall()
    return [(r["group_id"], r["image_id"], r["rel_path"]) for r in rows]


def cmd_list(args):
    conn = _open_db(args)
    entries = _pending_deletes(conn)
    print(f"{len(entries)} item(s) marked for deletion in {args.db}\n")
    for group_id, image_id, rel in entries:
        converted, matches = _resolve(rel, args.converted, args.source)
        c_state = "ok" if converted.exists() else "MISSING"
        image_matches = _image_matches(matches)
        if len(image_matches) == 1:
            extra = f"  (+ {len(matches) - 1} sidecar file(s))" if len(matches) > 1 else ""
            s_state = f"{image_matches[0]}{extra}"
        elif not image_matches:
            non_image = f" (found only non-image file(s): {', '.join(str(m) for m in matches)})" if matches else ""
            s_state = f"NO SOURCE MATCH{non_image}"
        else:
            paths = ", ".join(str(m) for m in image_matches)
            s_state = f"AMBIGUOUS ({len(image_matches)} image files): {paths}"
        print(f"- group {group_id}  {rel}")
        print(f"    converted: {converted} [{c_state}]")
        print(f"    source:    {s_state}")


def cmd_purge(args):
    conn = _open_db(args)
    entries = _pending_deletes(conn)

    plan = []     # (group_id, image_id, rel, source_files)
    skipped = []  # (rel, reason)
    for group_id, image_id, rel in entries:
        _, matches = _resolve(rel, args.converted, args.source)
        image_matches = _image_matches(matches)
        if len(image_matches) > 1:
            paths = ", ".join(str(m) for m in image_matches)
            skipped.append((rel, f"ambiguous source ({len(image_matches)} image files): {paths}"))
            continue
        if not image_matches:
            non_image = f" (found only non-image file(s), left alone: {', '.join(str(m) for m in matches)})" if matches else ""
            skipped.append((rel, f"no source match{non_image}"))
            continue
        # Delete every matched file (image + any sidecars like .pp3/.xmp)
        # together, so a sidecar isn't left orphaned behind its image.
        plan.append((group_id, image_id, rel, matches))

    print(f"Will delete {len(plan)} item(s); skipping {len(skipped)}.\n")
    for group_id, image_id, rel, matches in plan:
        print(f"DELETE {rel}  (kept the rest of group {group_id})")
        for m in matches:
            print(f"   source:    {m}")
    for rel, reason in skipped:
        print(f"SKIP   {rel}  -- {reason}")

    if args.dry_run:
        print("\n[dry-run] nothing deleted.")
        return
    if not plan:
        print("\nNothing to delete.")
        return
    if not args.yes:
        try:
            input(f"\nPress Enter to permanently delete {len(plan)} item(s), Ctrl-C to abort... ")
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    deleted = 0
    with log_path.open("a") as logf:
        for group_id, image_id, rel, matches in plan:
            ok = True
            for target in matches:
                try:
                    if target.exists():
                        target.unlink()
                        logf.write(f"{_now_iso()}\t{target}\n")
                        deleted += 1
                        print(f"removed {target}")
                    else:
                        print(f"skip (already gone) {target}")
                except OSError as e:
                    print(f"ERROR removing {target}: {e}", file=sys.stderr)
                    ok = False
            if ok:
                db.mark_purged(conn, group_id, image_id)
    print(f"\nDeleted {deleted} file(s); log appended to {log_path}")
    print("Next: ../prune/3prune-orphaned-converted.py to reconcile converted,")
    print("      then ../sync/sync-converted push to propagate the removals to the Pi.")


def build_parser():
    p = argparse.ArgumentParser(prog="purge_duplicates", description=__doc__)
    p.add_argument("--db", type=Path, default=config.DB_PATH, help="dedupe sqlite DB (default: $CHRONICLE_DEDUPE_DB)")
    p.add_argument("--converted", type=Path, default=config.CONVERTED, help="desktop $CONVERTED root")
    p.add_argument("--source", type=Path, default=config.SOURCE, help="desktop $SOURCE root")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("list", help="preview what is marked for deletion, no deletion")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("purge", help="delete source originals for decision='delete' duplicates")
    sp.add_argument("--dry-run", action="store_true", help="print plan only")
    sp.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    sp.add_argument("--log", default=str(config.DELETE_LOG), help="append deleted paths here")
    sp.set_defaults(func=cmd_purge)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
