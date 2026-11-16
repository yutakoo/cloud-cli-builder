"use strict";
// Cloud CLI Builder フロントエンド（ライブラリ不使用）

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("ccb." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ccb." + k, JSON.stringify(v)); } catch { /* ignore */ } },
};

// サーバーが HTML に埋め込んだ初期データ（<script type="application/json" id="bootData">）
const BOOT = (() => { try { return JSON.parse($("#bootData").textContent); } catch { return {}; } })();

// 日本語検索辞書（app/ja.py の SEARCH_VERBS / SEARCH_NOUNS をサーバーから受け取る）
const JA = BOOT.ja_search || { verbs: {}, nouns: {} };
const JA_WORDS = Object.keys({ ...JA.verbs, ...JA.nouns }).sort((a, b) => b.length - a.length);
function tokenMatches(name, hay, token) {
  if (hay.includes(token)) return true;
  const words = name.split(/[\s-]+/);
  let rest = token, used = false;
  for (const jw of JA_WORDS) {
    if (!rest.includes(jw)) continue;
    const ok = JA.verbs[jw] ? JA.verbs[jw].some((e) => words.includes(e)) : JA.nouns[jw].some((e) => name.includes(e));
    if (!ok) return false;
    rest = rest.split(jw).join(""); used = true;
  }
  rest = rest.replace(/^[のをにでと 　]+|[のをにでと 　]+$/g, "");
  return used && (!rest || hay.includes(rest));
}
const BASE = { aws: "aws" };

