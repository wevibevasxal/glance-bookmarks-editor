/* glance-bookmarks-editor front end. Vanilla JS; DOM built with textContent only (no innerHTML with data). */
"use strict";

const CDN = "https://cdn.jsdelivr.net/gh/selfhst/icons/webp/";
const EMOJI = ["🎉", "💍", "✈️", "🏖️", "🎄", "🎓", "👶", "❤️", "🏠", "🚗", "🎁", "🩺", "⚽", "🎃", "🎆", "🐣", "🌸", "📅"];
const S = {
  csrf: "", loginCsrf: "", view: "links", w: 0,
  links: null, linksOrig: "", cd: null, cdOrig: "",
  index: null, indexLoading: null, library: null,
};

/* ------------------------------------------------------------ helpers */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false)
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}
const clone = (o) => JSON.parse(JSON.stringify(o));
const pad = (n) => String(n).padStart(2, "0");

function toast(text, kind = "ok", ms = 4500) {
  const t = h("div", { class: `toast ${kind}`, text });
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "err" ? 12000 : ms);
}

class ApiError extends Error {
  constructor(msg, status, data) { super(msg); this.status = status; this.data = data || {}; }
}
async function api(method, path, body) {
  const opt = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  if (method !== "GET") opt.headers["X-CSRF-Token"] = path === "/api/login" ? S.loginCsrf : S.csrf;
  let r;
  try { r = await fetch(path, opt); } catch (e) { throw new ApiError("Can't reach the editor (network error)", 0); }
  let data = {};
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (r.status === 401 && path !== "/api/login" && path !== "/api/session") {
    showLogin("Your session ended. Sign in again — unsaved changes are kept.");
    throw new ApiError(data.error || "login required", 401, data);
  }
  if (!r.ok) throw new ApiError(data.error || `HTTP ${r.status}`, r.status, data);
  return data;
}

function confirmDlg(title, text, { ok = "OK", cancel = "Cancel", pre = null, danger = false } = {}) {
  const d = $("#confirm-dlg");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text || "";
  const p = $("#confirm-pre");
  p.hidden = pre === null;
  p.replaceChildren();
  if (pre !== null) {
    for (const line of String(pre).split("\n")) {
      const cls = /^\+(?!\+\+)/.test(line) ? "add" : /^-(?!--)/.test(line) ? "del" : null;
      p.append(h("span", { class: cls, text: line + "\n" }));
    }
  }
  $("#confirm-ok").textContent = ok;
  $("#confirm-ok").className = "btn " + (danger ? "danger" : "primary");
  $("#confirm-cancel").textContent = cancel;
  $("#confirm-cancel").hidden = cancel === null;
  d.returnValue = "";
  d.showModal();
  return new Promise((res) => d.addEventListener("close", () => res(d.returnValue === "ok"), { once: true }));
}

function showSaveError(e, what) {
  if (e.status === 409 && e.data.kind === "conflict") {
    confirmDlg("Changed elsewhere", e.message, { ok: "Reload now", cancel: "Keep my edits" })
      .then((yes) => { if (yes) { S.links = null; S.cd = null; loadView(S.view, true); } });
  } else if (e.data && e.data.detail) {
    confirmDlg(`${what} not saved`, e.message, { pre: e.data.detail, cancel: null });
  } else if (e.status !== 401) {
    toast(`${what} not saved: ${e.message}`, "err");
  }
}

function reportReload(res, what) {
  if (!res || res.status === "unchanged") { toast(`${what}: nothing changed`); return; }
  const r = res.reload || {};
  if (r.state === "reloaded") toast(`${what} saved · Glance reloaded`);
  else if (r.state === "errors") toast(`${what} saved, but Glance reports:\n${r.message}`, "err");
  else toast(`${what} saved · ${r.message || "reload not confirmed"}`, "warn");
}

/* ------------------------------------------------------------ login */
function showLogin(msg) {
  $("#boot").hidden = true;
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#login-msg").textContent = msg || "";
  $("#login-msg").className = "msg err";
  api("GET", "/api/session").then(enterApp).catch((e) => {
    if (e.status === 401) {
      S.loginCsrf = e.data.login_csrf || "";
      if (e.data.password_set === false)
        $("#login-msg").textContent = "No password set yet. In the container run: glance-bookmarks-editor passwd";
    }
  });
  setTimeout(() => $("#login-form [name=password]").focus(), 50);
}

$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const btn = $("button[type=submit]", f);
  btn.disabled = true;
  $("#login-msg").textContent = "";
  try {
    const r = await api("POST", "/api/login", { user: f.user.value, password: f.password.value });
    f.password.value = "";
    enterApp(r);
  } catch (e) {
    $("#login-msg").textContent = e.message;
    if (e.status === 403) {   // login token expired: get a fresh one
      try { await api("GET", "/api/session"); } catch (e2) { S.loginCsrf = e2.data.login_csrf || ""; }
    }
  } finally { btn.disabled = false; }
});

