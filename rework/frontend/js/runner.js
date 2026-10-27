// rework — 빌드·테스트 러너 UI (하단 ‘빌드·테스트’ 패널)
// 작업 폴더의 소스를 격리 안에서 컴파일·실행·시험하고, 출력과 문제(파일:줄:열)를 보여 준다. 문제를 누르면 편집기의 그 줄로 간다.
import { api } from "/js/api.js";

let C = null;     // {S,$,el,toast,showPanel,WS}
const R = {
  tasks: null, sandbox: null, root: null,
  command: "", taskId: "", timeout: 300, isolated: true, syncDeletes: false,
  state: null, offset: 0, poll: null, history: [],
  rendered: false,
};
try { R.history = JSON.parse(localStorage.getItem("rework-run-history") || "[]").filter((x) => typeof x === "string").slice(0, 12); } catch {}

export function initRunner(ctx) { C = ctx; }

const KIND_LABEL = { build: "빌드", test: "시험", run: "실행" };

function remember(cmd) {
  R.history = [cmd, ...R.history.filter((x) => x !== cmd)].slice(0, 12);
  try { localStorage.setItem("rework-run-history", JSON.stringify(R.history)); } catch {}
}

export async function loadTasks() {
  try {
    const r = await api.runTasks();
    if (R.root && r.root !== R.root) { R.state = null; R.offset = 0; R.command = ""; R.taskId = ""; }       // 다른 작업 폴더
    R.tasks = r.tasks; R.sandbox = r.sandbox; R.root = r.root; R.err = null;
    if (!R.command && R.tasks.length) { R.command = R.tasks[0].command; R.taskId = R.tasks[0].id; }
  } catch (err) {
    R.tasks = null; R.root = null; R.err = err.message;
  }
}

/** 하단 패널을 보여 준다(showPanel 이 처음·다시 열릴 때 onShow 를 부른다). */
export function openPanel() { C.showPanel("run"); }

let showing = null;
/** 패널이 보일 때: 작업 목록을 다시 읽고 그린다(겹쳐 불리면 하나만). */
export function onShow() {
  if (!showing) showing = (async () => { try { await loadTasks(); render(); } finally { showing = null; } })();
  return showing;
}

function running() { return R.state && R.state.status === "running"; }

async function start() {
  const cmd = (R.command || "").trim();
  if (!cmd) { C.toast("실행할 명령을 입력하거나 작업을 고르세요.", "err"); return; }
  if (running()) return;
  if (!R.isolated && !confirm("격리 없이 이 컴퓨터에서 직접 실행합니다.\n\n악성코드에서 나온 코드라면 파일 삭제·정보 유출 같은 일이 실제로 일어날 수 있습니다.\n신뢰하는 코드일 때만 계속하세요. 계속할까요?")) return;
  await C.WS.saveAll().catch(() => {});                 // 편집 중인 파일을 먼저 저장해 최신 소스로 빌드한다
  try {
    R.offset = 0;
    R.state = await api.runStart({ command: cmd, timeout: R.timeout, sandbox: R.isolated, sync_deletes: R.syncDeletes });
    R.offset = R.state.offset || 0;
    remember(cmd);
    render(true);
    poll();
  } catch (err) { C.toast("실행 실패: " + err.message, "err"); }
}

async function stop() {
  try { await api.runStop(); } catch (err) { C.toast("중단 실패: " + err.message, "err"); }
}

function poll() {
  if (R.poll) return;
  R.poll = setInterval(async () => {
    try {
      const s = await api.runState(R.offset);
      if (s.status === "none") { stopPoll(); return; }
      const out = $out();
      if (out && s.output) {
        const bottom = out.scrollHeight - out.scrollTop - out.clientHeight < 12;
        if (s.output_truncated) out.textContent = "";
        out.append(document.createTextNode(s.output));
        if (bottom) out.scrollTop = out.scrollHeight;
      }
      R.offset = s.offset;
      R.state = s;
      paintStatus();
      if (s.status !== "running") { stopPoll(); finish(s); }
    } catch { stopPoll(); }
  }, 350);
}
function stopPoll() { if (R.poll) { clearInterval(R.poll); R.poll = null; } }

