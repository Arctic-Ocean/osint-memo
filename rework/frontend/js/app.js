// rework — 프론트엔드 메인 로직
import { api } from "/js/api.js";

// ---- 상태 ---------------------------------------------------------------
const S = {
  info: null,
  tabs: [],              // {addr, mode}  mode: decompile|disasm|hex
  active: -1,            // 활성 탭 index
  funcNameToAddr: new Map(),
  funcByAddr: new Map(),
  varNameToSlot: new Map(),   // 현재 디컴파일 함수의 name->slot
  aiMessages: [],
  aiCfg: null,
  sideView: "functions",
  focusAddr: null,
};

// ---- DOM 헬퍼 -----------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) e.setAttribute(k, v);
  }
  for (const kid of kids) {
    if (kid === null || kid === undefined) continue;
    e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return e;
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const hx = (n) => "0x" + (n >>> 0 === n ? n.toString(16) : BigInt(n).toString(16));
const hxa = (n) => (typeof n === "bigint" ? n : BigInt(n)).toString(16).padStart(8, "0");

function toast(msg, kind = "") {
  const t = el("div", { class: "toast-item " + kind }, msg);
  $("#toast").append(t);
  setTimeout(() => t.remove(), 2600);
}
function setMsg(m) { $("#sb-msg").textContent = m || ""; }

// ---- 모달(입력/선택) ----------------------------------------------------
function modal({ title, fields, okText = "확인" }) {
  return new Promise((resolve) => {
    $("#modal-title").textContent = title;
    const body = $("#modal-body");
    body.innerHTML = "";
    const inputs = {};
    for (const f of fields) {
      if (f.label) body.append(el("label", {}, f.label));
      let inp;
      if (f.type === "select") {
        inp = el("select");
        for (const o of f.options) inp.append(el("option", { value: o.value, ...(o.value === f.value ? { selected: "" } : {}) }, o.text));
      } else if (f.type === "textarea") {
        inp = el("textarea", { rows: f.rows || 3 }); inp.value = f.value || "";
      } else {
        inp = el("input", { type: f.type || "text", placeholder: f.placeholder || "" });
        inp.value = f.value ?? "";
      }
      inputs[f.name] = inp;
      body.append(inp);
    }
    $("#modal").classList.remove("hidden");
    const first = Object.values(inputs)[0];
    if (first) { first.focus(); if (first.select) first.select(); }
    const close = (val) => {
      $("#modal").classList.add("hidden");
      $("#modal-ok").onclick = $("#modal-cancel").onclick = null;
      body.onkeydown = null;
      resolve(val);
    };
    const collect = () => { const o = {}; for (const [k, i] of Object.entries(inputs)) o[k] = i.value; return o; };
    $("#modal-ok").textContent = okText;
    $("#modal-ok").onclick = () => close(collect());
    $("#modal-cancel").onclick = () => close(null);
    body.onkeydown = (e) => { if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") { e.preventDefault(); close(collect()); } };
  });
}
async function prompt1(title, label, value = "") {
  const r = await modal({ title, fields: [{ name: "v", label, value }] });
  return r ? r.v : null;
}

// ---- 아이콘(SVG) --------------------------------------------------------
const ICON = {
  open: '<path d="M2 4a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v2H2V4Z"/><path d="M2 8h20l-2 10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1L2 8Z" opacity=".6"/>',
  functions: '<path d="M4 20c2 0 2.5-1.2 3-4l1.2-7C8.8 5 9.6 3.5 12 3.5c1 0 1.6.3 2 .7"/><path d="M6 10h7"/>',
  strings: '<path d="M4 7V5h12v2M10 5v14M8 19h4"/>',
  symbols: '<circle cx="7" cy="8" r="3"/><path d="M14 5h6v6M20 5l-6 6"/><rect x="4" y="15" width="6" height="5" rx="1"/>',
  imports: '<path d="M12 3v10m0 0-4-4m4 4 4-4M5 17v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2"/>',
  bookmarks: '<path d="M6 3h12v18l-6-4-6 4V3Z"/>',
  script: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M13 5l-2 14"/>',
  ai: '<path d="M12 3a4 4 0 0 1 4 4v1a4 4 0 0 1 0 8v1a4 4 0 0 1-8 0v-1a4 4 0 0 1 0-8V7a4 4 0 0 1 4-4Z"/><circle cx="9.5" cy="10" r="1"/><circle cx="14.5" cy="10" r="1"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M19 5l-2 2M7 17l-2 2"/>',
};
function paintIcons() {
  $$("#activitybar .act").forEach((b) => {
    const key = b.dataset.view || (b.dataset.cmd === "toggle-ai" ? "ai" : null);
    if (key && ICON[key]) {
      b.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${ICON[key]}</svg>`;
    }
  });
}

// ---- 레이아웃: 액티비티바 / 패널 토글 / 테마 ---------------------------
function setSideView(v) {
  S.sideView = v;
  $$("#activitybar .act").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  const titles = { open: "열기", functions: "함수", strings: "문자열", symbols: "심볼",
                   imports: "임포트 / 익스포트", bookmarks: "북마크", script: "스크립트 콘솔", settings: "설정" };
  $("#side-title").textContent = titles[v] || v;
  $("#side-count").textContent = "";
  renderSide(v);
}
function toggleAI() {
  $("#aipanel").classList.toggle("hidden");
  $("#split-ai").classList.toggle("hidden", $("#aipanel").classList.contains("hidden"));
  if (!$("#aipanel").classList.contains("hidden")) $("#ai-input").focus();
}
function toggleTheme() {
  const r = document.documentElement;
  const cur = document.body.dataset.theme === "light" ? "light" : "dark";
  const next = cur === "light" ? "dark" : "light";
  document.body.dataset.theme = next; r.dataset.theme = next;
  try { localStorage.setItem("rework-theme", next); } catch {}
}

// ---- 파일 열기 ----------------------------------------------------------
let fsCwd = "";
async function renderOpen() {
  const body = $("#side-body");
  body.innerHTML = "";
  const up = el("div", { class: "upload-zone" });
  up.innerHTML = '파일을 끌어다 놓거나 <label for="up-input">찾아보기</label>' +
    '<input id="up-input" type="file">';
  body.append(up);
  up.querySelector("#up-input").addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    setMsg("업로드 중…");
    try { const info = await api.upload(f); await onOpened(info); }
    catch (err) { toast("열기 실패: " + err.message, "err"); }
  });
  up.addEventListener("dragover", (e) => { e.preventDefault(); up.style.borderColor = "var(--accent)"; });
  up.addEventListener("dragleave", () => { up.style.borderColor = ""; });
  up.addEventListener("drop", async (e) => {
    e.preventDefault(); up.style.borderColor = "";
    const f = e.dataTransfer.files[0]; if (!f) return;
    try { const info = await api.upload(f); await onOpened(info); }
    catch (err) { toast("열기 실패: " + err.message, "err"); }
  });
  const pathRow = el("div", { class: "fs-path" }, "로컬 경로 탐색");
  body.append(pathRow);
  await listDir(fsCwd);
}
async function listDir(path) {
  try {
    const r = await api.fs(path);
    fsCwd = r.path;
    const body = $("#side-body");
    $$(".fs-list", body).forEach((e) => e.remove());
    const list = el("div", { class: "fs-list" });
    $(".fs-path").textContent = r.path;
    if (r.parent && r.parent !== r.path) {
      list.append(el("div", { class: "list-item", onclick: () => listDir(r.parent) },
        el("span", { class: "li-name fs-up" }, ".. (상위 폴더)")));
    }
    for (const ent of r.entries) {
      const item = el("div", { class: "list-item" },
        el("span", { class: "li-name" }, (ent.dir ? "📁 " : "📄 ") + ent.name),
        ent.dir ? null : el("span", { class: "li-sub" }, fmtSize(ent.size)));
      item.onclick = ent.dir ? () => listDir(ent.path) : () => openPath(ent.path);
      list.append(item);
    }
    body.append(list);
  } catch (err) { toast("디렉터리 열기 실패: " + err.message, "err"); }
}
const fmtSize = (n) => n > 1 << 20 ? (n / (1 << 20)).toFixed(1) + "M"
  : n > 1024 ? (n / 1024).toFixed(1) + "K" : n + "B";
async function openPath(path) {
  setMsg("여는 중…");
  try { const info = await api.open(path); await onOpened(info); }
  catch (err) { toast("열기 실패: " + err.message, "err"); setMsg(""); }
}
async function onOpened(info) {
  S.info = info;
  S.tabs = []; S.active = -1;
  $("#file-name").textContent = info.name;
  $("#file-meta").textContent =
    `${info.fmt} · ${info.arch}/${info.bits}bit · ${info.counts.functions}개 함수 · ${fmtSize(info.size)}`;
  $("#sb-arch").textContent = `${info.fmt} ${info.arch}`;
  setMsg(`${info.name} 열림`);
  // 함수 이름 맵 구축
  const all = await api.functions("", 0, 100000);
  S.funcNameToAddr.clear(); S.funcByAddr.clear();
  for (const f of all.items) { S.funcNameToAddr.set(f.name, f.vaddr); S.funcByAddr.set(f.vaddr, f); }
  setSideView("functions");
  // 진입점/ main 열기
  const main = S.funcNameToAddr.get("main") ?? info.entrypoint;
  if (main) openFunc(main);
}

// ---- 사이드바 뷰 라우팅 -------------------------------------------------
function renderSide(v) {
  if (!S.info && v !== "open" && v !== "settings") {
    $("#side-body").innerHTML = '<div class="empty-list">먼저 바이너리를 여세요.</div>';
    return;
  }
  ({ open: renderOpen, functions: renderFunctions, strings: renderStrings,
     symbols: renderSymbols, imports: renderImports, bookmarks: renderBookmarks,
     script: renderScript, settings: renderSettings }[v] || (() => {}))();
}

function searchList(onSearch, placeholder) {
  const body = $("#side-body"); body.innerHTML = "";
  const box = el("div", { class: "search-box" });
  const inp = el("input", { type: "text", placeholder });
  box.append(inp); body.append(box);
  const listWrap = el("div"); body.append(listWrap);
  let t;
  inp.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => onSearch(inp.value, listWrap), 180); });
  onSearch("", listWrap);
  return inp;
}

async function renderFunctions() {
  searchList(async (q, wrap) => {
    const r = await api.functions(q, 0, 500);
    $("#side-count").textContent = `${r.total}개`;
    wrap.innerHTML = "";
    if (!r.items.length) { wrap.append(el("div", { class: "empty-list" }, "결과 없음")); return; }
    for (const f of r.items) {
      const badge = f.is_import ? el("span", { class: "li-badge imp" }, "imp")
        : f.is_export ? el("span", { class: "li-badge exp" }, "exp")
        : f.xref_to ? el("span", { class: "li-badge" }, "×" + f.xref_to) : null;
      const item = el("div", { class: "list-item", "data-addr": f.vaddr },
        el("span", { class: "li-addr" }, hxa(f.vaddr)),
        el("span", { class: "li-name" }, f.name), badge);
      item.onclick = () => openFunc(f.vaddr);
      wrap.append(item);
    }
  }, "함수 이름·주소 검색");
}

async function renderStrings() {
  searchList(async (q, wrap) => {
    const r = await api.strings(q, 0, 800);
    $("#side-count").textContent = `${r.total}개`;
    wrap.innerHTML = "";
    if (!r.items.length) { wrap.append(el("div", { class: "empty-list" }, "결과 없음")); return; }
    for (const s of r.items) {
      const item = el("div", { class: "list-item", title: s.value },
        el("span", { class: "li-addr" }, s.addr ? hxa(s.addr) : "—"),
        el("span", { class: "li-name" }, s.value));
      item.onclick = () => s.addr ? showXrefsFor(s.addr, `문자열 ${hx(s.addr)}`) : null;
      wrap.append(item);
    }
  }, "문자열 검색");
}

async function renderSymbols() {
  searchList(async (q, wrap) => {
    const r = await api.symbols(q, 0, 800);
    $("#side-count").textContent = `${r.total}개`;
    wrap.innerHTML = "";
    for (const s of r.items) {
      const item = el("div", { class: "list-item" },
        el("span", { class: "li-addr" }, s.addr ? hxa(s.addr) : "—"),
        el("span", { class: "li-name" }, s.name),
        s.is_import ? el("span", { class: "li-badge imp" }, "imp") : null);
      item.onclick = () => s.addr ? navigate(s.addr) : null;
      wrap.append(item);
    }
  }, "심볼 검색");
}

async function renderImports() {
  const body = $("#side-body"); body.innerHTML = "";
  const [imps, exps] = await Promise.all([api.imports(), api.exports()]);
  body.append(el("div", { class: "side-head", style: "border-top:none" }, `임포트 (${imps.length})`));
  for (const s of imps) {
    body.append(el("div", { class: "list-item", title: s.library || "",
      onclick: () => s.addr ? navigate(s.addr) : null },
      el("span", { class: "li-name" }, s.name),
      s.library ? el("span", { class: "li-sub" }, s.library) : null));
  }
  body.append(el("div", { class: "side-head" }, `익스포트 (${exps.length})`));
  for (const s of exps) {
    body.append(el("div", { class: "list-item", onclick: () => navigate(s.addr) },
      el("span", { class: "li-addr" }, hxa(s.addr)),
      el("span", { class: "li-name" }, s.name)));
  }
}

async function renderBookmarks() {
  const body = $("#side-body"); body.innerHTML = "";
  const bms = await api.bookmarks();
  $("#side-count").textContent = `${bms.length}개`;
  if (!bms.length) { body.append(el("div", { class: "empty-list" }, "북마크가 없습니다.\n명령에서 주소에 북마크를 추가하세요.")); return; }
  for (const b of bms) {
    body.append(el("div", { class: "list-item", onclick: () => navigate(b.addr) },
      el("span", { class: "li-addr" }, hxa(b.addr)),
      el("span", { class: "li-name" }, b.label || "(라벨 없음)")));
  }
}

// ---- 스크립트 콘솔 ------------------------------------------------------
let scriptCode = "# rk: 로드된 바이너리 분석 API\n# 예) 모든 함수 이름 출력\nfor f in rk.functions():\n    print(hex(f.vaddr), rk.name(f.vaddr))\n";
function renderScript() {
  const body = $("#side-body"); body.innerHTML = "";
  const wrap = el("div", { class: "script-wrap" });
  const ta = el("textarea", { class: "script-ed", spellcheck: "false" });
  ta.value = scriptCode;
  ta.addEventListener("input", () => scriptCode = ta.value);
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Tab") { e.preventDefault(); const s = ta.selectionStart;
      ta.value = ta.value.slice(0, s) + "    " + ta.value.slice(ta.selectionEnd);
      ta.selectionStart = ta.selectionEnd = s + 4; scriptCode = ta.value; }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runScript(); }
  });
  const bar = el("div", { class: "script-bar" },
    el("button", { class: "primary", onclick: runScript }, "실행 (Ctrl+Enter)"));
  wrap.append(ta, bar);
  body.append(wrap);
}
async function runScript() {
  if (!S.info) { toast("먼저 바이너리를 여세요.", "err"); return; }
  showPanel("output");
  const out = $("#panel-output");
  out.append(el("div", { class: "out-cmd" }, ">>> 스크립트 실행"));
  try {
    const r = await api.script(scriptCode);
    if (r.output) out.append(el("div", {}, r.output));
    if (!r.ok && r.error) out.append(el("div", { class: "out-err" }, r.error));
  } catch (err) { out.append(el("div", { class: "out-err" }, err.message)); }
  out.scrollTop = out.scrollHeight;
}

// ---- 설정 ---------------------------------------------------------------
async function renderSettings() {
  const body = $("#side-body"); body.innerHTML = "";
  if (!S.aiCfg) { try { S.aiCfg = await api.aiConfigGet(); } catch {} }
  const sec = (t) => body.append(el("div", { class: "side-head", style: "border-top:none" }, t));
  sec("화면");
  const themeBtn = el("div", { class: "list-item", onclick: toggleTheme },
    el("span", { class: "li-name" }, "🌓 테마 전환 (다크/라이트)"));
  body.append(themeBtn);
  sec("AI 분석 도우미");
  body.append(el("div", { class: "list-item", onclick: openAISettings },
    el("span", { class: "li-name" }, "⚙ AI 제공자·모델 설정")));
  if (S.aiCfg) {
    body.append(el("div", { class: "fs-path" },
      `제공자: ${S.aiCfg.provider} · 모델: ${S.aiCfg.model}` + (S.aiCfg.has_key ? " · 키 저장됨" : "")));
  }
  sec("정보");
  body.append(el("div", { class: "dec-note", style: "margin:10px 12px" },
    "rework는 로컬 전용 리버스 엔지니어링 워크벤치입니다. 분석은 모두 이 컴퓨터에서 처리되며, 외부 전송은 AI 도우미를 직접 사용할 때만 일어납니다."));
}

// ---- 탭 / 에디터 --------------------------------------------------------
function tabIndex(addr) { return S.tabs.findIndex((t) => t.addr === addr); }
function openFunc(addr, mode) {
  addr = Number(addr);
  let i = tabIndex(addr);
  if (i < 0) { S.tabs.push({ addr, mode: mode || "decompile" }); i = S.tabs.length - 1; }
  else if (mode) S.tabs[i].mode = mode;
  S.active = i;
  renderTabs(); renderEditor();
}
function closeTab(addr) {
  const i = tabIndex(addr); if (i < 0) return;
  S.tabs.splice(i, 1);
  if (S.active >= S.tabs.length) S.active = S.tabs.length - 1;
  renderTabs(); renderEditor();
}
function renderTabs() {
  const bar = $("#tabs"); bar.innerHTML = "";
  S.tabs.forEach((t, i) => {
    const f = S.funcByAddr.get(t.addr);
    const name = (f && f.name) || hx(t.addr);
    const tab = el("div", { class: "tab" + (i === S.active ? " active" : "") },
      el("span", { class: "tab-name" }, name),
      el("button", { class: "tab-x", title: "닫기", onclick: (e) => { e.stopPropagation(); closeTab(t.addr); } }, "×"));
    tab.onclick = () => { S.active = i; renderTabs(); renderEditor(); };
    bar.append(tab);
  });
}
function curTab() { return S.active >= 0 ? S.tabs[S.active] : null; }

async function renderEditor() {
  const ed = $("#editor");
  const tab = curTab();
  if (!tab) {
    ed.innerHTML = '<div class="empty-state"><div class="es-big">rework</div>' +
      '<div class="es-sub">리버스 엔지니어링 워크벤치</div></div>';
    return;
  }
  ed.innerHTML = "";
  const toolbar = el("div", { class: "view-toolbar" });
  const seg = el("div", { class: "seg" });
  for (const [m, label] of [["decompile", "의사코드"], ["disasm", "디스어셈블리"], ["hex", "헥스"]]) {
    const b = el("button", { class: tab.mode === m ? "active" : "" }, label);
    b.onclick = () => { tab.mode = m; renderEditor(); };
    seg.append(b);
  }
  const f = S.funcByAddr.get(tab.addr);
  toolbar.append(seg,
    el("span", { class: "vt-title" }, (f && f.name) || hx(tab.addr)),
    el("span", { class: "vt-spacer" }),
    el("button", { class: "vt-btn", onclick: () => renameFunc(tab.addr), title: "함수 이름 변경 (F2)" }, "이름변경"),
    el("button", { class: "vt-btn", onclick: () => askAIaboutFunc(tab.addr) }, "AI 설명"),
    el("button", { class: "vt-btn", onclick: () => toggleBookmark(tab.addr) }, "북마크"));
  ed.append(toolbar);
  const view = el("div");
  ed.append(view);
  $("#sb-addr").textContent = hx(tab.addr);
  try {
    if (tab.mode === "decompile") await renderDecompile(view, tab.addr);
    else if (tab.mode === "disasm") await renderDisasm(view, tab.addr);
    else await renderHex(view, tab.addr);
  } catch (err) { view.append(el("div", { class: "dec-note" }, "오류: " + err.message)); }
  await loadXrefs(tab.addr);
  if (S.focusAddr) { scrollToAddr(S.focusAddr); S.focusAddr = null; }
}

function scrollToAddr(addr) {
  const node = $(`[data-ins="${addr}"]`);
  if (node) { node.classList.add("cur"); node.scrollIntoView({ block: "center" }); }
}

// ---- 디스어셈블리 렌더 --------------------------------------------------
function renderOperands(ins) {
  // op_str을 이스케이프 + 즉값 색칠, 해석된 타깃은 뒤에 링크로 덧붙인다.
  let ops = esc(ins.op_str).replace(/\b(0x[0-9a-f]+)\b/g, '<span class="tok-imm">$1</span>');
  const extra = [];
  for (const t of ins.targets || []) {
    if (t.string != null) {
      extra.push(`<span class="ref-str" data-nav="${t.target}" title="${esc(t.string)}">&quot;${esc(t.string.slice(0, 40))}&quot;</span>`);
    } else if (t.name) {
      extra.push(`<span class="ref-link" data-nav="${t.target}">${esc(t.name)}</span>`);
    } else if (t.kind === "call" || t.kind === "jump") {
      extra.push(`<span class="ref-link" data-nav="${t.target}">${hx(t.target)}</span>`);
    }
  }
  return ops + (extra.length ? '  <span class="ins-flag">→</span> ' + extra.join("  ") : "");
}
async function renderDisasm(view, addr) {
  const d = await api.func(addr);
  const wrap = el("div", { class: "code-view asm" });
  const head = el("div", { class: "asm-fnhead" },
    el("span", { class: "fn-name" }, d.name), `   ${hx(d.addr)} · ${d.size} 바이트 · 피참조 ${d.xrefs.length}`);
  wrap.append(head);
  for (const ins of d.instructions) {
    const row = el("div", { class: "ins", "data-ins": ins.addr });
    row.innerHTML =
      `<span class="a">${hxa(ins.addr)}</span>` +
      `<span class="bytes">${ins.bytes}</span>` +
      `<span class="m ${ins.flow === "ret" ? "flow-ret" : ""}">${esc(ins.mnemonic)}</span>` +
      `<span class="o">${renderOperands(ins)}` +
      (ins.comment ? ` <span class="cmt">; ${esc(ins.comment)}</span>` : "") + `</span>`;
    row.addEventListener("dblclick", () => commentAt(ins.addr, ins.comment || ""));
    wrap.append(row);
  }
  view.append(wrap);
  wireNav(view);
}

// ---- 디컴파일(의사코드) 렌더 -------------------------------------------
const KW = new Set(["if", "else", "goto", "return", "while", "do", "for", "break", "continue"]);
function tokenizeDec(text, varSet) {
  const re = /("(?:[^"\\]|\\.)*")|([A-Za-z_]\w*)|(0x[0-9a-fA-F]+|\d+)|(\s+)|([^\sA-Za-z0-9_"]+)/g;
  let out = "", m;
  while ((m = re.exec(text))) {
    if (m[1]) out += `<span class="str">${esc(m[1])}</span>`;
    else if (m[2]) {
      const id = m[2];
      const after = text.slice(re.lastIndex).match(/^\s*\(/);
      if (KW.has(id)) out += `<span class="kw">${id}</span>`;
      else if (after && !varSet.has(id)) out += `<span class="call" data-call="${esc(id)}">${esc(id)}</span>`;
      else if (varSet.has(id)) out += `<span class="var" data-var="${esc(id)}">${esc(id)}</span>`;
      else out += esc(id);
    }
    else if (m[3]) out += `<span class="tok-imm">${esc(m[3])}</span>`;
    else if (m[4]) out += m[4];
    else out += esc(m[5]);
  }
  return out;
}
async function renderDecompile(view, addr) {
  const d = await api.decompile(addr);
  S.varNameToSlot.clear();
  const varSet = new Set();
  for (const v of d.variables || []) { varSet.add(v.name); S.varNameToSlot.set(v.name, v.slot); }
  const wrap = el("div", { class: "code-view dec" });
  for (const ln of d.lines) {
    const pad = "  ".repeat(ln.indent);
    let html;
    if (ln.kind === "comment") html = `<span class="comment">${esc(ln.text)}</span>`;
    else if (ln.kind === "label") html = `<span class="label">${esc(ln.text)}</span>`;
    else if (ln.kind === "sig") html = `<span class="dec-sig">${tokenizeDec(ln.text, varSet)}</span>`;
    else html = tokenizeDec(ln.text, varSet);
    const row = el("div", { class: "dec-line", "data-ins": ln.addr, html: esc(pad) + html });
    wrap.append(row);
  }
  view.append(wrap);
  if (d.note) view.append(el("div", { class: "dec-note" }, "ℹ " + d.note));
  // 변수 더블클릭 → 리네임
  wrap.addEventListener("dblclick", (e) => {
    const v = e.target.closest(".var");
    if (v) { renameVar(addr, v.dataset.var); }
  });
  // 호출 이름 클릭 → 이동
  wrap.addEventListener("click", (e) => {
    const c = e.target.closest(".call");
    if (c) { const a = S.funcNameToAddr.get(c.dataset.call); if (a != null) navigate(a); }
  });
}

// ---- 헥스 렌더 ----------------------------------------------------------
async function renderHex(view, addr) {
  const r = await api.hex(addr, 512);
  const bytes = r.bytes.match(/../g) || [];
  const wrap = el("div", { class: "hex" });
  for (let i = 0; i < bytes.length; i += 16) {
    const chunk = bytes.slice(i, i + 16);
    const hexp = chunk.map((b, k) => b + (k === 7 ? " " : "")).join(" ").padEnd(49, " ");
    const asc = chunk.map((b) => { const c = parseInt(b, 16); return c >= 32 && c < 127 ? String.fromCharCode(c) : "."; }).join("");
    wrap.append(el("div", { class: "hex-row", html:
      `<span class="hex-off">${hxa(r.addr + i)}</span>  ` +
      `<span class="hex-b">${hexp}</span> <span class="hex-ascii">|${esc(asc)}|</span>` }));
  }
  view.append(wrap);
}

// ---- 네비게이션 / 상호참조 ---------------------------------------------
function wireNav(root) {
  root.addEventListener("click", (e) => {
    const n = e.target.closest("[data-nav]");
    if (n) navigate(Number(n.dataset.nav));
  });
}
async function navigate(addr) {
  addr = Number(addr);
  try {
    const c = await api.navigate(addr);
    if (c.type === "string" || c.type === "data") {
      S.focusAddr = null; openFunc(addr, "hex");
    } else {
      // 함수 시작이 아니면 포함 함수로 보정하지 않고 그대로 임시 함수로 연다
      S.focusAddr = addr; openFunc(addr, curTab() ? curTab().mode === "hex" ? "disasm" : curTab().mode : "disasm");
    }
  } catch { S.focusAddr = addr; openFunc(addr, "disasm"); }
}
async function loadXrefs(addr) {
  const panel = $("#panel-xrefs");
  try {
    const xs = await api.xrefs(addr);
    renderXrefList(panel, xs, addr);
  } catch { panel.innerHTML = ""; }
}
function renderXrefList(panel, xs, addr) {
  panel.innerHTML = "";
  panel.append(el("div", { class: "xref-empty", style: "padding:4px 14px;color:var(--fg-dim)" },
    `${hx(addr)} 로의 참조 ${xs.length}건`));
  if (!xs.length) { panel.append(el("div", { class: "xref-empty" }, "참조 없음")); return; }
  for (const x of xs) {
    const item = el("div", { class: "xref-item", onclick: () => { S.focusAddr = x.from; navigate(x.func); } },
      el("span", { class: "xref-kind" }, x.kind),
      el("span", {}, `${hxa(x.from)}  ${x.func_name || hx(x.func)}`));
    panel.append(item);
  }
}
async function showXrefsFor(addr, label) {
  showPanel("xrefs");
  const xs = await api.xrefs(addr);
  renderXrefList($("#panel-xrefs"), xs, addr);
  setMsg(label + " 참조 " + xs.length + "건");
}

// ---- 리네임 / 코멘트 / 북마크 ------------------------------------------
async function renameFunc(addr) {
  const cur = S.funcByAddr.get(addr);
  const v = await prompt1("함수 이름 변경", hx(addr), (cur && cur.name) || "");
  if (v === null) return;
  await api.rename(addr, v);
  const r = await api.functions("", 0, 100000);
  S.funcNameToAddr.clear(); S.funcByAddr.clear();
  for (const f of r.items) { S.funcNameToAddr.set(f.name, f.vaddr); S.funcByAddr.set(f.vaddr, f); }
  renderTabs(); renderEditor();
  toast("이름 변경됨", "ok");
}
async function renameVar(func, name) {
  const slot = S.varNameToSlot.get(name);
  if (!slot) return;
  const v = await prompt1("변수 이름 변경", `${slot} →`, name);
  if (v === null) return;
  await api.varname(func, slot, v);
  renderEditor();
}
async function commentAt(addr, cur) {
  const v = await prompt1("주석", hx(addr), cur);
  if (v === null) return;
  await api.comment(addr, v);
  renderEditor();
}
async function toggleBookmark(addr) {
  const label = await prompt1("북마크 추가/해제", hx(addr) + " 라벨(비우면 해제)", "");
  if (label === null) return;
  const r = await api.bookmark(addr, label);
  toast(r.bookmarked ? "북마크 추가" : "북마크 해제", "ok");
}

// ---- 명령 팔레트 --------------------------------------------------------
const COMMANDS = [
  { id: "open", icon: "📂", title: "파일 열기", run: () => setSideView("open") },
  { id: "theme", icon: "🌓", title: "테마 전환", run: toggleTheme },
  { id: "ai", icon: "🤖", title: "AI 채팅 열기/닫기", run: toggleAI },
  { id: "ai-settings", icon: "⚙", title: "AI 설정", run: openAISettings },
  { id: "script", icon: "⌨", title: "스크립트 콘솔", run: () => setSideView("script") },
  { id: "strings", icon: "❝", title: "문자열 보기", run: () => setSideView("strings") },
  { id: "goto", icon: "⌖", title: "주소로 이동…", run: async () => {
      const v = await prompt1("주소로 이동", "예: 0x1189"); if (v) navigate(parseInt(v, v.startsWith("0x") ? 16 : 16)); } },
];
let palItems = [], palSel = 0;
function openPalette(prefix = "") {
  $("#palette").classList.remove("hidden");
  const inp = $("#palette-input");
  inp.value = prefix; inp.focus();
  updatePalette(prefix);
  inp.oninput = () => updatePalette(inp.value);
  inp.onkeydown = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); palSel = Math.min(palSel + 1, palItems.length - 1); paintPalette(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); palSel = Math.max(palSel - 1, 0); paintPalette(); }
    else if (e.key === "Enter") { e.preventDefault(); runPalette(palSel); }
    else if (e.key === "Escape") { closePalette(); }
  };
}
function closePalette() { $("#palette").classList.add("hidden"); }
async function updatePalette(q) {
  palSel = 0;
  if (q.startsWith(">")) {
    const t = q.slice(1).trim().toLowerCase();
    palItems = COMMANDS.filter((c) => c.title.toLowerCase().includes(t))
      .map((c) => ({ icon: c.icon, title: c.title, sub: "명령", run: c.run }));
  } else if (q.startsWith("0x") || /^\d/.test(q)) {
    const addr = parseInt(q, 16);
    palItems = [{ icon: "⌖", title: `주소 ${hx(addr)} 로 이동`, sub: "goto", run: () => navigate(addr) }];
  } else {
    const term = q.startsWith("@") ? q.slice(1) : q;
    if (S.info) {
      const r = await api.functions(term, 0, 40);
      palItems = r.items.map((f) => ({ icon: "ƒ", title: f.name, sub: hx(f.vaddr), run: () => openFunc(f.vaddr) }));
    } else palItems = [];
    if (!q) palItems = COMMANDS.map((c) => ({ icon: c.icon, title: c.title, sub: "명령", run: c.run }));
  }
  paintPalette();
}
function paintPalette() {
  const ul = $("#palette-list"); ul.innerHTML = "";
  palItems.forEach((it, i) => {
    const li = el("li", { class: i === palSel ? "sel" : "" },
      el("span", { class: "pl-ico" }, it.icon),
      el("span", {}, it.title),
      el("span", { class: "pl-sub" }, it.sub || ""));
    li.onmouseenter = () => { palSel = i; paintPalette(); };
    li.onclick = () => runPalette(i);
    ul.append(li);
  });
}
function runPalette(i) { const it = palItems[i]; if (!it) return; closePalette(); it.run(); }

// ---- AI 채팅 ------------------------------------------------------------
function aiPush(role, content) {
  S.aiMessages.push({ role, content });
  const box = $("#ai-messages");
  const m = el("div", { class: "ai-msg " + role });
  m.innerHTML = esc(content).replace(/`([^`]+)`/g, "<code>$1</code>");
  box.append(m); box.scrollTop = box.scrollHeight;
  return m;
}
async function sendAI(extraContext = "") {
  const inp = $("#ai-input");
  const text = inp.value.trim();
  if (!text && !extraContext) return;
  inp.value = "";
  let userContent = text;
  let system = "당신은 리버스 엔지니어링을 돕는 보안 분석 도우미입니다. 한국어로 간결하고 정확하게 답하세요. 의사코드는 휴리스틱 분석 결과이므로 추측에는 근거를 밝히세요.";
  if ($("#ai-attach-fn").checked && curTab()) {
    try {
      const d = await api.decompile(curTab().addr);
      const code = d.lines.map((l) => "  ".repeat(l.indent) + l.text).join("\n");
      extraContext = `다음은 함수 ${d.name}의 휴리스틱 의사코드입니다:\n\n\`\`\`c\n${code}\n\`\`\`\n`;
    } catch {}
  }
  const display = text || "(현재 함수 분석)";
  aiPush("user", display + (extraContext ? "\n\n[함수 의사코드 첨부됨]" : ""));
  const msgs = [...S.aiMessages.filter((m) => m.role !== "sys").map((m) => ({ role: m.role, content: m.content }))];
  if (extraContext) msgs[msgs.length - 1] = { role: "user", content: extraContext + "\n질문: " + (text || "이 함수가 무엇을 하는지 설명해 주세요.") };
  $("#ai-status").textContent = "생각 중…";
  $("#ai-send").disabled = true;
  try {
    const r = await api.aiChat(msgs, system);
    if (r.ok) aiPush("assistant", r.content || "(빈 응답)");
    else { aiPush("sys", "⚠ " + r.error); }
  } catch (err) { aiPush("sys", "⚠ " + err.message); }
  $("#ai-status").textContent = "";
  $("#ai-send").disabled = false;
}
function askAIaboutFunc(addr) {
  if ($("#aipanel").classList.contains("hidden")) toggleAI();
  $("#ai-attach-fn").checked = true;
  $("#ai-input").value = "이 함수가 무슨 일을 하는지 설명해 주세요.";
  sendAI();
}
async function openAISettings() {
  if (!S.aiCfg) { try { S.aiCfg = await api.aiConfigGet(); } catch { S.aiCfg = {}; } }
  const r = await modal({
    title: "AI 제공자 설정", okText: "저장",
    fields: [
      { name: "provider", label: "제공자", type: "select", value: S.aiCfg.provider,
        options: [{ value: "ollama", text: "Ollama (로컬)" }, { value: "openai", text: "OpenAI 호환" }, { value: "anthropic", text: "Anthropic (Claude)" }] },
      { name: "base", label: "엔드포인트 (base URL)", value: S.aiCfg.base },
      { name: "model", label: "모델", value: S.aiCfg.model },
      { name: "api_key", label: "API 키 (Ollama는 불필요)", type: "password", value: "" },
    ],
  });
  if (!r) return;
  const patch = { provider: r.provider, base: r.base, model: r.model };
  if (r.api_key) patch.api_key = r.api_key;
  S.aiCfg = await api.aiConfigSet(patch);
  toast("AI 설정 저장됨", "ok");
  if (S.sideView === "settings") renderSettings();
}

// ---- 하단 패널 ----------------------------------------------------------
function showPanel(name) {
  $$("#bottom .ptab").forEach((b) => b.classList.toggle("active", b.dataset.panel === name));
  $$("#bottom .panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
  $("#bottom").classList.remove("collapsed");
}

// ---- 스플리터 드래그 ----------------------------------------------------
function dragSplit(handle, onMove) {
  handle.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const move = (ev) => onMove(ev);
    const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); document.body.style.userSelect = ""; };
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
  });
}

// ---- 키바인딩 -----------------------------------------------------------
function keybind(e) {
  const mod = e.ctrlKey || e.metaKey;
  if (mod && (e.key === "k" || e.key === "p")) { e.preventDefault(); openPalette(e.key === "p" ? "@" : ""); return; }
  if (e.key === "/" && e.target.tagName !== "INPUT" && e.target.tagName !== "TEXTAREA") { e.preventDefault(); openPalette(); return; }
  if (mod && e.shiftKey && (e.key === "A" || e.key === "a")) { e.preventDefault(); toggleAI(); return; }
  if (mod && e.key === "b") { e.preventDefault(); $("#sidebar").classList.toggle("hidden"); $("#split-side").classList.toggle("hidden"); return; }
  if (mod && e.key === "j") { e.preventDefault(); $("#bottom").classList.toggle("collapsed"); return; }
  if (mod && e.key === "`") { e.preventDefault(); setSideView("script"); return; }
  if (e.key === "F2" && curTab()) { e.preventDefault(); renameFunc(curTab().addr); return; }
  if (e.altKey && /^[1-5]$/.test(e.key)) {
    e.preventDefault();
    setSideView(["functions", "strings", "symbols", "imports", "bookmarks"][Number(e.key) - 1]);
  }
}

// ---- 부트스트랩 ---------------------------------------------------------
function boot() {
  paintIcons();
  try { const t = localStorage.getItem("rework-theme"); if (t) { document.body.dataset.theme = t; document.documentElement.dataset.theme = t; } } catch {}
  // 액티비티바
  $$("#activitybar .act").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.cmd === "toggle-ai") toggleAI();
    else setSideView(b.dataset.view);
  }));
  // 타이틀바 버튼
  $$("[data-cmd]").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.cmd === "palette") openPalette(">");
    else if (b.dataset.cmd === "toggle-theme") toggleTheme();
    else if (b.dataset.cmd === "toggle-ai") toggleAI();
  }));
  // 하단 패널 탭
  $$("#bottom .ptab").forEach((b) => b.addEventListener("click", () => showPanel(b.dataset.panel)));
  // AI
  $("#ai-send").addEventListener("click", () => sendAI());
  $("#ai-settings-btn").addEventListener("click", openAISettings);
  $("#ai-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendAI(); }
  });
  // 팔레트/모달 바깥 클릭 닫기
  $("#palette").addEventListener("mousedown", (e) => { if (e.target.id === "palette") closePalette(); });
  // 스플리터
  dragSplit($("#split-side"), (e) => {
    const w = Math.max(180, Math.min(600, e.clientX - 48));
    $("#sidebar").style.width = w + "px";
  });
  dragSplit($("#split-ai"), (e) => {
    const w = Math.max(260, Math.min(640, window.innerWidth - e.clientX));
    $("#aipanel").style.width = w + "px";
  });
  dragSplit($("#split-bottom"), (e) => {
    const rect = $("#main").getBoundingClientRect();
    const h = Math.max(60, Math.min(rect.height - 120, rect.bottom - e.clientY));
    $("#bottom").style.height = h + "px";
  });
  // 키
  document.addEventListener("keydown", keybind);
  // 초기 화면
  setSideView("open");
  aiPush("sys", "AI 도우미입니다. 설정(⚙)에서 Ollama 또는 API 키를 지정한 뒤 질문하세요.");
}
boot();
