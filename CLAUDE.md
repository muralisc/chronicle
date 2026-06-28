# CLAUDE.md

Guidance for working in **chronicle** — a two-machine pipeline that organizes
footage (photos, video later) and shows it on an "on this day in prior years"
frame. See `README.md` for the user-facing overview.

## Core model

- **`footage` (originals on M1) is the single source of truth.**
  `footage_converted` is 100% derived and is never edited in place — additions,
  re-encodes, and deletions all flow from M1 and are mirrored with
  `rsync --delete`.
- **Two machines:**
  - **M1** = desktop, source of truth, *not always on*. Runs `ingest/`,
    `prune/`, `sync/`.
  - **M2** = Raspberry Pi, always-on viewer, has `footage_converted` only. Runs
    `viewer/` (`cli.py serve / index`).
- **Delete-marks live only in M2's sqlite DB.** M2 never runs extraction logic;
  M1 pulls a copy of the DB and reads it.

## Layout

```
chronicle/
  common/   media_common.py — shared date/EXIF/progress helpers (rich; exiftool is lazy)
  ingest/   1import (organize by EXIF) + 2encode (downsize)        [Python venv]
  prune/    delete_marked.py (delete source) + 3prune (reconcile converted) [Python venv]
  viewer/   Flask photo-frame app + photoframe/ package (runs on the Pi)    [Python venv: env/]
  sync/     sync-converted — bash rsync/ssh wrapper (push + pull-marks)
```

Each `*/README.md` documents that stage. `sync/` is the only non-Python piece.

## The pipeline

```
M1  ingest/2encode  footage -> footage_converted   (1import fills footage first)
M1  sync/sync-converted push                        footage_converted --delete--> M2
M2  viewer/cli.py serve                             user marks deletes (-> M2's DB)

# delete cycle, on M1:
M1  sync/sync-converted pull-marks      rsync M2's photoframe.sqlite* -> ~/.cache/chronicle/
M1  prune/delete_marked.py list/purge   reads that DB; deletes SOURCE originals only
M1  prune/3prune-orphaned-converted.py  drops converted whose source is now gone
M1  sync/sync-converted push            --delete propagates removals + reindexes M2
```

## Conventions / gotchas

- **`common/` sharing:** scripts in `ingest/` and `prune/` import `media_common`
  via a 2-line `sys.path` shim (`parent.parent / "common"`) at the top — keep it
  when adding/moving such scripts. `common/` is an implicit namespace dir (no
  `__init__.py`).
- **`exiftool` is imported lazily** inside `media_common.read_metadata()`, so
  EXIF-free consumers (`prune/3prune`, `prune/delete_marked`) don't need
  `PyExifTool`. Don't hoist that import back to module top.
- **`prune/delete_marked.py` is intentionally stdlib-only** and has **no
  dependency on the `viewer/photoframe` package** — keep it self-contained
  (config via `CONVERTED`/`SOURCE`/`CHRONICLE_MARKS_DB` env vars). It reads marks
  straight from the pulled DB (`--from-db`); `--marks FILE` is an alt text input.
- **`sync/sync-converted` is pure bash** (rsync + ssh orchestration). The marks
  path must stay CLI-free; `push` may still `ssh … cli.py index` because that is
  M2 maintaining its *own* DB, not a cross-machine dependency.
- **Source ↔ converted mapping:** converted is `<stem>.jpg`; the source original
  is recovered by globbing `<stem>*` in the mirrored relative dir (it may be
  `.CR3`/`.HEIC`/…). Paths are stored relative to `$CONVERTED` so the same rows
  resolve on either machine.
- **Layout on disk:** `$CONVERTED/YYYY/YYYY_MM_DD/[<model-subdir>/]<file>.jpg`.
- **Deletions are confirmed:** `delete_marked.py` and `3prune` default to preview;
  real deletes need `--yes`/`--delete` or an interactive prompt. Keep that.

## Dev commands

```bash
# per-stage venvs (sync needs none)
python -m venv ingest/venv && ingest/venv/bin/pip install -r ingest/requirements.txt
python -m venv prune/venv  && prune/venv/bin/pip  install -r prune/requirements.txt
python -m venv viewer/env  && viewer/env/bin/pip  install -r viewer/requirements.txt
# ingest also needs the exiftool + imagemagick (`magick`) CLIs on PATH

# quick smoke checks
ingest/venv/bin/python ingest/2encode-images-for-viewing.py --help
prune/venv/bin/python  prune/delete_marked.py --help        # stdlib-only
viewer/env/bin/python  viewer/cli.py select --n 5 --date 2026-06-26
bash -n sync/sync-converted
```

There is no test suite; verify changes by running the relevant `--help`/dry-run
(`-n` / `--dry-run` / preview) and, for the viewer, `cli.py select`.
