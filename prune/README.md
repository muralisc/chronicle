# prune — the M1-side delete cycle

These tools run on **Machine 1** (the desktop, where `$SOURCE` lives) and apply
deletions that were marked on the viewer. `footage` (the originals) is the single
source of truth; `footage_converted` is derived and reconciled to it.

The order is: pull the marks → delete source → reconcile converted → push.

```bash
# 0) pull the latest delete-marks off the Pi (extracts on M1, see ../sync)
../sync/sync-converted pull-marks            # -> ~/chronicle-delete-marks.txt

# 1) delete the SOURCE originals for marked paths (preview, then for real)
venv/bin/python delete_marked.py purge --dry-run
venv/bin/python delete_marked.py purge                  # prompts before deleting
#   --converted-too  also delete the converted .jpg here (default: leave to step 2)

# 2) reconcile footage_converted: drop converted files whose source is now gone
venv/bin/python 3prune-orphaned-converted.py \
    -s ~/data00/footage -c ~/data00/footage_converted   # preview
venv/bin/python 3prune-orphaned-converted.py \
    -s ~/data00/footage -c ~/data00/footage_converted --delete

# 3) propagate the removals to the Pi (rsync --delete + reindex on M2)
../sync/sync-converted push
```

## Files

- `delete_marked.py` — reads the marks file (rel_paths) and deletes the matching
  **source** originals. Stdlib-only; configured via `CONVERTED`, `SOURCE`, and
  `CHRONICLE_MARKS` env vars (defaults under `~/data00`). The source original is
  recovered by globbing `<stem>*` in the mirrored relative dir (it may be
  `.CR3`/`.HEIC` while the converted file is `.jpg`).
- `3prune-orphaned-converted.py` — removes converted files with no surviving
  source. Imports `media_common` from `../common` (needs `click` + `rich`).

## Setup

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt
```
