#!/usr/bin/env python3
"""Offline deletion tool -- runs on the desktop, where $SOURCE exists.

Reads the marks file pulled from the Pi (one rel_path per line, produced by
``cli.py export-marks`` and fetched by ``sync/sync-converted pull-marks``) and
deletes, for each entry, the original under $SOURCE -- the single source of
truth. The converted ``.jpg`` is left alone; reconcile it afterwards with
``ingest/3prune-orphaned-converted.py`` and propagate the removal to the Pi with
``sync/sync-converted push`` (which mirrors with ``--delete``). Pass
``--converted-too`` to also delete the converted copy here.

Source is recovered by globbing ``<basename-without-ext>*`` in the mirrored
relative dir (the source may be .CR3/.HEIC/etc while the converted file is .jpg).

Nothing is deleted without ``--yes`` (or an interactive confirmation); use
``--dry-run`` to preview. Paths are relative to $CONVERTED so the same marks
file works even though the Pi mounts the converted tree elsewhere.
"""

import argparse
import glob as globmod
import os
import sys
from datetime import datetime
from pathlib import Path


def _expand(value: str) -> Path:
    return Path(value).expanduser()


# Self-contained config (mirrors viewer/photoframe/config.py defaults) so this
# M1-side tool has no dependency on the viewer package.
CONVERTED = _expand(os.environ.get("CONVERTED", "~/data00/footage_converted"))
SOURCE = _expand(os.environ.get("SOURCE", "~/data00/footage"))
MARKS_FILE = _expand(os.environ.get("CHRONICLE_MARKS", "~/chronicle-delete-marks.txt"))


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _resolve(rel_path, converted_root, source_root):
    """Return (converted_path, [source_matches]) for one rel_path."""
    rel = Path(rel_path)
    converted = converted_root / rel
    src_dir = source_root / rel.parent
    stem = rel.stem
    matches = []
    if src_dir.is_dir():
        pattern = globmod.escape(stem) + "*"
        matches = sorted(p for p in src_dir.glob(pattern) if p.is_file())
    return converted, matches


def _read_marks(marks_file):
    lines = Path(marks_file).read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip()]


def cmd_list(args):
    converted_root, source_root = args.converted, args.source
    rels = _read_marks(args.marks)
    print(f"{len(rels)} marked path(s) in {args.marks}\n")
    for rel in rels:
        converted, matches = _resolve(rel, converted_root, source_root)
        c_state = "ok" if converted.exists() else "MISSING"
        if len(matches) == 1:
            s_state = str(matches[0])
        elif not matches:
            s_state = "NO SOURCE MATCH"
        else:
            s_state = f"AMBIGUOUS ({len(matches)} matches)"
        print(f"- {rel}")
        print(f"    converted: {converted} [{c_state}]")
        print(f"    source:    {s_state}")


def cmd_purge(args):
    converted_root, source_root = args.converted, args.source
    rels = _read_marks(args.marks)

    plan = []        # (rel, converted_path, [source_files])
    skipped = []     # (rel, reason)
    for rel in rels:
        converted, matches = _resolve(rel, converted_root, source_root)
        if len(matches) > 1:
            skipped.append((rel, f"ambiguous source ({len(matches)} matches)"))
            continue
        if not matches:
            skipped.append((rel, "no source match"))
            continue
        plan.append((rel, converted, matches))

    keep_note = "" if args.converted_too else "  (kept -- reconcile with 3prune)"
    print(f"Will delete {len(plan)} item(s); skipping {len(skipped)}.\n")
    for rel, converted, matches in plan:
        print(f"DELETE {rel}")
        miss = "" if converted.exists() else "  (missing)"
        print(f"   converted: {converted}{miss if args.converted_too else keep_note}")
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
        for rel, converted, matches in plan:
            targets = [*matches, converted] if args.converted_too else list(matches)
            for target in targets:
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
    print(f"\nDeleted {deleted} file(s); log appended to {log_path}")
    if args.converted_too:
        print("Run `cli.py index` on the Pi to prune the now-missing rows.")
    else:
        print("Next: ingest/3prune-orphaned-converted.py to reconcile converted,")
        print("      then sync/sync-converted push to propagate the removals to M2.")


def build_parser():
    p = argparse.ArgumentParser(prog="delete_marked", description=__doc__)
    p.add_argument("--marks", default=str(MARKS_FILE), help="marks file (rel_paths)")
    p.add_argument("--converted", type=Path, default=CONVERTED, help="desktop $CONVERTED root")
    p.add_argument("--source", type=Path, default=SOURCE, help="desktop $SOURCE root")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("list", help="preview what is marked, no deletion")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("purge", help="delete source originals for marked paths")
    sp.add_argument("--dry-run", action="store_true", help="print plan only")
    sp.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    sp.add_argument(
        "--converted-too",
        action="store_true",
        help="also delete the converted .jpg (default: leave it for 3prune + sync)",
    )
    sp.add_argument(
        "--log",
        default=str(MARKS_FILE.with_name("chronicle-deleted.log")),
        help="append deleted paths here",
    )
    sp.set_defaults(func=cmd_purge)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
