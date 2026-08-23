"""Flask web app: duplicate-group review UI.

Runs on the desktop (M1) -- reads/writes this tool's own dedupe.sqlite
(written by find_duplicates.py) and serves thumbnails straight out of
$CONVERTED. Decisions made here (keep/delete per image) are just DB writes;
nothing is ever deleted by this app -- run purge_duplicates.py separately
once you're happy with your decisions (see README.md).
"""

import logging

from flask import Flask, abort, g, jsonify, render_template, request, send_file

import config
import db

logging.basicConfig(filename=str(config.LOG_FILE), level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)

db.init_schema(db.connect(config.DB_PATH))


def _get_conn():
    if "conn" not in g:
        g.conn = db.connect(config.DB_PATH)
        g.conn.execute("PRAGMA busy_timeout=5000;")
    return g.conn


@app.teardown_appcontext
def _close_conn(exc):
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


@app.route("/")
def index():
    return render_template("review.html")


@app.route("/api/groups")
def api_groups():
    return jsonify({"groups": db.list_groups(_get_conn(), pending_only=True)})


@app.route("/api/stats")
def api_stats():
    return jsonify(db.get_stats(_get_conn()))


@app.route("/thumb/<int:image_id>")
def thumb(image_id):
    row = db.get_image(_get_conn(), image_id)
    if row is None:
        abort(404)
    path = config.CONVERTED / row["rel_path"]
    if not path.exists():
        log.warning("image %s file missing on disk: %s", image_id, path)
        abort(404)
    return send_file(path)


_VALID_DECISIONS = {"keep", "delete", "pending"}


@app.route("/api/decide", methods=["POST"])
def api_decide():
    body = request.get_json(silent=True) or {}
    group_id, image_id, decision = body.get("group_id"), body.get("image_id"), body.get("decision")
    if decision not in _VALID_DECISIONS or not isinstance(group_id, int) or not isinstance(image_id, int):
        abort(400)
    try:
        db.set_decision(_get_conn(), group_id, image_id, decision)
    except KeyError:
        abort(404)
    log.info("group %s image %s decision -> %s", group_id, image_id, decision)
    return jsonify({"group_id": group_id, "image_id": image_id, "decision": decision})


@app.route("/api/groups/<int:group_id>/apply_recommended", methods=["POST"])
def api_apply_recommended(group_id):
    conn = _get_conn()
    groups = {g["group_id"]: g for g in db.list_groups(conn, pending_only=False)}
    group = groups.get(group_id)
    if group is None:
        abort(404)
    for member in group["members"]:
        decision = "keep" if member["recommended_keep"] else "delete"
        db.set_decision(conn, group_id, member["image_id"], decision)
    log.info("group %s: applied recommended decisions", group_id)
    return jsonify({"group_id": group_id, "applied": True})


def main():
    from waitress import serve

    print(f"[dedupe] DB: {config.DB_PATH}")
    print(f"[dedupe] serving on http://{config.HOST}:{config.PORT}")
    log.info("serving on http://%s:%s", config.HOST, config.PORT)
    serve(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
