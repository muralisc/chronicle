#!/usr/bin/env python3
"""Offline rotation-fix tool -- runs on the desktop, where $SOURCE exists.

Reads pending rotate ops straight from the same pulled copy of the Pi's
sqlite DB that ``sync/sync-converted pull-marks`` rsyncs to M1 (the viewer
queues them into a ``pending_operations`` table when the rotate buttons are
tapped). For each photo with a nonzero net pending rotation (clockwise
degrees, summed mod 360), composes it with the SOURCE original's existing
EXIF Orientation tag and rewrites that tag -- metadata-only, lossless, works
across JPEG/HEIC/RAW alike. The converted ``.jpg`` is left alone except for
being deleted so a later ``ingest/2encode-images-for-viewing.py`` run
regenerates it with the new orientation baked in (2encode has no --force
flag; it only (re)encodes files that don't already exist at the destination).

Source is recovered the same way as ``delete_marked.py``: globbing
``<basename-without-ext>*`` in the mirrored relative dir. Paths are stored
relative to $CONVERTED so the same rows resolve on either machine.

Nothing is written without ``--yes`` (or an interactive confirmation); use
``--dry-run`` to preview. After a successful ``apply`` run, the ids of the
pending_operations rows that were applied are written to
$CHRONICLE_ROTATE_IDS_FILE (overwritten each run) for
``sync/sync-converted clear-ops`` to hand to the Pi afterwards -- run that
only after the regenerated converted file has actually been pushed.
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


# Self-contained config (mirrors delete_marked.py) so this M1-side tool has no
# dependency on the viewer package.
CONVERTED = _expand(os.environ.get("CONVERTED", "~/data00/footage_converted"))
SOURCE = _expand(os.environ.get("SOURCE", "~/data00/footage"))
# Same DB copy `sync-converted pull-marks` drops here -- pending_operations
# lives alongside delete-marks in the one file, no separate pull needed.
MARKS_DB = _expand(os.environ.get("CHRONICLE_MARKS_DB", "~/.cache/chronicle/photoframe.sqlite"))
ROTATE_LOG = _expand(os.environ.get("CHRONICLE_ROTATE_LOG", "~/chronicle-rotated.log"))
ROTATE_IDS_FILE = _expand(
    os.environ.get("CHRONICLE_ROTATE_IDS_FILE", "~/.cache/chronicle/chronicle-rotate-applied-ids.txt")
)

# EXIF Orientation codes <-> clockwise degrees, rotation-only (no mirroring).
_DEG_FROM_CODE = {1: 0, 6: 90, 3: 180, 8: 270}
_CODE_FROM_DEG = {v: k for k, v in _DEG_FROM_CODE.items()}


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


def _pending_rotations_from_db(db_path):
    """Group pending rotate ops by rel_path: {rel_path: (op_ids, net_deg)}."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT po.id, po.op, p.rel_path FROM pending_operations po "
            "JOIN photos p ON p.id = po.photo_id "
            "WHERE po.op LIKE 'rotate_%' ORDER BY p.rel_path, po.id"
        ).fetchall()
    finally:
        conn.close()

    grouped = {}
    for op_id, op, rel_path in rows:
        deg = int(op.removeprefix("rotate_"))
        ids, net = grouped.setdefault(rel_path, ([], 0))
        ids.append(op_id)
        grouped[rel_path] = (ids, (net + deg) % 360)
    return grouped


def _read_orientation(path):
    """Current numeric EXIF Orientation of ``path`` (1 if absent/unreadable)."""
    proc = subprocess.run(
        ["exiftool", "-n", "-s3", "-Orientation", str(path)],
        capture_output=True, text=True,
    )
    out = proc.stdout.strip()
    return int(out) if out.isdigit() else 1


