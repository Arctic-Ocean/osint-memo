// rework — 노트북(Jupyter 식 셀) UI
// 작업 폴더의 `.ipynb` 를 열어 코드·마크다운 셀을 편집하고, 코드 셀은 서버의 파이썬 커널(`rk` 분석 API 포함)에서 실행한다.
// 파일 형식은 nbformat 4 라서 진짜 Jupyter 에서도 열린다. 출력은 text/plain·스트림·오류·PNG 만 보여 주고(HTML 은 보안상 표시하지 않는다) 저장할 때 같이 저장한다.
import { api } from "/js/api.js";

let C = null;                 // {S,$,$$,el,toast,renderTabs,renderEditor,modal}
const docs = new Map();       // 상대 경로 → {path, nb, mtime_ns, dirty, sel, saving}
const K = { running: false, doc: null, cell: null, poll: null, status: "idle", count: 0, info: null };

export function initNotebook(ctx) { C = ctx; }
export const isNotebookPath = (p) => /\.ipynb$/i.test(p || "");

const ANSI = /\x1b\[[0-9;?]*[A-Za-z]/g;
let idSeq = 0;
const newId = () => "c" + Date.now().toString(36) + (idSeq++).toString(36);
const joinText = (v) => (Array.isArray(v) ? v.join("") : typeof v === "string" ? v : "");
const splitLines = (t) => { const out = []; let cur = ""; for (const ch of t) { cur += ch; if (ch === "\n") { out.push(cur); cur = ""; } } if (cur) out.push(cur); return out; };

// ---- nbformat 읽기·쓰기 -----------------------------------------------------------
function parse(text) {
  let raw;
  try { raw = JSON.parse(text || "{}"); } catch { throw new Error("올바른 노트북(JSON) 파일이 아닙니다."); }
  if (!raw || typeof raw !== "object" || !Array.isArray(raw.cells || [])) throw new Error("노트북 형식이 아닙니다(cells 가 없습니다).");
  const cells = (raw.cells || []).map((c) => {
    const type = c.cell_type === "markdown" ? "markdown" : "code";
    const cell = { id: typeof c.id === "string" ? c.id : newId(), cell_type: type, metadata: c.metadata && typeof c.metadata === "object" ? c.metadata : {},
      source: joinText(c.source) };
    if (type === "code") {
      cell.execution_count = Number.isInteger(c.execution_count) ? c.execution_count : null;
      cell.outputs = (Array.isArray(c.outputs) ? c.outputs : []).map(normOutput).filter(Boolean);
    }
    return cell;
  });
  if (!cells.length) cells.push({ id: newId(), cell_type: "code", metadata: {}, source: "", execution_count: null, outputs: [] });
  return { cells, metadata: raw.metadata && typeof raw.metadata === "object" ? raw.metadata : {}, nbformat: 4, nbformat_minor: Math.max(5, raw.nbformat_minor | 0) };
}
function normOutput(o) {
  if (!o || typeof o !== "object") return null;
  if (o.output_type === "stream") return { output_type: "stream", name: o.name === "stderr" ? "stderr" : "stdout", text: joinText(o.text) };
  if (o.output_type === "error") return { output_type: "error", ename: String(o.ename || ""), evalue: String(o.evalue || ""), traceback: (Array.isArray(o.traceback) ? o.traceback : []).map(String) };
  if (o.output_type === "execute_result" || o.output_type === "display_data") {
    const data = {};
    for (const [k, v] of Object.entries(o.data || {})) data[k] = k.startsWith("image/") ? joinText(v).replace(/\s+/g, "") : joinText(v);
    const out = { output_type: o.output_type, data, metadata: o.metadata && typeof o.metadata === "object" ? o.metadata : {} };
    if (o.output_type === "execute_result") out.execution_count = Number.isInteger(o.execution_count) ? o.execution_count : null;
    return out;
  }
  return null;
}
function serialize(nb) {
  const cells = nb.cells.map((c) => {
    const base = { cell_type: c.cell_type, id: c.id, metadata: c.metadata || {}, source: splitLines(c.source) };
    if (c.cell_type !== "code") return base;
    return { ...base, execution_count: c.execution_count, outputs: c.outputs.map((o) => {
      if (o.output_type === "stream") return { output_type: "stream", name: o.name, text: splitLines(o.text) };
      if (o.output_type === "error") return { output_type: "error", ename: o.ename, evalue: o.evalue, traceback: o.traceback };
      const data = {};
      for (const [k, v] of Object.entries(o.data)) data[k] = k.startsWith("image/") ? v : splitLines(v);
      return o.output_type === "execute_result" ? { output_type: "execute_result", data, metadata: o.metadata, execution_count: o.execution_count }
        : { output_type: "display_data", data, metadata: o.metadata };
    }) };
  });
  const meta = { kernelspec: { display_name: "Python 3", language: "python", name: "python3" }, language_info: { name: "python" }, ...nb.metadata };
  return JSON.stringify({ cells, metadata: meta, nbformat: 4, nbformat_minor: nb.nbformat_minor || 5 }, null, 1) + "\n";
}
export const emptyNotebook = () => serialize({ cells: [{ id: newId(), cell_type: "code", metadata: {}, source: "", execution_count: null, outputs: [] }], metadata: {}, nbformat_minor: 5 });

// ---- 탭 관리 ---------------------------------------------------------------------------
export async function openNotebook(rel) {
  const S = C.S;
  const addr = "nb:" + rel;
  let idx = S.tabs.findIndex((t) => t.addr === addr);
  if (idx < 0) {
    let r;
    try { r = await api.wsFile(rel); } catch (err) { C.toast("노트북을 열지 못했습니다: " + err.message, "err"); return; }
    let nb;
    try { nb = parse(r.text); } catch (err) { C.toast(`${rel}: ${err.message}`, "err"); return; }
    docs.set(rel, { path: rel, nb, mtime_ns: r.mtime_ns, dirty: false, sel: 0, readonly: !!r.readonly });
    S.tabs.push({ addr, kind: "notebook", path: rel });
    idx = S.tabs.length - 1;
  }
  S.active = idx;
  C.renderTabs();
  await C.renderEditor();
  refreshKernelInfo();
}
export const isDirty = (tab) => !!(tab && tab.kind === "notebook" && docs.get(tab.path) && docs.get(tab.path).dirty);
export const anyDirty = () => Array.from(docs.values()).some((d) => d.dirty);
export function closeTab(rel, force = false) {
  const d = docs.get(rel);
  if (d && d.dirty && !force && !confirm(`‘${rel}’ 에 저장하지 않은 변경이 있습니다. 그래도 닫을까요?`)) return false;
  if (K.doc === rel) interrupt();
  docs.delete(rel);
  const S = C.S;
  const i = S.tabs.findIndex((t) => t.addr === "nb:" + rel);
  if (i >= 0) { S.tabs.splice(i, 1); if (S.active >= S.tabs.length) S.active = S.tabs.length - 1; }
  C.renderTabs(); C.renderEditor();
  return true;
}
export async function saveCurrent() {
  const t = C.S.tabs[C.S.active];
  if (t && t.kind === "notebook") return save(t.path);
  return false;
}
/** 빌드 전에: 저장하지 않은 노트북을 모두 저장한다. */
export async function saveAll() {
  for (const [rel, d] of docs) if (d.dirty && !d.readonly) await save(rel);
}
/** 탐색기에서 이름을 바꿨을 때: 열려 있는 노트북 탭(폴더째 옮겨진 것 포함)의 경로를 새로 맞춘다. */
export function renameDocs(rel, to) {
  for (const [old, d] of [...docs.entries()]) {
    if (old !== rel && !old.startsWith(rel + "/")) continue;
    const nrel = to + old.slice(rel.length);
    docs.delete(old); d.path = nrel; docs.set(nrel, d);
    const t = C.S.tabs.find((x) => x.kind === "notebook" && x.path === old);
    if (t) { t.path = nrel; t.addr = "nb:" + nrel; }
  }
}
/** 파일·폴더를 지웠을 때: 그 아래에 열려 있던 노트북 탭을 (확인 없이) 닫는다. */
export function closeUnder(rel) {
  for (const old of [...docs.keys()]) if (old === rel || old.startsWith(rel + "/")) closeTab(old, true);
}
async function save(rel, force = false) {
  const d = docs.get(rel);
  if (!d || d.saving) return false;
  d.saving = true;
  try {
    const r = await api.wsSave({ path: rel, text: serialize(d.nb), encoding: "utf-8", expected_mtime_ns: d.mtime_ns, force });
    d.mtime_ns = r.mtime_ns; d.dirty = false;
    C.toast(`저장했습니다: ${rel.split("/").pop()}`, "ok");
    C.renderTabs(); paintToolbarState();
    return true;
  } catch (err) {
    if (err.conflict) {
      d.saving = false;
      if (confirm(`‘${rel}’ 이(가) 열어 본 뒤 디스크에서 바뀌었습니다.\n내 노트북으로 덮어쓸까요?`)) return save(rel, true);
      return false;
    }
    C.toast("저장하지 못했습니다: " + err.message, "err");
    return false;
  } finally { d.saving = false; }
}
function markDirty(d) { if (!d.dirty) { d.dirty = true; C.renderTabs(); } }

// ---- 커널 ------------------------------------------------------------------------------------
async function refreshKernelInfo() {
  try { K.info = await api.nbInfo(); K.status = K.info.status; K.count = K.info.execution_count; paintToolbarState(); } catch { /* 서버 없음 */ }
}
function paintToolbarState() {
  const chip = C.$("#nb-kstate");
  if (chip) {
    const lab = K.running ? "실행 중…" : { idle: "대기", ok: "대기", error: "대기(마지막 셀 오류)", interrupted: "대기(중단됨)" }[K.status] || "대기";
    chip.textContent = "● 커널 " + lab; chip.className = "nb-kstate " + (K.running ? "run" : "idle");
  }
  const stop = C.$("#nb-stop"); if (stop) stop.disabled = !K.running;
  const t = C.S.tabs[C.S.active];
  const sv = C.$("#nb-save"); if (sv && t && docs.get(t.path)) sv.textContent = docs.get(t.path).dirty ? "💾 저장 ●" : "💾 저장";
}

/** 한 셀을 실행하고 끝날 때까지 기다린다. 성공하면 true. */
async function execCell(d, i) {
  const cell = d.nb.cells[i];
  if (!cell || cell.cell_type !== "code") return true;
  if (K.running) { C.toast("다른 셀이 실행 중입니다. 끝나길 기다리거나 중단하세요.", "err"); return false; }
  cell.outputs = []; cell.execution_count = null;
  K.running = true; K.doc = d.path; K.cell = cell.id; K.status = "running";
  markDirty(d);
  paintCell(d, i); paintToolbarState();
  let ok = true;
  try {
    const r = await api.nbExec(cell.source);
    cell.execution_count = r.execution_count;
    paintCell(d, i);
    let since = 0;
    for (;;) {
      await new Promise((res) => setTimeout(res, 150));
      const p = await api.nbPoll(since);
      for (const ev of p.events) {
        const { index, ...o } = ev;
        const norm = normOutput(o);
        if (norm) {
          const last = cell.outputs[cell.outputs.length - 1];
          if (norm.output_type === "stream" && last && last.output_type === "stream" && last.name === norm.name) last.text += norm.text;
          else cell.outputs.push(norm);
        }
      }
      since = p.total;
      if (p.events.length) paintOutputs(d, i);
      K.status = p.status;
      if (!p.running) { ok = p.status === "ok"; break; }
    }
  } catch (err) {
    ok = false;
    cell.outputs.push({ output_type: "error", ename: "오류", evalue: err.message, traceback: [] });
  } finally {
    K.running = false; K.doc = null; K.cell = null;
    paintCell(d, i); paintToolbarState();
  }
  return ok;
}
async function interrupt() {
  try { await api.nbInterrupt(); } catch { /* 이미 끝남 */ }
}
async function restart() {
  if (!confirm("커널을 재시작하면 변수가 모두 사라집니다. 계속할까요?")) return;
  try { await api.nbRestart(); K.running = false; K.status = "idle"; K.count = 0; C.toast("커널을 재시작했습니다.", "ok"); paintToolbarState(); }
  catch (err) { C.toast("재시작 실패: " + err.message, "err"); }
}

// ---- 편집 동작 ---------------------------------------------------------------------------------
function addCell(d, at, type = "code") {
  d.nb.cells.splice(at, 0, { id: newId(), cell_type: type, metadata: {}, source: "", ...(type === "code" ? { execution_count: null, outputs: [] } : {}) });
  d.sel = at; markDirty(d); renderBody(d, true);
}
function deleteCell(d, i) {
  if (d.nb.cells.length === 1) { d.nb.cells[0] = { id: newId(), cell_type: "code", metadata: {}, source: "", execution_count: null, outputs: [] }; }
  else d.nb.cells.splice(i, 1);
  d.sel = Math.max(0, Math.min(d.sel, d.nb.cells.length - 1)); markDirty(d); renderBody(d, true);
}
function moveCell(d, i, dir) {
  const j = i + dir;
  if (j < 0 || j >= d.nb.cells.length) return;
  const [c] = d.nb.cells.splice(i, 1); d.nb.cells.splice(j, 0, c);
  d.sel = j; markDirty(d); renderBody(d, true);
}
function toggleType(d, i) {
  const c = d.nb.cells[i];
  if (c.cell_type === "code") { c.cell_type = "markdown"; delete c.outputs; delete c.execution_count; }
  else { c.cell_type = "code"; c.outputs = []; c.execution_count = null; }
  markDirty(d); renderBody(d, true);
}
function clearOutputs(d) {
  for (const c of d.nb.cells) if (c.cell_type === "code") { c.outputs = []; c.execution_count = null; }
  markDirty(d); renderBody(d, true);
}
async function runAll(d) {
  for (let i = 0; i < d.nb.cells.length; i++) {
    if (d.nb.cells[i].cell_type !== "code" || !d.nb.cells[i].source.trim()) continue;
    d.sel = i; highlightSel(d);
    if (!(await execCell(d, i))) { C.toast(`셀 ${i + 1} 에서 멈췄습니다.`, "err"); return; }
  }
}

// ---- 그리기 ---------------------------------------------------------------------------------------
export async function renderTab(ed, tab) {
  const d = docs.get(tab.path);
  ed.style.display = "flex"; ed.style.flexDirection = "column"; ed.style.overflow = "hidden";
  if (!d) { ed.append(C.el("div", { class: "empty-list" }, "노트북을 불러오지 못했습니다.")); return; }
  const { el } = C;
  const tb = el("div", { class: "view-toolbar nb-toolbar" },
    el("button", { class: "vt-btn primary", title: "선택한 셀 실행 (Shift+Enter)", onclick: () => runSel(d, true) }, "▶ 실행"),
    el("button", { class: "vt-btn", title: "모든 코드 셀을 차례로 실행(오류에서 멈춤)", onclick: () => runAll(d) }, "⏭ 모두 실행"),
    el("button", { id: "nb-stop", class: "vt-btn", title: "실행 중인 셀 중단", onclick: interrupt, disabled: "" }, "⏹ 중단"),
    el("button", { class: "vt-btn", title: "커널을 새로 시작(변수 초기화)", onclick: restart }, "↻ 재시작"),
    el("span", { class: "nb-sep" }),
    el("button", { class: "vt-btn", onclick: () => addCell(d, d.sel + 1, "code") }, "＋ 코드"),
    el("button", { class: "vt-btn", onclick: () => addCell(d, d.sel + 1, "markdown") }, "＋ 글"),
    el("button", { class: "vt-btn", title: "모든 출력 지우기", onclick: () => clearOutputs(d) }, "🧹"),
    el("span", { class: "vt-spacer" }),
    el("span", { id: "nb-kstate", class: "nb-kstate idle" }, ""),
    el("button", { id: "nb-save", class: "vt-btn primary", title: "저장 (Ctrl+S)", onclick: () => save(d.path) }, "💾 저장"));
  ed.append(tb);
  const body = el("div", { id: "nb-body", class: "nb-body" });
  ed.append(body);
  d.body = body;
  renderBody(d, true);
  paintToolbarState();
}

function renderBody(d) {
  const body = d.body;
  if (!body || !body.isConnected) return;
  body.innerHTML = "";
  d.nb.cells.forEach((c, i) => body.append(cellEl(d, c, i)));
  body.append(C.el("div", { class: "nb-add" },
    C.el("button", { class: "vt-btn", onclick: () => addCell(d, d.nb.cells.length, "code") }, "＋ 코드 셀"),
    C.el("button", { class: "vt-btn", onclick: () => addCell(d, d.nb.cells.length, "markdown") }, "＋ 글 셀")));
  highlightSel(d);
}
// 화면에 떠 있는 노트북에만 그린다(셀을 실행하는 동안 다른 탭으로 옮겨도 엉뚱한 노트북에 출력이 그려지지 않게)
function cellNodes(d) { return d.body && d.body.isConnected ? Array.from(d.body.querySelectorAll(":scope > .nb-cell")) : []; }
function highlightSel(d) { cellNodes(d).forEach((e, i) => e.classList.toggle("sel", i === d.sel)); }
function cellNode(d, i) { return cellNodes(d)[i]; }
function paintCell(d, i) {
  const node = cellNode(d, i);
  if (!node) return;
  const c = d.nb.cells[i];
  const run = K.running && K.cell === c.id;
  node.classList.toggle("running", run);
  const pr = node.querySelector(".nb-prompt");
  if (pr) pr.textContent = c.cell_type === "code" ? `[${run ? "*" : c.execution_count ?? " "}]` : "";
  paintOutputs(d, i);
}
function paintOutputs(d, i) {
  const node = cellNode(d, i);
  if (!node) return;
  const box = node.querySelector(".nb-out");
  if (!box) return;
  const c = d.nb.cells[i];
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 12;
  box.innerHTML = "";
  for (const o of c.outputs || []) box.append(outputEl(o));
  if (atBottom) box.scrollTop = box.scrollHeight;
}
const OUT_MAX = 200000;
function clip(t) { return t.length > OUT_MAX ? t.slice(0, OUT_MAX) + `\n… (+${t.length - OUT_MAX}자 생략)` : t; }
function outputEl(o) {
  const { el } = C;
  if (o.output_type === "stream") return el("pre", { class: "nb-stream " + o.name }, clip(o.text));
  if (o.output_type === "error") {
    const tb = o.traceback.length ? o.traceback.join("\n").replace(ANSI, "") : `${o.ename}: ${o.evalue}`;
    return el("pre", { class: "nb-err" }, clip(tb));
  }
  const png = o.data["image/png"];
  if (png && /^[A-Za-z0-9+/=]+$/.test(png) && png.length < 8_000_000) {
    return el("div", { class: "nb-img" }, el("img", { src: "data:image/png;base64," + png, alt: "출력 이미지" }));
  }
  const t = o.data["text/plain"];
  if (t !== undefined) {
    return el("div", { class: "nb-result" },
      o.output_type === "execute_result" ? el("span", { class: "nb-oprompt" }, `Out[${o.execution_count ?? " "}]:`) : null,
      el("pre", {}, clip(t.replace(ANSI, ""))));
  }
  return el("div", { class: "run-muted pad" }, "[이 출력 형식(HTML 등)은 표시하지 않습니다]");
}

function cellEl(d, c, i) {
  const { el } = C;
  const wrap = el("div", { class: "nb-cell " + (c.cell_type === "code" ? "code" : "md"), onmousedown: () => { if (d.sel !== i) { d.sel = i; highlightSel(d); } } });
  const ctl = el("div", { class: "nb-ctl" },
    el("button", { title: "이 셀 실행", onclick: () => { d.sel = i; runSel(d, false); } }, "▶"),
    el("button", { title: "위로", onclick: () => moveCell(d, i, -1) }, "↑"),
    el("button", { title: "아래로", onclick: () => moveCell(d, i, 1) }, "↓"),
    el("button", { title: "코드 ↔ 글", onclick: () => toggleType(d, i) }, "⇄"),
    el("button", { title: "아래에 코드 셀 추가", onclick: () => addCell(d, i + 1, "code") }, "＋"),
    el("button", { title: "삭제", onclick: () => deleteCell(d, i) }, "✕"));
  const main = el("div", { class: "nb-main" });
  if (c.cell_type === "markdown") {
    const view = el("div", { class: "nb-md", title: "더블클릭해서 편집" });
    const edit = el("textarea", { class: "nb-src", spellcheck: "false", rows: 3 });
    edit.value = c.source;
    const showView = () => { view.innerHTML = ""; view.append(renderMarkdown(c.source)); view.style.display = ""; edit.style.display = "none"; };
    const showEdit = () => { view.style.display = "none"; edit.style.display = ""; autosize(edit); edit.focus(); };
    edit.addEventListener("input", () => { c.source = edit.value; markDirty(d); autosize(edit); });
    edit.addEventListener("keydown", (e) => cellKeys(e, d, i, edit, () => { showView(); }));
    edit.addEventListener("blur", () => { if (c.source.trim()) showView(); });
    view.addEventListener("dblclick", showEdit);
    wrap.append(el("div", { class: "nb-gutter" }, ""), main, ctl);
    main.append(view, edit);
    if (c.source.trim()) showView(); else { view.style.display = "none"; setTimeout(() => autosize(edit), 0); }
    wrap._showView = showView; wrap._showEdit = showEdit;
    return wrap;
  }
  const ta = el("textarea", { class: "nb-src", spellcheck: "false", rows: 2, placeholder: "# 파이썬 코드 — Shift+Enter 로 실행. 예: rk.table([{'이름': rk.name(f.vaddr)} for f in rk.functions()[:5]])" });
  ta.value = c.source;
  ta.addEventListener("input", () => { c.source = ta.value; markDirty(d); autosize(ta); });
  ta.addEventListener("keydown", (e) => cellKeys(e, d, i, ta));
  main.append(ta, el("div", { class: "nb-out" }));
  const running = K.running && K.cell === c.id;
  if (running) wrap.classList.add("running");
  wrap.append(el("div", { class: "nb-gutter" }, el("span", { class: "nb-prompt" }, `[${running ? "*" : c.execution_count ?? " "}]`)), main, ctl);
  setTimeout(() => { autosize(ta); paintOutputs(d, i); }, 0);
  return wrap;
}
function autosize(ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight + 2, 700) + "px"; }

