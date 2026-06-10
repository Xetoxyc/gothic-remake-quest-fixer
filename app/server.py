"""Flask server for the Gothic 1 Remake quest fixer (local web app)."""
import io
import os
import time
import uuid
import threading

from flask import Flask, request, jsonify, send_file, send_from_directory, abort

import g1r

STATIC = os.path.join(os.path.dirname(__file__), "static")
OODLE_LIB = os.environ.get("OODLE_LIB", "/app/liboo2corelinux64.so.9")
MAX_UPLOAD = 64 * 1024 * 1024          # .sav files are a few MB
SESSION_TTL = 30 * 60                  # seconds

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD

_oodle = None
_sessions = {}                         # token -> {sav, payload, quests, ts}
_lock = threading.Lock()


def oodle():
    global _oodle
    if _oodle is None:
        if not os.path.exists(OODLE_LIB):
            raise RuntimeError(f"Oodle library not found at {OODLE_LIB}")
        _oodle = g1r.Oodle(OODLE_LIB)
    return _oodle


def _gc():
    now = time.time()
    for t in [t for t, s in _sessions.items() if now - s["ts"] > SESSION_TTL]:
        _sessions.pop(t, None)


# ------------------------------------------------------------------ static
@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.route("/<path:p>")
def static_files(p):
    return send_from_directory(STATIC, p)


# ------------------------------------------------------------------ api
@app.post("/api/load")
def load():
    f = request.files.get("save")
    if not f:
        return jsonify(error="no file uploaded"), 400
    data = f.read()
    try:
        c = g1r.Container(data)
        payload = g1r.decompress_payload(c, oodle())
        quests = g1r.list_quests(payload)
    except Exception as e:
        return jsonify(error=str(e)), 400

    token = uuid.uuid4().hex
    with _lock:
        _gc()
        _sessions[token] = {"sav": data, "payload": payload, "ts": time.time()}
        # keep memory bounded: at most 6 sessions
        if len(_sessions) > 6:
            oldest = min(_sessions, key=lambda t: _sessions[t]["ts"])
            _sessions.pop(oldest, None)

    return jsonify(
        token=token,
        filename=f.filename or "G1R.sav",
        slot=g1r.slot_name(c),
        chunks=c.n_chunks,
        states=g1r.EQUEST_STATES,
        quests=[{"id": q["val_off"], "key": q["key"], "name": q["name"], "state": q["state"]}
                for q in quests],
    )


@app.post("/api/patch")
def patch():
    body = request.get_json(force=True, silent=True) or {}
    token = body.get("token")
    changes = body.get("changes") or []
    with _lock:
        sess = _sessions.get(token)
        if sess:
            sess["ts"] = time.time()
    if not sess:
        return jsonify(error="session expired; please re-upload your save"), 410
    if not changes:
        return jsonify(error="no changes selected"), 400

    valid = set(g1r.EQUEST_STATES)
    edits = []
    for ch in changes:
        if ch.get("new_state") not in valid:
            return jsonify(error=f"invalid state {ch.get('new_state')!r}"), 400
        edits.append({"val_off": int(ch["id"]), "new_state": ch["new_state"]})

    try:
        new_payload = g1r.apply_edits(sess["payload"], edits)
        c = g1r.Container(sess["sav"])
        out = g1r.rebuild(c, oodle(), new_payload)
    except Exception as e:
        return jsonify(error=str(e)), 400

    fname = (body.get("filename") or "G1R.sav")
    base = fname[:-4] if fname.lower().endswith(".sav") else fname
    return send_file(io.BytesIO(out), mimetype="application/octet-stream",
                     as_attachment=True, download_name=f"{base}.fixed.sav")


@app.get("/api/health")
def health():
    try:
        ok = os.path.exists(OODLE_LIB)
        return jsonify(ok=ok, oodle=OODLE_LIB)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "3000")), threaded=True)
