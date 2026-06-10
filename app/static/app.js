"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

let session = null;            // {token, filename, states}
let quests = [];               // [{id, key, name, state}]
let attributes = [];           // [{id, set, name, label, value, tab, advanced}]
let skills = [];               // [{id, label, category, tier, tiers}]
let inventory = [];            // [{id, item, label, count}]
let itemDb = [];               // [{id, label, category}] valid items from the save
const invAdds = [];            // [{item, label, count}] queued new items to add
const questChanges = new Map();  // id -> new_state
const attrChanges = new Map();   // id -> number
const skillChanges = new Map();  // id -> new_tier
const invChanges = new Map();    // id -> number

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
drop.addEventListener("drop", e => { const f = e.dataTransfer.files[0]; if (f) upload(f); });

async function upload(file) {
  $("#load-error").classList.add("hidden");
  $("#loading").classList.remove("hidden");
  const fd = new FormData(); fd.append("save", file);
  try {
    const r = await fetch("/api/load", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "failed to read save");
    session = { token: j.token, filename: j.filename, states: j.states };
    quests = j.quests; attributes = j.attributes || []; skills = j.skills || [];
    inventory = j.inventory || [];
    itemDb = j.item_db || []; invAdds.length = 0;
    questChanges.clear(); attrChanges.clear(); skillChanges.clear(); invChanges.clear();
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
  computeGlossCats();
  const gl = quests.filter(isGlossary).length;
  $("#save-meta").textContent =
    (j.slot ? `“${j.slot}”  ·  ` : "") +
    `${attributes.length} attributes · ${inventory.length} items · ${quests.length - gl} quests · ${gl} glossary`;
  renderAttrs("character"); renderSkills();
  renderItemDb(); renderInventory(); renderGlossaryTabs(); renderQuests();
  updateBar();
}

$("#reset").onclick = () => location.reload();

$$(".tab").forEach(t => t.onclick = () => {
  $$(".tab").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $$(".panel").forEach(p => p.classList.toggle("hidden", p.dataset.panel !== t.dataset.tab));
});
$("#adv-toggle").onchange = () => renderAttrs("character");

// ---------------------------------------------------------------- attributes
const setLabel = (s) => s.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " ");

function renderAttrs(tab) {
  const showAdv = $("#adv-toggle").checked;
  const items = attributes.filter(a =>
    a.tab === tab && (tab !== "character" || showAdv || !a.advanced));
  const groups = {};
  items.forEach(a => (groups[a.set] ??= []).push(a));
  const el = $(`#attrs-${tab}`);
  if (!items.length) { el.innerHTML = `<div class="empty">no editable values</div>`; return; }
  el.innerHTML = Object.entries(groups).map(([set, list]) => `
    <div class="grp">
      <h3>${esc(setLabel(set))}</h3>
      <div class="grid">${list.map(attrRow).join("")}</div>
    </div>`).join("");
  el.querySelectorAll("input[data-id]").forEach(i => i.oninput = onAttr);
}

function attrRow(a) {
  const val = attrChanges.has(a.id) ? attrChanges.get(a.id) : a.value;
  return `<div class="attr ${attrChanges.has(a.id) ? "changed" : ""}">
    <label title="${esc(a.name)}">${esc(a.label)}</label>
    <input type="number" step="any" data-id="${a.id}" data-orig="${a.value}" value="${val}">
  </div>`;
}

function onAttr(e) {
  const id = +e.target.dataset.id, orig = parseFloat(e.target.dataset.orig);
  const v = e.target.value;
  if (v === "" || parseFloat(v) === orig) attrChanges.delete(id);
  else attrChanges.set(id, parseFloat(v));
  e.target.closest(".attr").classList.toggle("changed", attrChanges.has(id));
  updateBar();
}

// ---------------------------------------------------------------- skills
function renderSkills() {
  const el = $("#skills-list");
  if (!skills.length) { el.innerHTML = `<div class="empty">no skills found</div>`; return; }
  const groups = {};
  skills.forEach(s => (groups[s.category] ??= []).push(s));
  el.innerHTML = Object.entries(groups).map(([cat, list]) =>
    `<div class="grp"><h3>${esc(cat)}</h3><div class="grid">${list.map(skillRow).join("")}</div></div>`
  ).join("") +
    `<p class="muted">Tier changes &amp; <b>Untrained (unlearn)</b> edit the effect in place.
     <b>“(learn)”</b> options are <b>experimental</b> — they clone an effect spec and retarget it,
     so verify in-game.</p>`;
  el.querySelectorAll("select[data-skill]").forEach(s => s.onchange = onSkill);
}

