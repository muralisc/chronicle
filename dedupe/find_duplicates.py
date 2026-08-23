#!/usr/bin/env python3
"""Scan $CONVERTED for exact and near-duplicate images and record the groups
in this tool's own SQLite DB for review -- runs on the desktop (M1), where
both $CONVERTED and $SOURCE exist.

Two-tier detection, both computed on the already-downsized converted JPEGs
(never on the RAW/HEIC originals):
  1. Exact duplicates: SHA-256 of the converted JPEG's bytes. Since
     2encode-images-for-viewing.py is a deterministic function of the source
     file, byte-identical originals produce byte-identical converted JPEGs.
  2. Near-duplicates: perceptual hash (pHash, via `imagehash`+Pillow),
     compared by Hamming distance (<= --threshold, default 10, out of a
     64-bit hash). Comparisons are only made between images whose photo_date
     is the same day or an adjacent day -- duplicates are almost always shot
     close together in time, and this keeps the comparison cost far below a
     full O(n^2) scan of the whole library. Groups are the connected
     components of the resulting distance graph (union-find).

For each group, the member with the "best" resolved SOURCE file is flagged
recommended_keep (RAW beats non-RAW, then larger source file size, then
larger resolution, then earlier photo_date / lexicographically-first
rel_path as a final, deterministic tie-break) -- a recommendation only:
every member starts at decision='pending' until a human decides in the
review UI (see app.py). Source files are recovered the same way
prune/delete_marked.py and prune/apply_rotations.py do: globbing
<stem>* in the mirrored relative source dir.

This tool only ever writes to its own dedupe.sqlite -- it never touches
$CONVERTED or $SOURCE -- so there is no --dry-run flag; rerun as often as
you like. Rerunning recomputes groups from scratch but preserves any
decision already made (see db.replace_groups).

Usage:
    find_duplicates.py
    find_duplicates.py --threshold 6 -j 8 -v
"""

import glob as globmod
import hashlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import click
import imagehash
from PIL import Image

import config
import db

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from media_common import Stats, console, date_from_path, make_progress, print_summary

# Camera RAW formats (mirrors ingest/2encode-images-for-viewing.py's RAW_EXTS;
# duplicated rather than imported -- that script's hyphenated filename isn't a
# valid Python module name, and this is the same small-constant duplication
# the repo already accepts for the stem-resolve helper below).
RAW_EXTS = {"cr3", "cr2", "nef", "arw", "dng", "raf", "orf", "rw2"}


def discover_files(converted_root: Path) -> list[Path]:
    return sorted(p for p in converted_root.rglob("*.jpg") if p.is_file())


def _glob_matches(rel_path: Path, source_root: Path) -> list[Path]:
    """Same stem-glob as prune/delete_marked.py's _resolve: <stem>* in the
    mirrored relative source dir. May include same-stem sidecars."""
    src_dir = source_root / rel_path.parent
    if not src_dir.is_dir():
        return []
    pattern = globmod.escape(rel_path.stem) + "*"
    return sorted(p for p in src_dir.glob(pattern) if p.is_file())


def _is_image_file(path: Path) -> bool:
    """True if exiftool identifies path's content as an image -- used to tell
    a real source apart from a same-stem sidecar (e.g. a RawTherapee .pp3)."""
    proc = subprocess.run(
        ["exiftool", "-s3", "-MIMEType", str(path)],
        capture_output=True, text=True,
    )
    return proc.stdout.strip().startswith("image/")


def resolve_source(rel_path: Path, source_root: Path) -> Optional[Path]:
    """The single unambiguous source image for rel_path, or None if the
    source is missing or ambiguous (matches more than one real image)."""
    matches = _glob_matches(rel_path, source_root)
    if len(matches) <= 1:
        return matches[0] if matches else None
    images = [m for m in matches if _is_image_file(m)]
    return images[0] if len(images) == 1 else None


def hash_one(f: Path, converted_root: Path, source_root: Path, hash_size: int) -> dict:
    """Worker (runs in a thread). Returns the fields for db.upsert_image."""
    rel = f.relative_to(converted_root)
    stat = f.stat()
    with Image.open(f) as img:
        img.load()
        width, height = img.size
        phash = imagehash.phash(img, hash_size=hash_size)
    sha256 = hashlib.sha256(f.read_bytes()).hexdigest()
    photo_date = date_from_path(f)
    source = resolve_source(rel, source_root)
    return {
        "rel_path": str(rel),
        "file_size": stat.st_size,
        "mtime": stat.st_mtime,
        "width": width,
        "height": height,
        "phash": str(phash),
        "sha256": sha256,
        "photo_date": photo_date.strftime("%Y-%m-%d") if photo_date else None,
        "source_ext": source.suffix.lstrip(".").upper() if source else None,
        "source_size": source.stat().st_size if source else None,
        "is_raw": bool(source and source.suffix.lower().lstrip(".") in RAW_EXTS),
    }