async function finish(s) {
  render(false);
  const ok = s.status === "done" && s.exit_code === 0;
  C.toast(s.summary || "작업이 끝났습니다.", ok ? "ok" : "err");
  if ((s.changes || []).some((c) => c.status !== "skipped")) {
    try { await C.WS.refreshAfterRun(); } catch {}
  }
}

const $out = () => C.$("#run-out");

function paintStatus() {
  const st = C.$("#run-status");
  if (!st) return;
  const s = R.state;
  st.innerHTML = "";
  if (!s || s.status === "none") { st.append(C.el("span", { class: "run-muted" }, "아직 실행하지 않았습니다.")); return; }
  const label = { running: "실행 중…", done: s.exit_code === 0 ? "성공" : `실패 (종료 코드 ${s.exit_code})`, timeout: "시간 초과", stopped: "중단됨", limit: "자원 한도 초과", error: "실행 못 함" }[s.status] || s.status;
  st.append(C.el("span", { class: "run-badge " + (s.status === "done" && s.exit_code === 0 ? "ok" : s.status === "running" ? "run" : "bad") }, label),
    C.el("span", { class: "run-muted" }, ` ${s.elapsed}초`),
    s.sandboxed ? C.el("span", { class: "run-chip", title: (s.sandbox && s.sandbox.summary) || "" }, "🔒 격리") : C.el("span", { class: "run-chip warn" }, "⚠ 격리 없음"));
  if (s.summary && s.status !== "running") st.append(C.el("span", { class: "run-sum" }, s.summary));
  const run = s.status === "running";
  const b = C.$("#run-go"), x = C.$("#run-stop");
  if (b) b.disabled = run;
  if (x) x.disabled = !run;
}

function problemsPane() {
  const { el } = C;
  const box = el("div", { class: "run-side" });
  const s = R.state || {};
  const probs = s.problems || [];
  const changes = s.changes || [];
  box.append(el("div", { class: "run-sidehead" }, `문제 ${probs.length}`));
  if (!probs.length) box.append(el("div", { class: "run-muted pad" }, s.status === "running" ? "…" : "없음"));
  for (const p of probs) {
    const loc = p.file ? `${p.file}:${p.line}` : "";
    const row = el("div", { class: "run-prob sev-" + p.severity, title: p.message,
      onclick: () => { if (p.file) C.WS.openFile(p.file, { line: p.line, col: p.col }).catch((err) => C.toast("열 수 없음: " + err.message, "err")); } },
      el("span", { class: "rp-ico" }, p.severity === "error" ? "⛔" : p.severity === "warning" ? "⚠" : p.severity === "trace" ? "↳" : "ℹ"),
      el("span", { class: "rp-loc" }, loc), el("span", { class: "rp-msg" }, p.message));
    box.append(row);
  }
  if (changes.length) {
    box.append(el("div", { class: "run-sidehead" }, `되가져온 파일 ${changes.filter((c) => c.status === "new" || c.status === "modified" || c.status === "deleted").length}`));
    for (const c of changes) {
      box.append(el("div", { class: "run-chg st-" + c.status, title: c.reason || "",
        onclick: () => { if (c.status === "new" || c.status === "modified") C.WS.openFile(c.path).catch(() => {}); } },
        el("span", { class: "rc-st" }, { new: "새 파일", modified: "변경", deleted: "삭제", conflict: "충돌", skipped: "건너뜀" }[c.status] || c.status),
        el("span", { class: "rc-path" }, c.path)));
    }
  }
  const info = s.copy;
  if (info && info.skipped && info.skipped.length) {
    box.append(el("div", { class: "run-muted pad", title: info.skipped.slice(0, 30).join("\n") }, `격리 복사에서 제외 ${info.skipped.length}개(.git·큰 파일 등)`));
  }
  return box;
}

