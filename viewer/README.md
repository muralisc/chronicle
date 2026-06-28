# Photo Frame — "On This Day"

A Raspberry Pi photo frame that shows a rotating subset of downsized photos from
`$CONVERTED`, one per minute, picking a fresh subset every few hours. The subset
favours **this day in prior years**, prefers **never-shown** photos, then the
**least-recently-shown**. You can flag photos for deletion from the screen or
your phone; a separate desktop tool actually deletes them later.

## Design at a glance

- **Datastore:** SQLite via stdlib `sqlite3` (WAL mode), no ORM. Paths are stored
  *relative to* `$CONVERTED` so the same DB/marks resolve on the Pi and the
  desktop even though they mount the tree at different points.
- **Web:** Flask served by `waitress` (the only two dependencies).
- **Frontend:** vanilla HTML/CSS/JS, no build step. Chromium honours EXIF
  orientation, so no image processing is needed.
- **Display:** Chromium `--kiosk` pointed at `http://localhost:5000` — which also
  makes the UI reachable from a phone on the same wifi.

Expected layout of the converted tree (anything else is logged and skipped):

```
$CONVERTED/YYYY/YYYY_MM_DD/[<model-subdir>/]<filename>.jpg
```

## Pi vs desktop split

| Machine | Has        | Runs                                   |
|---------|------------|----------------------------------------|
| Pi      | `$CONVERTED` only (no `$SOURCE`) | `cli.py index / serve` |
| Desktop | `$SOURCE` + `$CONVERTED`        | `../prune/delete_marked.py`, `../sync/sync-converted` |

The Pi only writes delete-marks into its own sqlite DB — it never runs the
extraction. From the desktop, `sync/sync-converted pull-marks` pulls a copy of
the Pi's DB and runs `cli.py export-marks --stdout` against it locally, writing
`~/chronicle-delete-marks.txt`. `prune/delete_marked.py purge` then removes the
**source originals** only; the converted copies are reconciled by
`prune/3prune-orphaned-converted.py` and propagated to the Pi by the next
`sync/sync-converted push` (whose `rsync --delete` drops them and reindexes the
Pi). See the top-level `../README.md` for the full pipeline.

## Configuration (environment variables, with defaults)

| Var | Default | Meaning |
|-----|---------|---------|
| `CONVERTED` | `~/data00/footage_converted` | downsized images root |
| `SOURCE` | `~/data00/footage` | originals (desktop only) |
| `PHOTOFRAME_DB` | `~/photoframe.sqlite` | database |
| `PHOTOFRAME_MARKS` | `~/photoframe-delete-marks.txt` | exported marks |
| `PHOTOFRAME_PORT` | `5000` | web port |
| `PHOTOFRAME_N` | `10` | photos per rotation (`n`) |
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
