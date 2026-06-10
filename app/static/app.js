"use strict";
const $ = (s) => document.querySelector(s);

let session = null;            // {token, filename, states}
let quests = [];               // [{id, key, name, state}]
const changes = new Map();     // id -> new_state

// ---------------------------------------------------------------- upload
const drop = $("#drop"), fileInput = $("#file");
$("#browse").onclick = () => fileInput.click();
drop.onclick = (e) => { if (e.target.tagName !== "BUTTON") fileInput.click(); };
fileInput.onchange = () => fileInput.files[0] && upload(fileInput.files[0]);
["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add("over");
}));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove("over");
}));
drop.addEventListener("drop", e => {
  const f = e.dataTransfer.files[0];
  if (f) upload(f);
});

async function upload(file) {
  $("#load-error").classList.add("hidden");
  $("#loading").classList.remove("hidden");
  const fd = new FormData();
  fd.append("save", file);
  try {
    const r = await fetch("/api/load", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "failed to read save");
    session = { token: j.token, filename: j.filename, states: j.states };
    quests = j.quests;
    changes.clear();
    showEditor(j);
  } catch (e) {
    $("#load-error").textContent = "⚠ " + e.message;
    $("#load-error").classList.remove("hidden");
  } finally {
    $("#loading").classList.add("hidden");
  }
}

// ---------------------------------------------------------------- editor
function showEditor(j) {
  $("#upload-card").classList.add("hidden");
  $("#edit-card").classList.remove("hidden");
  $("#bar").classList.remove("hidden");
  $("#save-name").textContent = j.filename;
  $("#save-meta").textContent =
    (j.slot ? `“${j.slot}”  ·  ` : "") + `${quests.length} quest objectives`;
  render();
  updateBar();
}

$("#reset").onclick = () => location.reload();
$("#search").oninput = render;
$("#only-changed").onchange = render;

function render() {
  const q = $("#search").value.trim().toLowerCase();
  const onlyChanged = $("#only-changed").checked;
  const list = $("#list");
  const rows = quests.filter(it => {
    if (onlyChanged && !changes.has(it.id)) return false;
    if (!q) return true;
    return it.key.toLowerCase().includes(q);
  });
  $("#count").textContent = `${rows.length} shown`;
  if (!rows.length) { list.innerHTML = `<div class="empty">no matching quests</div>`; return; }

  const opts = (sel) => session.states.map(s =>
    `<option ${s === sel ? "selected" : ""}>${s}</option>`).join("");

  list.innerHTML = rows.slice(0, 600).map(it => {
    const cur = it.state;
    const sel = changes.get(it.id) ?? cur;
    return `<div class="row ${changes.has(it.id) ? "changed" : ""}" data-id="${it.id}">
      <div class="name" title="${esc(it.key)}">${esc(it.name)}<small>${esc(it.key)}</small></div>
      <span class="st ${cur}">${cur}</span>
      <span class="arrow">→</span>
      <select data-id="${it.id}">${opts(sel)}</select>
    </div>`;
  }).join("") + (rows.length > 600 ? `<div class="empty">…and ${rows.length - 600} more — refine your search</div>` : "");

  list.querySelectorAll("select").forEach(s => s.onchange = onPick);
}

function onPick(e) {
  const id = +e.target.dataset.id;
  const it = quests.find(q => q.id === id);
  const val = e.target.value;
  if (val === it.state) changes.delete(id);
  else changes.set(id, val);
  e.target.closest(".row").classList.toggle("changed", changes.has(id));
  updateBar();
}

function updateBar() {
  const n = changes.size;
  $("#pending").textContent = n === 1 ? "1 change" : `${n} changes`;
  $("#generate").disabled = n === 0;
  $("#clear").disabled = n === 0;
}

$("#clear").onclick = () => { changes.clear(); render(); updateBar(); };

// ---------------------------------------------------------------- generate
$("#generate").onclick = async () => {
  const btn = $("#generate");
  btn.disabled = true; btn.textContent = "Recompiling…";
  try {
    const r = await fetch("/api/patch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: session.token, filename: session.filename,
        changes: [...changes].map(([id, new_state]) => ({ id, new_state })),
      }),
    });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || "patch failed"); }
    const blob = await r.blob();
    const cd = r.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename="?([^"]+)"?/);
    download(blob, m ? m[1] : "G1R.fixed.sav");
    toast("✓ Saved. Back up your original, then load the .fixed.sav");
  } catch (e) {
    toast("⚠ " + e.message);
  } finally {
    btn.textContent = "Generate fixed save"; btn.disabled = changes.size === 0;
  }
};

function download(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

let toastT;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.add("hidden"), 5000);
}

const esc = (s) => (s || "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
