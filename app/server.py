"""Flask server for the Gothic 1 Remake savegame editor (local web app)."""
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
        attrs = g1r.list_player_attributes(payload)
        skills = g1r.list_player_skills(payload)
        inventory = g1r.find_player_inventory(payload)
        item_db = g1r.list_item_db(payload)
        passages = g1r.list_passages(payload)
        behaviours = g1r.list_behaviours(payload)
        crimes = g1r.list_crimes(payload)
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
        attributes=[{"id": a["base_off"], "set": a["set"], "name": a["name"],
                     "label": a["label"], "value": a["value"], "tab": a["tab"],
                     "advanced": a["advanced"]} for a in attrs],
        skills=[{"id": s["fid"], "label": s["label"], "category": s["category"],
                 "tier": s["tier"], "tiers": s["tiers"], "learned": s["learned"]}
                for s in skills],
        inventory=[{"id": it["id"], "item": it["item"], "label": it["label"],
                    "count": it["count"]} for it in inventory],
        item_db=item_db,
        passages=[{"name": p["name"], "value": p["value"]} for p in passages],
        behaviours=behaviours,
        crimes=[{"criminal": c["criminal"], "guild": c["guild"], "guild_label": c["guild_label"],
                 "count": c["count"], "active": c["active"]} for c in crimes],
        quests=[{"id": q["val_off"], "key": q["key"], "name": q["name"], "state": q["state"]}
                for q in quests],
    )


@app.post("/api/patch")
def patch():
    body = request.get_json(force=True, silent=True) or {}
    token = body.get("token")
    quest_changes = body.get("quest_changes") or body.get("changes") or []
    attr_changes = body.get("attr_changes") or []
    skill_changes = body.get("skill_changes") or []
    inv_changes = body.get("inv_changes") or []
    inv_adds = body.get("inv_adds") or []
    passage_changes = body.get("passage_changes") or []
    passage_adds = body.get("passage_adds") or []
    crime_forgive = body.get("crime_forgive") or []
    with _lock:
        sess = _sessions.get(token)
        if sess:
            sess["ts"] = time.time()
    if not sess:
        return jsonify(error="session expired; please re-upload your save"), 410
    if not (quest_changes or attr_changes or skill_changes or inv_changes or inv_adds
            or passage_changes or passage_adds or crime_forgive):
        return jsonify(error="no changes selected"), 400

    aedits = [{"base_off": int(ch["id"]), "value": ch["value"]} for ch in attr_changes]
    iedits = [{"id": int(ch["id"]), "value": ch["value"]} for ch in inv_changes]

    try:
        payload = sess["payload"]
        if aedits:
            payload = g1r.apply_attribute_edits(payload, aedits)   # length-neutral first
        if iedits:
            payload = g1r.apply_inventory_edits(payload, iedits)   # length-neutral too
        if passage_changes:
            payload = g1r.apply_passage_edits(payload, passage_changes)   # length-neutral too
        if crime_forgive:
            payload = g1r.apply_crime_edits(payload, crime_forgive)       # length-neutral too

        # quest + skill edits are length-changing and share ancestors
        # (m_GenericData), so they must be applied together in one pass.
        valid = set(g1r.EQUEST_STATES)
        replaces, deletes = [], []
        if quest_changes:
            qoff = {q["val_off"] for q in g1r.list_quests(payload)}
            for ch in quest_changes:
                if ch.get("new_state") not in valid:
                    return jsonify(error=f"invalid state {ch.get('new_state')!r}"), 400
                if int(ch["id"]) not in qoff:
                    return jsonify(error="unknown quest"), 400
                replaces.append((int(ch["id"]), g1r.ENUM_PREFIX + ch["new_state"]))
        learns = []
        if skill_changes:
            sr, sd, learns = g1r.build_skill_ops(payload, skill_changes)
            replaces += sr
            deletes += sd
        if replaces or deletes:
            payload = g1r.apply_ops(payload, replaces=replaces, deletes=deletes)
        for base, tier in learns:                          # experimental: clone + retarget
            payload = g1r.learn_skill(payload, base, tier)
        for add in inv_adds:                                # experimental: clone an item slot
            payload = g1r.add_item(payload, add["item"], int(add.get("count", 1)))
        for add in passage_adds:                            # experimental: new story flag
            payload = g1r.add_passage(payload, add["name"], int(add.get("value", 1)))

        c = g1r.Container(sess["sav"])
        out = g1r.rebuild(c, oodle(), payload)
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