function cellKeys(e, d, i, ta, afterRun) {
  if (e.isComposing || e.keyCode === 229) return;                 // 한글 등 IME 조합 중
  if (e.key === "Escape") { ta.blur(); return; }                  // 탭 키가 들여쓰기에 쓰이므로, Esc 로 셀에서 빠져나온다
  if (e.key === "Tab" && !e.shiftKey) {
    e.preventDefault();
    const s = ta.selectionStart, t = ta.selectionEnd;
    ta.value = ta.value.slice(0, s) + "    " + ta.value.slice(t);
    ta.selectionStart = ta.selectionEnd = s + 4;
    ta.dispatchEvent(new Event("input"));
    return;
  }
  if (e.key === "Enter" && (e.shiftKey || e.ctrlKey || e.altKey)) {
    e.preventDefault();
    d.sel = i;
    const c = d.nb.cells[i];
    if (c.cell_type === "markdown") { if (afterRun) afterRun(); if (e.shiftKey) focusCell(d, i + 1, true); return; }
    execCell(d, i).then(() => {
      if (e.altKey) addCell(d, i + 1, "code");
      else if (e.shiftKey) focusCell(d, i + 1, true);
    });
  }
}
function focusCell(d, i, createIfLast) {
  if (i >= d.nb.cells.length) { if (createIfLast) addCell(d, i, "code"); else return; }
  d.sel = i; highlightSel(d);
  const node = cellNode(d, i);
  const ta = node && node.querySelector("textarea.nb-src");
  if (ta && ta.offsetParent !== null) ta.focus();
  else if (node && node._showEdit) node._showEdit();
  if (node) node.scrollIntoView({ block: "nearest" });
}
function runSel(d, advance) {
  const i = d.sel;
  const c = d.nb.cells[i];
  if (!c) return;
  if (c.cell_type === "markdown") { const n = cellNode(d, i); if (n && n._showView) n._showView(); if (advance) focusCell(d, i + 1, true); return; }
  execCell(d, i).then(() => { if (advance) focusCell(d, i + 1, true); });
}

