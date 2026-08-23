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
    `prune/`, `dedupe/`, `sync/`.
  - **M2** = Raspberry Pi, always-on viewer, has `footage_converted` only. Runs
    `viewer/` (`cli.py serve / index`).
- **Delete-marks live only in M2's sqlite DB.** M2 never runs extraction logic;
  M1 pulls a copy of the DB and reads it.
- **Pending photo-edit operations (currently: rotate fixes) live in the same
  M2 sqlite DB, in a `pending_operations` table.** M1 pulls the same DB copy
  (no separate sync step), applies each op to the SOURCE original (EXIF
  `Orientation` only — never touching `footage_converted` directly), then
  tells M2 to clear the applied rows via `sync/sync-converted clear-ops` ->
  `cli.py clear-ops`. M1 never writes into M2's live DB directly.

## Layout

```
chronicle/
  common/   media_common.py — shared date/EXIF/progress helpers (rich; exiftool is lazy)
  ingest/   1import (organize by EXIF) + 2encode (downsize)        [Python venv]
  prune/    delete_marked.py (delete source) + 3prune (reconcile converted) [Python venv]
  dedupe/   find_duplicates.py (hash/group) + app.py (review UI) + purge_duplicates.py [Python venv]
  viewer/   Flask photo-frame app + photoframe/ package (runs on the Pi)    [Python venv: env/]
  sync/     sync-converted — bash rsync/ssh wrapper (push + pull-marks)
```

Each `*/README.md` documents that stage. `sync/` is the only non-Python piece.
`dedupe/` runs entirely on M1 (it needs both `footage` and `footage_converted`,
unlike `viewer/`) and owns its own SQLite DB, `dedupe.sqlite` — produced
locally by `find_duplicates.py`, never pulled from M2 like `photoframe.sqlite`
is.

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

# rotate cycle, on M1 (independent of the delete cycle, same pulled DB):
M1  sync/sync-converted pull-marks      (same DB pull as above -- pending_operations lives alongside marks)
M1  prune/apply_rotations.py apply      reads pending rotate ops; writes EXIF Orientation on SOURCE only
M1  ingest/2encode-images-for-viewing.py regenerates the converted files apply_rotations.py deleted
M1  sync/sync-converted push            delivers the regenerated converted files to M2
M1  sync/sync-converted clear-ops       tells M2 to drop the pending_operations rows just applied

# dedupe cycle, on M1 (independent, own local-only dedupe.sqlite -- no pull step):
M1  dedupe/find_duplicates.py           hashes footage_converted, writes groups to dedupe.sqlite
M1  dedupe/app.py                       browser review UI; Keep/Delete only record a decision
M1  dedupe/purge_duplicates.py purge    deletes SOURCE for decision='delete' photos
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
- **`prune/delete_marked.py` is intentionally stdlib-only** (no `PyExifTool`,
  no dependency on the `viewer/photoframe` package) — keep it self-contained
  (config via `CONVERTED`/`SOURCE`/`CHRONICLE_MARKS_DB` env vars). It reads marks
  straight from the pulled DB (`--from-db`); `--marks FILE` is an alt text input.
  It does shell out to the `exiftool` **CLI** (not the library) when a glob
  resolves to more than one match, to tell a real image apart from a same-stem
  sidecar (see below) — that's its one external-process dependency.
- **`sync/sync-converted` is pure bash** (rsync + ssh orchestration). The marks
  path must stay CLI-free; `push` may still `ssh … cli.py index` because that is
  M2 maintaining its *own* DB, not a cross-machine dependency.
- **Source ↔ converted mapping:** converted is `<stem>.jpg`; the source original
  is recovered by globbing `<stem>*` in the mirrored relative dir (it may be
  `.CR3`/`.HEIC`/…). Paths are stored relative to `$CONVERTED` so the same rows
  resolve on either machine. That glob also catches same-stem sidecars (e.g. a
  RawTherapee `.pp3` next to a `.CR3`); when it returns more than one match,
  `delete_marked.py` and `apply_rotations.py` both check each with
  `exiftool -MIMEType` and only count real images (`image/*`) as "ambiguous" —
  sidecars are reported for debugging but don't block resolution, and
  `delete_marked.py` still deletes every matched file (image + sidecars)
  together once resolved.
- **Layout on disk:** `$CONVERTED/YYYY/YYYY_MM_DD/[<model-subdir>/]<file>.jpg`.
- **Deletions are confirmed:** `delete_marked.py` and `3prune` default to preview;
  real deletes need `--yes`/`--delete` or an interactive prompt. Keep that.
- **`prune/apply_rotations.py` mutates SOURCE originals' EXIF `Orientation`**,
  so it follows the same confirmed-by-default posture as `delete_marked.py`
  (`--dry-run` / `--yes` / interactive prompt). It's stdlib + `exiftool`
  subprocess only (no Pillow anywhere in this repo — everything shells out to
  `magick`/`exiftool`), and reuses the same pulled DB (`CHRONICLE_MARKS_DB`)
  as `delete_marked.py` rather than a separate sync step, since
  `pending_operations` lives in the same M2 sqlite file as delete-marks.
- **`dedupe/` duplicates the stem-glob `_resolve`/`_is_image_file` helper and
  the `RAW_EXTS` constant a third time** (`delete_marked.py` and
  `apply_rotations.py` already each have their own copy) rather than
  centralizing them — keep doing that if you touch this again; it's an
  intentional pattern in this repo, not something to "fix" into a partial
  shared module.
- **"rotation" means image-orientation only.** The slideshow's currently-
  selected batch of photos is called the **subset** (`subset`/`subset_meta`
  tables, `selector.py`'s `select_subset`/`ensure_subset`/`subset_meta`,
  `/api/subset`(`/reselect`), `config.SUBSET_REFRESH_MINS`, the JS `subset`
  array) — it was originally named "rotation" too, which collided with this
  photo-rotation feature; it was renamed to `subset` (matching
  `config.SUBSET_SIZE`'s existing naming) precisely to keep "rotation"
  unambiguous. Never reintroduce bare "rotation" for the subset concept.

## Dev commands

```bash
# per-stage venvs (sync needs none)
python -m venv ingest/venv && ingest/venv/bin/pip install -r ingest/requirements.txt
python -m venv prune/venv  && prune/venv/bin/pip  install -r prune/requirements.txt
python -m venv dedupe/venv && dedupe/venv/bin/pip install -r dedupe/requirements.txt
python -m venv viewer/env  && viewer/env/bin/pip  install -r viewer/requirements.txt
# ingest and dedupe also need the exiftool CLI on PATH; ingest also needs imagemagick (`magick`)

# quick smoke checks
ingest/venv/bin/python ingest/2encode-images-for-viewing.py --help
prune/venv/bin/python  prune/delete_marked.py --help        # stdlib-only
dedupe/venv/bin/python dedupe/find_duplicates.py --help
dedupe/venv/bin/python dedupe/purge_duplicates.py --help
viewer/env/bin/python  viewer/cli.py select --n 5 --date 2026-06-26
bash -n sync/sync-converted
```

There is no test suite; verify changes by running the relevant `--help`/dry-run
(`-n` / `--dry-run` / preview) and, for the viewer, `cli.py select`.