function enterApp(sess) {
  S.csrf = sess.csrf;
  $("#login").hidden = true;
  $("#boot").hidden = true;
  $("#app").hidden = false;
  loadView(S.view);
}

$("#logout").addEventListener("click", async () => {
  if (isDirty() && !(await confirmDlg("Sign out?", "You have unsaved changes; they will be lost.", { ok: "Sign out", danger: true }))) return;
  try { await api("POST", "/api/logout", {}); } catch (e) { /* ignore */ }
  S.csrf = ""; S.links = null; S.cd = null; S.linksOrig = ""; S.cdOrig = "";
  updateSavebar();
  showLogin("Signed out.");
  $("#login-msg").className = "msg";
});

/* ------------------------------------------------------------ navigation */
$("#nav").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-view]");
  if (b) loadView(b.dataset.view);
});

async function loadView(view, force = false) {
  S.view = view;
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  try {
    if (view === "links") {
      if (!S.links || force) { S.links = await api("GET", "/api/links"); S.linksOrig = JSON.stringify(S.links.widgets); }
      renderLinks();
    } else if (view === "countdown") {
      if (!S.cd || force) { S.cd = await api("GET", "/api/countdown"); S.cdOrig = cdKey(); }
      renderCountdown();
    } else if (view === "icons") {
      await loadLibrary();
      renderLibrary();
    } else if (view === "history") {
      renderHistory(await api("GET", "/api/history"));
    }
  } catch (e) {
    if (e.status !== 401) toast(e.message, "err");
  }
  updateSavebar();
}

/* ------------------------------------------------------------ dirty state + save */
function cdKey() { return S.cd ? JSON.stringify([S.cd.birthdays, S.cd.events]) : ""; }
function linksDirty() { return !!S.links && JSON.stringify(S.links.widgets) !== S.linksOrig; }
function cdDirty() { return !!S.cd && cdKey() !== S.cdOrig; }
function isDirty() { return linksDirty() || cdDirty(); }

function updateSavebar() {
  const parts = [];
  if (linksDirty()) parts.push("tiles");
  if (cdDirty()) parts.push("countdown");
  $("#savebar").hidden = parts.length === 0;
  $("#savebar-text").textContent = `Unsaved changes: ${parts.join(" and ")}`;
}
window.addEventListener("beforeunload", (ev) => { if (isDirty()) { ev.preventDefault(); ev.returnValue = ""; } });

$("#discard").addEventListener("click", async () => {
  if (!(await confirmDlg("Discard changes?", "Your unsaved edits will be lost.", { ok: "Discard", danger: true }))) return;
  if (linksDirty()) { S.links.widgets = JSON.parse(S.linksOrig); }
  if (cdDirty()) { const [b, e] = JSON.parse(S.cdOrig); S.cd.birthdays = b; S.cd.events = e; }
  loadView(S.view);
});

$("#save").addEventListener("click", async () => {
  const btn = $("#save");
  // Save exactly what was unsaved at the moment of the click; edits made while the
  // check runs stay marked as unsaved.
  const doLinks = linksDirty(), doCd = cdDirty();
  const linksSnap = doLinks ? JSON.stringify(S.links.widgets) : null;
  const cdSnap = doCd ? cdKey() : null;
  if (doCd) {
    const err = cdValidate();
    if (err) { toast(`Countdown not saved: ${err}`, "err"); loadView("countdown"); if (!doLinks) return; }
  }
  btn.disabled = true;
  btn.textContent = "Checking…";
  try {
    if (doLinks) {
      try {
        const r = await api("POST", "/api/links", { sha: S.links.sha, widgets: JSON.parse(linksSnap) });
        S.links.sha = r.sha; S.linksOrig = linksSnap;
        reportReload(r, "Tiles");
      } catch (e) { showSaveError(e, "Tiles"); }
    }
    if (doCd && !cdValidate()) {
      const [birthdays, events] = JSON.parse(cdSnap);
      try {
        const r = await api("POST", "/api/countdown", { sha: S.cd.sha, birthdays, events });
        S.cd.sha = r.sha; S.cdOrig = cdSnap;
        reportReload(r, "Countdown");
      } catch (e) { showSaveError(e, "Countdown"); }
    }
  } finally {
    btn.disabled = false; btn.textContent = "Save";
    S.library = null;
    if (S.view === "links") renderLinks();
    updateSavebar();
  }
});

/* ------------------------------------------------------------ sections (bookmarks widgets) */
const sectionName = (w) => (w && w.title) || "Untitled";
const groupName = (g) => (g && g.title) || "(untitled)";