// ---------- 画面の言語（通常は英語。「Claudeで日本語化」が成功したときだけ日本語） ----------
// 文言の辞書は app/ui_ja.json（英語 → 日本語）。"{0}" を含むキーは可変部分のあるパターン。
const UI_JA = BOOT.ui_ja || {};
// 可変部分のある文言（"{0}" を含むキー）。正規表現を試す前に、固定部分（先頭・末尾・途中）が
// 含まれているかを文字列比較で確かめる（長い文字列に対して正規表現が遅くなるのを防ぐ）
const UI_PAT = Object.keys(UI_JA).filter((k) => /\{\d\}/.test(k)).map((k) => {
  const lits = k.split(/\{\d\}/);
  const re = new RegExp("^" + lits.map((x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("(.+?)") + "$", "s");
  return { lits, re, ja: UI_JA[k] };
});
const UI_MAXLEN = Math.max(0, ...Object.keys(UI_JA).map((k) => k.length)) + 300;   // これより長い文字列は画面の文言ではない
const jaMemo = new Map();
function jaOf(s) {
  if (!s || s.length > UI_MAXLEN) return null;
  if (jaMemo.has(s)) return jaMemo.get(s);
  let r = null;
  if (Object.prototype.hasOwnProperty.call(UI_JA, s) && !/\{\d\}/.test(s)) r = UI_JA[s];
  else {
    for (const { lits, re, ja } of UI_PAT) {
      if (!s.startsWith(lits[0]) || !s.endsWith(lits[lits.length - 1])) continue;
      let pos = lits[0].length, ok = true;
      for (let i = 1; i < lits.length - 1 && ok; i++) { const j = s.indexOf(lits[i], pos); ok = j >= 0; pos = j + lits[i].length; }
      if (!ok) continue;
      const m = s.match(re);
      if (m) { r = ja.replace(/\{(\d)\}/g, (_, i) => m[+i + 1]); break; }
    }
  }
  if (jaMemo.size > 5000) jaMemo.clear();
  jaMemo.set(s, r);
  return r;
}
function t(s) { return S.lang === "ja" ? (jaOf(s) ?? s) : s; }
const I18N_SKIP = "script,style,code,pre,textarea,[translate=no],.fdoc,.orig,#cmdSummary";
const I18N_ATTRS = ["placeholder", "title"];
const i18nOrig = { text: new Map(), attr: new Map() };
// 訳さない部分（コマンド名・説明文など）は、要素ごと丸ごと飛ばして走査する
const I18N_FILTER = { acceptNode: (n) => (n.nodeType === 1 && n.matches(I18N_SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) };
function translateTree(root) {
  if (root.nodeType === 3) root = root.parentNode;
  if (!root || root.nodeType !== 1 || root.closest(I18N_SKIP)) return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, I18N_FILTER);
  for (let n = root; n; n = w.nextNode()) {
    if (n.nodeType === 3) {
      const raw = n.nodeValue, s = raw.trim();
      const ja = s && jaOf(s);
      if (ja != null) { if (!i18nOrig.text.has(n)) i18nOrig.text.set(n, raw); n.nodeValue = raw.replace(s, ja); }
      continue;
    }
    for (const a of I18N_ATTRS) {
      const v = n.getAttribute(a), ja = v && jaOf(v.trim());
      if (ja == null) continue;
      const o = i18nOrig.attr.get(n) || {};
      if (!(a in o)) o[a] = v;
      i18nOrig.attr.set(n, o); n.setAttribute(a, ja);
    }
  }
}
// 追加されたものだけを訳す。追加されたのが文字（項目の間の改行など）なら、その文字だけを見る
// （親要素ごと走査すると、一覧を描き直すたびに「項目数 × 項目数」回の処理になって非常に遅くなる）
function translateText(n) {
  const p = n.parentElement;
  if (!p || p.closest(I18N_SKIP)) return;
  const raw = n.nodeValue, s = raw.trim();
  const ja = s && jaOf(s);
  if (ja != null) { if (!i18nOrig.text.has(n)) i18nOrig.text.set(n, raw); n.nodeValue = raw.replace(s, ja); }
}
const i18nObs = new MutationObserver((ms) => {
  for (const m of ms) for (const n of m.addedNodes) {
    if (n.nodeType === 1) translateTree(n);
    else if (n.nodeType === 3) translateText(n);
  }
});
function setLang(lang) {
  if (S.lang === lang) return;
  S.lang = lang;
  document.documentElement.lang = lang;
  $("#aiLocalizeLabel").textContent = lang === "ja" ? "English" : "Claudeで日本語化";
  $("#homeLangBtn").textContent = lang === "ja" ? "English" : "日本語";
  if (lang === "ja") {
    translateTree(document.body);
    i18nObs.observe(document.body, { childList: true, subtree: true });
  } else {
    i18nObs.disconnect();
    for (const [n, raw] of i18nOrig.text) n.nodeValue = raw;
    for (const [el, o] of i18nOrig.attr) for (const a in o) el.setAttribute(a, o[a]);
    i18nOrig.text.clear(); i18nOrig.attr.clear();
  }
}

const S = {
  provider: "aws",
  services: [],
  service: null,
  commands: [],
  detail: null,
  values: {},              // "p:--name" / "pos:name" → 値
  globals: { aws: {}, ...store.get("globals", {}) },
  favorites: [],
  favTab: "current",
  shell: store.get("shell", "bash"),
  lang: "en",
};

// ---------- API ----------
async function api(path, opt = {}) {
  // 書き込み系には専用ヘッダーを付ける（サーバーはこれがない POST/PUT/DELETE を拒否＝他サイトからの CSRF 対策）
  const r = await fetch(path, { ...opt, headers: { ...(opt.headers || {}), "X-CCB": "1" } });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.json();
}

// ---------- テーマ ----------
// テーマ（id は data-theme の値。"light"/"dark" は以前の保存値との互換のため）
const THEMES = [
  ["", "Auto (system)"], ["light", "Paper"], ["dark", "Ink"], ["space", "Space"], ["hawaiian", "Hawaiian"],
  ["sakura", "Sakura"], ["forest", "Forest"], ["synthwave", "Synthwave"], ["deepsea", "Deep sea"],
  ["terminal", "Terminal"], ["nordic", "Nordic"],
];
function applyTheme(id) {
  if (id) document.documentElement.dataset.theme = id; else delete document.documentElement.dataset.theme;
  $$("#themeMenu [data-theme-id]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.themeId === (id || ""))));
}
function renderThemeMenu() {
  const cur = store.get("theme", null) || "";
  $("#themeMenu").innerHTML = THEMES.map(([id, name]) => `
    <button class="theme-opt" role="menuitemradio" aria-checked="${id === cur}" data-theme-id="${id}">
      <span class="swatch"${id ? ` data-theme="${id}"` : ' data-theme-auto'}><i class="sw-strip"></i><i class="sw-mark"></i></span>
      <span class="theme-name">${esc(name)}</span>
    </button>`).join("");
}
renderThemeMenu();
applyTheme(store.get("theme", null));
function closeThemeMenu() { $("#themeMenu").hidden = true; $("#themeToggle").setAttribute("aria-expanded", "false"); }
$("#themeToggle").onclick = (e) => {
  e.stopPropagation();
  const open = $("#themeMenu").hidden;
  $("#themeMenu").hidden = !open; $("#themeToggle").setAttribute("aria-expanded", String(open));
  if (open) $("#themeMenu [aria-checked=true]")?.focus();
};
$("#themeMenu").onclick = (e) => {
  const b = e.target.closest("[data-theme-id]");
  if (!b) return;
  const id = b.dataset.themeId;
  store.set("theme", id || null); applyTheme(id);
};
document.addEventListener("click", (e) => { if (!e.target.closest(".theme-picker")) closeThemeMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#themeMenu").hidden) { closeThemeMenu(); $("#themeToggle").focus(); } });

// ---------- 初期化（サービス一覧の読み込み） ----------
async function loadProvider(p, { keepHash = false } = {}) {
  S.provider = p;
  $("#globalSearch").value = ""; hideGlobal();
  S.service = null; S.detail = null; S.commands = []; clearAI();
  $("#nav").dataset.level = "services";
  $("#commandList").innerHTML = `<li class="empty-small">Select a service on the left to see its commands</li>`; $("#commandsTitle").textContent = "Commands"; $("#serviceDocLink").hidden = true;
  showBuilder(false);
  $("#serviceList").innerHTML = `<li class="empty-small">Loading…</li>`;
  S.services = await api(`/api/${p}/services`);
  renderServices();
  renderFavorites();
  if (!keepHash) setHash();
}

// ---------- サービス ----------
function favSet() {
  return new Set(S.favorites.filter((f) => f.provider === S.provider).map((f) => f.service));
}
function renderServices() {
  const q = $("#serviceFilter").value.trim().toLowerCase();
  const onlyJa = $("#onlyJa").checked;
  const favs = favSet();
  const list = S.services.filter((s) =>
    (!onlyJa || s.ja) && (!q || s.name.includes(q) || s.ja.toLowerCase().includes(q) || s.title.toLowerCase().includes(q)));
  // お気に入りのあるサービス → よく使うサービス（app/ja.py の並び順） → 名前順
  list.sort((a, b) => (favs.has(b.name) - favs.has(a.name)) || (a.rank - b.rank) || a.name.localeCompare(b.name));
  $("#serviceList").innerHTML = list.map((s) => `
    <li data-name="${esc(s.name)}" class="${S.service === s.name ? "active" : ""}">
      <span class="n" translate="no"><span>${favs.has(s.name) ? '<span class="star">★</span> ' : ""}${esc(s.name)}</span><small>${s.count}</small></span>
      <span class="j" translate="no">${esc(s.title)}</span>
    </li>`).join("") || `<li class="empty-small">No matches</li>`;
}
$("#serviceFilter").oninput = renderServices;
$("#onlyJa").onchange = () => { store.set("onlyJa", $("#onlyJa").checked); renderServices(); };
$("#onlyJa").checked = store.get("onlyJa", false);
$("#navBack").onclick = () => {
  $("#nav").dataset.level = "services";
  $(`#serviceList li[data-name="${CSS.escape(S.service || "")}"]`)?.scrollIntoView({ block: "nearest" });
  $("#serviceFilter").focus({ preventScroll: true });
};
$("#serviceList").onclick = (e) => { const li = e.target.closest("li[data-name]"); if (li) selectService(li.dataset.name); };

async function selectService(name, { keepHash = false } = {}) {
  S.service = name;
  $("#nav").dataset.level = "commands";
  $$("#serviceList li").forEach((li) => li.classList.toggle("active", li.dataset.name === name));
  const svc = S.services.find((s) => s.name === name);
  $("#commandsTitle").textContent = `Commands — ${name}`;
  const dl = $("#serviceDocLink");
  if (svc) { dl.href = svc.doc_url; dl.hidden = false; }
  $("#commandFilter").value = "";
  $("#commandList").innerHTML = `<li class="empty-small">Loading…</li>`;
  S.commands = await api(`/api/${S.provider}/services/${encodeURIComponent(name)}`);
  renderCommands();
  if (!keepHash) setHash();
}

// ---------- コマンド一覧 ----------
function matchCmd(c, q) {
  // 英字はコマンド名の単語の先頭で一致（例: "inst" → describe-instances）。日本語は辞書で照合
  if (!q) return true;
  const words = "-" + c.name.replace(/[\s-]+/g, "-");
  return q.split(/\s+/).every((x) => /^[\x00-\x7f]+$/.test(x)
    ? words.includes("-" + x.replace(/[\s-]+/g, "-"))
    : tokenMatches(c.name, (c.ja || "").toLowerCase(), x));
}
function renderCommands() {
  const q = $("#commandFilter").value.trim().toLowerCase();
  const favNames = new Set(S.favorites.filter((f) => f.provider === S.provider && f.service === S.service).map((f) => f.operation));
  const list = S.commands.filter((c) => matchCmd(c, q));
  $("#commandList").innerHTML = list.map((c) => `
    <li data-name="${esc(c.name)}" class="${S.detail && S.detail.name === c.name ? "active" : ""}" title="${esc(c.summary)}">
      <span class="n"><span translate="no">${favNames.has(c.name) ? '<span class="star">★</span> ' : ""}${esc(c.name)}</span>${c.required ? `<small>Required ${c.required}</small>` : ""}</span>
      <span class="j" translate="no">${esc(c.summary || "")}</span>
    </li>`).join("") || `<li class="empty-small">No matches</li>`;
}
$("#commandFilter").oninput = renderCommands;
$("#commandList").onclick = (e) => { const li = e.target.closest("li[data-name]"); if (li) selectCommand(li.dataset.name); };

// ---------- ビルダー ----------
function showBuilder(on) {
  $("#builderEmpty").hidden = on; $("#builderBody").hidden = !on;
  $("#preview").hidden = false; $("#preview").classList.toggle("idle", !on);   // トップページではお気に入りボタンだけ表示
  if (!on) {
    renderHomeQuick();
    setLang(store.get("homeLang", "en") === "ja" ? "ja" : "en");   // トップ画面は辞書だけで日本語にできる（前回の選択を覚える）
  }
}

// ---------- トップページ ----------
const QUICK = {
  aws: [["sts", "get-caller-identity"], ["ec2", "describe-instances"], ["s3", "ls"], ["s3", "sync"],
        ["logs", "tail"], ["ssm", "start-session"], ["iam", "list-roles"], ["ce", "get-cost-and-usage"]],
};
function renderHomeQuick() {
  const favs = S.favorites.filter((f) => f.provider === S.provider && f.state && f.state.name).slice(0, 4);
  const base = BASE[S.provider];
  $("#homeQuick").innerHTML =
    `<span class="home-quick-label">Frequently used</span>` +
    QUICK[S.provider].map(([svc, cmd]) => `<button class="chip" translate="no" data-svc="${esc(svc)}" data-cmd="${esc(cmd)}">${esc(base)} ${esc(svc)} ${esc(cmd)}</button>`).join("") +
    (favs.length ? `<span class="home-quick-label">Favorites</span>` +
      favs.map((f) => `<button class="chip fav" translate="no" data-fav="${f.id}">★ ${esc(f.title)}</button>`).join("") : "");
}
$("#homeQuick").onclick = async (e) => {
  const b = e.target.closest("button.chip");
  if (!b) return;
  if (b.dataset.fav) return openState(S.favorites.find((f) => String(f.id) === b.dataset.fav).state);
  await selectService(b.dataset.svc, { keepHash: true });
  await selectCommand(b.dataset.cmd);
};
function goHome() {
  S.detail = null; clearAI();
  $$("#commandList li").forEach((li) => li.classList.remove("active"));
  showBuilder(false);
  $("#builder").scrollTop = 0; $("#builderEmpty").scrollTop = 0;
  setHash();
}
$("#homeLink").onclick = (e) => { e.preventDefault(); goHome(); };
$("#homeLangBtn").onclick = () => {
  const lang = S.lang === "ja" ? "en" : "ja";
  store.set("homeLang", lang);
  setLang(lang);
};

async function selectCommand(name, { values = null, keepHash = false } = {}) {
  const d = await api(`/api/${S.provider}/command?service=${encodeURIComponent(S.service)}&name=${encodeURIComponent(name)}`);
  S.detail = d;
  S.values = values ? { ...values } : {};
  $("#pasteNote").hidden = true;
  clearAI();
  $$("#commandList li").forEach((li) => li.classList.toggle("active", li.dataset.name === name));
  showBuilder(true);
  $("#crumbService").textContent = d.service;
  $("#crumbCommand").textContent = d.name;
  $("#cmdJa").textContent = d.name;
  $("#cmdSummary").textContent = d.description || d.summary || "";
  $("#cmdSummary").hidden = !(d.description || d.summary);
  $("#cmdSummary").classList.remove("ai-text");
  $("#cmdSummary").classList.add("clamp");
  $("#cmdSummary").title = "Click to show the full text";
  $("#cmdSummary").dataset.orig = d.description || d.summary || "";
  for (const a of [$("#cmdDocLink"), $("#previewDocLink")]) { a.href = d.doc_url; a.textContent = d.doc_url; }
  $("#paramFilter").value = ""; $("#onlyFilled").checked = false;
  renderFields();
  build();
  $("#builderBody").scrollTop = 0;
  if (!keepHash) setHash();
}

function fieldHTML(p, key, scope) {
  const v = scope === "g" ? (S.globals[S.provider][p.name] ?? "") : (S.values[key] ?? "");
  const type = p.type || "string";
  const id = `f_${scope}_${p.name.replace(/[^\w-]/g, "_")}`;
  const listId = p.suggest ? `${id}_dl` : "";
  const dl = p.suggest ? `<datalist id="${listId}">${p.suggest.map((s) => `<option value="${esc(s)}">`).join("")}</datalist>` : "";
  let input;
  const attrs = `id="${id}" data-key="${esc(key)}" data-scope="${scope}"`;
  switch (type) {
    case "boolean":
      input = `<select ${attrs}><option value="">(not set)</option><option translate="no" value="${esc(p.name)}">${esc(p.name)}</option><option translate="no" value="${esc(p.neg)}">${esc(p.neg)}</option></select>`;
      break;
    case "flag":
      input = `<label class="chk"><input type="checkbox" ${attrs} ${v ? "checked" : ""}> Include</label>`;
      break;
    case "enum":
      input = `<select ${attrs}><option value="">(not set)</option>${p.choices.map((c) => `<option translate="no" ${c === v ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
      break;
    case "json":
      input = `<textarea ${attrs} class="mono" rows="2" placeholder="JSON or shorthand syntax">${esc(v)}</textarea>
        <div class="row">
          ${p.shorthand ? `<button class="btn ghost mini" data-insert="${esc(p.shorthand)}">Shorthand example</button>` : ""}
          ${p.example ? `<button class="btn ghost mini" data-insert="${esc(p.example)}">JSON skeleton</button>` : ""}
        </div>`;
      break;
    case "list":
      input = `<input ${attrs} value="${esc(v)}" placeholder="Space-separated values${p.choices ? ` (e.g. ${esc(p.choices.slice(0, 3).join(" "))})` : ""}">`;
      break;
    case "number":
      input = `<input ${attrs} value="${esc(v)}" inputmode="numeric" placeholder="Number">`;
      break;
    default:
      input = `<input ${attrs} value="${esc(v)}" ${listId ? `list="${listId}"` : ""} placeholder="${esc(p.hint || "")}">`;
  }
  const typeLabel = { boolean: "Boolean", flag: "Flag", enum: "Choice", json: "JSON", list: "List", number: "Number", string: "String" }[type] || type;
  return `<div class="field ${v ? "filled" : ""}" data-pname="${esc(p.name)}" data-scope="${scope}" data-name="${esc(p.name.toLowerCase())} ${esc((p.key || "").toLowerCase())}">
    <div class="fname"><span translate="no">${esc(p.name)}</span>${p.required ? '<span class="req">Required</span>' : ""}<span class="ftype">${typeLabel}</span>
      ${p.doc ? `<div class="fdoc clamp" title="Click to expand">${esc(p.doc)}</div>` : ""}
    </div>
    <div class="finput">${input}${dl}</div>
  </div>`;
}

function renderFields() {
  const d = S.detail;
  const pos = d.positionals || [];
  $("#positionals").hidden = !pos.length;
  $("#positionalFields").innerHTML = pos.map((p) => fieldHTML(p, "pos:" + p.name, "pos")).join("");
  const req = d.params.filter((p) => p.required);
  const opt = d.params.filter((p) => !p.required);
  $("#requiredGroup").hidden = !req.length;
  $("#requiredFields").innerHTML = req.map((p) => fieldHTML(p, "p:" + p.name, "p")).join("");
  $("#optionalFields").innerHTML = opt.map((p) => fieldHTML(p, "p:" + p.name, "p")).join("") || `<div class="empty-small">No options</div>`;
  $("#optCount").textContent = opt.length;
  $("#paginationGroup").hidden = !d.pagination.length;
  $("#paginationFields").innerHTML = d.pagination.map((p) => fieldHTML(p, "p:" + p.name, "p")).join("");
  $("#globalFields").innerHTML = d.global_options.map((p) => fieldHTML(p, p.name, "g")).join("");
  // select の初期値（boolean）
  $$("#builderBody select[data-key]").forEach((s) => {
    const v = s.dataset.scope === "g" ? S.globals[S.provider][s.dataset.key] : S.values[s.dataset.key];
    if (v) s.value = v;
  });
  applyAI();
}

function filterFields() {
  const q = $("#paramFilter").value.trim().toLowerCase();
  const only = $("#onlyFilled").checked;
  $$("#optionalFields .field").forEach((f) => {
    f.hidden = (q && !f.dataset.name.includes(q)) || (only && !f.classList.contains("filled"));
  });
}
$("#paramFilter").oninput = filterFields;
$("#cmdSummary").onclick = (e) => {
  if (e.target.closest("button") || e.currentTarget.classList.contains("ai-text")) return;
  e.currentTarget.classList.toggle("clamp");
};
$("#onlyFilled").onchange = filterFields;

$("#builderBody").addEventListener("input", onFieldChange);
$("#builderBody").addEventListener("change", onFieldChange);
function onFieldChange(e) {
  const el = e.target;
  if (!el.dataset || !el.dataset.key) return;
  const v = el.type === "checkbox" ? (el.checked ? "1" : "") : el.value;
  if (el.dataset.scope === "g") {
    if (v) S.globals[S.provider][el.dataset.key] = v; else delete S.globals[S.provider][el.dataset.key];
    store.set("globals", S.globals);
  } else if (v) S.values[el.dataset.key] = v; else delete S.values[el.dataset.key];
  el.closest(".field").classList.toggle("filled", !!v);
  build();
}
$("#builderBody").addEventListener("click", (e) => {
  const b = e.target.closest("[data-insert]");
  if (b) {
    e.preventDefault();
    const ta = b.closest(".finput").querySelector("textarea");
    ta.value = b.dataset.insert; ta.dispatchEvent(new Event("input", { bubbles: true })); ta.focus();
    return;
  }
  const doc = e.target.closest(".fdoc");
  if (doc) doc.classList.toggle("clamp");
});

// ---------- コマンド生成 ----------
// 値の引用符の付け方は quote.js（テストできるよう別ファイル）
const { quote, splitList } = ShellQuote;

function buildParts() {
  const d = S.detail;
  if (!d) return null;
  const shell = S.shell;
  const base = [{ t: BASE[S.provider], c: "base" }];
  [d.service, d.name].forEach((p) => base.push({ t: p, c: "base" }));
  for (const p of d.positionals || []) {
    const v = S.values["pos:" + p.name];
    if (!v) continue;
    base.push({ t: quote(v, shell), c: "val" });
  }
  const lines = [base];
  const all = [...d.params, ...d.pagination];
  for (const p of all) {
    const v = S.values["p:" + p.name];
    if (!v) continue;
    if (p.type === "boolean" && v !== p.name && v !== p.neg) continue;   // 選択肢にない値（読み込んだ状態など）は使わない
    const opt = { t: p.type === "boolean" ? v : p.name, c: "opt", k: p.name };
    if (p.type === "boolean" || p.type === "flag") lines.push([opt]);
    else if (p.type === "list") lines.push([opt, ...splitList(v).map((x) => ({ t: quote(x, shell), c: "val" }))]);
    else lines.push([opt, { t: quote(v.trim(), shell), c: "val" }]);
  }
  for (const g of d.global_options) {
    const v = S.globals[S.provider][g.name];
    if (!v) continue;
    if (g.type === "flag") lines.push([{ t: g.name, c: "opt", k: g.name }]);
    else lines.push([{ t: g.name, c: "opt", k: g.name }, { t: quote(v, shell), c: "val" }]);
  }
  return lines;
}

function joinLines(lines, html) {
  const cont = { bash: " \\\n  ", pwsh: " `\n  ", cmd: " ^\n  ", bat: " ^\n  ", oneline: " " }[S.shell];
  const fmt = (tok) => (html ? `<span class="tok-${tok.c}"${tok.k ? ` data-k="${esc(tok.k)}"` : ""}>${esc(tok.t)}</span>` : tok.t);
  return lines.map((l) => l.map(fmt).join(" ")).join(html ? esc(cont) : cont);
}

function commandText() { const l = buildParts(); return l ? joinLines(l, false) : ""; }

function build() {
  const lines = buildParts();
  if (!lines) return;
  $("#cmdOutput").innerHTML = joinLines(lines, true);
  updateRisk();
}
// コマンド欄のオプションをクリック → その入力欄へ移動してマーカーで示す
$("#cmdOutput").addEventListener("click", (e) => {
  const tok = e.target.closest("[data-k]");
  if (!tok) return;
  const f = $$("#builderBody .field[data-pname]").find((x) => x.dataset.pname === tok.dataset.k);
  if (!f) return;
  const det = f.closest("details");
  if (det) det.open = true;
  f.hidden = false;
  f.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  f.classList.remove("jump"); void f.offsetWidth; f.classList.add("jump");
  f.querySelector("[data-key]")?.focus({ preventScroll: true });
});
$("#shellSelect").value = S.shell;
$("#shellSelect").onchange = (e) => { S.shell = e.target.value; store.set("shell", S.shell); build(); };
$("#resetBtn").onclick = () => { S.values = {}; renderFields(); build(); };

// ---------- コピー ----------
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); ta.remove();
  }
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 1800);
}
function currentState() {
  return { provider: S.provider, service: S.service, name: S.detail.name, values: S.values, globals: S.globals[S.provider] };
}
$("#copyBtn").onclick = async () => {
  const text = commandText();
  await copyText(text);
  toast(t("Copied"));
  const cb = $("#copyBtn"), flip = (done) => { $(".lbl", cb).hidden = done; $(".lbl-done", cb).hidden = !done; cb.classList.toggle("done", done); };
  flip(true); clearTimeout(cb._t); cb._t = setTimeout(() => flip(false), 1500);
  const out = $("#cmdOutput"); out.classList.remove("copied"); void out.offsetWidth; out.classList.add("copied");
  const hist = store.get("history", []);
  hist.unshift({ provider: S.provider, title: `${S.detail.service} ${S.detail.name}`, command: text, doc_url: S.detail.doc_url,
                 service: S.service, operation: S.detail.name, state: currentState(), at: new Date().toISOString() });
  store.set("history", hist.filter((h, i) => i === 0 || h.command !== text).slice(0, 30));
  if (S.favTab === "history") renderFavorites();
};

// ---------- お気に入り ----------
async function loadFavorites() {
  S.favorites = await api("/api/favorites");
  $("#favCount").textContent = S.favorites.length;
}
function renderFavorites() {
  const q = $("#favFilter").value.trim().toLowerCase();
  let items;
  if (S.favTab === "history") items = store.get("history", []).filter((h) => h.provider === "aws").map((h) => ({ ...h, _hist: true }));
  else items = S.favorites.filter((f) => f.provider === "aws");
  items = items.filter((f) => !q || `${f.title} ${f.memo || ""} ${f.command}`.toLowerCase().includes(q));
  $("#favList").innerHTML = items.map((f, i) => `
    <li class="fav-item" data-i="${i}">
      <div class="t"><span class="p ${f.provider}">AWS</span>${esc(f.title)}</div>
      ${f.memo ? `<div class="m">${esc(f.memo)}</div>` : ""}
      <pre>${esc(f.command)}</pre>
      ${/^https:\/\//.test(f.doc_url || "") ? `<div class="d">Reference: <a href="${esc(f.doc_url)}" target="_blank" rel="noopener">${esc(f.doc_url)}</a></div>` : ""}
      <div class="a" style="margin-top:6px">
        <button class="btn primary" data-act="copy">Copy command</button>
        ${f.state && f.state.name ? `<button class="btn" data-act="open">Open in builder</button>` : ""}
        ${f._hist ? `<button class="btn" data-act="save">★ Save</button>` : `<button class="btn ghost" data-act="edit">Edit</button><button class="btn ghost" data-act="del">Delete</button>`}
      </div>
    </li>`).join("") || `<li class="empty-small">${S.favTab === "history" ? "No copy history yet" : "No favorites yet.<br>Build a command and press “＋ Add to favorites”."}</li>`;
  $("#favList")._items = items;
}
$("#favToggle").onclick = () => { $("#favDrawer").hidden = !$("#favDrawer").hidden; renderFavorites(); };
$("#favClose").onclick = () => ($("#favDrawer").hidden = true);
$("#favFilter").oninput = renderFavorites;
$$(".drawer-tabs button").forEach((b) => (b.onclick = () => {
  S.favTab = b.dataset.tab; $$(".drawer-tabs button").forEach((x) => x.classList.toggle("active", x === b)); renderFavorites();
}));

$("#favList").onclick = async (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const f = $("#favList")._items[+btn.closest(".fav-item").dataset.i];
  switch (btn.dataset.act) {
    case "copy":
      await copyText(f.command); toast(t("Copied"));
      if (f.id) api(`/api/favorites/${f.id}/use`, { method: "POST" });
      break;
    case "open": await openState(f.state); break;
    case "save": openFavDialog({ ...f, id: null }); break;
    case "edit": openFavDialog(f); break;
    case "del":
      if (confirm(t(`Delete "${f.title}"?`))) {
        await api(`/api/favorites/${f.id}`, { method: "DELETE" });
        await loadFavorites(); renderFavorites(); renderServices(); renderCommands();
      }
      break;
  }
};

let editing = null;
function openFavDialog(f) {
  editing = f;
  const form = $("#favForm");
  $("#favDialogTitle").textContent = t(f.id ? "Edit favorite" : "Add to favorites");
  form.title.value = f.title || ""; form.memo.value = f.memo || ""; form.command.value = f.command || "";
  $("#favDialog").showModal();
}
$("#saveFavBtn").onclick = () => {
  const d = S.detail;
  openFavDialog({ provider: S.provider, title: `${d.service} ${d.name}`, command: commandText(),
                  service: S.service, operation: d.name, doc_url: d.doc_url, state: currentState(), memo: "" });
};
$("#favDialog").addEventListener("close", async () => {
  if ($("#favDialog").returnValue !== "ok" || !editing) return;
  const form = $("#favForm");
  const body = { ...editing, title: form.title.value, memo: form.memo.value, command: form.command.value };
  try {
    if (editing.id) await api(`/api/favorites/${editing.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    else await api("/api/favorites", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    toast(t("Saved"));
  } catch (err) { toast(t("Save failed: " + err.message)); }
  await loadFavorites(); renderFavorites(); renderServices(); renderCommands();
});
$("#favImport").onchange = async (e) => {
  const file = e.target.files[0]; if (!file) return;
  try {
    const r = await api("/api/favorites/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: await file.text() });
    toast(t(`Imported ${r.imported}`));
  } catch (err) { toast(t("Import failed: " + err.message)); }
  e.target.value = ""; await loadFavorites(); renderFavorites();
};

// 保存・インポートされた入力状態は信用しない。文字列の値だけを取り出す
function strMap(o) {
  const out = {};
  if (o && typeof o === "object" && !Array.isArray(o)) {
    for (const [k, v] of Object.entries(o)) if (typeof v === "string") out[k] = v;
  }
  return out;
}
async function openState(st) {
  if (!st || typeof st.service !== "string" || typeof st.name !== "string") return toast(t("This favorite cannot be opened in the builder."));
  if (st.globals) { S.globals[S.provider] = strMap(st.globals); store.set("globals", S.globals); }
  await selectService(st.service, { keepHash: true });
  await selectCommand(st.name, { values: strMap(st.values) });
  $("#favDrawer").hidden = true;
}

// ---------- 横断検索（コマンド名は単語の先頭で一致。説明文での一致は別枠） ----------
let gsTimer, gsItems = [], gsSel = -1, gsPasteRaw = null;
const ASCII = /^[\x00-\x7f]+$/;
function hideGlobal() { $("#globalResults").hidden = true; gsSel = -1; }
// 一致した部分に印を付ける（wordOnly: 説明文は単語として一致した所だけ）
function hl(text, tokens, wordOnly = false) {
  const ts = tokens.filter((x) => ASCII.test(x)).map((x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!ts.length) return esc(text);
  const re = new RegExp(wordOnly ? `(?<![\\w-])(?:${ts.join("|")})(?![\\w-])` : `(?<=^|[\\s-])(?:${ts.join("|")})`, "gi");
  let out = "", last = 0;
  for (const m of String(text).matchAll(re)) {
    out += esc(text.slice(last, m.index)) + `<mark>${esc(m[0])}</mark>`;
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}
function renderGlobal(q, res, load) {
  let tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (tokens[0] === "aws") tokens = tokens.slice(1);
  const jaQuery = tokens.some((x) => !ASCII.test(x));
  gsItems = [];
  const row = (it, desc) => {
    gsItems.push({ kind: "cmd", it });
    const line2 = desc ? hl(it.summary || "", tokens, true) : jaQuery && it.ja ? esc(it.ja) : esc(it.summary || "");
    return `<div class="item" data-i="${gsItems.length - 1}" translate="no">
      <div class="c">${BASE[S.provider]} ${hl(`${it.service} ${it.name}`, tokens)}</div><div class="j">${line2}</div></div>`;
  };
  let html = "";
  if (load) {
    gsItems.push({ kind: "load" });
    html += `<div class="item load" data-i="0"><div class="c"><b>↵ Load this command</b></div>
      <div class="j" translate="no">${esc(load)}</div></div>`;
  }
  if (res && res.service) html += `<div class="gs-head">Commands in ${esc(res.service)}</div>`;
  if (res) html += res.results.map((it) => row(it, false)).join("");
  if (res && res.total > res.results.length) html += `<div class="gs-foot">${esc(t(`Showing ${res.results.length} of ${res.total}. Add another word to narrow down.`))}</div>`;
  if (res && res.descriptions.length) html += `<div class="gs-head">Also mentioned in descriptions</div>` + res.descriptions.map((it) => row(it, true)).join("");
  if (!gsItems.length) html = `<div class="gs-foot">No matches</div>`;
  $("#globalResults").innerHTML = html;
  $("#globalResults").hidden = false;
  gsSel = gsItems.length ? 0 : -1;
  $$("#globalResults .item[data-i]").forEach((el, i) => el.classList.toggle("sel", i === gsSel));
}
$("#globalSearch").addEventListener("paste", (e) => {
  // 複数行のコマンドを貼ると、入力欄（1行）では改行が消えてしまうので、元の文字列を保持して1行に整える
  const raw = (e.clipboardData || window.clipboardData)?.getData("text") || "";
  if (!PasteCmd.commandLine(raw)) return;
  e.preventDefault();
  e.stopPropagation();
  gsPasteRaw = raw;
  $("#globalSearch").value = PasteCmd.commandLine(raw);
  $("#globalSearch").dispatchEvent(new Event("input"));
});
// 入力欄以外で Ctrl+V したときも、AWS CLI のコマンドならそのまま読み込む
document.addEventListener("paste", (e) => {
  if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
  const raw = (e.clipboardData || window.clipboardData)?.getData("text") || "";
  if (!PasteCmd.looksLikeCommand(raw)) return;
  e.preventDefault();
  loadPasted(raw);
});
$("#globalSearch").oninput = (e) => {
  clearTimeout(gsTimer);
  if (e.isTrusted) gsPasteRaw = null;   // 手で書き換えたら、貼り付けた元の文字列は使わない
  const q = $("#globalSearch").value.trim();
  if (!q) return hideGlobal();
  const isCmd = PasteCmd.looksLikeCommand(gsPasteRaw || q);
  // コマンドを貼った／書いたときは、オプションより前（サービス名・コマンド名）で候補も探す
  const sq = isCmd ? PasteCmd.commandLine(gsPasteRaw || q).split(/\s+--/)[0] : q;
  gsTimer = setTimeout(async () => {
    const res = await api(`/api/${S.provider}/search?q=${encodeURIComponent(sq)}`);
    if ($("#globalSearch").value.trim() !== q) return;
    renderGlobal(sq, res, isCmd ? q : null);
  }, 160);
};
$("#globalSearch").onkeydown = (e) => {
  const items = $$("#globalResults .item[data-i]");
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    gsSel = Math.max(0, Math.min(items.length - 1, gsSel + (e.key === "ArrowDown" ? 1 : -1)));
    items.forEach((el, i) => el.classList.toggle("sel", i === gsSel));
    items[gsSel]?.scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter" && gsSel <= 0 && PasteCmd.looksLikeCommand(gsPasteRaw || $("#globalSearch").value)) {
    e.preventDefault();   // コマンドが入っていれば、候補の表示を待たずに読み込む
    clearTimeout(gsTimer);
    loadPasted(gsPasteRaw || $("#globalSearch").value);
  } else if (e.key === "Enter" && gsItems.length && !$("#globalResults").hidden) {
    e.preventDefault();
    pickGlobal(gsItems[Math.max(gsSel, 0)]);
  } else if (e.key === "Escape") hideGlobal();
};
$("#globalResults").onclick = (e) => { const el = e.target.closest(".item[data-i]"); if (el) pickGlobal(gsItems[+el.dataset.i]); };
document.addEventListener("click", (e) => { if (!e.target.closest(".global-search")) hideGlobal(); });
async function pickGlobal(x) {
  if (!x) return;
  if (x.kind === "load") return loadPasted(gsPasteRaw || $("#globalSearch").value);
  const it = x.it;
  hideGlobal();
  await selectService(it.service, { keepHash: true });
  await selectCommand(it.name);
  $(`#serviceList li[data-name="${CSS.escape(it.service)}"]`)?.scrollIntoView({ block: "nearest" });
  $(`#commandList li[data-name="${CSS.escape(it.name)}"]`)?.scrollIntoView({ block: "nearest" });
}

// ---------- 貼り付けたコマンドを読み込む（実行はしない） ----------
const PASTE_MSG = {
  extra_lines: (w) => `Only the first aws command was loaded; ${w.n} other line(s) were ignored.`,
  removed: (w) => `Removed the part after the command: ${w.text}`,
  unknown: (w) => `${w.name} is not an option of this command and was skipped.`,
  novalue: (w) => `${w.name} had no value and was skipped.`,
  joined: (w) => `${w.name} had several values and they were combined into one. Check the value.`,
  choice: (w) => `${w.name}: "${w.value}" is not one of the allowed values.`,
  variable: (w) => `${w.name} contains a shell variable or command substitution. Replace it with a real value.`,
  extra: (w) => `Extra argument "${w.value}" was skipped.`,
  cleared: (w) => `Global options not in the pasted command were cleared: ${w.names}`,
};
async function loadPasted(raw) {
  const r = PasteCmd.parse(raw);
  if (r.error) return toast(t(r.error === "not_aws" ? "This is not an AWS CLI command." : "Type the service and the command (e.g. aws ec2 describe-instances)."));
  let d;
  try {
    d = await api(`/api/${S.provider}/command?service=${encodeURIComponent(r.service)}&name=${encodeURIComponent(r.command)}`);
  } catch { return toast(t(`Command not found: aws ${r.service} ${r.command}`)); }
  if (S.detail && Object.keys(S.values).length && !confirm(t("Replace the current inputs with the pasted command?"))) return;
  const m = PasteCmd.mapArgs(d, r.args);
  hideGlobal(); $("#globalSearch").value = ""; gsPasteRaw = null;
  // 共通オプションも貼ったコマンドどおりにする（前の --profile / --region が残ると別の環境に向いてしまうため）
  const cleared = Object.keys(S.globals[S.provider]).filter((k) => !(k in m.globals));
  S.globals[S.provider] = { ...m.globals }; store.set("globals", S.globals);
  await selectService(r.service, { keepHash: true });
  await selectCommand(r.command, { values: m.values });
  $(`#commandList li[data-name="${CSS.escape(r.command)}"]`)?.scrollIntoView({ block: "nearest" });
  const warns = [...r.warnings, ...m.warnings, ...(cleared.length ? [{ code: "cleared", names: cleared.join(", ") }] : [])];
  const n = Object.keys(m.values).length + Object.keys(m.globals).length;
  const note = $("#pasteNote");
  note.innerHTML = `<div class="pn-head"><b>${esc(t(`Loaded ${n} value(s) from the pasted command.`))}</b>
      <button class="linkbtn" data-close-paste>${esc(t("Dismiss"))}</button></div>` +
    (warns.length ? `<ul>${warns.map((w) => `<li>${esc(t(PASTE_MSG[w.code](w)))}</li>`).join("")}</ul>` : "");
  note.classList.toggle("has-warn", warns.length > 0);
  note.hidden = false;
}
$("#pasteNote").addEventListener("click", (e) => { if (e.target.closest("[data-close-paste]")) $("#pasteNote").hidden = true; });

// ---------- 危険な操作の警告（削除・停止など）。--dry-run があれば付けることを勧める ----------
const RISK_DANGER = new Set(["delete", "terminate", "purge", "remove", "deregister", "revoke", "destroy", "reset", "rm", "rb"]);
const RISK_CAUTION = new Set(["stop", "reboot", "restart", "disable", "detach", "disassociate", "release", "replace", "cancel", "reject", "mv"]);
const RISK_OPTS = {
  "--force": "--force skips safety checks.",
  "--delete": "--delete removes files at the destination that are not in the source.",
  "--recursive": "--recursive applies to everything under the path.",
  "--skip-final-snapshot": "--skip-final-snapshot: no final snapshot will be taken.",
  "--delete-automated-backups": "--delete-automated-backups also deletes automated backups.",
  "--force-delete-without-recovery": "--force-delete-without-recovery: the secret cannot be recovered.",
  "--no-verify-ssl": "--no-verify-ssl disables SSL certificate verification.",
};
function updateRisk() {
  const d = S.detail, el = $("#riskNote"), out = $("#cmdOutput");
  if (!d) { el.hidden = true; return; }
  const verb = d.name.split(/[-\s]/)[0];
  const set = (k) => S.values["p:" + k] || S.globals[S.provider][k];
  let level = RISK_DANGER.has(verb) ? "danger" : RISK_CAUTION.has(verb) ? "caution" : "";
  const optNotes = Object.keys(RISK_OPTS).filter((k) => set(k) && (k !== "--recursive" || ["rm", "mv"].includes(d.name)) && (k !== "--delete" || d.name === "sync"));
  if (optNotes.length && !level) level = "danger";
  out.classList.toggle("risk-danger", level === "danger");
  out.classList.toggle("risk-caution", level === "caution");
  if (!level) { el.hidden = true; return; }
  const dry = d.params.find((p) => p.name === "--dry-run" && p.type === "boolean");
  const dryOn = S.values["p:--dry-run"] === "--dry-run";
  el.className = `risk-note cmd-only ${level}`;
  el.innerHTML = `<b>${esc(t(level === "danger"
      ? "⚠ This command deletes or permanently changes resources. Check the target, --region and --profile before running it."
      : "This command stops or changes resources. Check the target before running it."))}</b>` +
    (optNotes.length ? `<ul>${optNotes.map((k) => `<li>${esc(t(RISK_OPTS[k]))}</li>`).join("")}</ul>` : "") +
    (dry ? (dryOn
      ? `<div class="risk-dry ok">${esc(t("--dry-run is set: AWS only checks your permissions and changes nothing."))}</div>`
      : `<div class="risk-dry"><button class="btn mini" data-add-dryrun>${esc(t("Add --dry-run"))}</button> ${esc(t("Check permissions first without changing anything."))}</div>`) : "");
  el.hidden = false;
}
$("#riskNote").addEventListener("click", (e) => {
  if (!e.target.closest("[data-add-dryrun]")) return;
  S.values["p:--dry-run"] = "--dry-run";
  renderFields(); filterFields(); build();
});

// ---------- Claude 連携（ボタンを押したときだけ実行・結果はこの画面の表示中だけ保持） ----------
const CL = BOOT.claude || { available: false, reason: "" };
let aiSeq = 0;            // 実行中に別コマンドへ移ったら結果を捨てるための番号
const aiTimers = {};

function aiKey() { return S.detail ? `${S.provider}|${S.detail.service}|${S.detail.name}` : ""; }
function clearAI() {
  aiSeq++;
  S.ai = null;
  setLang("en");            // 別のコマンド・トップページへ移ったら英語に戻す
  for (const k of Object.keys(aiTimers)) { clearInterval(aiTimers[k]); delete aiTimers[k]; }
  $$(".ai-bar button").forEach((b) => (b.disabled = false));
  $("#aiStatus").textContent = ""; $("#aiStatus").className = "ai-status";
  $("#aiNote").hidden = true; $("#aiNote").innerHTML = "";
}
function filledNames() {
  return Object.keys(S.values).filter((k) => k.startsWith("p:")).map((k) => k.slice(2));
}
function aiBusy(btn, label) {
  const st = $("#aiStatus");
  const t0 = Date.now();
  btn.disabled = true;
  st.className = "ai-status busy";
  const tick = () => (st.textContent = `${t(label)}… ${Math.round((Date.now() - t0) / 1000)}s`);
  tick();
  aiTimers[btn.id] = setInterval(tick, 1000);
  return () => { clearInterval(aiTimers[btn.id]); delete aiTimers[btn.id]; btn.disabled = false; };
}
async function callClaude(task, extra) {
  const d = S.detail;
  return api(`/api/claude/${task}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: S.provider, service: d.service, name: d.name, ...extra }),
  });
}
function aiError(msg) {
  $("#aiStatus").className = "ai-status err";
  $("#aiStatus").textContent = msg;
}
function metaLine(m) {
  const parts = [esc(t("Sentences missing from the dictionary were translated by Claude (saved to the learned dictionary; Claude is not used for them next time)"))];
  if (m && m.model) parts.push(`${esc(t("Model"))}: ${esc(m.model)}`);
  if (m && m.duration_ms) parts.push(`${Math.round(m.duration_ms / 1000)}s`);
  if (m && m.cost_usd != null) parts.push(`~$${Number(m.cost_usd).toFixed(3)}`);
  return parts.join(" / ");
}

$("#aiLocalizeBtn").onclick = async () => {
  if (S.lang === "ja") return selectCommand(S.detail.name, { values: S.values, keepHash: true });   // 「English」で英語に戻す
  // Claude 連携していなければ何もしない（辞書分も日本語化しない）
  if (!CL.available) return aiError(CL.reason);
  // 辞書にある文は辞書で訳し、辞書にない文だけ Claude に送る
  const seq = ++aiSeq, key = aiKey(), btn = $("#aiLocalizeBtn");
  const done = aiBusy(btn, "Translating");
  try {
    const data = await callClaude("localize", { scope: $("#aiScope").value, filled: filledNames() });
    if (seq !== aiSeq || key !== aiKey()) return;          // 別のコマンドに移動済み → 捨てる
    S.ai = { key, data };
    setLang("ja");                                          // 画面の文言も日本語に（ui_ja.json。Claude 不要）
    const m = data._meta || {};
    $("#aiStatus").className = "ai-status ok";
    $("#aiStatus").textContent = `日本語化しました（辞書 ${m.dict_hits || 0}文 / Claude ${m.sent || 0}文` +
      (m.failed ? ` / 訳せなかった ${m.failed}文（もう一度押すとその文だけ再試行）` : m.untranslated ? ` / 辞書になく英語のまま ${m.untranslated}文` : "") + "）";
    renderFields(); filterFields();   // 作り直してから Claude の結果を重ねる（applyAI は renderFields 内で実行）
  } catch (err) {
    if (seq === aiSeq) aiError(t("Failed: " + err.message));
  } finally { if (seq === aiSeq) done(); }
};

function applyAI() {
  if (!S.ai || S.ai.key !== aiKey()) return;
  const a = S.ai.data;
  if (a.title_ja) $("#cmdJa").textContent = a.title_ja;
  if (a.service_ja) $("#crumbService").textContent = `${S.detail.service}（${a.service_ja}）`;
  if (a.description_ja) {
    const el = $("#cmdSummary");
    el.hidden = false;
    el.classList.remove("clamp"); el.classList.add("ai-text");
    el.innerHTML = `${esc(a.description_ja)}${el.dataset.orig ? ` <button class="linkbtn" data-toggle-orig>${t("Show original")}</button><span class="orig" hidden>${esc(el.dataset.orig)}</span>` : ""}`;
  }
  const byName = Object.fromEntries((a.params || []).map((p) => [p.name, p]));
  $$("#builderBody .field[data-pname]").forEach((f) => {
    if (f.querySelector(".fai")) return;
    const p = byName[f.dataset.pname];
    if (!p) return;
    const box = document.createElement("div");
    box.className = "fai";
    box.innerHTML = esc(p.ja || "");
    const doc = f.querySelector(".fdoc");
    if (doc) { doc.hidden = true; box.innerHTML += ` <button class="linkbtn" data-toggle-doc>${t("Original")}</button>`; }
    f.querySelector(".fname").appendChild(box);
  });
  if (a._meta && a._meta.sent) { $("#aiNote").hidden = false; $("#aiNote").innerHTML = metaLine(a._meta); }
}

$("#builderBody").addEventListener("click", (e) => {
  const tg = e.target;
  if (tg.matches("[data-toggle-orig]")) {
    const o = tg.nextElementSibling; o.hidden = !o.hidden; tg.textContent = t(o.hidden ? "Show original" : "Hide original");
  } else if (tg.matches("[data-toggle-doc]")) {
    const doc = tg.closest(".fname").querySelector(".fdoc"); doc.hidden = !doc.hidden; doc.classList.remove("clamp");
  }
});


if (!CL.available) {
  $$(".ai-bar .btn").forEach((b) => (b.title = CL.reason));
  $(".ai-bar").classList.add("unavailable");
}

// ---------- URL（#/aws/ec2/describe-instances） ----------
function setHash() {
  const parts = [S.provider, S.service, S.detail && S.detail.name].filter(Boolean).map(encodeURIComponent);
  history.replaceState(null, "", "#/" + parts.join("/"));
  lastHash = location.hash;
}
async function route() {
  const [, svc, cmd] = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  await loadProvider("aws", { keepHash: true });
  if (svc) {
    await selectService(svc, { keepHash: true });
    if (cmd) await selectCommand(cmd, { keepHash: true }).catch(() => {});
  }
}

// アドレスバーで #/... を直接書き換えたとき
let lastHash = location.hash;
window.addEventListener("hashchange", () => {
  if (location.hash === lastHash) return;
  lastHash = location.hash;
  route();
});

(async function init() {
  await loadFavorites().catch(() => {});
  await route();
})();
