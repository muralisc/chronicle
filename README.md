# chronicle

A two-machine pipeline for organizing footage (photos, and video later) and
showing it on an "on this day in prior years" frame.

**`footage` (the originals on the desktop) is the single source of truth.**
`footage_converted` is 100% derived from it and is mirrored to the viewer; it is
never edited in place — additions, encodes, and deletions all flow from the
desktop and are pushed with `rsync --delete`.

## Machine split

| Machine | Has | Runs |
|---------|-----|------|
| **M1** desktop (source of truth, not always on) | `footage` + `footage_converted` | `ingest/`, `prune/` (incl. rotate fixes), `sync/sync-converted` |
| **M2** Pi (always-on viewer) | `footage_converted` only | `viewer/` (`cli.py serve / index`) — also queues delete-marks and rotate ops |

## The pipeline

```
M1  ingest/1import-media-by-exif.py        dump    -> footage
M1  ingest/2encode-images-for-viewing.py   footage -> footage_converted   (idempotent)
M1  sync/sync-converted push               footage_converted --delete--> M2

M2  viewer/cli.py serve                     show photos; user marks deletes and queues
                                            rotate fixes on screen/phone (both live in
                                            the Pi's sqlite DB)

# delete cycle (run on M1 when convenient):
M1  sync/sync-converted pull-marks          rsync a copy of the Pi's DB to M1
M1  prune/delete_marked.py list             review the marked rows (read from that DB)
M1  prune/delete_marked.py purge            delete SOURCE originals only (confirm)
M1  prune/3prune-orphaned-converted.py      remove now-orphaned converted on M1
M1  sync/sync-converted push                --delete propagates removals -> M2
                                            (push also reindexes on M2 to drop rows)

# rotate cycle (independent of the delete cycle, same pulled DB):
M1  prune/apply_rotations.py apply          write EXIF Orientation on SOURCE only (confirm)
M1  ingest/2encode-images-for-viewing.py    regenerate converted files apply_rotations.py deleted
M1  sync/sync-converted push                deliver the regenerated converted files -> M2
M1  sync/sync-converted clear-ops           tell M2 to drop the applied pending_operations rows
```

## Layout

```
chronicle/
  common/   media_common.py — shared date/EXIF/progress helpers (ingest + prune)
  ingest/   1import / 2encode — import & downsize footage          (see ingest/README.md)
  prune/    delete_marked.py + 3prune — the M1 delete cycle        (see prune/README.md)
  viewer/   the photo-frame web app (runs on the Pi)               (see viewer/README.md)
  sync/     sync-converted — rsync M1<->M2 wrapper + marks pull
```

## Setup

Each Python subtree keeps its own venv:

```bash
python -m venv ingest/venv && ingest/venv/bin/pip install -r ingest/requirements.txt
python -m venv prune/venv  && prune/venv/bin/pip  install -r prune/requirements.txt
python -m venv viewer/env  && viewer/env/bin/pip  install -r viewer/requirements.txt
# ingest also needs the exiftool / imagemagick CLIs on PATH (see ingest/README.md)
```

`sync/sync-converted` is pure bash; configure the M2 host and paths via env vars
(see the header of that script). Defaults assume `M2_HOST=pi@machine2` and
`footage_converted` under `~/data00/` on both machines.