// ---- 마크다운(안전한 최소 변환: DOM 으로만 만든다) --------------------------------------------------------
export function renderMarkdown(src) {
  const root = document.createElement("div");
  const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
  let i = 0;
  const inline = (parent, text) => {
    const re = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(~~[^~\n]+~~)/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) parent.append(document.createTextNode(text.slice(last, m.index)));
      const tok = m[0];
      const node = document.createElement(tok.startsWith("`") ? "code" : tok.startsWith("**") ? "strong" : tok.startsWith("~~") ? "del" : "em");
      node.textContent = tok.startsWith("**") || tok.startsWith("~~") ? tok.slice(2, -2) : tok.slice(1, -1);
      parent.append(node);
      last = m.index + tok.length;
    }
    if (last < text.length) parent.append(document.createTextNode(text.slice(last)));
  };
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*$/.test(line)) { i++; continue; }
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      const buf = []; i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) buf.push(lines[i++]);
      i++;
      const pre = document.createElement("pre"); pre.className = "nb-md-code"; pre.textContent = buf.join("\n"); root.append(pre);
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { const e = document.createElement("h" + h[1].length); inline(e, h[2]); root.append(e); i++; continue; }
    if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) { root.append(document.createElement("hr")); i++; continue; }
    if (/^\s*[-*+]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const list = document.createElement(ordered ? "ol" : "ul");
      while (i < lines.length && (ordered ? /^\s*\d+[.)]\s+/.test(lines[i]) : /^\s*[-*+]\s+/.test(lines[i]))) {
        const li = document.createElement("li"); inline(li, lines[i].replace(/^\s*(?:[-*+]|\d+[.)])\s+/, "")); list.append(li); i++;
      }
      root.append(list); continue;
    }
    if (/^>\s?/.test(line)) {
      const bq = document.createElement("blockquote"); const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ""));
      inline(bq, buf.join(" ")); root.append(bq); continue;
    }
    const p = document.createElement("p"); const buf = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^(#{1,6}\s|```|>\s?|\s*[-*+]\s+|\s*\d+[.)]\s+)/.test(lines[i])) buf.push(lines[i++]);
    inline(p, buf.join(" ")); root.append(p);
  }
  return root;
}

export async function newNotebookFlow(dirRel = "") {
  const name = await C.prompt1("새 노트북", `${dirRel || "(작업 폴더)"} 안에 만들 이름`, "analysis.ipynb");
  if (!name) return null;
  const fname = /\.ipynb$/i.test(name.trim()) ? name.trim() : name.trim() + ".ipynb";
  const rel = (dirRel ? dirRel + "/" : "") + fname;
  try {
    await api.wsOp({ op: "create", path: rel, kind: "file" });
    const st = await api.wsStat(rel);
    await api.wsSave({ path: rel, text: emptyNotebook(), encoding: "utf-8", expected_mtime_ns: st.mtime_ns, force: false });
  } catch (err) { C.toast("노트북을 만들지 못했습니다: " + err.message, "err"); return null; }
  return rel;
}