function skillRow(s) {
  const sel = skillChanges.get(s.id) ?? s.tier;
  const opts = s.tiers.map(o =>
    `<option value="${o.value}" ${o.value === sel ? "selected" : ""}>${esc(o.label)}</option>`).join("");
  return `<div class="attr ${skillChanges.has(s.id) ? "changed" : ""} ${s.learned ? "" : "fresh"}">
    <label title="${esc(s.category)}">${esc(s.label)}${s.learned ? "" : ` <span class="tag">not learned</span>`}</label>
    <select data-skill="${s.id}">${opts}</select>
  </div>`;
}

function onSkill(e) {
  const id = e.target.dataset.skill;          // string fid (numeric for learned, "new:…" for learnable)
  const s = skills.find(x => x.id === id);
  if (e.target.value === s.tier) skillChanges.delete(id);
  else skillChanges.set(id, e.target.value);
  e.target.closest(".attr").classList.toggle("changed", skillChanges.has(id));
  updateBar();
}

// ---------------------------------------------------------------- inventory
$("#search-inv").oninput = renderInventory;
$("#only-changed-inv").onchange = renderInventory;
$("#add-item-btn").onclick = onAddItem;
$("#add-item-key").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); onAddItem(); } };

let _addUid = 0;
const optText = (it) => `${it.label} — ${it.id}`;   // WYSIWYG: what you see is the value

function renderItemDb() {
  // option value IS the visible text (label + id), so picking it is unambiguous
  $("#itemdb").innerHTML = itemDb.map(it => `<option value="${esc(optText(it))}"></option>`).join("");
}

function resolveItemKey(raw) {
  raw = raw.trim();
  if (!raw) return null;
  const picked = itemDb.find(it => optText(it) === raw);     // selected from the list
  if (picked) return picked.id;
  if (/^[A-Za-z0-9_]{2,80}$/.test(raw)) return raw;          // a raw item key
  const byLabel = itemDb.filter(it => it.label.toLowerCase() === raw.toLowerCase());
  return byLabel.length === 1 ? byLabel[0].id : null;        // a unique friendly name
}

function onAddItem() {
  const inp = $("#add-item-key");
  const key = resolveItemKey(inp.value);
  if (!key || !/^[A-Za-z0-9_]{2,80}$/.test(key)) {
    toast("pick an item from the list, or type a key like ItAt_Amulet_Fire"); return;
  }
  const qty = Math.max(1, Math.floor(+$("#add-item-qty").value || 1));
  const known = itemDb.find(it => it.id === key);
  invAdds.push({ uid: ++_addUid, item: key, label: known ? known.label : key, count: qty, known: !!known });
  inp.value = ""; $("#add-item-qty").value = 1;
  renderInventory(); updateBar();
}

function renderInventory() {
  const q = $("#search-inv").value.trim().toLowerCase();
  const onlyChanged = $("#only-changed-inv").checked;
  const el = $("#list-inv");

  const adds = invAdds.map(a => `<div class="attr added">
      <label title="${esc(a.item)}">+ ${esc(a.label)}${a.known ? "" : ` <span class="tag">new key</span>`}<small>${esc(a.item)}</small></label>
      <input type="number" step="1" min="1" data-add="${a.uid}" value="${a.count}">
      <button type="button" class="link rm" data-rm="${a.uid}" title="remove">✕</button>
    </div>`).join("");

  const rows = inventory.filter(it => (!onlyChanged || invChanges.has(it.id))
    && (!q || it.label.toLowerCase().includes(q) || it.item.toLowerCase().includes(q)));
  rows.sort((a, b) => b.count - a.count);
  $("#count-inv").textContent = `${rows.length} shown${invAdds.length ? ` · ${invAdds.length} to add` : ""}`;

  const items = rows.slice(0, 800).map(it => {
    const val = invChanges.has(it.id) ? invChanges.get(it.id) : it.count;
    return `<div class="attr ${invChanges.has(it.id) ? "changed" : ""}">
      <label title="${esc(it.item)}">${esc(it.label)}<small>${esc(it.item)}</small></label>
      <input type="number" step="1" min="0" data-inv="${it.id}" data-orig="${it.count}" value="${val}">
    </div>`;
  }).join("");

  el.innerHTML = (adds ? `<div class="grid">${adds}</div>` : "")
    + (rows.length ? `<div class="grid">${items}</div>` : (adds ? "" : `<div class="empty">no matching items</div>`))
    + (rows.length > 800 ? `<div class="empty">…${rows.length - 800} more — refine search</div>` : "");

  el.querySelectorAll("input[data-inv]").forEach(i => i.oninput = onInvPick);
  el.querySelectorAll("input[data-add]").forEach(i => i.oninput = (e) => {
    const a = invAdds.find(x => x.uid === +e.currentTarget.dataset.add);
    if (a) a.count = Math.max(1, Math.floor(+e.currentTarget.value || 1));
  });
  el.querySelectorAll("[data-rm]").forEach(b => b.onclick = (e) => {
    const uid = +e.currentTarget.dataset.rm;
    const idx = invAdds.findIndex(x => x.uid === uid);
    if (idx >= 0) invAdds.splice(idx, 1);
    renderInventory(); updateBar();
  });
}