$("#widget-tabs").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-w]");
  if (!b) return;
  S.w = Number(b.dataset.w);
  renderLinks();
});

function renderSections() {
  const ws = S.links.widgets;
  if (S.w >= ws.length) S.w = ws.length - 1;
  if (S.w < 0) S.w = 0;
  $("#widget-tabs").replaceChildren(...ws.map((w, i) => h("button", {
    dataset: { w: String(i) }, class: (i === S.w ? "active" : "") + (w.title ? "" : " untitled"),
    title: "Click to open, drag to reorder", text: sectionName(w) })));
  $("#section-name").textContent = sectionName(ws[S.w]);
  $("#section-name").classList.toggle("untitled", !ws[S.w].title);
  $("#delete-section").disabled = ws.length <= 1;
  $("#delete-section").title = ws.length <= 1 ? "The last section can't be deleted" : "Delete this section";
}

if (window.Sortable) {
  Sortable.create($("#widget-tabs"), {
    draggable: "button", animation: 150,
    onEnd: (e) => {
      if (e.oldIndex === e.newIndex) return;
      const ws = S.links.widgets, active = ws[S.w];
      const [w] = ws.splice(e.oldIndex, 1);
      ws.splice(e.newIndex, 0, w);
      S.w = ws.indexOf(active);
      changed();
    },
  });
}

$("#add-section").addEventListener("click", () => { if (S.links) openNameDlg("section-new"); });
$("#rename-section").addEventListener("click", () => { if (S.links) openNameDlg("section"); });
$("#delete-section").addEventListener("click", async () => {
  const ws = S.links.widgets;
  if (ws.length <= 1) return;
  const w = ws[S.w];
  const n = w.groups.reduce((a, g) => a + g.links.length, 0);
  if (!(await confirmDlg("Delete section?",
    `"${sectionName(w)}" and its ${w.groups.length} group(s) and ${n} tile(s) will be removed from the Home page.`,
    { ok: "Delete section", danger: true }))) return;
  ws.splice(S.w, 1);
  S.w = Math.max(0, S.w - 1);
  changed();
});

/* ------------------------------------------------------------ tiles */

const iconSrc = (icon) => {
  if (!icon) return null;
  if (icon.startsWith("/assets/icons/")) return "/icons/" + encodeURIComponent(icon.slice(14));
  return null;   // si:/di:/external icons aren't previewed (CSP); they're kept as they are
};
function iconEl(icon) {
  const src = iconSrc(icon);
  if (src) return h("img", { src, alt: "", loading: "lazy" });
  return h("span", { class: "noicon", title: icon || "no icon", text: icon ? "ext" : "—" });
}

function renderLinks() {
  if (!S.links) return;
  renderSections();
  const widget = S.links.widgets[S.w];
  const key = (w, g, l) => [w.title, g.title, JSON.stringify(l)].join("\u0000");
  const origSet = new Set(JSON.parse(S.linksOrig).flatMap((w) => (w.groups || []).flatMap((g) => (g.links || []).map((l) => key(w, g, l)))));
  const box = $("#groups");
  box.replaceChildren();
  widget.groups.forEach((g, gi) => {
    const ul = h("ul", { class: "links", dataset: { gi } });
    g.links.forEach((l, li) => {
      const changed = !origSet.has(key(widget, g, l));
      ul.append(h("li", { class: "link" + (changed ? " changed" : ""), dataset: { li } },
        h("span", { class: "grip", title: "Drag to move", text: "⠿" }),
        iconEl(l.icon),
        h("div", { class: "link-text" },
          h("div", { class: "link-title", text: l.title }),
          h("div", { class: "link-url", text: l.url })),
        h("div", { class: "link-actions" },
          h("button", { class: "icon-btn", title: "Edit", text: "✎", onclick: () => openLink(S.w, gi, li) }),
          h("button", { class: "icon-btn", title: "Delete", text: "✕", onclick: () => deleteLink(gi, li) }))));
    });
    box.append(h("div", { class: "group", dataset: { gi } },
      h("div", { class: "group-head" },
        h("span", { class: "grip", title: "Drag to reorder groups", text: "⠿" }),
        h("span", { class: "group-title" + (g.title ? "" : " untitled"), text: groupName(g) }),
        h("span", { class: "group-count", text: `${g.links.length}` }),
        h("button", { class: "icon-btn", title: "Add a tile to this group", text: "+", onclick: () => openLink(S.w, gi, null) }),
        h("button", { class: "icon-btn", title: "Rename group", text: "✎", onclick: () => openNameDlg("group", gi) }),
        h("button", { class: "icon-btn", title: "Delete group", text: "✕", onclick: () => deleteGroup(gi) })),
      ul));
  });
  if (!widget.groups.length) box.append(h("p", { class: "hint", text: "No groups in this section yet. Add one with + Group." }));
  if (window.Sortable) {
    Sortable.create(box, {
      handle: ".group-head .grip", draggable: ".group", animation: 150,
      onEnd: (e) => { if (e.oldIndex !== e.newIndex) { const [g] = widget.groups.splice(e.oldIndex, 1); widget.groups.splice(e.newIndex, 0, g); } changed(); },
    });
    $$(".links", box).forEach((ul) => Sortable.create(ul, {
      group: "links", handle: ".grip", animation: 150,
      onEnd: (e) => {
        const from = widget.groups[Number(e.from.dataset.gi)], to = widget.groups[Number(e.to.dataset.gi)];
        const [l] = from.links.splice(e.oldIndex, 1);
        to.links.splice(e.newIndex, 0, l);
        changed();
      },
    }));
  }
}
function changed() { renderLinks(); updateSavebar(); }

