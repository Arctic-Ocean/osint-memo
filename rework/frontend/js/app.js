// rework — 프론트엔드 메인 로직
import { api } from "/js/api.js";
import * as WS from "/js/workspace.js";
import * as DX from "/js/dbgx.js";

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
  dbg: { active: false, state: null, regs: [], prevRegs: {}, stack: [], locals: [], breaks: [], disasm: [] },
  sbx: null,             // 서버의 격리 상태·정책·보고서
  sbxForm: null,         // 사이드바 격리 설정 입력값(저장 전)
  sbxOff: false,         // 이번 세션만 비격리로 시작(저장되지 않음)
  sbxNoGuard: false,     // 이번 세션만 '디버거 보호를 확인하지 못해도 시작'(저장되지 않음)
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
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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
  explorer: '<path d="M4 3h9l3 3v4"/><path d="M4 3v18h7"/><path d="M13 14l2 2 4-4"/><rect x="12" y="11" width="8" height="10" rx="1" opacity=".55"/>',
  open: '<path d="M2 4a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v2H2V4Z"/><path d="M2 8h20l-2 10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1L2 8Z" opacity=".6"/>',
  functions: '<path d="M4 20c2 0 2.5-1.2 3-4l1.2-7C8.8 5 9.6 3.5 12 3.5c1 0 1.6.3 2 .7"/><path d="M6 10h7"/>',
  strings: '<path d="M4 7V5h12v2M10 5v14M8 19h4"/>',
  symbols: '<circle cx="7" cy="8" r="3"/><path d="M14 5h6v6M20 5l-6 6"/><rect x="4" y="15" width="6" height="5" rx="1"/>',
  imports: '<path d="M12 3v10m0 0-4-4m4 4 4-4M5 17v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2"/>',
  bookmarks: '<path d="M6 3h12v18l-6-4-6 4V3Z"/>',
  script: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M13 5l-2 14"/>',
  debug: '<rect x="8" y="7" width="8" height="11" rx="4"/><path d="M12 7V4m-4 6H3m18 0h-5M6 5l2 2m10-2-2 2M5 19l2-2m12 2-2-2M9 10h6"/>',
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
  const titles = { open: "열기", explorer: "탐색기", functions: "함수", strings: "문자열", symbols: "심볼",
                   imports: "임포트 / 익스포트", bookmarks: "북마크", script: "스크립트 콘솔",
                   debug: "디버그", settings: "설정" };
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
  WS.onThemeChange();
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
    await openLocalFile(f);
  });
  up.addEventListener("dragover", (e) => { e.preventDefault(); up.style.borderColor = "var(--accent)"; });
  up.addEventListener("dragleave", () => { up.style.borderColor = ""; });
  up.addEventListener("drop", () => { up.style.borderColor = ""; });      // 열기는 창 전체의 drop 처리기가 한다
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
    for (const d of r.drives || []) {            // Windows 드라이브
      list.append(el("div", { class: "list-item", onclick: () => listDir(d) }, el("span", { class: "li-name" }, "💽 " + d)));
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
// 사용자가 고른·끌어다 놓은 파일 열기. 데스크톱 앱에서는 실제 경로를 얻어 복사 없이 그 자리에서 열고(큰 파일도 즉시),
// 브라우저에서는 서버로 올린다.
async function openLocalFile(f) {
  const desk = window.reworkDesktop;
  if (desk) {
    const real = desk.pathForFile(f);
    if (real) {
      const bp = await desk.backendPath(real);
      if (bp) { await openPath(bp); return; }
    }
  }
  setMsg("업로드 중…");
  try { const info = await api.upload(f); await onOpened(info); }
  catch (err) { toast("열기 실패: " + err.message, "err"); setMsg(""); }
}
function hasFiles(e) { return !!(e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files")); }
function initFileDrop() {
  // 창 어디에 놓아도 연다(그러지 않으면 브라우저가 그 파일 주소로 이동하려 한다)
  document.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  document.addEventListener("drop", async (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (!f) return;
    const item = e.dataTransfer.items && e.dataTransfer.items[0];
    const entry = item && item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
    if (entry && entry.isDirectory && window.reworkDesktop) {          // 폴더를 놓으면 작업 폴더로 연다(데스크톱: 실제 경로를 알 수 있다)
      const real = window.reworkDesktop.pathForFile(f);
      const bp = real ? await window.reworkDesktop.backendPath(real) : "";
      if (bp) { await WS.openFolder(bp); return; }
    }
    if (entry && entry.isDirectory) { toast("폴더는 끌어다 놓을 수 없습니다. 탐색기의 ‘폴더 열기’를 쓰세요.", "err"); return; }
    await openLocalFile(f);
  });
  // 데스크톱 셸의 ‘파일 > 열기…’·‘폴더 열기…’·명령줄로 받은 경로
  if (window.reworkDesktop && window.reworkDesktop.onOpenPath) window.reworkDesktop.onOpenPath((p) => openPath(p));
  if (window.reworkDesktop && window.reworkDesktop.onOpenFolder) window.reworkDesktop.onOpenFolder((p) => WS.openFolder(p));
}
async function openPath(path) {
  setMsg("여는 중…");
  try { const info = await api.open(path); await onOpened(info); }
  catch (err) { toast("열기 실패: " + err.message, "err"); setMsg(""); }
}
async function onOpened(info) {
  S.info = info;
  S.tabs = S.tabs.filter((t) => t.kind === "file");     // 분석 탭은 새 바이너리에 맞게 비우되, 편집 중인 파일 탭(저장 안 한 변경 포함)은 둔다
  S.active = S.tabs.length ? 0 : -1;
  dbgReset();   // 서버는 바이너리를 열 때 디버그 세션을 종료하므로 프론트 상태도 맞춘다
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
  if (!S.info && v !== "open" && v !== "settings" && v !== "explorer") {
    $("#side-body").innerHTML = '<div class="empty-list">먼저 바이너리를 여세요.</div>';
    return;
  }
  ({ open: renderOpen, explorer: WS.renderExplorer, functions: renderFunctions, strings: renderStrings,
     symbols: renderSymbols, imports: renderImports, bookmarks: renderBookmarks,
     script: renderScript, debug: renderDebugSidebar, settings: renderSettings }[v] || (() => {}))();
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
  if (S.tabs[i].kind === "file") { WS.closeFileTab(S.tabs[i].path); return; }     // 저장 안 한 변경이 있으면 확인
  S.tabs.splice(i, 1);
  if (S.active >= S.tabs.length) S.active = S.tabs.length - 1;
  renderTabs(); renderEditor();
}
function renderTabs() {
  const bar = $("#tabs"); bar.innerHTML = "";
  S.tabs.forEach((t, i) => {
    const f = S.funcByAddr.get(t.addr);
    const dirty = WS.isFileTabDirty(t);
    const name = t.kind === "debug" ? "⬤ 디버거" : t.kind === "file" ? t.path.split("/").pop() : (f && f.name) || hx(t.addr);
    const tab = el("div", { class: "tab" + (i === S.active ? " active" : "") + (dirty ? " dirty" : ""), title: t.kind === "file" ? t.path : "" },
      el("span", { class: "tab-name" }, (dirty ? "● " : "") + name),
      el("button", { class: "tab-x", title: "닫기", onclick: (e) => { e.stopPropagation(); closeTab(t.addr); } }, "×"));
    tab.onclick = () => { S.active = i; renderTabs(); renderEditor(); };
    bar.append(tab);
  });
}
function curTab() { return S.active >= 0 ? S.tabs[S.active] : null; }

async function renderEditor() {
  const ed = $("#editor");
  const tab = curTab();
  WS.clearStatus();
  ed.style.overflow = "";
  if (!tab) {
    ed.innerHTML = '<div class="empty-state"><div class="es-big">rework</div>' +
      '<div class="es-sub">리버스 엔지니어링 워크벤치</div></div>';
    return;
  }
  ed.innerHTML = "";
  ed.style.overflow = "";
  if (tab.kind === "file") { await WS.renderFileTab(ed, tab); return; }
  if (tab.kind === "debug") {
    const tb = el("div", { class: "view-toolbar" });
    const st = S.dbg.state || {};
    const statusText = { stopped: "중단됨", running: "실행 중", exited: "종료됨" }[st.status] || st.status || "";
    const mk = (label, title, action, dis) => el("button", { class: "vt-btn", title,
      disabled: dis ? "" : null, onclick: () => dbgDo(action) }, label);
    const run = st.status === "running", dead = st.status === "exited";
    tb.append(
      dead ? mk("↻ 다시 실행", "F5", "run", false) : mk("▶ 계속", "F5", "continue", run),
      mk("⤼ 한 줄", "F10", "next", run || dead),
      mk("↓ 스텝", "F11", "step", run || dead),
      mk("» 명령", "stepi", "stepi", run || dead),
      mk("⤴ 함수끝", "Shift+F11", "finish", run || dead),
      mk("⏸ 멈춤", "실행 중 멈춤", "interrupt", !run),
      ...DX.reverseButtons(st, mk),
      el("span", { class: "vt-title" }, "디버거"),
      el("span", { class: "vt-spacer" }),
      el("span", { class: "dbg-badge " + (st.status || "") }, statusText + (st.remote ? ` · 원격 ${st.remote}` : "") +
        (st.frame ? ` · ${st.frame.func || ""} @ ${st.frame.addr || ""}` : "")),
      el("button", { class: "vt-btn", onclick: () => dbgStop() }, "⏹ 정지"));
    ed.append(tb);
    const why = DX.stopSummary(st);
    if (why) ed.append(el("div", { class: "dbg-stopbar " + why.kind }, why.text));
    const view = el("div", { style: "flex:1 1 auto;min-height:0;overflow:hidden;display:flex" });
    ed.append(view);
    renderDebuggerWorkspace(view);
    ed.style.display = "flex"; ed.style.flexDirection = "column";
    $("#sb-addr").textContent = st.frame ? st.frame.addr : "";
    return;
  }
  ed.style.display = ""; ed.style.flexDirection = "";
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
    tab.mode === "decompile" ? el("button", { class: "vt-btn", title: "의사코드를 작업 폴더의 .c 파일로 저장해 편집", onclick: () => exportPseudo(tab.addr) }, "소스로 저장") : null,
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

async function exportPseudo(addr) {
  try {
    const d = await api.decompile(addr);
    const code = d.lines.map((l) => "  ".repeat(l.indent) + l.text).join("\n");
    const header = `/* rework 의사코드 — ${d.name} @ ${hx(addr)} (${S.info ? S.info.name : ""})\n * 휴리스틱 결과이므로 정확한 의미는 디스어셈블리로 확인하세요. */\n`;
    await WS.savePseudo(d.name, header, code + "\n");
  } catch (err) { toast("의사코드를 저장하지 못했습니다: " + err.message, "err"); }
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
const KW = new Set(["if", "else", "goto", "return", "while", "do", "for", "break", "continue", "switch", "case", "default", "static", "const", "struct", "sizeof"]);
const TYPE_RE = /^(?:u?int(?:8|16|32|64)_t|void|char|short|int|long|float|double|unsigned|signed|bool)$/;
function tokenizeDec(text, varSet) {
  const re = /("(?:[^"\\]|\\.)*")|([A-Za-z_]\w*)|(0x[0-9a-fA-F]+|\d+)|(\s+)|([^\sA-Za-z0-9_"]+)/g;
  let out = "", m;
  while ((m = re.exec(text))) {
    if (m[1]) out += `<span class="str">${esc(m[1])}</span>`;
    else if (m[2]) {
      const id = m[2];
      const after = text.slice(re.lastIndex).match(/^\s*\(/);
      if (KW.has(id)) out += `<span class="kw">${id}</span>`;
      else if (TYPE_RE.test(id)) out += `<span class="ty">${id}</span>`;
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
  { id: "explorer", icon: "🗂", title: "탐색기(작업 폴더) 열기", run: () => setSideView("explorer") },
  { id: "folder", icon: "📁", title: "작업 폴더 열기…", run: () => WS.pickFolder() },
  { id: "save", icon: "💾", title: "편집 중인 파일 저장 (Ctrl+S)", run: () => WS.saveCurrent() },
  { id: "theme", icon: "🌓", title: "테마 전환", run: toggleTheme },
  { id: "ai", icon: "🤖", title: "AI 채팅 열기/닫기", run: toggleAI },
  { id: "ai-settings", icon: "⚙", title: "AI 설정", run: openAISettings },
  { id: "script", icon: "⌨", title: "스크립트 콘솔", run: () => setSideView("script") },
  { id: "debug", icon: "⬤", title: "디버깅 시작 (F5)", run: () => { setSideView("debug"); if (!S.dbg.active) dbgStart(); } },
  { id: "debug-stop", icon: "⏹", title: "디버깅 정지", run: () => dbgStop() },
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
  const fileCtx = $("#ai-attach-fn").checked ? WS.currentFileContext() : null;
  if (fileCtx) {
    extraContext = `다음은 파일 ${fileCtx.name}의 내용입니다${fileCtx.truncated ? "(앞부분 12000자)" : ""}:\n\n\`\`\`\n${fileCtx.text}\n\`\`\`\n`;
  } else if ($("#ai-attach-fn").checked && curTab() && curTab().kind !== "file") {
    try {
      const d = await api.decompile(curTab().addr);
      const code = d.lines.map((l) => "  ".repeat(l.indent) + l.text).join("\n");
      extraContext = `다음은 함수 ${d.name}의 휴리스틱 의사코드입니다:\n\n\`\`\`c\n${code}\n\`\`\`\n`;
    } catch {}
  }
  const display = text || (fileCtx ? "(현재 파일 분석)" : "(현재 함수 분석)");
  aiPush("user", display + (extraContext ? (fileCtx ? "\n\n[편집 중인 파일 첨부됨]" : "\n\n[함수 의사코드 첨부됨]") : ""));
  const msgs = [...S.aiMessages.filter((m) => m.role !== "sys").map((m) => ({ role: m.role, content: m.content }))];
  if (extraContext) msgs[msgs.length - 1] = { role: "user", content: extraContext + "\n질문: " + (text || (fileCtx ? "이 파일의 내용을 설명하고 문제점이 있으면 알려 주세요." : "이 함수가 무엇을 하는지 설명해 주세요.")) };
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

// ---- 디버거 (동적 분석) ------------------------------------------------
const DBG_TAB = "__debugger__";
let dbgPoll = null;

// ---- 격리 실행(샌드박스) 설정 ------------------------------------------
const SBX_DEFAULTS = { network: "none", memory_mb: 2048, cpu_seconds: 600, cpu_total_seconds: 1800,
                       wall_seconds: 0, max_processes: 256, extra_ro: [] };
async function loadSandbox(recheck = false) {
  try {
    S.sbx = recheck ? await api.sandboxRecheck() : await api.sandboxStatus();
    if (!S.sbxForm) S.sbxForm = sbxFormFrom(S.sbx.policy);
  } catch (e) { S.sbx = { error: e.message }; }
  if (S.sideView === "debug" && !S.dbg.active) renderDebugSidebar();
}
// 격리 상태를 아직 모르거나(불러오는 중·실패) 확인하지 못한 동안에는 저장된 설정 대신 기본값을 보내지 않도록 시작을 막는다
function sbxKnown() { return !!(S.sbx && !S.sbx.error); }
function sbxFormFrom(p) {
  return { network: p.network, memory_mb: p.memory_mb, cpu_seconds: p.cpu_seconds, cpu_total_seconds: p.cpu_total_seconds,
           wall_seconds: p.wall_seconds, max_processes: p.max_processes, extra_ro: (p.extra_ro || []).join("\n") };
}
function sbxPayload() {
  const f = S.sbxForm || {};
  const num = (v, name) => {
    const n = Number(v);
    if (!Number.isInteger(n)) throw new Error(`${name} 는 정수여야 합니다.`);
    return n;
  };
  return {
    mode: S.sbxOff ? "off" : "on", network: f.network || "none",
    ...(S.sbxNoGuard ? { require_gdb_guard: false } : {}),
    memory_mb: num(f.memory_mb, "메모리"), cpu_seconds: num(f.cpu_seconds, "CPU 시간"),
    cpu_total_seconds: num(f.cpu_total_seconds, "총 CPU 시간"),
    wall_seconds: num(f.wall_seconds, "실행 시간"), max_processes: num(f.max_processes, "프로세스 수"),
    extra_ro: String(f.extra_ro || "").split("\n").map((x) => x.trim()).filter(Boolean),
  };
}
function sbxLayerList(layers) {
  const box = el("div", { class: "sbx-layers" });
  for (const l of layers || []) {
    box.append(el("div", { class: "sbx-layer " + (l.active ? "on" : "off"), title: l.detail || "" },
      el("span", { class: "sbx-mark" }, l.active ? "✓" : "✗"),
      el("span", {}, l.label),
      l.detail ? el("div", { class: "sbx-detail" }, l.detail) : null));
  }
  return box;
}
function sandboxBadge(info) {
  if (!info) return null;
  return el("span", { class: "sbx-badge " + (info.enabled ? "on" : "off"), title: info.summary || "" },
    info.enabled ? "🔒 격리" : "⚠ 비격리");
}
function sandboxCard() {
  const box = el("div", { class: "sbx" });
  const sb = S.sbx;
  if (!sb) { box.append(el("div", { class: "sbx-note" }, "격리 상태 확인 중…")); return box; }
  if (sb.error) {
    box.append(el("div", { class: "dbg-warn", style: "margin:0" }, "격리 상태를 확인하지 못했습니다: " + sb.error),
      el("button", { class: "mini-btn wide", style: "margin-top:6px", onclick: () => { S.sbx = null; loadSandbox(true); renderDebugSidebar(); } }, "↻ 다시 확인"));
    return box;
  }
  const ok = sb.status.available;
  const f = S.sbxForm;
  box.append(el("div", { class: "sbx-head" }, el("b", {}, "격리 실행"),
    el("span", { class: "sbx-chip " + (ok ? "ok" : "bad") }, ok ? "사용 가능" : "사용 불가"),
    el("button", { class: "mini-btn", title: "다시 확인", onclick: () => loadSandbox(true) }, "↻")));
  if (!ok) {
    box.append(el("div", { class: "sbx-note bad" }, sb.status.reason),
      sb.status.hint ? el("div", { class: "sbx-note" }, sb.status.hint) : null);
  } else if (sb.status.reason) {
    box.append(el("div", { class: "sbx-note" }, sb.status.reason));
  }
  const guard = sb.guard || {};
  if (!S.sbxOff && guard.ready === false) {
    box.append(el("div", { class: "dbg-warn", style: "margin:0 0 6px" },
      "⚠ 디버거 보호를 쓸 수 없습니다: " + (guard.reason || "알 수 없음") +
      ". 그대로는 시작되지 않습니다(대상이 디버거의 보고를 위조할 수 있어서)."),
      el("label", { class: "sbx-opt" + (S.sbxNoGuard ? " sel" : "") },
        el("input", { type: "checkbox", ...(S.sbxNoGuard ? { checked: "" } : {}),
          onchange: (e) => { S.sbxNoGuard = e.target.checked; renderDebugSidebar(); } }),
        el("span", {}, el("b", {}, "디버거 보호 없이 시작(이번 한 번)"),
          el("div", { class: "sbx-sub" }, "격리는 유지되지만 대상이 디버거 보고를 위조할 수 있습니다. 저장되지 않습니다."))));
  }
  const radio = (off, title, sub) => el("label", { class: "sbx-opt" + (S.sbxOff === off ? " sel" : "") },
    el("input", { type: "radio", name: "sbx-mode", ...(S.sbxOff === off ? { checked: "" } : {}),
      onchange: () => { S.sbxOff = off; renderDebugSidebar(); } }),
    el("span", {}, el("b", {}, title), el("div", { class: "sbx-sub" }, sub)));
  box.append(radio(false, "격리 실행 (권장)", "네트워크 차단 · 읽기 전용 파일시스템 · 시스템 콜 제한 · 자원 한도"),
             radio(true, "비격리", "신뢰하는 파일만. 이 컴퓨터에서 그대로 실행됩니다."));
  if (S.sbxOff) box.append(el("div", { class: "dbg-warn", style: "margin:6px 0 0" },
    "⚠ 악성코드라면 파일 삭제·유출·네트워크 공격이 그대로 일어납니다. 비격리는 저장되지 않으며 시작할 때마다 선택해야 합니다."));
  if (!S.sbxOff) {
    const row = (label, input) => el("div", { class: "sbx-row" }, el("label", {}, label), input);
    const num = (key, min) => el("input", { type: "number", value: f[key], min: String(min),
      oninput: (e) => { f[key] = e.target.value; } });
    const net = el("select", { onchange: (e) => { f.network = e.target.value; renderDebugSidebar(); } },
      el("option", { value: "none", ...(f.network === "none" ? { selected: "" } : {}) }, "차단 (기본)"),
      el("option", { value: "host", ...(f.network === "host" ? { selected: "" } : {}) }, "호스트 네트워크 공유 (위험)"));
    box.append(row("네트워크", net));
    if (f.network === "host") box.append(el("div", { class: "dbg-warn", style: "margin:0 0 6px" },
      "⚠ 대상이 이 컴퓨터의 네트워크(로컬 서비스·이 서버 포함)로 통신할 수 있습니다." +
      (sb.server && sb.server.token_auth ? "" : " 서버를 `run.py --require-token` 으로 실행해야 시작할 수 있습니다.")));
    const adv = el("details", { class: "sbx-adv" }, el("summary", {}, "자원 한도 · 추가 경로"));
    adv.append(row("메모리(MB)", num("memory_mb", 64)), row("CPU 시간(초/프로세스)", num("cpu_seconds", 1)),
      row("총 CPU(초)", num("cpu_total_seconds", 1)),
      row("실행 시간(초, 0=무제한)", num("wall_seconds", 0)), row("프로세스 수", num("max_processes", 1)),
      el("label", { class: "sbx-lbl" }, "추가로 읽기 전용 노출할 경로 (한 줄에 하나)"),
      el("textarea", { rows: "2", placeholder: "/home/me/inputs", oninput: (e) => { f.extra_ro = e.target.value; } }, f.extra_ro),
      el("div", { class: "sbx-sub" }, "`< 파일` 로 입력을 줄 파일이 있는 폴더 등. 격리 사용자가 읽을 수 있어야 합니다."),
      el("button", { class: "mini-btn wide", onclick: sbxSave }, "설정 저장"));
    box.append(adv);
  }
  const rep = el("details", { class: "sbx-adv" },
    el("summary", {}, `적용되는 보호 (${(sb.report.layers || []).filter((l) => l.active).length}/${(sb.report.layers || []).length})`));
  rep.append(sbxLayerList(sb.report.layers));
  box.append(rep);
  return box;
}
async function sbxSave() {
  try {
    const p = sbxPayload(); delete p.mode;
    S.sbx = await api.sandboxSave(p);
    S.sbxForm = sbxFormFrom(S.sbx.policy);
    toast("격리 설정 저장됨", "ok");
    renderDebugSidebar();
  } catch (e) { toast("저장 실패: " + e.message, "err"); }
}

function renderDebugSidebar() {
  const body = $("#side-body"); body.innerHTML = "";
  if (!S.info) { body.append(el("div", { class: "empty-list" }, "먼저 바이너리를 여세요.")); return; }
  const d = S.dbg;
  if (!d.active) {
    if (!S.sbx) loadSandbox();
    if (S.sbx && !S.sbx.error && S.sbx.debugger && !S.sbx.debugger.supported) {
      // 이 운영체제에서는 디버거 자체가 없다(Windows): 시작 버튼 대신 이유와 대안을 보여 준다
      body.append(el("div", { class: "dbg-warn" }, el("b", {}, "이 환경에서는 디버거를 쓸 수 없습니다. "), S.sbx.debugger.reason,
        el("div", { class: "sbx-sub" }, "정적 분석(디스어셈블·디컴파일·문자열·심볼)은 그대로 쓸 수 있습니다.")));
      return;
    }
    if (!S.sbxForm) S.sbxForm = sbxFormFrom(SBX_DEFAULTS);
    const unavailable = S.sbx && !S.sbx.error && !S.sbx.status.available;
    const unknown = !sbxKnown() && !S.sbxOff;                 // 상태를 불러오는 중이거나 실패했다
    if (d.endedInfo) {                                         // 이전 세션이 우리가 멈춘 것이 아니라 스스로 끝났다 — 이유를 남겨 둔다
      body.append(el("div", { class: "dbg-warn" }, el("b", {}, "이전 디버그 세션이 종료되었습니다. "), d.endedInfo.reason,
        el("div", { class: "sbx-sub" }, "마지막 출력은 하단 ‘디버그 콘솔’에 남아 있습니다. 다시 시작하면 지워집니다.")));
    }
    if (S.sbxOff || unavailable) {
      body.append(el("div", { class: "dbg-warn" },
        "⚠ 디버깅은 대상 바이너리를 이 컴퓨터에서 실제로 실행합니다. " +
        (unavailable && !S.sbxOff ? "지금은 격리를 쓸 수 없어 시작할 수 없습니다. 아래 안내를 따르거나 신뢰하는 파일에 한해 ‘비격리’를 선택하세요."
                                  : "신뢰할 수 없는 파일은 격리 실행으로 여세요.")));
    } else if (!unknown) {
      body.append(el("div", { class: "dbg-safe" },
        "🔒 대상 프로그램은 격리된 환경(네트워크 차단·읽기 전용 파일시스템)에서 실행됩니다."));
    }
    body.append(el("div", { style: "padding:0 12px" }, sandboxCard()));
    const wrap = el("div", { class: "dbg-start" });
    wrap.append(el("label", {}, "실행 인자 (선택)"));
    const args = el("input", { id: "dbg-args", type: "text", placeholder: "예: input.bin --verbose" });
    wrap.append(args);
    wrap.append(el("button", { class: "primary", style: "margin-top:8px;width:100%",
      ...((unavailable || unknown) && !S.sbxOff ? { disabled: "" } : {}),
      onclick: () => dbgStart() }, S.sbxOff ? "⚠ 격리 없이 시작" : "▶ 디버깅 시작"));
    const rcb = el("input", { type: "checkbox", ...(S.dbgRemoteOn ? { checked: "" } : {}),
      onchange: (e) => { S.dbgRemoteOn = e.target.checked; renderDebugSidebar(); } });
    wrap.append(el("label", { class: "dbg-remote-opt", title: "프로그램을 이 컴퓨터가 아니라 gdbserver 가 도는 원격(임베디드 보드·VM 등)에서 실행합니다" },
      rcb, " 원격 gdbserver 에 연결"));
    if (S.dbgRemoteOn) {
      wrap.append(el("input", { id: "dbg-remote", type: "text", spellcheck: "false", placeholder: "host:port — 예: 192.168.0.5:1234",
        value: S.dbgRemote || "", oninput: (e) => { S.dbgRemote = e.target.value; } }));
      wrap.append(el("div", { class: "dbg-hint" },
        "원격에서 `gdbserver :1234 ./program` 을 먼저 실행해 두세요. 여기서 연 바이너리는 같은 파일이어야 심볼이 맞습니다. 이 모드는 서버가 토큰 인증(`--require-token`)일 때만 쓸 수 있고, 로컬에서는 프로그램이 실행되지 않습니다."));
    }
    wrap.append(el("div", { class: "dbg-hint" },
      "시작하면 main에 중단점이 설정됩니다. 입력이 필요한 프로그램은 하단 ‘디버그 콘솔’의 입력창으로 보내거나, 실행 인자에 `< 파일`을 쓰세요(격리에서는 그 파일의 폴더를 ‘추가 경로’에 넣어야 합니다)."));
    body.append(wrap);
    return;
  }
  // 상태
  const st = d.state || {};
  const statusText = { stopped: "중단됨", running: "실행 중", exited: "종료됨", loaded: "로드됨" }[st.status] || st.status;
  const sline = el("div", { class: "dbg-status " + (st.status || "") },
    el("b", {}, statusText),
    st.stop_reason ? el("span", {}, " · " + st.stop_reason) : null,
    st.frame ? el("span", { class: "dbg-frame" }, ` · ${st.frame.func || ""} @ ${st.frame.addr || ""}`) : null,
    st.remote ? el("span", { class: "dbg-badge", title: "원격 gdbserver — 프로그램은 원격에서 실행 중입니다" }, "원격 " + st.remote) : null,
    st.status === "exited" && st.exit_code != null ? el("span", {}, ` · 종료코드 ${st.exit_code}`) : null,
    st.sandbox ? el("span", {}, " ") : null, sandboxBadge(st.sandbox));
  body.append(sline);
  if (st.status === "exited" && st.sandbox_leftover > 0) body.append(el("div", { class: "dbg-warn" },
    `프로그램은 끝났지만 샌드박스 안에 프로세스가 ${st.sandbox_leftover}개 남아 있습니다(데몬·자식). ⏹ 정지로 모두 종료됩니다.`));
  if (st.sandbox && !st.sandbox.enabled) body.append(el("div", { class: "dbg-warn" },
    "⚠ 이 세션은 격리 없이 실행 중입니다. 대상이 이 컴퓨터의 파일·네트워크에 그대로 접근할 수 있습니다."));
  if (st.sandbox && st.sandbox.enabled) {
    const det = el("details", { class: "sbx-adv", style: "margin:6px 12px" }, el("summary", {}, st.sandbox.summary));
    det.append(sbxLayerList(st.sandbox.layers));
    body.append(det);
  }
  // 실행 제어
  const bar = el("div", { class: "dbg-controls" });
  const btn = (label, title, action, disabled) => el("button",
    { class: "dbg-ctl", title, disabled: disabled ? "" : null, onclick: () => dbgDo(action) }, label);
  const running = st.status === "running";
  const dead = st.status === "exited";
  bar.append(
    dead ? btn("↻", "다시 실행 (F5)", "run", false) : btn("▶", "계속 (F5)", "continue", running),
    btn("⤼", "한 줄 넘어 (F10)", "next", running || dead),
    btn("↓", "한 줄 안으로 (F11)", "step", running || dead),
    btn("»", "명령 단위 (stepi)", "stepi", running || dead),
    btn("⤴", "함수 끝까지 (Shift+F11)", "finish", running || dead),
    btn("⏸", "멈춤", "interrupt", !running),
    el("button", { class: "dbg-ctl stop", title: "정지 (Shift+F5)", onclick: () => dbgStop() }, "⏹"));
  body.append(bar);
  // 콜 스택
  body.append(el("div", { class: "side-head", style: "border-top:none" }, "콜 스택"));
  if (!d.stack.length) body.append(el("div", { class: "empty-list", style: "padding:8px 12px" }, "—"));
  const selLevel = (d.state && d.state.selected_frame) || 0;
  for (const f of d.stack) {
    body.append(el("div", { class: "list-item" + (Number(f.level) === selLevel ? " sel-frame" : ""), title: "클릭: 이 프레임의 지역변수·레지스터 보기",
      onclick: async () => { try { await api.dbgFrame(Number(f.level)); await dbgRefresh(); openDebuggerTab(); } catch (err) { toast("프레임 선택 실패: " + err.message, "err"); } } },
      el("span", { class: "li-addr" }, f.level), el("span", { class: "li-name" }, f.func || f.addr),
      f.line ? el("span", { class: "li-sub" }, `:${f.line}`) : null));
  }
  if (d.stackTruncated) body.append(el("div", { class: "empty-list", style: "padding:4px 12px" },
    `… 위쪽 ${d.stack.length}개 프레임만 표시합니다(스택이 매우 깊습니다 — 무한 재귀일 수 있습니다).`));
  // 중단점 · 로그포인트 · 워치포인트
  DX.renderBreakpoints(body);
}

let dbgStarting = false;
async function dbgStart() {
  if (dbgStarting || S.dbg.active) return;   // F5 연타·버튼 더블클릭으로 시작 흐름이 겹치지 않게
  dbgStarting = true;
  const args = ($("#dbg-args") && $("#dbg-args").value.trim()) || "";
  let sandbox;
  try { sandbox = sbxPayload(); } catch (e) { toast(e.message, "err"); dbgStarting = false; return; }
  if (S.sbxOff && !confirm("격리 없이 시작합니다.\n\n이 파일이 악성코드라면 이 컴퓨터의 파일 삭제·정보 유출·네트워크 공격이 실제로 일어날 수 있습니다.\n신뢰하는 파일일 때만 계속하세요. 계속할까요?")) {
    dbgStarting = false; return;
  }
  if (S.dbg.endedInfo) {                     // 지난 세션의 종료 사유·콘솔을 치운다(비격리 선택은 이번 시작에 필요하니 유지)
    const off = S.sbxOff, noGuard = S.sbxNoGuard;
    dbgReset();
    S.sbxOff = off; S.sbxNoGuard = noGuard;
  }
  setMsg("디버거 시작 중…");
  try {
    const remote = S.dbgRemoteOn ? (S.dbgRemote || "").trim() : "";
    if (S.dbgRemoteOn && !remote) { toast("원격 주소(host:port)를 입력하세요.", "err"); dbgStarting = false; setMsg(""); return; }
    const started = await api.dbgStart(["main"], sandbox, remote || undefined);
    S.dbg.active = true;
    S.dbg.argsCache = args;
    const missed = started.failed_breakpoints || [];
    // main 중단점까지 실행
    const r = await api.dbgExec("run", args);
    S.dbg.state = r.state;
    await dbgRefresh();
    if (!S.dbg.active) return;               // 그 사이 세션이 끝났다면 중단
    openDebuggerTab();
    showPanel("debugcon");
    growBottomForConsole();
    if (r.state.status === "running") {
      startDbgPoll();                         // 중단점이 없거나 입력 대기 등: 멈출 때까지 폴링
      toast("프로그램 실행 중…", "ok");
    } else {
      toast("디버깅 시작 — main 중단", "ok");
    }
    if (missed.length) toast("중단점 설정 실패: " + missed.map((m) => m.location).join(", "), "err");
  } catch (err) {
    toast("디버거 시작 실패: " + err.message, "err"); setMsg("");
    S.dbg.active = false;
    try { await api.dbgStop(); } catch {}   // 서버에 반쯤 만들어진 세션이 남지 않게
  } finally { dbgStarting = false; }
}
function dbgReset() {
  stopDbgPoll();
  S.sbxOff = false;                                  // 비격리는 한 세션에만 유효하다
  S.sbxNoGuard = false;
  S.dbg = { active: false, state: null, regs: [], prevRegs: {}, stack: [], locals: [], breaks: [], disasm: [],
            stackTruncated: false, endedInfo: null };
  const dc = $("#panel-debugcon");                  // 다음 세션에서 콘솔 패널을 처음부터 다시 그리게 한다
  if (dc) { dc.dataset.rendered = ""; dc.innerHTML = ""; }
  stdinHistPos = -1;
  closeTab(DBG_TAB);
  if (S.sideView === "debug") renderDebugSidebar();
}
async function dbgStop() {
  try { await api.dbgStop(); } catch {}
  dbgReset();
  setMsg("디버깅 종료");
}
// 서버 쪽 세션이 사라졌거나 gdb가 죽은 경우
async function dbgEnded(state) {
  const was = S.dbg.active;
  if (!was) return;                                 // 이미 처리됨(겹친 폴링·갱신이 같은 종료를 또 알려도 남겨 둔 종료 사유를 지우지 않는다)
  const reason = state && state.end_reason;
  if (was && reason) {
    // 서버가 한도·디버거 사망으로 세션을 끝냈다: 이유와 마지막 출력을 지우지 않고 남긴다(토스트만으로는 놓치기 쉽다)
    stopDbgPoll();
    S.dbg.active = false;                           // 이후 폴링·갱신이 겹쳐 같은 처리를 반복하지 않게 먼저 내린다
    let log = "";
    try { log = (await api.dbgOutput(false)).merged || ""; } catch {}
    const sbxOff = S.sbxOff, noGuard = S.sbxNoGuard;
    dbgReset();
    S.sbxOff = sbxOff; S.sbxNoGuard = noGuard;
    S.dbg.endedInfo = { reason, log };
    showPanel("debugcon"); growBottomForConsole();
    renderDebugConsolePanel();
    if (S.sideView === "debug") renderDebugSidebar();
    setMsg("");
    toast("디버그 세션 종료: " + reason, "err");
    return;
  }
  dbgReset();
  if (was) toast("디버그 세션이 종료되었습니다.", "err");
}
let dbgBusy = false;
function rerenderDebugViews() {
  if (S.sideView === "debug") renderDebugSidebar();
  if (curTab() && curTab().addr === DBG_TAB) renderEditor();
}
async function dbgDo(action) {
  // 이미 실행 중이거나 요청이 진행 중이면 같은 명령을 다시 보내지 않는다(서버가 400 을 낸다).
  if (action !== "interrupt" && ((S.dbg.state && S.dbg.state.status === "running") || dbgBusy)) return;
  dbgBusy = true;
  setMsg(`디버거: ${action}…`);
  // 서버는 프로그램이 멈출 때까지(최대 EXEC_WAIT) 응답을 미룬다. 입력을 기다리는 프로그램이면 그동안
  // 화면이 '중단됨'으로 남고 프롬프트도 안 보이므로, 오래 걸리면 곧바로 '실행 중'으로 보이고 폴링한다.
  let settled = false;                 // 응답이 도착했는가 — 도착한 뒤(상태를 새로 그리는 await 중)에 타이머가 '실행 중'으로
                                       // 되돌려 놓고 폴링을 켜면, 뒤이은 stopDbgPoll 이 폴링을 꺼 화면이 '실행 중'에 갇힌다
  const early = setTimeout(() => {
    if (settled || !dbgBusy || !S.dbg.active) return;
    S.dbg.state = { ...(S.dbg.state || {}), status: "running", stop_reason: null };
    rerenderDebugViews();
    startDbgPoll();
  }, 300);
  try {
    const r = await api.dbgExec(action, S.dbg.argsCache || "");
    settled = true; clearTimeout(early);
    if (r.state.alive === false) { dbgEnded(r.state); return; }     // gdb 가 죽은 것이지 프로그램이 끝난 것이 아니다
    S.dbg.state = r.state;
    await dbgRefresh();
    if (r.state.status === "running") startDbgPoll();
    else { stopDbgPoll(); if (r.state.status === "exited") toast("프로그램 종료 (코드 " + (r.state.exit_code ?? "?") + ")", "ok"); }
  } catch (err) {
    settled = true; clearTimeout(early);
    toast("디버그 실패: " + err.message, "err");
    await dbgRefresh();   // 세션이 이미 사라졌다면 여기서 감지해 UI를 비활성으로 돌린다
  } finally {
    clearTimeout(early);
    dbgBusy = false;
  }
  setMsg("");
}
let dbgPollBusy = false;
function startDbgPoll() {
  stopDbgPoll();
  dbgPoll = setInterval(async () => {
    if (dbgPollBusy) return;                 // 응답이 늦을 때 요청이 겹쳐 쌓이지 않게
    dbgPollBusy = true;
    try {
      const r = await api.dbgState();
      if (r.state.status === "none" || r.state.alive === false) { dbgEnded(r.state); return; }
      if (r.state.status !== "running") { stopDbgPoll(); await dbgRefresh(); }
      else { await dbgRefreshOutput(); }
    } catch { stopDbgPoll(); }
    finally { dbgPollBusy = false; }
  }, 700);
}
function stopDbgPoll() { if (dbgPoll) { clearInterval(dbgPoll); dbgPoll = null; } }

async function dbgRefresh() {
  try {
    const s = await api.dbgState();
    if (s.state.status === "none" || s.state.alive === false) { dbgEnded(s.state); return; }
    S.dbg.state = s.state;
    if (s.state.status !== "exited") S.dbg.leftoverRecheck = false;
    else if (s.state.sandbox_leftover > 0 && !S.dbg.leftoverRecheck) {
      // 막 끝난 대상이 남긴 데몬은 아직 자식을 만드는 중일 수 있어 한 번 더 세어 표시를 바로잡는다
      S.dbg.leftoverRecheck = true;
      setTimeout(() => { if (S.dbg.active) dbgRefresh(); }, 1200);
    }
    if (s.state.status === "stopped") {
      const [regs, stack, locals, breaks, dis] = await Promise.all([
        api.dbgRegs().catch(() => ({ registers: [] })),
        api.dbgStack().catch(() => ({ stack: [] })),
        api.dbgLocals().catch(() => ({ variables: [] })),
        api.dbgBreaks().catch(() => []),
        api.dbgDisasm().catch(() => ({ instructions: [] })),
      ]);
      S.dbg.prevRegs = {}; for (const r of S.dbg.regs) S.dbg.prevRegs[r.name] = r.value;
      S.dbg.regs = regs.registers || [];
      S.dbg.stack = stack.stack || [];
      S.dbg.stackTruncated = !!stack.truncated;
      S.dbg.locals = locals.variables || [];
      S.dbg.breaks = Array.isArray(breaks) ? breaks : [];
      S.dbg.disasm = dis.instructions || [];
    } else if (s.state.status === "exited") {
      try { S.dbg.breaks = await api.dbgBreaks(); } catch {}
    }
  } catch (err) { /* 세션 종료됨 */ }
  try { await DX.evalWatches(); } catch {}
  if (S.sideView === "debug") renderDebugSidebar();
  if (curTab() && curTab().addr === DBG_TAB) renderEditor();
  await dbgRefreshOutput();
  syncStaticRip();
}
async function dbgRefreshOutput() {
  try {
    const o = await api.dbgOutput(false);
    const panel = $("#panel-debugcon");
    if (!panel || !panel.dataset.rendered) return;
    const pre = $("#dbgcon-out");
    if (!pre) return;
    const next = o.merged ?? ((o.target || "") + (o.console ? "\n" + o.console : ""));
    if (pre.textContent === next) return;               // 바뀐 게 없으면 DOM 을 건드리지 않는다(스크롤·선택 보존)
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed && sel.anchorNode && pre.contains(sel.anchorNode)) return;   // 선택 중이면 미룬다
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 8;
    pre.textContent = next;
    if (atBottom) pre.scrollTop = pre.scrollHeight;     // 맨 아래를 보고 있을 때만 새 출력을 따라간다
  } catch {}
}

async function dbgToggleBreakAt(addr) {
  // 라이브 디스어셈블리에서 런타임 주소에 중단점 토글
  const existing = S.dbg.breaks.find((b) => b.addr && parseInt(b.addr, 16) === parseInt(addr, 16));
  try {
    if (existing) await api.dbgBreak({ op: "delete", number: existing.number });
    else await api.dbgBreak({ op: "add", location: "*" + addr });
    await dbgRefresh();
  } catch (err) { toast("중단점 실패: " + err.message, "err"); }
}

function openDebuggerTab() {
  let i = tabIndex(DBG_TAB);
  if (i < 0) { S.tabs.push({ addr: DBG_TAB, kind: "debug" }); i = S.tabs.length - 1; }
  S.active = i; renderTabs(); renderEditor();
}

function renderDebuggerWorkspace(ed) {
  ed.innerHTML = "";
  const d = S.dbg;
  const st = d.state || {};
  const selLevel = st.selected_frame || 0;
  const selFrame = d.stack.find((f) => Number(f.level) === selLevel);
  const wrap = el("div", { class: "dbgws" });
  // 좌: 라이브 디스어셈블리
  const left = el("div", { class: "dbgws-disasm" });
  left.append(el("div", { class: "dbgws-head" }, selLevel ? `라이브 디스어셈블리 (프레임 #${selLevel}의 pc)` : "라이브 디스어셈블리 (현재 $pc)",
    el("span", { class: "dbgws-sub" }, "클릭: 중단점 · Alt+클릭: 여기까지 실행")));
  const curAddr = selFrame && selFrame.addr ? parseInt(selFrame.addr, 16) : (st.frame && st.frame.addr ? parseInt(st.frame.addr, 16) : null);
  const bpAddrs = new Set(d.breaks.filter((b) => b.addr).map((b) => parseInt(b.addr, 16)));
  if (!d.disasm.length) left.append(el("div", { class: "empty-list" }, st.status === "exited" ? "프로그램이 종료되었습니다." : "중단 상태가 아닙니다."));
  for (const ins of d.disasm) {
    const a = parseInt(ins.addr, 16);
    const isCur = curAddr != null && a === curAddr;
    const hasBp = bpAddrs.has(a);
    const row = el("div", { class: "dbg-ins" + (isCur ? " cur" : ""), title: "클릭: 중단점 토글 · Alt+클릭: 여기까지 실행",
      onclick: (e) => (e.altKey ? DX.runTo(ins.addr) : dbgToggleBreakAt(ins.addr)) });
    row.innerHTML =
      `<span class="dbg-bp ${hasBp ? "on" : ""}">●</span>` +
      `<span class="dbg-arrow">${isCur ? "▶" : ""}</span>` +
      `<span class="a">${esc((ins.addr || "").replace(/^0x0*/, ""))}</span>` +
      `<span class="o">${esc(ins.inst || "")}</span>` +
      (ins.func ? `<span class="dbg-fn">${esc(ins.func)}${ins.offset ? "+" + ins.offset : ""}</span>` : "");
    left.append(row);
  }
  // 우: 탭(상태 · 스택 · 메모리 · 감시식)
  const right = el("div", { class: "dbgws-side" });
  DX.mountSide(right, () => {
    const pane = document.createDocumentFragment();
    // 레지스터
    pane.append(el("div", { class: "dbgws-head" }, selLevel ? `레지스터 (프레임 #${selLevel})` : "레지스터 (더블클릭 수정)"));
    const regGrid = el("div", { class: "reg-grid" });
    const showRegs = d.regs.filter((r) => /^(rax|rbx|rcx|rdx|rsi|rdi|rbp|rsp|r8|r9|r10|r11|r12|r13|r14|r15|rip|eflags|pc|sp|lr|x\d+|w\d+)$/i.test(r.name)).slice(0, 40);
    for (const r of (showRegs.length ? showRegs : d.regs.slice(0, 32))) {
      const changed = d.prevRegs[r.name] !== undefined && d.prevRegs[r.name] !== r.value;
      const cell = el("div", { class: "reg" + (changed ? " changed" : ""), title: "더블클릭: 값 수정 · Alt+클릭: 이 값을 메모리 탭에서 보기",
        ondblclick: () => editReg(r.name, r.value),
        onclick: (e) => { if (e.altKey && /^0x[0-9a-f]+$/i.test(r.value || "")) DX.gotoMemory(r.value); } },
        el("span", { class: "reg-n" }, r.name),
        el("span", { class: "reg-v" }, r.value));
      regGrid.append(cell);
    }
    pane.append(regGrid);
    // 지역변수/인자
    pane.append(el("div", { class: "dbgws-head" }, "지역변수 · 인자 (더블클릭 수정)"));
    const locWrap = el("div", { class: "loc-list" });
    if (!d.locals.length) locWrap.append(el("div", { class: "empty-list" }, "—"));
    for (const v of d.locals) {
      locWrap.append(el("div", { class: "loc" + (v.arg ? " arg" : ""), title: "더블클릭: 값 수정",
        ondblclick: () => editVar(v.name, v.value) },
        el("span", { class: "loc-n" }, (v.arg ? "arg " : "") + v.name),
        el("span", { class: "loc-v" }, String(v.value ?? ""))));
    }
    pane.append(locWrap);
    return pane;
  });
  wrap.append(left, right);
  ed.append(wrap);
}
async function editReg(name, cur) {
  const v = await prompt1("레지스터 수정 (런타임 조작)", `${name} =`, cur || "");
  if (v === null) return;
  try { await api.dbgSetReg(name, v); await dbgRefresh(); toast(`${name} = ${v}`, "ok"); }
  catch (err) { toast("실패: " + err.message, "err"); }
}
async function editVar(name, cur) {
  const v = await prompt1("변수 값 수정 (런타임 조작)", `${name} =`, String(cur ?? ""));
  if (v === null) return;
  try { await api.dbgSetVar(name, v); await dbgRefresh(); toast(`${name} = ${v}`, "ok"); }
  catch (err) { toast("실패: " + err.message, "err"); }
}

// 대상 프로그램 stdin 입력 (전용 pty). 옵션·기록은 패널을 다시 그려도 유지한다.
const stdinOpts = { newline: true, escapes: false };
const stdinHist = [];
let stdinHistPos = -1;

async function dbgSendStdin(inp, opts) {
  const st = S.dbg.state || {};
  if (st.status === "exited") { toast("프로그램이 종료되어 입력을 받을 수 없습니다. ↻ 다시 실행하세요.", "err"); return; }
  const text = opts.eof ? "" : inp.value;
  if (!opts.eof && !text && !opts.newline) return;
  try {
    await api.dbgStdin(text, opts);
    if (!opts.eof && text && stdinHist[stdinHist.length - 1] !== text) stdinHist.push(text);
    stdinHistPos = -1;
    inp.value = "";
  } catch (err) { toast("입력 실패: " + err.message, "err"); return; }
  // 에코와 프로그램 출력은 비동기로 도착하므로 간격을 두고 몇 번 갱신한다
  for (const ms of [150, 500, 1200]) setTimeout(dbgRefreshOutput, ms);
  // 입력을 받은 프로그램이 곧 끝나거나 진행할 수 있다: 실행 중인데 폴링이 꺼져 있으면 켠다
  setTimeout(() => {
    if (S.dbg.active && S.dbg.state && S.dbg.state.status === "running" && !dbgPoll) startDbgPoll();
  }, 300);
}

function renderDebugConsolePanel() {
  const panel = $("#panel-debugcon");
  stdinHistPos = -1;                 // 새로 그린 입력창은 비어 있으므로 기록 위치도 처음으로
  panel.innerHTML = "";
  panel.dataset.rendered = "1";
  if (!S.dbg.active) {
    if (S.dbg.endedInfo) {                          // 세션이 스스로 끝난 경우: 종료 사유와 마지막 출력을 보여 준다
      panel.append(el("div", { class: "dbg-warn", style: "margin:6px" }, el("b", {}, "세션 종료: "), S.dbg.endedInfo.reason),
        el("pre", { id: "dbgcon-ended", class: "dbgcon-out" }, S.dbg.endedInfo.log));
      const pre = panel.querySelector("#dbgcon-ended"); if (pre) pre.scrollTop = pre.scrollHeight;
    } else panel.append(el("div", { class: "empty-list" }, "디버그 세션이 없습니다."));
    panel.dataset.rendered = ""; return;
  }
  const pre = el("pre", { id: "dbgcon-out", class: "dbgcon-out" });
  panel.append(pre);

  // 대상 프로그램 stdin
  const sRow = el("div", { class: "dbgcon-input dbgcon-stdin" });
  const sIn = el("input", { type: "text", id: "dbg-stdin", spellcheck: "false", autocomplete: "off",
    placeholder: "프로그램에 보낼 입력 — Enter 전송, ↑↓ 이전 입력" });
  const cbNl = el("input", { type: "checkbox", ...(stdinOpts.newline ? { checked: "" } : {}) });
  const cbEsc = el("input", { type: "checkbox", ...(stdinOpts.escapes ? { checked: "" } : {}) });
  cbNl.addEventListener("change", () => { stdinOpts.newline = cbNl.checked; });
  cbEsc.addEventListener("change", () => { stdinOpts.escapes = cbEsc.checked; });
  const send = () => dbgSendStdin(sIn, { newline: stdinOpts.newline, escapes: stdinOpts.escapes });
  sIn.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return;   // 한글 등 IME 조합 중 Enter 는 전송이 아니다
    if (e.key === "Enter") { e.preventDefault(); send(); }
    else if (e.key === "ArrowUp" && stdinHist.length) {
      e.preventDefault();
      stdinHistPos = stdinHistPos < 0 ? stdinHist.length - 1 : Math.max(0, stdinHistPos - 1);
      sIn.value = stdinHist[stdinHistPos];
    } else if (e.key === "ArrowDown" && stdinHistPos >= 0) {
      e.preventDefault();
      stdinHistPos += 1;
      if (stdinHistPos >= stdinHist.length) { stdinHistPos = -1; sIn.value = ""; }
      else sIn.value = stdinHist[stdinHistPos];
    }
  });
  sRow.append(
    el("span", { class: "dbgcon-lbl", title: "대상 프로그램의 stdin" }, "입력 ▸"), sIn,
    el("label", { class: "dbgcon-opt", title: "끝에 줄바꿈(Enter)을 붙입니다" }, cbNl, "줄바꿈"),
    el("label", { class: "dbgcon-opt", title: "\\n \\xHH \\033 같은 이스케이프를 바이트로 보냅니다. ^C·^D·0x7f 같은 제어 바이트도 변형 없이 그대로 전달됩니다" }, cbEsc, "\\x 해석"),
    el("button", { class: "vt-btn", onclick: send }, "보내기"),
    el("button", { class: "vt-btn", title: "EOF(^D) 보내기 — 입력 끝",
      onclick: () => dbgSendStdin(sIn, { eof: true, newline: false, escapes: false }) }, "EOF"));
  panel.append(sRow);

  // gdb 명령
  const row = el("div", { class: "dbgcon-input" });
  const inp = el("input", { type: "text", placeholder: "gdb 명령 입력 (예: info registers, x/4xw $sp)" });
  inp.addEventListener("keydown", async (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key !== "Enter") return;
    const cmd = inp.value.trim(); if (!cmd) return; inp.value = "";
    try { await api.dbgConsole(cmd); await dbgRefresh(); }
    catch (err) { toast("명령 실패: " + err.message, "err"); }
  });
  row.append(inp);
  panel.append(row);
  dbgRefreshOutput();
}

// 디버깅 중 정적 디스어셈블리 뷰의 현재 명령을 강조(load_bias 보정)
function syncStaticRip() {
  const st = S.dbg.state;
  if (!st || !st.frame || st.load_bias == null) return;
  const rtAddr = parseInt(st.frame.addr, 16);
  const staticAddr = rtAddr - st.load_bias;
  $$("#editor .ins.cur").forEach((e) => e.classList.remove("cur"));
  const node = $(`#editor [data-ins="${staticAddr}"]`);
  if (node) node.classList.add("cur");
}

// ---- 하단 패널 ----------------------------------------------------------
let userResizedBottom = false;      // 사용자가 스플리터로 하단 패널 높이를 직접 조절했는가
// 디버그 콘솔은 출력 + 입력 줄 두 개가 들어가야 하므로 세션을 시작할 때 한 번 키운다.
// 사용자가 높이를 직접 조절했다면 존중하고, 접어 둔 패널(Ctrl+J)도 되살리지 않는다.
function growBottomForConsole() {
  if (userResizedBottom) return;
  const bot = $("#bottom");
  if (!bot.classList.contains("collapsed") && bot.offsetHeight < 260) bot.style.height = "260px";
}
function showPanel(name) {
  $$("#bottom .ptab").forEach((b) => b.classList.toggle("active", b.dataset.panel === name));
  $$("#bottom .panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
  $("#bottom").classList.remove("collapsed");
  if (name === "debugcon") {
    const dc = $("#panel-debugcon");
    // 이미 그려져 있으면 출력만 갱신한다(다시 그리면 입력 중이던 내용·기록 위치가 사라진다)
    if (S.dbg.active && dc.dataset.rendered && dc.querySelector("#dbg-stdin")) dbgRefreshOutput();
    else renderDebugConsolePanel();
  }
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
  // 편집기(Monaco)에 포커스가 있으면 편집기의 키(Ctrl+K 코드 접기 연쇄·Ctrl+B 등)를 가로채지 않는다. 저장·AI 창·디버거 키만 처리한다.
  if (e.target && e.target.closest && e.target.closest(".monaco-editor")) {
    if (mod && !e.shiftKey && (e.key === "s" || e.key === "S")) { e.preventDefault(); WS.saveCurrent(); return; }
    if (mod && e.shiftKey && (e.key === "A" || e.key === "a")) { e.preventDefault(); toggleAI(); return; }
    if (mod && e.shiftKey && (e.key === "E" || e.key === "e")) { e.preventDefault(); setSideView("explorer"); return; }
    if (S.dbg.active && (e.key === "F5" || e.key === "F10" || e.key === "F11")) { /* 아래 디버거 처리로 */ } else return;
  }
  if (mod && !e.shiftKey && (e.key === "s" || e.key === "S") && curTab() && curTab().kind === "file") { e.preventDefault(); WS.saveCurrent(); return; }
  if (mod && e.shiftKey && (e.key === "E" || e.key === "e")) { e.preventDefault(); setSideView("explorer"); return; }
  // 디버거 단축키(세션 활성 시)
  if (S.dbg.active) {
    if (e.key === "F5" && e.shiftKey) { e.preventDefault(); dbgStop(); return; }
    if (e.key === "F5") { e.preventDefault(); dbgDo(S.dbg.state && S.dbg.state.status === "exited" ? "run" : "continue"); return; }
    if (e.key === "F10") { e.preventDefault(); dbgDo("next"); return; }
    if (e.key === "F11" && e.shiftKey) { e.preventDefault(); dbgDo("finish"); return; }
    if (e.key === "F11") { e.preventDefault(); dbgDo("step"); return; }
  } else if (e.key === "F5" && S.info && e.target.tagName !== "INPUT" && e.target.tagName !== "TEXTAREA") {
    e.preventDefault(); setSideView("debug"); dbgStart(); return;
  }
  if (mod && (e.key === "k" || e.key === "p")) { e.preventDefault(); openPalette(e.key === "p" ? "@" : ""); return; }
  if (e.key === "/" && e.target.tagName !== "INPUT" && e.target.tagName !== "TEXTAREA") { e.preventDefault(); openPalette(); return; }
  if (mod && e.shiftKey && (e.key === "A" || e.key === "a")) { e.preventDefault(); toggleAI(); return; }
  if (mod && e.key === "b") { e.preventDefault(); $("#sidebar").classList.toggle("hidden"); $("#split-side").classList.toggle("hidden"); return; }
  if (mod && e.key === "j") { e.preventDefault(); $("#bottom").classList.toggle("collapsed"); return; }
  if (mod && e.key === "`") { e.preventDefault(); setSideView("script"); return; }
  if (e.key === "F2" && curTab() && !curTab().kind) { e.preventDefault(); renameFunc(curTab().addr); return; }
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
    userResizedBottom = true;
    $("#bottom").style.height = h + "px";
  });
  // 키
  document.addEventListener("keydown", keybind);
  WS.initWorkspace({ S, $, $$, el, toast, setMsg, prompt1, renderTabs, renderEditor, setSideView, openFunc, openPath, showPanel });
  DX.initDbgx({ S, $, el, esc, toast, modal, dbgRefresh, dbgDo, openDebuggerTab });
  initFileDrop();
  // 초기 화면
  setSideView("open");
  aiPush("sys", "AI 도우미입니다. 설정(⚙)에서 Ollama 또는 API 키를 지정한 뒤 질문하세요.");
}
boot();