def _write_orientation(path, code):
    proc = subprocess.run(
        ["exiftool", "-m", "-overwrite_original_in_place", f"-Orientation#={code}", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError(tail[-1] if tail else f"exiftool exit {proc.returncode}")


def _build_plan(db_path, converted_root, source_root):
    """(plan, skipped). plan entries: (rel_path, op_ids, net_deg, source_path).
    skipped entries: (rel_path, op_ids, reason)."""
    plan, skipped = [], []
    for rel_path, (op_ids, net_deg) in _pending_rotations_from_db(db_path).items():
        _, matches = _resolve(rel_path, converted_root, source_root)
        if len(matches) > 1:
            skipped.append((rel_path, op_ids, f"ambiguous source ({len(matches)} matches)"))
            continue
        if not matches:
            skipped.append((rel_path, op_ids, "no source match"))
            continue
        source = matches[0]
        old_code = _read_orientation(source)
        if old_code not in _DEG_FROM_CODE:
            skipped.append(
                (rel_path, op_ids, f"unsupported/mirrored source Orientation={old_code}, skipping")
            )
            continue
        plan.append((rel_path, op_ids, net_deg, source))
    return plan, skipped


def cmd_list(args):
    plan, skipped = _build_plan(args.from_db, args.converted, args.source)
    print(f"{len(plan)} photo(s) with pending rotation(s) from DB {args.from_db}\n")
    for rel_path, op_ids, net_deg, source in plan:
        old_code = _read_orientation(source)
        old_deg = _DEG_FROM_CODE[old_code]
        new_deg = (old_deg + net_deg) % 360
        state = "NO-OP (cancels out)" if net_deg == 0 else f"{old_deg}° -> {new_deg}°"
        print(f"- {rel_path}  ops={op_ids} net=+{net_deg}°  {state}")
        print(f"    source: {source}")
    for rel_path, op_ids, reason in skipped:
        print(f"SKIP   {rel_path}  ops={op_ids} -- {reason}")


def cmd_apply(args):
    plan, skipped = _build_plan(args.from_db, args.converted, args.source)

    print(f"Will apply rotation to {len(plan)} item(s); skipping {len(skipped)}.\n")
    for rel_path, op_ids, net_deg, source in plan:
        note = "no-op (cancels out)" if net_deg == 0 else f"+{net_deg}°"
        print(f"ROTATE {rel_path}  ops={op_ids}  {note}")
    for rel_path, op_ids, reason in skipped:
        print(f"SKIP   {rel_path}  ops={op_ids} -- {reason}")

    if args.dry_run:
        print("\n[dry-run] nothing written.")
        return
    if not plan:
        print("\nNothing to apply.")
        return
    if not args.yes:
        try:
            input(f"\nPress Enter to rewrite EXIF Orientation for {len(plan)} source(s), Ctrl-C to abort... ")
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    applied_ids = []
    with log_path.open("a") as logf:
        for rel_path, op_ids, net_deg, source in plan:
            try:
                if net_deg != 0:
                    old_code = _read_orientation(source)
                    new_code = _CODE_FROM_DEG[(_DEG_FROM_CODE[old_code] + net_deg) % 360]
                    _write_orientation(source, new_code)
                    converted, _ = _resolve(rel_path, args.converted, args.source)
                    if converted.exists():
                        converted.unlink()
                    logf.write(f"{_now_iso()}\t{rel_path}\t{op_ids}\t+{net_deg}\t{old_code}->{new_code}\n")
                    print(f"rotated {rel_path} (+{net_deg}°)")
                else:
                    logf.write(f"{_now_iso()}\t{rel_path}\t{op_ids}\tnoop\n")
                    print(f"no-op {rel_path} (cancelled out)")
                applied_ids.extend(op_ids)
            except (OSError, RuntimeError) as e:
                print(f"ERROR rotating {rel_path}: {e}", file=sys.stderr)

    ids_path = Path(args.ids_file)
    ids_path.parent.mkdir(parents=True, exist_ok=True)
    ids_path.write_text("\n".join(str(i) for i in applied_ids) + ("\n" if applied_ids else ""))

    print(f"\nApplied {len(applied_ids)} op(s); log appended to {log_path}")
    print(f"Applied op ids written to {ids_path}")
    print("Next: ingest/2encode-images-for-viewing.py (regenerates the deleted converted files),")
    print("      then sync/sync-converted push, then sync/sync-converted clear-ops.")


def build_parser():
    p = argparse.ArgumentParser(prog="apply_rotations", description=__doc__)
    p.add_argument("--from-db", default=str(MARKS_DB),
                   help="pulled sqlite DB to read pending rotate ops from (default)")
    p.add_argument("--converted", type=Path, default=CONVERTED, help="desktop $CONVERTED root")
    p.add_argument("--source", type=Path, default=SOURCE, help="desktop $SOURCE root")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("list", help="preview pending rotations, no writes")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("apply", help="rewrite EXIF Orientation for pending rotations")
    sp.add_argument("--dry-run", action="store_true", help="print plan only")
    sp.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    sp.add_argument("--log", default=str(ROTATE_LOG), help="append applied rotations here")
    sp.add_argument("--ids-file", default=str(ROTATE_IDS_FILE),
                     help="write applied pending_operations ids here (overwritten)")
    sp.set_defaults(func=cmd_apply)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