async function deleteLink(gi, li) {
  const l = S.links.widgets[S.w].groups[gi].links[li];
  if (!(await confirmDlg("Delete tile?", `${l.title} — ${l.url}`, { ok: "Delete", danger: true }))) return;
  S.links.widgets[S.w].groups[gi].links.splice(li, 1);
  changed();
}

async function deleteGroup(gi) {
  const g = S.links.widgets[S.w].groups[gi];
  if (g.links.length && !(await confirmDlg("Delete group?",
    `"${groupName(g)}" has ${g.links.length} tile(s). They will be deleted with it.`, { ok: "Delete group and tiles", danger: true }))) return;
  S.links.widgets[S.w].groups.splice(gi, 1);
  changed();
}

/* name dialog: new/rename group, new/rename section */
const ND = { mode: "group", gi: null };
function openNameDlg(mode, gi = null) {
  Object.assign(ND, { mode, gi });
  const f = $("#group-form");
  const w = S.links.widgets[S.w];
  $("#group-dlg-title").textContent = {
    "group": gi === null ? `New group in ${sectionName(w)}` : "Rename group",
    "section-new": "New section",
    "section": "Rename section",
  }[mode];
  f.title.value = mode === "group" ? (gi === null ? "" : w.groups[gi].title || "") : mode === "section" ? w.title || "" : "";
  $("#group-msg").textContent = "";
  $("#group-dlg").showModal();
  f.title.focus();
}
$("#add-group").addEventListener("click", () => openNameDlg("group", null));
$("#group-form").addEventListener("submit", (ev) => {
  const title = ev.target.title.value.trim();
  const ws = S.links.widgets;
  let err = textErr(title, 80, true);
  if (!err && ND.mode === "group" && ws[S.w].groups.some((g, i) => g.title === title && i !== ND.gi)) err = "another group already has that name";
  if (!err && ND.mode !== "group" && ws.some((w, i) => w.title === title && (ND.mode === "section-new" || i !== S.w))) err = "another section already has that name";
  if (err) { ev.preventDefault(); $("#group-msg").textContent = `Name ${err}`; $("#group-msg").className = "msg err"; return; }
  if (ND.mode === "section-new") {
    ws.push({ type: "bookmarks", title, groups: [] });
    S.w = ws.length - 1;
  } else if (ND.mode === "section") {
    ws[S.w].title = title;
  } else if (ND.gi === null) {
    const groups = ws[S.w].groups;
    const ref = groups[0] || ws.flatMap((x) => x.groups)[0] || {};
    const g = { title };
    g["same-tab"] = ref["same-tab"] !== undefined ? ref["same-tab"] : true;
    g["hide-arrow"] = ref["hide-arrow"] !== undefined ? ref["hide-arrow"] : true;
    g.links = [];
    groups.push(g);
  } else ws[S.w].groups[ND.gi].title = title;
  changed();
});

function textErr(v, max, required) {
  if (required && !v) return "is required";
  if (v.length > max) return `is longer than ${max} characters`;
  if (/[\x00-\x1f\x7f]/.test(v)) return "contains a control character";
  if (v.includes("${")) return "must not contain ${";
  return "";
}

