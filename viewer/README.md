# Photo Frame — "On This Day"

A Raspberry Pi photo frame that shows a rotating subset of downsized photos from
`$CONVERTED`, one per minute, picking a fresh subset every few hours. The subset
favours **this day in prior years**, prefers **never-shown** photos, then the
**least-recently-shown**. You can flag photos for deletion, or queue a
clockwise rotation fix (90°/180°/270°), from the screen or your phone; a
separate desktop tool actually applies both later.

Note: the slideshow's currently-selected batch of photos above is called the
**subset** (`selector.py`, `/api/subset`, `config.SUBSET_REFRESH_MINS`) — kept
distinct from "rotation", which always means the image-orientation fix below
(`rotate`/`rotate_deg`).

## Design at a glance

- **Datastore:** SQLite via stdlib `sqlite3` (WAL mode), no ORM. Paths are stored
  *relative to* `$CONVERTED` so the same DB/marks resolve on the Pi and the
  desktop even though they mount the tree at different points.
- **Web:** Flask served by `waitress` (the only two dependencies).
- **Frontend:** vanilla HTML/CSS/JS, no build step. Chromium honours EXIF
  orientation, so no image processing is needed.
- **Display:** Chromium `--kiosk` pointed at `http://localhost:5000/slideshow`
  — which also makes the UI reachable from a phone on the same wifi.
- **Routes:** `/` — index page linking to Slideshow and Stats; `/slideshow` —
  the fullscreen kiosk slideshow (moved off `/` so the index page could take
  its place); `/stats` — read-only library/viewing/pending-action counts,
  server-rendered, no JS.

Expected layout of the converted tree (anything else is logged and skipped):

```
$CONVERTED/YYYY/YYYY_MM_DD/[<model-subdir>/]<filename>.jpg
```

## Pi vs desktop split

| Machine | Has        | Runs                                   |
|---------|------------|----------------------------------------|
| Pi      | `$CONVERTED` only (no `$SOURCE`) | `cli.py index / serve / clear-ops` |
| Desktop | `$SOURCE` + `$CONVERTED`        | `../prune/delete_marked.py`, `../prune/apply_rotations.py`, `../sync/sync-converted` |

The Pi only writes delete-marks and pending operations (currently: rotate
fixes) into its own sqlite DB — it never runs the extraction. From the
desktop, `sync/sync-converted pull-marks` just rsyncs a copy of the Pi's DB to
M1; `prune/delete_marked.py` reads the marked rows straight from that copy (no
marks file, no remote CLI) and `purge` removes the **source originals** only.
The converted copies are reconciled by `prune/3prune-orphaned-converted.py`
and propagated to the Pi by the next `sync/sync-converted push` (whose `rsync
--delete` drops them and reindexes the Pi). See the top-level `../README.md`
for the full pipeline.

**Rotate fixes** follow a parallel path: tapping a rotate button in the
slideshow (`POST /api/rotate/<id>` with `{"deg": 90|180|270}`) queues a row in
a `pending_operations` table in the same DB (cumulative — each tap adds
another op; net rotation is the sum mod 360, shown instantly via a CSS
transform client-side). `prune/apply_rotations.py` reads that same pulled DB
copy, composes the net rotation with the SOURCE original's existing EXIF
`Orientation` tag (metadata-only, lossless, works across JPEG/HEIC/RAW), and
deletes the stale converted `.jpg` so the next `ingest/2encode-images-for-viewing.py`
run regenerates it. Unlike deletion, this is a **round trip**: after the
regenerated file is pushed to the Pi, `sync/sync-converted clear-ops` SSHes in
and runs `cli.py clear-ops` to delete the now-applied `pending_operations`
rows from the Pi's *live* DB (M1 never writes into that DB directly — only
the Pi's own Flask app and `cli.py` do). Run `clear-ops` only after the push
has actually delivered the rotated file, never before.

## Configuration (environment variables, with defaults)

| Var | Default | Meaning |
|-----|---------|---------|
| `CONVERTED` | `~/data00/footage_converted` | downsized images root |
| `SOURCE` | `~/data00/footage` | originals (desktop only) |
| `PHOTOFRAME_DB` | `~/photoframe.sqlite` | database (display history + delete marks + pending operations) |
| `PHOTOFRAME_PORT` | `5000` | web port |
| `PHOTOFRAME_N` | `10` | photos per subset (`n`) |
| `PHOTOFRAME_X_MINS` | `180` | minutes before a new subset (`x`) |
| `PHOTOFRAME_WINDOW_DAYS` | `3` | ± days around today |
| `PHOTOFRAME_SLIDE_SECONDS` | `60` | per-photo dwell |
| `PHOTOFRAME_LOG` | `~/photoframe.log` | rotating app log (indexing, selection, marks) |

## Usage

```bash
python -m venv env && ./env/bin/pip install -r requirements.txt

# Pi (or dev): build the index, then serve
CONVERTED=~/data00/footage_converted ./env/bin/python cli.py index
./env/bin/python cli.py serve            # http://<host>:5000

# Inspect a selection without serving (optionally pretend it's another day)
./env/bin/python cli.py select --n 10 --date 2026-06-26

# M1-invoked over ssh by ../sync/sync-converted clear-ops -- not run manually
./env/bin/python cli.py clear-ops --ids-file /path/to/ids
```

The marks-pull and source deletion run on the desktop from the `prune/` and
`sync/` stages — see `../prune/README.md` and the top-level `../README.md`.

## Deploy on the Pi (systemd user units)

Adjust the `Environment=CONVERTED=` line in `systemd/photoframe-web.service` to
the Pi's mount point, then:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/photoframe-*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now photoframe-web.service photoframe-kiosk.service
loginctl enable-linger "$USER"   # so the units run without an active login
```

The existing `raspberrypi-rtc` timer governs the Pi's on/off hours (it halts
8pm–midnight and wakes it), so no sleep logic lives here.