function onInvPick(e) {
  const id = +e.target.dataset.inv, orig = +e.target.dataset.orig, v = e.target.value;
  if (v === "" || +v === orig) invChanges.delete(id);
  else invChanges.set(id, Math.max(0, Math.floor(+v)));
  e.target.closest(".attr").classList.toggle("changed", invChanges.has(id));
  updateBar();
}

// ---------------------------------------------------------------- quests + glossary
// glossaries share one structure: Quest_<root> > <Name>Glossary > Unlock + Entry…
// categories are detected from the save (so new ones appear automatically).
let glossCats = [];          // [{root, label}]
let activeGloss = null;      // active sub-tab root
const shortKey = (q) => q.key.split(".").pop();

function computeGlossCats() {
  const keyset = new Set(quests.map(shortKey));
  const roots = new Set();
  for (const k of keyset) {                 // a group overview = "<root>_<Name>Glossary" whose <root> is itself a quest
    if (!k.endsWith("Glossary")) continue;
    const i = k.lastIndexOf("_");
    if (i > 0 && keyset.has(k.slice(0, i))) roots.add(k.slice(0, i));
  }
  glossCats = [...roots].sort().map(root => ({
    root, label: root.replace(/^Quest_/, "").replace(/Glossary$/, "")
  }));
  if (!glossCats.some(c => c.root === activeGloss)) activeGloss = glossCats[0]?.root ?? null;
}

const glossCat = (q) => {
  const k = shortKey(q);
  let best = null;
  for (const c of glossCats)
    if ((k === c.root || k.startsWith(c.root + "_")) && (!best || c.root.length > best.root.length)) best = c;
  return best;
};
const isGlossary = (q) => glossCat(q) !== null;
function glossGroup(q) {                 // "<Name>Glossary" within its category, or null for the root
  const c = glossCat(q); if (!c) return null;
  const k = shortKey(q);
  if (k === c.root) return null;
  const rem = k.slice(c.root.length + 1);
  const i = rem.indexOf("Glossary");
  return i < 0 ? null : rem.slice(0, i + "Glossary".length);
}
const glossGroupLabel = (g) => g.replace(/Glossary$/, "");
function glossEntryLabel(q, c, g) {
  const k = shortKey(q), pre = c.root + "_" + g;
  return k.slice(pre.length + 1).replace(/([a-z])([A-Z0-9])/g, "$1 $2");
}

$("#search-quests").oninput = () => renderQuestPanel("quests");
$("#only-changed-quests").onchange = () => renderQuestPanel("quests");
$("#search-glossary").oninput = renderGlossaryPanel;
$("#only-changed-glossary").onchange = renderGlossaryPanel;

function renderQuests() { renderQuestPanel("quests"); renderGlossaryPanel(); }

const questOpts = (sel) => session.states.map(s =>
  `<option ${s === sel ? "selected" : ""}>${s}</option>`).join("");

function questRow(it, name) {
  const sel = questChanges.get(it.id) ?? it.state;
  return `<div class="row ${questChanges.has(it.id) ? "changed" : ""}">
    <div class="name" title="${esc(it.key)}">${esc(name ?? it.name)}<small>${esc(it.key)}</small></div>
    <span class="st ${it.state}">${it.state}</span><span class="arrow">→</span>
    <select data-id="${it.id}">${questOpts(sel)}</select>
  </div>`;
}

