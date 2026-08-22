#!/usr/bin/env python3
"""Offline deletion tool -- runs on the desktop, where $SOURCE exists.

Reads the marked rows straight from a copy of the Pi's sqlite DB that
``sync/sync-converted pull-marks`` rsyncs to M1 (no marks file, no remote CLI),
and deletes, for each entry, the original under $SOURCE -- the single source of
truth. The converted ``.jpg`` is left alone; reconcile it afterwards with
``3prune-orphaned-converted.py`` and propagate the removal to the Pi with
``sync/sync-converted push`` (which mirrors with ``--delete``). Pass
``--converted-too`` to also delete the converted copy here.

Source is recovered by globbing ``<basename-without-ext>*`` in the mirrored
relative dir (the source may be .CR3/.HEIC/etc while the converted file is .jpg).
Paths are stored relative to $CONVERTED so the same rows resolve on either
machine. Pass ``--marks FILE`` to read rel_paths from a text file instead of a DB.

That glob also catches same-stem sidecars (e.g. a RawTherapee ``.pp3`` next to
a ``.CR3``); when it returns more than one match, each is checked with
``exiftool -MIMEType`` (the one external dependency this otherwise stdlib-only
tool has) and only real images count towards "ambiguous source" -- once
resolved, every matched file (image + sidecars) is still deleted together, so
sidecars aren't left orphaned behind their image.

Nothing is deleted without ``--yes`` (or an interactive confirmation); use
``--dry-run`` to preview.
"""

import argparse
import glob as globmod
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _expand(value: str) -> Path:
    return Path(value).expanduser()


# Self-contained config (mirrors viewer/photoframe/config.py defaults) so this
# M1-side tool has no dependency on the viewer package.
CONVERTED = _expand(os.environ.get("CONVERTED", "~/data00/footage_converted"))
SOURCE = _expand(os.environ.get("SOURCE", "~/data00/footage"))
# DB copy that `sync-converted pull-marks` drops here (filename mirrors M2's DB).
MARKS_DB = _expand(os.environ.get("CHRONICLE_MARKS_DB", "~/.cache/chronicle/photoframe.sqlite"))
DELETE_LOG = _expand(os.environ.get("CHRONICLE_DELETE_LOG", "~/chronicle-deleted.log"))


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


def _is_image_file(path):
    """True if exiftool identifies ``path``'s content as an image -- used to
    tell a real source apart from a same-stem sidecar (e.g. RawTherapee's
    ``.pp3``), which the naive ``<stem>*`` glob also matches."""
    proc = subprocess.run(
        ["exiftool", "-s3", "-MIMEType", str(path)],
        capture_output=True, text=True,
    )
    return proc.stdout.strip().startswith("image/")


def _image_matches(matches):
    """Filter glob matches down to real images when there's more than one
    (skip the exiftool calls in the common single-match case)."""
    if len(matches) <= 1:
        return matches
    return [m for m in matches if _is_image_file(m)]


def _read_marks(marks_file):
    lines = Path(marks_file).read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip()]


def _marked_from_db(db_path):
    """Read marked rel_paths directly from a pulled photoframe sqlite DB."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT rel_path FROM photos WHERE marked_for_delete = 1 ORDER BY rel_path"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _load_rels(args):
    """(rel_paths, human-readable source description). Text file overrides the DB."""
    if args.marks:
        return _read_marks(args.marks), f"marks file {args.marks}"
    return _marked_from_db(args.from_db), f"DB {args.from_db}"


def cmd_list(args):
    converted_root, source_root = args.converted, args.source
    rels, src_desc = _load_rels(args)
    print(f"{len(rels)} marked path(s) from {src_desc}\n")
    for rel in rels:
        converted, matches = _resolve(rel, converted_root, source_root)
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
        print(f"- {rel}")
        print(f"    converted: {converted} [{c_state}]")
        print(f"    source:    {s_state}")


def cmd_purge(args):
    converted_root, source_root = args.converted, args.source
    rels, _ = _load_rels(args)

    plan = []        # (rel, converted_path, [source_files])
    skipped = []     # (rel, reason)
    for rel in rels:
        converted, matches = _resolve(rel, converted_root, source_root)
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
        print("Next: sync/sync-converted push to propagate the removals to the Pi.")
    else:
        print("Next: 3prune-orphaned-converted.py to reconcile converted,")
        print("      then sync/sync-converted push to propagate the removals to the Pi.")


def build_parser():
    p = argparse.ArgumentParser(prog="delete_marked", description=__doc__)
    p.add_argument("--from-db", default=str(MARKS_DB),
                   help="pulled sqlite DB to read marked rows from (default)")
    p.add_argument("--marks", default=None,
                   help="read rel_paths from this text file instead of the DB")
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
        default=str(DELETE_LOG),
        help="append deleted paths here",
    )
    sp.set_defaults(func=cmd_purge)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