export function render(fresh = false) {
  const panel = C.$("#panel-run");
  if (!panel) return;
  const { el } = C;
  panel.innerHTML = "";
  panel.dataset.rendered = "1";
  if (!R.root) {
    panel.append(el("div", { class: "empty-list", style: "padding:14px" },
      R.err || "먼저 작업 폴더를 여세요. (탐색기 ▸ 폴더 열기) — 폴더의 소스를 격리된 환경에서 빌드·시험합니다."));
    return;
  }
  // 도구 줄
  const sel = el("select", { class: "run-task", title: "감지된 작업", onchange: (e) => {
    const t = (R.tasks || []).find((x) => x.id === e.target.value);
    if (t) { R.command = t.command; R.taskId = t.id; cmdInp.value = t.command; }
  } }, el("option", { value: "" }, "작업 선택…"));
  for (const kind of ["build", "test", "run"]) {
    const group = (R.tasks || []).filter((t) => t.kind === kind);
    if (!group.length) continue;
    const og = el("optgroup", { label: KIND_LABEL[kind] });
    for (const t of group) og.append(el("option", { value: t.id, ...(t.id === R.taskId ? { selected: "" } : {}) }, t.label));
    sel.append(og);
  }
  const cmdInp = el("input", { type: "text", class: "run-cmd", spellcheck: "false", list: "run-hist", value: R.command,
    placeholder: "명령 — 예: make, gcc -g a.c -o a && ./a, python3 -m pytest -q", oninput: (e) => { R.command = e.target.value; R.taskId = ""; },
    onkeydown: (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); start(); } } });
  const hist = el("datalist", { id: "run-hist" });
  for (const h of R.history) hist.append(el("option", { value: h }));
  const to = el("input", { type: "number", class: "run-to", min: 5, max: 3600, value: R.timeout, title: "제한 시간(초)",
    onchange: (e) => { R.timeout = Math.max(5, Math.min(3600, +e.target.value || 300)); } });
  const iso = el("input", { type: "checkbox", ...(R.isolated ? { checked: "" } : {}), onchange: (e) => { R.isolated = e.target.checked; render(); } });
  const del = el("input", { type: "checkbox", ...(R.syncDeletes ? { checked: "" } : {}), onchange: (e) => { R.syncDeletes = e.target.checked; } });
  const bar = el("div", { class: "run-bar" }, sel, cmdInp, hist,
    el("button", { id: "run-go", class: "vt-btn primary", onclick: start, title: "실행 (Ctrl+Shift+B)" }, "▶ 실행"),
    el("button", { id: "run-stop", class: "vt-btn", onclick: stop, disabled: "", title: "중단" }, "⏹"),
    el("label", { class: "run-opt", title: "작업 폴더를 격리된 복사본에서 실행하고, 끝나면 새로 생기거나 바뀐 파일만 검증해서 되가져옵니다. 끄면 이 컴퓨터에서 직접 실행합니다." }, iso, " 격리"),
    el("label", { class: "run-opt", title: "격리 안에서 지운 파일을 작업 폴더에서도 지웁니다(그 사이 바뀌지 않은 파일만)" }, del, " 삭제도 반영"),
    to, el("span", { class: "run-muted" }, "초"));
  panel.append(bar);
  if (R.sandbox && R.isolated && !R.sandbox.available) {
    panel.append(el("div", { class: "dbg-warn", style: "margin:4px 8px" }, el("b", {}, "격리를 쓸 수 없습니다: "), R.sandbox.reason || "", " ", R.sandbox.hint || "",
      " 신뢰하는 코드라면 ‘격리’를 끄고 실행할 수 있습니다."));
  }
  panel.append(el("div", { id: "run-status", class: "run-statusline" }));
  const body = el("div", { class: "run-body" });
  const out = el("pre", { id: "run-out", class: "run-out" });
  body.append(out, problemsPane());
  panel.append(body);
  const s = R.state;
  if (s && s.status !== "none") {
    // 새로 그릴 때는 지금까지의 전체 출력을 다시 받아 채운다
    api.runState(0).then((full) => { out.textContent = full.output || ""; out.scrollTop = out.scrollHeight; R.offset = full.offset; }).catch(() => {});
  } else if (!fresh) {
    out.textContent = "";
  }
  paintStatus();
}

/** Ctrl+Shift+B: 마지막 명령(없으면 첫 번째 빌드 작업)을 실행한다. */
export async function runDefault() {
  C.showPanel("run");
  await onShow();
  if (!R.command && R.tasks && R.tasks.length) { const t = R.tasks.find((x) => x.kind === "build") || R.tasks[0]; R.command = t.command; R.taskId = t.id; }
  render();
  start();
}