function renderQuestPanel(tab) {
  const q = $(`#search-${tab}`).value.trim().toLowerCase();
  const onlyChanged = $(`#only-changed-${tab}`).checked;
  const list = $(`#list-${tab}`);
  const rows = quests.filter(it => !isGlossary(it)
    && (!onlyChanged || questChanges.has(it.id))
    && (!q || it.key.toLowerCase().includes(q)));
  $(`#count-${tab}`).textContent = `${rows.length} shown`;
  if (!rows.length) { list.innerHTML = `<div class="empty">no matching quests</div>`; return; }
  list.innerHTML = rows.slice(0, 600).map(it => questRow(it)).join("")
    + (rows.length > 600 ? `<div class="empty">…and ${rows.length - 600} more — refine your search</div>` : "");
  list.querySelectorAll("select").forEach(s => s.onchange = onQuestPick);
}

const openGloss = new Set();
const effState = (it) => questChanges.has(it.id) ? questChanges.get(it.id) : it.state;

function glossRow(it, name, state, disabled) {
  const opts = session.states.map(s => `<option ${s === state ? "selected" : ""}>${s}</option>`).join("");
  return `<div class="row ${questChanges.has(it.id) ? "changed" : ""} ${disabled ? "locked" : ""}">
    <div class="name" title="${esc(it.key)}">${esc(name)}<small>${esc(it.key)}</small></div>
    <span class="st ${it.state}">${it.state}</span><span class="arrow">→</span>
    <select data-id="${it.id}" ${disabled ? "disabled" : ""}>${opts}</select>
  </div>`;
}

// derive the locked nodes (group overviews + each category root) from their
// children, over the FULL set (so search/filter never skews the result).
function deriveGlossary() {
  for (const c of glossCats) {
    const inCat = quests.filter(it => glossCat(it) === c);
    const root = inCat.find(it => shortKey(it) === c.root);
    const byGroup = new Map();
    inCat.forEach(it => { const g = glossGroup(it); if (g) (byGroup.get(g) || byGroup.set(g, []).get(g)).push(it); });

    let anyChildEdited = false;
    const groupOv = [];
    for (const [g, list] of byGroup) {
      const overview = list.find(it => shortKey(it) === c.root + "_" + g);
      const unlock = list.find(it => it.key.endsWith("Unlock"));
      const entries = list.filter(it => it !== overview && it !== unlock);
      const childEdited = [unlock, ...entries].some(it => it && questChanges.has(it.id));
      if (childEdited) anyChildEdited = true;
      let ov = overview ? overview.state : "Succeeded";
      if (overview) {
        if (childEdited) {
          const unlocked = unlock ? effState(unlock) === "Succeeded" : true;
          ov = (unlocked && entries.every(e => effState(e) === "Succeeded")) ? "Succeeded" : "Available";
          ov === overview.state ? questChanges.delete(overview.id) : questChanges.set(overview.id, ov);
        } else { questChanges.delete(overview.id); ov = overview.state; }
      }
      groupOv.push(ov);
    }
    if (root) {
      if (anyChildEdited) {
        const rd = groupOv.every(s => s === "Succeeded") ? "Succeeded" : "Available";
        rd === root.state ? questChanges.delete(root.id) : questChanges.set(root.id, rd);
      } else questChanges.delete(root.id);
    }
  }
}

function renderGlossaryTabs() {
  const nav = $("#gloss-subtabs");
  nav.innerHTML = glossCats.length < 2 ? "" : glossCats.map(c =>
    `<button class="subtab ${c.root === activeGloss ? "active" : ""}" data-cat="${esc(c.root)}">${esc(c.label)}</button>`
  ).join("");
  nav.querySelectorAll(".subtab").forEach(b => b.onclick = () => {
    activeGloss = b.dataset.cat;
    renderGlossaryTabs(); renderGlossaryPanel();
  });
}

