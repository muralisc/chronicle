# dedupe — find, review, and remove duplicate photos

Runs entirely on **Machine 1** (the desktop), since it needs both `$SOURCE`
(the originals, to resolve and delete) and `$CONVERTED` (the downsized JPEGs,
which it hashes and displays thumbnails from). It has its own SQLite DB,
`dedupe.sqlite` -- unlike `photoframe.sqlite`, this one is produced locally by
`find_duplicates.py`; it is never pulled from M2.

The order is: scan → review in the browser → purge → reconcile → push, the
same shape as the [delete cycle](../prune/README.md) but fed from this tool's
own decisions instead of M2's marks.

```bash
# 0) install (own venv, like the other stages)
python -m venv venv && venv/bin/pip install -r requirements.txt
# also needs the exiftool CLI on PATH (used for sidecar/ambiguity checks)

# 1) scan $CONVERTED, hash, group duplicates -- writes only to dedupe.sqlite,
#    never touches $CONVERTED or $SOURCE, so it's always safe to (re)run
venv/bin/python find_duplicates.py
#   --threshold N   max Hamming distance treated as a near-duplicate (default 10)
#   -j N            parallel hashing workers

# 2) review in the browser: for each group, pick which photo to keep
venv/bin/python app.py                       # http://<M1-host>:5050
#   each group shows a "recommended keep" badge (RAW > larger source >
#   higher resolution > earlier date, as a tie-break); nothing is deleted
#   here -- Keep/Delete buttons only record a decision in dedupe.sqlite

# 3) preview, then delete the SOURCE originals for decision='delete' photos
venv/bin/python purge_duplicates.py list
venv/bin/python purge_duplicates.py purge --dry-run
venv/bin/python purge_duplicates.py purge    # prompts before deleting

# 4) reconcile footage_converted and propagate to the Pi, exactly like the
#    normal delete cycle
../prune/venv/bin/python ../prune/3prune-orphaned-converted.py \
    -s ~/data00/footage -c ~/data00/footage_converted --delete
../sync/sync-converted push
```

Rerunning `find_duplicates.py` recomputes groups from scratch but preserves
any decision you've already made for an image that reappears in a new group
-- a rescan never resets your review progress.

## Detection approach

- **Exact duplicates**: SHA-256 of the converted JPEG. Since
  `2encode-images-for-viewing.py` is a deterministic function of the source
  file, byte-identical originals always produce byte-identical converted
  JPEGs.
- **Near-duplicates**: perceptual hash (pHash, DCT-based, via `imagehash`)
  compared by Hamming distance, bucketed by `photo_date` (± 1 day) to keep
  the comparison cost well below a full library-wide scan.
- Both are computed on the already-downsized converted JPEG, never on the
  RAW/HEIC source -- cheap, and avoids needing a RAW decoder in this venv.

## Files

- `config.py` -- env-var config (`CONVERTED`, `SOURCE`, `CHRONICLE_DEDUPE_DB`, …)
- `db.py` -- SQLite schema (`images`, `duplicate_groups`, `duplicate_members`) + helpers
- `find_duplicates.py` -- scan, hash, group; the only writer of `images`/groups
- `app.py` -- Flask review UI (`templates/review.html`, `static/review.{css,js}`)
- `purge_duplicates.py` -- mirrors `../prune/delete_marked.py`: deletes SOURCE
  files for `decision='delete'` members, using the same stem-glob source
  resolution and exiftool-based sidecar/ambiguity check
