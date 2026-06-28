# ingest — import & downsize footage

Runs on Machine 1. Brings a media dump into `footage` (organized by EXIF date)
and downsizes it into `footage_converted` for the viewer. Shared helpers live in
`../common/media_common.py` (imported via a small `sys.path` shim at the top of
each script). The delete/reconcile tools moved to `../prune/`.

## Setup

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt
# also needs the exiftool and imagemagick (`magick`) CLIs on PATH
```

## Script 1 — `1import-media-by-exif.py`

```bash
venv/bin/python 1import-media-by-exif.py \
    --dst ~/data00/footage \
    --op mv --default-camera iPhone_13_mini \
    --src ~/data00/footage/uncategorised/whatsapp-8kuzhi-sisters
```

## Script 2 — `2encode-images-for-viewing.py`

```bash
venv/bin/python 2encode-images-for-viewing.py \
    -s ~/data00/footage/ \
    -d ~/data00/footage_converted/ \
    -vvvn \
    --regex '2023' \
    --ignore 'murali_kuru_marriage'
```

## Verbose semantics (`-v` / `-vv` / `-vvv`)

Both ingest scripts (and `../prune/3prune-orphaned-converted.py`) share the same
verbosity behaviour. Summary counts are always shown regardless of verbosity.

| Level     | `1import`             | `2encode`             | `3prune` (in prune/)       |
| --------- | --------------------- | --------------------- | -------------------------- |
| (default) | filenames, no SKIP    | filenames, no SKIP    | filenames, no KEEP         |
| `-v`      | src-relative paths    | src-relative paths    | converted-relative paths   |
| `-vv`     | + per-file SKIP lines | + per-file SKIP lines | + per-file KEEP lines      |
| `-vvv`    | full absolute paths   | full absolute paths   | full absolute paths        |