function renderGlossaryPanel() {
  deriveGlossary();                                   // derive ALL categories
  const c = glossCats.find(x => x.root === activeGloss) || glossCats[0];
  const el = $("#list-glossary");
  if (!c) { el.innerHTML = `<div class="empty">no glossary in this save</div>`; $("#count-glossary").textContent = ""; return; }

  const q = $("#search-glossary").value.trim().toLowerCase();
  const onlyChanged = $("#only-changed-glossary").checked;
  const match = it => (!onlyChanged || questChanges.has(it.id)) && (!q || it.key.toLowerCase().includes(q));
  const grank = (it, g) => it.key.endsWith("Unlock") ? 0 : (shortKey(it) === c.root + "_" + g ? 1 : 2);
  const expand = !!(q || onlyChanged);

  const inCat = quests.filter(it => glossCat(it) === c);
  const shown = inCat.filter(match);
  $("#count-glossary").textContent = `${shown.length} shown`;
  if (!shown.length) { el.innerHTML = `<div class="empty">no matching entries</div>`; return; }

  const root = inCat.find(it => shortKey(it) === c.root);
  const groups = new Map();
  shown.forEach(it => { const g = glossGroup(it); if (g) (groups.get(g) || groups.set(g, []).get(g)).push(it); });

  let html = (root && match(root)) ? glossRow(root, `${c.label} Glossary (auto)`, effState(root), true) : "";
  for (const [g, list] of [...groups].sort((a, b) => a[0].localeCompare(b[0]))) {
    list.sort((a, b) => grank(a, g) - grank(b, g));
    const overview = inCat.find(it => shortKey(it) === c.root + "_" + g);
    const unlock = inCat.find(it => glossGroup(it) === g && it.key.endsWith("Unlock"));
    const unlocked = unlock ? effState(unlock) === "Succeeded" : true;
    const full = inCat.filter(it => glossGroup(it) === g);
    const done = full.filter(it => effState(it) === "Succeeded").length;
    const changed = full.filter(it => questChanges.has(it.id)).length;
    const gid = c.root + "|" + g;
    const open = expand || openGloss.has(gid);
    const body = list.map(it =>
      it === overview ? glossRow(it, "Overview (auto)", effState(it), true)
        : it === unlock ? glossRow(it, "Unlock", effState(it), false)
          : glossRow(it, glossEntryLabel(it, c, g), effState(it), !unlocked)
    ).join("");
    html += `<details class="gloss"${open ? " open" : ""} data-g="${esc(gid)}">
      <summary>${esc(glossGroupLabel(g))} <span class="muted">${done}/${full.length}</span>
        ${changed ? `<span class="tag">${changed} changed</span>` : ""}</summary>
      <div class="gloss-body">${body}</div>
    </details>`;
  }
  el.innerHTML = html;
  el.querySelectorAll("select[data-id]:not([disabled])").forEach(s => s.onchange = onGlossaryPick);
  el.querySelectorAll("details.gloss").forEach(d =>
    d.ontoggle = () => { d.open ? openGloss.add(d.dataset.g) : openGloss.delete(d.dataset.g); });
}

function onGlossaryPick(e) {
  const id = +e.target.dataset.id;
  const it = quests.find(x => x.id === id);
  if (e.target.value === it.state) questChanges.delete(id);
  else questChanges.set(id, e.target.value);
  renderGlossaryPanel();          // re-derive overviews + root + entry gating
  updateBar();
}

function onQuestPick(e) {
  const id = +e.target.dataset.id;
  const it = quests.find(q => q.id === id);
  if (e.target.value === it.state) questChanges.delete(id);
  else questChanges.set(id, e.target.value);
  e.target.closest(".row").classList.toggle("changed", questChanges.has(id));
  updateBar();
}

// ---------------------------------------------------------------- generate
function updateBar() {
  const n = attrChanges.size + questChanges.size + skillChanges.size + invChanges.size + invAdds.length;
  $("#pending").textContent = n === 1 ? "1 change" : `${n} changes`;
  $("#generate").disabled = n === 0;
  $("#clear").disabled = n === 0;
}

$("#clear").onclick = () => {
  attrChanges.clear(); questChanges.clear(); skillChanges.clear(); invChanges.clear();
  invAdds.length = 0;
  renderAttrs("character"); renderSkills();
  renderInventory(); renderQuests(); updateBar();
};

$("#generate").onclick = async () => {
  const btn = $("#generate");
  btn.disabled = true; btn.textContent = "Recompiling…";
  try {
    const r = await fetch("/api/patch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: session.token, filename: session.filename,
        attr_changes: [...attrChanges].map(([id, value]) => ({ id, value })),
        inv_changes: [...invChanges].map(([id, value]) => ({ id, value })),
        inv_adds: invAdds.map(a => ({ item: a.item, count: a.count })),
        skill_changes: [...skillChanges].map(([id, new_tier]) => ({ id, new_tier })),
        quest_changes: [...questChanges].map(([id, new_state]) => ({ id, new_state })),
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
    btn.textContent = "Generate fixed save"; updateBar();
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

const esc = (s) => (s ?? "").toString().replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