/* link dialog */
const LF = { w: 0, gi: 0, li: null, icon: "", pending: null };
function openLink(w, gi, li) {
  if (!S.links.widgets[w].groups.length) { toast("This section has no groups yet. Add one with + Group first.", "warn"); return; }
  Object.assign(LF, { w, gi: gi ?? 0, li, pending: null });
  const l = li === null ? {} : S.links.widgets[w].groups[gi].links[li];
  const f = $("#link-form");
  $("#link-dlg-title").textContent = li === null ? "New tile" : "Edit tile";
  $("#link-ok").textContent = li === null ? "Add" : "Update";
  f.title.value = l.title || "";
  f.url.value = l.url || "";
  f.description.value = l.description || "";
  f.same_tab.value = l["same-tab"] === undefined ? "" : String(l["same-tab"]);
  const sel = f.group;
  sel.replaceChildren();
  S.links.widgets.forEach((wd, wi) => {
    const og = h("optgroup", { label: sectionName(wd) });
    wd.groups.forEach((g, i) => og.append(h("option", { value: `${wi}:${i}`, text: groupName(g) })));
    sel.append(og);
  });
  sel.value = `${w}:${LF.gi}`;
  setFormIcon(l.icon || "", null);
  $("#lf-force").checked = false;
  $("#lf-search").value = "";
  $("#lf-results").replaceChildren();
  $("#link-msg").textContent = "";
  $("#link-dlg").showModal();
  if (li === null) f.title.focus();
}
$("#add-link").addEventListener("click", () => openLink(S.w, 0, null));

function setFormIcon(icon, pending) {
  LF.icon = icon; LF.pending = pending;
  const img = $("#lf-icon-img");
  const src = pending ? CDN + pending + ".webp" : iconSrc(icon);
  if (src) img.src = src; else img.removeAttribute("src");
  $("#lf-icon-name").textContent = pending ? `${pending}.webp (from selfh.st, downloaded on ${LF.li === null ? "Add" : "Update"})`
    : icon ? icon : "No icon";
  $$("#lf-results .icon-opt, #lf-library .icon-opt").forEach((o) => o.classList.toggle("selected",
    o.dataset.name === (pending || "") || (!pending && o.dataset.icon === icon)));
}
$("#lf-icon-clear").addEventListener("click", () => setFormIcon("", null));

$$("#link-form .subtabs button").forEach((b) => b.addEventListener("click", async () => {
  $$("#link-form .subtabs button").forEach((x) => x.classList.toggle("active", x === b));
  $("#lf-src-selfhst").hidden = b.dataset.src !== "selfhst";
  $("#lf-src-library").hidden = b.dataset.src !== "library";
  if (b.dataset.src === "library") { await loadLibrary(); renderFormLibrary(); }
}));

$("#lf-search").addEventListener("input", debounce(() => renderSearch($("#lf-search").value, $("#lf-results"), (name) => setFormIcon("", name), 30), 150));
$("#lf-lib-filter").addEventListener("input", () => renderFormLibrary());

function renderFormLibrary() {
  const q = $("#lf-lib-filter").value.trim().toLowerCase();
  const box = $("#lf-library");
  box.replaceChildren();
  for (const it of (S.library || []).filter((x) => x.file.toLowerCase().includes(q))) {
    box.append(h("div", { class: "icon-opt" + (LF.icon === it.icon && !LF.pending ? " selected" : ""), dataset: { icon: it.icon },
      onclick: () => setFormIcon(it.icon, null) },
    h("img", { src: "/icons/" + encodeURIComponent(it.file), alt: "", loading: "lazy" }),
    h("span", { class: "name", text: it.file })));
  }
}

