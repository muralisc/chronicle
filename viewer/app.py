"""Flask web app: fullscreen slideshow + mark-for-delete API.

Runs on the Pi (Chromium kiosk) and is reachable from a phone on the same wifi.
Only ``$CONVERTED`` and the SQLite DB are needed here -- no ``$SOURCE``.
"""

import logging
from datetime import date

from flask import Flask, abort, g, jsonify, render_template, send_file

from photoframe import config, db, indexer, logging_setup, selector

# Configure logging however the app is launched (waitress, flask dev server, …).
logging_setup.setup_logging(config.LOG_FILE)
log = logging.getLogger(__name__)

app = Flask(__name__)

# Ensure the schema exists once at import time.
db.init_schema(db.connect(config.DB_PATH))


def _get_conn():
    """One SQLite connection per request thread (WAL handles concurrency)."""
    if "conn" not in g:
        g.conn = db.connect(config.DB_PATH)
        g.conn.execute("PRAGMA busy_timeout=5000;")
    return g.conn


@app.teardown_appcontext
def _close_conn(exc):
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


def _rotation_payload(rows):
    today_year = date.today().year
    payload = []
    for r in rows:
        photo_year = int(r["photo_date"][:4])
        payload.append(
            {
                "id": r["id"],
                "url": f"/photo/{r['id']}",
                "date": r["photo_date"],
                "years_ago": today_year - photo_year,
                "rel_path": r["rel_path"],
                "marked": bool(r["marked_for_delete"]),
            }
        )
    return payload


@app.route("/")
def home():
    return render_template("slideshow.html", slide_seconds=config.SLIDE_SECONDS)


@app.route("/api/rotation")
def api_rotation():
    conn = _get_conn()
    rows = selector.ensure_rotation(
        conn, config.SUBSET_SIZE, config.WINDOW_DAYS, config.ROTATION_MINS
    )
    return jsonify({"photos": _rotation_payload(rows), **selector.rotation_meta(conn)})


@app.route("/api/rotation/reselect", methods=["POST"])
def api_reselect():
    conn = _get_conn()
    log.info("on-demand reselection requested")
    rows = selector.select_rotation(conn, config.SUBSET_SIZE, config.WINDOW_DAYS)
    return jsonify({"photos": _rotation_payload(rows), **selector.rotation_meta(conn)})


@app.route("/photo/<int:photo_id>")
def photo(photo_id):
    row = db.get_photo(_get_conn(), photo_id)
    if row is None:
        log.warning("photo %s not in database", photo_id)
        abort(404)
    path = config.CONVERTED / row["rel_path"]
    if not path.exists():
        log.warning("photo %s file missing on disk: %s", photo_id, path)
        abort(404)
    return send_file(path)


@app.route("/api/mark/<int:photo_id>", methods=["POST"])
def api_mark(photo_id):
    try:
        new_state = db.toggle_mark(_get_conn(), photo_id)
    except KeyError:
        log.warning("mark requested for unknown photo %s", photo_id)
        abort(404)
    log.info("photo %s mark %s", photo_id, "set" if new_state else "cleared")
    return jsonify({"id": photo_id, "marked": new_state})


def main():
    """Serve via waitress (production) for ``cli.py serve``."""
    from waitress import serve

    # Index once at startup so a fresh DB is populated.
    startup_conn = db.connect(config.DB_PATH)
    db.init_schema(startup_conn)
    stats = indexer.index(startup_conn, config.CONVERTED, config.INDEX_ERROR_LOG)
    startup_conn.close()
    print(f"[photoframe] index: {stats}")
    print(f"[photoframe] serving on http://{config.HOST}:{config.PORT}")
    log.info("serving on http://%s:%s", config.HOST, config.PORT)
    serve(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
