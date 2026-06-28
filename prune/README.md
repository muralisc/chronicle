# prune — the M1-side delete cycle

These tools run on **Machine 1** (the desktop, where `$SOURCE` lives) and apply
deletions that were marked on the viewer. `footage` (the originals) is the single
source of truth; `footage_converted` is derived and reconciled to it.

The order is: pull the marks → delete source → reconcile converted → push.

```bash
# 0) pull a copy of the Pi's DB to M1 (just an rsync, see ../sync)
../sync/sync-converted pull-marks            # -> ~/.cache/chronicle/photoframe.sqlite

# 1) delete the SOURCE originals for marked paths (reads the pulled DB directly)
venv/bin/python delete_marked.py list                   # review what's marked
venv/bin/python delete_marked.py purge --dry-run
venv/bin/python delete_marked.py purge                  # prompts before deleting
#   --converted-too  also delete the converted .jpg here (default: leave to step 2)
#   --marks FILE     read rel_paths from a text file instead of the DB

# 2) reconcile footage_converted: drop converted files whose source is now gone
venv/bin/python 3prune-orphaned-converted.py \
    -s ~/data00/footage -c ~/data00/footage_converted   # preview
venv/bin/python 3prune-orphaned-converted.py \
    -s ~/data00/footage -c ~/data00/footage_converted --delete

# 3) propagate the removals to the Pi (rsync --delete + reindex on M2)
../sync/sync-converted push
```

## Files

- `delete_marked.py` — reads the marked rows straight from the pulled DB copy
  (`--from-db`, default `~/.cache/chronicle/photoframe.sqlite`; `CHRONICLE_MARKS_DB`
  to override) and deletes the matching **source** originals. Stdlib-only;
  `CONVERTED`/`SOURCE` env vars set the roots. The source original is recovered by
  globbing `<stem>*` in the mirrored relative dir (it may be `.CR3`/`.HEIC` while
  the converted file is `.jpg`). `--marks FILE` reads a text list instead.
- `3prune-orphaned-converted.py` — removes converted files with no surviving
  source. Imports `media_common` from `../common` (needs `click` + `rich`).

## Setup

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt
```