$("#link-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const msg = $("#link-msg");
  msg.className = "msg err";
  const title = f.title.value.trim(), url = f.url.value.trim(), desc = f.description.value.trim();
  let err = textErr(title, 100, true) && `Label ${textErr(title, 100, true)}`;
  if (!err) {
    try { const u = new URL(url); if (!/^https?:$/.test(u.protocol) || !u.host) throw 0; } catch (e) { err = "URL must start with http:// or https://"; }
    if (!err && (/\s/.test(url) || url.includes("${"))) err = "URL must not contain spaces or ${";
  }
  if (!err && textErr(desc, 300, false)) err = `Description ${textErr(desc, 300, false)}`;
  if (err) { msg.textContent = err; return; }
  let icon = LF.icon;
  if (LF.pending) {
    msg.className = "msg"; msg.textContent = "Downloading icon…";
    try {
      const r = await api("POST", "/api/icons/download", { name: LF.pending, force: $("#lf-force").checked });
      icon = r.icon;
      if (r.status === "downloaded") toast(`Icon ${r.file} downloaded`);
      S.library = null;
    } catch (e) { msg.className = "msg err"; msg.textContent = `Icon: ${e.message}`; return; }
  }
  const [tw, tg] = f.group.value.split(":").map(Number);
  const src = S.links.widgets[LF.w].groups[LF.gi];
  const l = LF.li === null ? {} : clone(src.links[LF.li]);
  l.title = title; l.url = url;
  if (icon) l.icon = icon; else delete l.icon;
  if (desc) l.description = desc; else delete l.description;
  if (f.same_tab.value === "") delete l["same-tab"]; else l["same-tab"] = f.same_tab.value === "true";
  const ordered = { title: l.title, url: l.url, ...l };   // keep title/url first for readability
  if (LF.li !== null && tw === LF.w && tg === LF.gi) src.links[LF.li] = ordered;
  else {
    if (LF.li !== null) src.links.splice(LF.li, 1);
    S.links.widgets[tw].groups[tg].links.push(ordered);
  }
  $("#link-dlg").close();
  S.w = tw;
  changed();
});
$$("dialog [data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

/* ------------------------------------------------------------ selfh.st search */
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

async function loadIndex() {
  if (S.index) return S.index;
  if (!S.indexLoading) S.indexLoading = api("GET", "/api/icons/index").then((r) => {
    S.index = r.items;
    if (r.stale) toast("Using a cached selfh.st list (couldn't refresh it)", "warn");
    return S.index;
  }).finally(() => { S.indexLoading = null; });
  return S.indexLoading;
}

async function renderSearch(q, box, onPick, limit) {
  q = q.trim().toLowerCase();
  box.replaceChildren();
  if (q.length < 2) return;
  let idx;
  try { idx = await loadIndex(); } catch (e) { box.append(h("div", { class: "more", text: e.message })); return; }
  const scored = [];
  for (const [ref, name, light] of idx) {
    const n = name.toLowerCase();
    const s = ref === q ? 0 : ref.startsWith(q) || n.startsWith(q) ? 1 : ref.includes(q) || n.includes(q) ? 2 : -1;
    if (s >= 0) scored.push([s, ref, name, light]);
  }
  scored.sort((a, b) => a[0] - b[0] || a[1].localeCompare(b[1]));
  for (const [, ref, name, light] of scored.slice(0, limit)) {
    const variants = [ref].concat(light ? [ref + "-light"] : []);
    const opt = h("div", { class: "icon-opt", dataset: { name: ref } },
      h("div", { class: "pair" }, variants.map((v) => h("img", {
        src: CDN + v + ".webp", alt: v, title: v, loading: "lazy", referrerpolicy: "no-referrer",
      }))),
      h("span", { class: "name", text: name }),
      h("div", { class: "variant" }, variants.map((v) => h("button", {
        type: "button", class: "btn small", text: v.endsWith("-light") ? "light" : "normal",
        title: `Use ${v}.webp`, dataset: { name: v }, onclick: (e) => { e.stopPropagation(); onPick(v); },
      }))));
    opt.addEventListener("click", () => onPick(ref));
    box.append(opt);
  }
  if (!scored.length) box.append(h("div", { class: "more", text: "No selfh.st icon matches. Try another name." }));
  else if (scored.length > limit) box.append(h("div", { class: "more", text: `${scored.length - limit} more — type more of the name` }));
}

/* ------------------------------------------------------------ icon library view */
async function loadLibrary(force) {
  if (!S.library || force) S.library = (await api("GET", "/api/icons/library")).items;
  return S.library;
}

$("#lib-search").addEventListener("input", debounce(() => renderSearch($("#lib-search").value, $("#lib-results"), libDownload, 48), 150));
$("#lib-filter").addEventListener("input", () => renderLibrary());

async function libDownload(name, force = false) {
  try {
    const r = await api("POST", "/api/icons/download", { name, force });
    toast(r.status === "exists" ? `${r.file} is already in the library` : `${r.file} downloaded`);
    await loadLibrary(true);
    renderLibrary();
  } catch (e) { toast(e.message, "err"); }
}

function renderLibrary() {
  const q = $("#lib-filter").value.trim().toLowerCase();
  const items = (S.library || []).filter((x) => x.file.toLowerCase().includes(q));
  $("#lib-count").textContent = `${(S.library || []).length} files`;
  const box = $("#library");
  box.replaceChildren();
  for (const it of items) {
    const used = it.used_by.length, other = it.other_refs.length;
    const usesText = used ? `used by ${used} tile${used > 1 ? "s" : ""}` : "unused";
    box.append(h("div", { class: "lib-item" },
      h("img", { src: "/icons/" + encodeURIComponent(it.file), alt: "", loading: "lazy" }),
      h("div", { class: "meta" },
        h("div", { class: "file", text: it.file }),
        h("div", { class: "uses" + (used ? " used" : ""), title: it.used_by.join("\n"), text: usesText }),
        other ? h("div", { class: "uses used", title: it.other_refs.join("\n"), text: `also in ${it.other_refs.join(", ")}` }) : null,
        h("div", { class: "uses", text: `${Math.max(1, Math.round(it.size / 1024))} KB${it.selfhst ? " · selfh.st" : " · local"}` })),
      h("div", { class: "acts" },
        it.selfhst ? h("button", { class: "btn small", text: "Re-download", title: "Fetch this name again from selfh.st",
          onclick: async () => { if (await confirmDlg("Re-download?", `Replace ${it.file} with the current selfh.st version?`, { ok: "Re-download" })) libDownload(it.file.replace(/\.webp$/, ""), true); } }) : null,
        h("button", { class: "btn small danger", text: "Delete", disabled: used || other ? true : null,
          title: used || other ? "In use — remove it from the tiles first" : "Delete this file",
          onclick: async () => {
            if (!(await confirmDlg("Delete icon?", `Delete assets/icons/${it.file}? This can't be undone (re-download from selfh.st if needed).`, { ok: "Delete", danger: true }))) return;
            try { await api("POST", "/api/icons/delete", { file: it.file }); toast(`${it.file} deleted`); await loadLibrary(true); renderLibrary(); } catch (e) { toast(e.message, "err"); }
          } }))));
  }
  if (!items.length) box.append(h("p", { class: "hint", text: "No icons match." }));
}

/* ------------------------------------------------------------ countdown */
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function parseDate(d) {
  let m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d || "");
  if (m) return { once: true, y: +m[1], m: +m[2], d: +m[3] };
  m = /^(\d{2})-(\d{2})$/.exec(d || "");
  if (m) return { once: false, y: new Date().getFullYear(), m: +m[1], d: +m[2] };
  return { once: false, y: new Date().getFullYear(), m: 0, d: 0 };
}
function fmtDate(p) { return p.once ? `${p.y}-${pad(p.m)}-${pad(p.d)}` : `${pad(p.m)}-${pad(p.d)}`; }
function dateErr(p) {
  if (!p.m || !p.d) return "pick a month and day";
  const y = p.once ? p.y : 2000;
  if (p.once && (!(p.y >= 1900 && p.y <= 2200))) return "year must be 1900–2200";
  const dt = new Date(Date.UTC(y, p.m - 1, p.d));
  if (dt.getUTCMonth() !== p.m - 1 || dt.getUTCDate() !== p.d) return "isn't a real date";
  return "";
}
function partErr(v, what, max) {
  if (!v) return `${what} is required`;
  if (/[|"\\]/.test(v)) return `${what} must not contain | " or \\`;
  const t = textErr(v, max, true);
  return t ? `${what} ${t}` : "";
}
function rowErr(kind, r) {
  const p = parseDate(r.date);
  return partErr(r.name, "Name", 40) || dateErr(p) && `Date ${dateErr(p)}` || (kind === "ev" ? partErr(r.emoji, "Emoji", 16) : "");
}
function cdValidate() {
  for (const [i, r] of S.cd.birthdays.entries()) { const e = rowErr("bd", r); if (e) return `Birthday ${i + 1}: ${e}`; }
  for (const [i, r] of S.cd.events.entries()) { const e = rowErr("ev", r); if (e) return `Event ${i + 1}: ${e}`; }
  return "";
}

function renderCountdown() {
  const cd = S.cd;
  $("#bd-left").textContent = `${cd.max_birthdays - cd.birthdays.length} of ${cd.max_birthdays} slots left`;
  $("#ev-left").textContent = `${cd.max_events - cd.events.length} of ${cd.max_events} slots left`;
  $("#bd-add").disabled = cd.birthdays.length >= cd.max_birthdays;
  $("#ev-add").disabled = cd.events.length >= cd.max_events;
  $("#bd-list").replaceChildren(...cd.birthdays.map((r, i) => cdRow("bd", r, i)));
  $("#ev-list").replaceChildren(...cd.events.map((r, i) => cdRow("ev", r, i)));
  $("#cd-problems").textContent = (cd.problems || []).join("\n");
  updateSavebar();
}

function cdRow(kind, r, i) {
  const list = kind === "bd" ? S.cd.birthdays : S.cd.events;
  const p = parseDate(r.date);
  const err = h("div", { class: "row-err" });
  const refresh = () => {
    const e = rowErr(kind, r);
    let warn = "";
    const q = parseDate(r.date);
    if (!e && q.once) {
      const t = new Date(q.y, q.m - 1, q.d), today = new Date(); today.setHours(0, 0, 0, 0);
      if (t < today) warn = "This date has passed; the widget hides it.";
    }
    err.textContent = e || warn;
    updateSavebar();
  };
  const set = (k, v) => { r[k] = v; refresh(); };
  const setDate = () => { r.date = fmtDate(p); refresh(); };
  const monthSel = h("select", { onchange: (e) => { p.m = +e.target.value; setDate(); } },
    h("option", { value: "0", text: "Month" }), MONTHS.map((m, k) => h("option", { value: String(k + 1), text: m })));
  monthSel.value = String(p.m);
  const daySel = h("select", { onchange: (e) => { p.d = +e.target.value; setDate(); } },
    h("option", { value: "0", text: "Day" }), Array.from({ length: 31 }, (_, k) => h("option", { value: String(k + 1), text: String(k + 1) })));
  daySel.value = String(p.d);
  const yearIn = h("input", { type: "number", min: "1900", max: "2200", value: String(p.y), oninput: (e) => { p.y = +e.target.value; setDate(); } });
  const yearLabel = h("label", {}, "Year", yearIn);
  yearLabel.hidden = !p.once;
  const fields = h("div", { class: "cd-fields" },
    h("label", { class: "wide" }, "Name", h("input", { value: r.name || "", maxlength: "40", oninput: (e) => set("name", e.target.value.trim()) })),
    h("label", {}, "Month", monthSel),
    h("label", {}, "Day", daySel));
  if (kind === "ev") {
    const rep = h("select", { onchange: (e) => { p.once = e.target.value === "once"; yearLabel.hidden = !p.once; setDate(); } },
      h("option", { value: "yearly", text: "Yearly" }), h("option", { value: "once", text: "One time" }));
    rep.value = p.once ? "once" : "yearly";
    const emojiIn = h("input", { value: r.emoji || "", maxlength: "16", oninput: (e) => set("emoji", e.target.value.trim()) });
    fields.append(h("label", {}, "Repeats", rep), yearLabel,
      h("label", { class: "wide" }, "Emoji", emojiIn,
        h("div", { class: "emoji-picks" }, EMOJI.map((em) => h("button", { type: "button", text: em, title: em,
          onclick: () => { emojiIn.value = em; set("emoji", em); } })))));
  }
  const row = h("div", { class: "cd-row" }, fields,
    h("button", { class: "icon-btn rm", title: "Remove", text: "✕", onclick: () => { list.splice(i, 1); renderCountdown(); } }), err);
  refresh();
  return row;
}

$("#bd-add").addEventListener("click", () => { S.cd.birthdays.push({ date: "", name: "" }); renderCountdown(); });
$("#ev-add").addEventListener("click", () => { S.cd.events.push({ date: "", name: "", emoji: "🎉" }); renderCountdown(); });

/* ------------------------------------------------------------ history */
function renderHistory(data) {
  for (const key of ["links", "countdown"]) {
    const box = $(`#hist-${key}`);
    box.replaceChildren();
    const items = data[key] || [];
    if (!items.length) box.append(h("p", { class: "hint", text: "No saved versions yet." }));
    for (const it of items) {
      box.append(h("div", { class: "hist-row" },
        h("span", { class: "when", text: it.when }),
        h("span", { class: "muted small", text: `${it.size} B` }),
        h("button", { class: "btn small", text: "View", onclick: () => viewHistory(key, it) }),
        h("button", { class: "btn small", text: "Restore", onclick: () => restoreHistory(key, it) })));
    }
  }
}
async function viewHistory(key, it) {
  try {
    const r = await api("GET", `/api/history/view?file=${key}&name=${encodeURIComponent(it.name)}`);
    const miss = (r.missing_icons || []).length
      ? ` Note: it uses icon file(s) no longer in the library (${r.missing_icons.join(", ")}); those tiles would show no icon until you pick a new one.` : "";
    confirmDlg(`${it.when}`, "Changes from the current file to this version:" + miss, { pre: r.diff, cancel: null, ok: "Close" });
  } catch (e) { toast(e.message, "err"); }
}
async function restoreHistory(key, it) {
  const dirty = key === "links" ? linksDirty() : cdDirty();
  if (!(await confirmDlg("Restore this version?",
    `Replace the current ${key === "links" ? "tiles" : "countdown"} with the version from ${it.when}? The current file is kept in History.` +
    (dirty ? " Your unsaved edits will be dropped." : ""), { ok: "Restore" }))) return;
  try {
    const r = await api("POST", "/api/history/restore", { file: key, name: it.name });
    reportReload(r, "Restore");
    if ((r.missing_icons || []).length)
      toast(`Restored tiles use icon file(s) no longer in the library: ${r.missing_icons.join(", ")}. Edit those tiles to pick an icon.`, "warn", 12000);
    if (key === "links") S.links = null; else S.cd = null;
    S.linksOrig = S.links ? S.linksOrig : ""; S.cdOrig = S.cd ? S.cdOrig : "";
    loadView("history");
  } catch (e) { showSaveError(e, "Restore"); }
}

/* ------------------------------------------------------------ start */
api("GET", "/api/session").then(enterApp).catch((e) => {
  $("#boot").hidden = true;
  if (e.status === 401) {
    S.loginCsrf = e.data.login_csrf || "";
    $("#login").hidden = false;
    if (e.data.password_set === false) $("#login-msg").textContent = "No password set yet. In the container run: glance-bookmarks-editor passwd";
    $("#login-form [name=password]").focus();
  } else {
    $("#boot").hidden = false;
    $("#boot").textContent = `Can't load the editor: ${e.message}`;
  }
});