def scan(conn, converted_root: Path, source_root: Path, hash_size: int, jobs: int) -> Stats:
    files = discover_files(converted_root)
    stats = Stats()

    to_hash = []
    for f in files:
        rel = str(f.relative_to(converted_root))
        stat = f.stat()
        existing = db.get_image_by_relpath(conn, rel)
        if existing and existing["file_size"] == stat.st_size and existing["mtime"] == stat.st_mtime:
            stats.skipped.append(f)
            continue
        to_hash.append(f)

    if to_hash:
        # PIL/imagehash release the GIL during decode/DCT, so threads give
        # real parallelism here (same rationale as 2encode's ThreadPoolExecutor).
        with make_progress() as prog:
            task = prog.add_task("Hashing", total=len(to_hash))
            with ThreadPoolExecutor(max_workers=jobs) as ex:
                futs = {
                    ex.submit(hash_one, f, converted_root, source_root, hash_size): f
                    for f in to_hash
                }
                for fut in as_completed(futs):
                    f = futs[fut]
                    prog.update(task, advance=1, description=f.name[:40])
                    try:
                        fields = fut.result()
                        db.upsert_image(conn, **fields)
                        stats.ok.append(f)
                    except Exception as e:
                        console.print(f"[red]FAIL[/red]  {f} — {e}")
                        stats.failed.append((f, str(e)))

    print_summary(stats, "hashed", skipped_label="skipped (unchanged)")
    return stats


def _hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _keep_key(img):
    """Ascending sort key -- the first (smallest) item is recommended_keep."""
    return (
        0 if img["is_raw"] else 1,
        -(img["source_size"] or 0),
        -(img["width"] * img["height"]),
        img["photo_date"] or "9999-99-99",
        img["rel_path"],
    )


def _build_group(method: str, ids: list[int], by_id: dict, exact: bool = False) -> dict:
    imgs = [by_id[i] for i in ids]
    keep = min(imgs, key=_keep_key)
    members = [
        {
            "image_id": img["id"],
            "distance": 0 if exact else _hamming(img["phash"], keep["phash"]),
            "recommended_keep": img["id"] == keep["id"],
        }
        for img in imgs
    ]
    return {"method": method, "members": members}


class _UnionFind:
    def __init__(self, ids):
        self.parent = {i: i for i in ids}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def compute_groups(images: list, threshold: int) -> list[dict]:
    by_id = {img["id"]: img for img in images}
    groups = []

    # 1) exact groups via sha256 -- pulled out first so they never also get
    #    considered for (redundant) phash comparison.
    exact_buckets: dict[str, list[int]] = {}
    for img in images:
        exact_buckets.setdefault(img["sha256"], []).append(img["id"])
    exact_ids = set()
    for ids in exact_buckets.values():
        if len(ids) < 2:
            continue
        exact_ids.update(ids)
        groups.append(_build_group("exact", ids, by_id, exact=True))

    # 2) phash groups among the rest, bucketed by photo_date (+/- 1 day) to
    #    keep pairwise comparisons cheap. Images with no photo_date are only
    #    compared against each other.
    remaining = [img for img in images if img["id"] not in exact_ids]
    date_buckets: dict[str, list] = {}
    for img in remaining:
        date_buckets.setdefault(img["photo_date"] or "", []).append(img)

    uf = _UnionFind([img["id"] for img in remaining])

    for key in sorted(k for k in date_buckets if k):
        d = date.fromisoformat(key)
        neighbor_keys = {key, (d - timedelta(days=1)).isoformat(), (d + timedelta(days=1)).isoformat()}
        neighbor_imgs = [img for k in neighbor_keys for img in date_buckets.get(k, [])]
        for a in date_buckets[key]:
            for b in neighbor_imgs:
                if b["id"] != a["id"] and _hamming(a["phash"], b["phash"]) <= threshold:
                    uf.union(a["id"], b["id"])

    unknown = date_buckets.get("", [])
    for i in range(len(unknown)):
        for j in range(i + 1, len(unknown)):
            if _hamming(unknown[i]["phash"], unknown[j]["phash"]) <= threshold:
                uf.union(unknown[i]["id"], unknown[j]["id"])

    clusters: dict[int, list[int]] = {}
    for img in remaining:
        clusters.setdefault(uf.find(img["id"]), []).append(img["id"])
    for ids in clusters.values():
        if len(ids) >= 2:
            groups.append(_build_group("phash", ids, by_id))

    return groups


def run(converted: Path, source: Path, db_path: Path, threshold: int, hash_size: int, jobs: int) -> None:
    conn = db.connect(db_path)
    db.init_schema(conn)
    try:
        scan(conn, converted, source, hash_size, jobs)
        groups = compute_groups(db.all_images(conn), threshold)
        db.replace_groups(conn, groups)
        exact = sum(1 for g in groups if g["method"] == "exact")
        phash = sum(1 for g in groups if g["method"] == "phash")
        console.print(
            f"\n[bold]{len(groups)} duplicate group(s)[/bold]: "
            f"{exact} exact, {phash} near-duplicate (phash distance <= {threshold})"
        )
        console.print(f"[dim]{db_path}[/dim]")
    finally:
        conn.close()


@click.command()
@click.option("--converted", type=click.Path(exists=True, file_okay=False), default=str(config.CONVERTED), show_default=True, help="$CONVERTED root to scan")
@click.option("--source", type=click.Path(file_okay=False), default=str(config.SOURCE), show_default=True, help="$SOURCE root, used to resolve/score originals")
@click.option("--db", "db_path", type=click.Path(), default=str(config.DB_PATH), show_default=True, help="dedupe sqlite DB to write")
@click.option("--threshold", type=int, default=config.THRESHOLD, show_default=True, help="max Hamming distance treated as a near-duplicate")
@click.option("--hash-size", type=int, default=config.HASH_SIZE, show_default=True, help="phash size (hash_size=8 -> 64-bit hash)")
@click.option("-j", "--jobs", type=int, default=os.cpu_count(), show_default=True, help="parallel hashing workers")
def main(converted, source, db_path, threshold, hash_size, jobs):
    run(Path(converted), Path(source), Path(db_path), threshold, hash_size, jobs)


if __name__ == "__main__":
    main()
