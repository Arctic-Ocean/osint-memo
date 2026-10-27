// rework — 디버거 심화 UI: 조건부 중단점·로그포인트·워치포인트 대화상자, 스택/메모리/감시식 탭, 기록·역방향 실행, 정지 이유 안내
// app.js 가 initDbgx(ctx) 로 필요한 도우미를 넘겨 준다(workspace.js 와 같은 방식).
import { api } from "/js/api.js";

let C = null;       // {S,$,el,esc,toast,modal,dbgRefresh,dbgDo,openDebuggerTab}
export const X = {
  tab: "state",                       // 오른쪽 패널의 탭: state | stack | memory | watch
  memAddr: "$sp", memLen: 128, memPrev: null, memData: null, memErr: "", regions: null,
  stackCount: 48, stackData: null, stackErr: "",
  watch: [],                          // 감시식 목록(세션이 바뀌어도 유지)
  watchVals: new Map(),               // 식 → {value}|{error}
  watchPrev: new Map(),
};

export function initDbgx(ctx) { C = ctx; }

const KIND_ICON = { break: "●", log: "✎", watch: "👁", rwatch: "👁r", awatch: "👁a" };
const KIND_NAME = { break: "중단점", log: "로그포인트", watch: "값 변경 감시", rwatch: "읽기 감시", awatch: "접근 감시" };

// 쉼표로 나누되 괄호·따옴표 안의 쉼표는 보존한다(`f(a, b), s` → ["f(a, b)", "s"])
export function splitArgs(text) {
  const out = []; let depth = 0, q = "", cur = "";
  for (const ch of String(text || "")) {
    if (q) { cur += ch; if (ch === q) q = ""; continue; }
    if (ch === '"' || ch === "'") { q = ch; cur += ch; continue; }
    if ("([{".includes(ch)) depth++;
    if (")]}".includes(ch)) depth = Math.max(0, depth - 1);
    if (ch === "," && depth === 0) { out.push(cur.trim()); cur = ""; continue; }
    cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out.filter(Boolean);
}

// ---- 중단점 목록(사이드바) ------------------------------------------------------
export function breakLabel(b) {
  if (b.kind === "log") return `${b.func || b.what || b.line || "?"}`;
  if (b.kind !== "break") return b.what || "?";
  return b.func || b.what || (b.line ? `:${b.line}` : "?");
}

export function renderBreakpoints(body) {
  const { el } = C;
  const d = C.S.dbg;
  const bhead = el("div", { class: "side-head" }, "중단점 · 감시",
    el("button", { class: "mini-btn", title: "중단점(조건·무시 횟수·임시) 추가", onclick: () => addBreak() }, "+"),
    el("button", { class: "mini-btn", title: "로그포인트(멈추지 않고 메시지만 출력) 추가", onclick: () => addLog() }, "✎"),
    el("button", { class: "mini-btn", title: "워치포인트(값이 바뀌거나 읽힐 때 멈춤) 추가", onclick: () => addWatch() }, "👁"));
  body.append(bhead);
  if (!d.breaks.length) body.append(el("div", { class: "empty-list", style: "padding:8px 12px" }, "없음"));
  for (const b of d.breaks) {
    const meta = [];
    if (b.cond) meta.push(el("span", { class: "bp-tag cond", title: "조건: 참일 때만 멈춥니다" }, "if " + b.cond));
    if (b.ignore && b.ignore !== "0") meta.push(el("span", { class: "bp-tag", title: "처음 N 번은 건너뜁니다" }, `건너뜀 ${b.ignore}`));
    if (b.temporary) meta.push(el("span", { class: "bp-tag", title: "한 번 멈추면 자동으로 지워집니다" }, "임시"));
    if (b.log) meta.push(el("span", { class: "bp-tag log", title: "멈추지 않고 출력합니다" }, `“${b.log.template.replace(/\\n$/, "")}”` + (b.log.args.length ? ` ← ${b.log.args.join(", ")}` : "")));
    if (b.times && b.times !== "0") meta.push(el("span", { class: "bp-hits", title: "지금까지 걸린 횟수" }, `×${b.times}`));
    const row = el("div", { class: "bp-item bp-" + b.kind },
      el("input", { type: "checkbox", ...(b.enabled ? { checked: "" } : {}),
        onchange: () => api.dbgBreak({ op: "toggle", number: b.number, enable: !b.enabled }).then(C.dbgRefresh) }),
      el("span", { class: "bp-ico", title: KIND_NAME[b.kind] || "" }, KIND_ICON[b.kind] || "●"),
      el("span", { class: "li-name", title: b.addr || "", onclick: () => C.openDebuggerTab() }, breakLabel(b)),
      ...meta,
      b.kind === "break" ? el("button", { class: "bp-edit", title: "조건·건너뛰기 수정", onclick: () => editBreak(b) }, "✎") : null,
      el("button", { class: "bp-del", title: "삭제",
        onclick: () => api.dbgBreak({ op: "delete", number: b.number }).then(C.dbgRefresh) }, "×"));
    body.append(row);
  }
}

const yesNo = [{ value: "", text: "아니오" }, { value: "1", text: "예" }];

export async function addBreak(defaultLocation = "") {
  const r = await C.modal({ title: "중단점 추가", okText: "추가", fields: [
    { name: "location", label: "위치 (함수·파일:줄·*주소)", value: defaultLocation, placeholder: "예: main, check, file.c:42, *0x1189" },
    { name: "condition", label: "조건 (선택) — 참일 때만 멈춤", placeholder: "예: i == 3 && ptr != 0" },
    { name: "ignore", label: "처음 N 번은 건너뛰기 (선택)", placeholder: "0" },
    { name: "temporary", label: "한 번만 멈추고 삭제", type: "select", options: yesNo, value: "" },
  ] });
  if (!r || !r.location.trim()) return;
  try {
    await api.dbgBreak({ op: "add", location: r.location.trim(), condition: r.condition.trim() || null,
      ignore: r.ignore.trim() || null, temporary: !!r.temporary });
    await C.dbgRefresh();
  } catch (err) { C.toast("중단점 실패: " + err.message, "err"); }
}

export async function editBreak(b) {
  const r = await C.modal({ title: `중단점 #${b.number} 수정 — ${breakLabel(b)}`, okText: "저장", fields: [
    { name: "condition", label: "조건 — 비우면 항상 멈춤", value: b.cond || "" },
    { name: "ignore", label: "처음 N 번은 건너뛰기", value: b.ignore || "0" },
  ] });
  if (!r) return;
  try {
    await api.dbgBreak({ op: "edit", number: b.number, condition: r.condition.trim(), ignore: r.ignore.trim() || "0" });
    await C.dbgRefresh();
  } catch (err) { C.toast("수정 실패: " + err.message, "err"); }
}

export async function addLog(defaultLocation = "") {
  const r = await C.modal({ title: "로그포인트 추가 — 멈추지 않고 출력", okText: "추가", fields: [
    { name: "location", label: "위치", value: defaultLocation, placeholder: "예: work, file.c:42, *0x1189" },
    { name: "template", label: "메시지 (printf 형식)", placeholder: "예: i=%d name=%s" },
    { name: "args", label: "인자 식 (쉼표로 구분)", placeholder: "예: i, name" },
    { name: "condition", label: "조건 (선택)", placeholder: "예: i > 5" },
  ] });
  if (!r || !r.location.trim() || !r.template) return;
  try {
    await api.dbgBreak({ op: "log", location: r.location.trim(), template: r.template, args: splitArgs(r.args),
      condition: r.condition.trim() || null });
    await C.dbgRefresh();
    C.toast("로그포인트를 달았습니다. 출력은 하단 ‘디버그 콘솔’에 나옵니다.", "ok");
  } catch (err) { C.toast("로그포인트 실패: " + err.message, "err"); }
}

export async function addWatch(defaultExpr = "") {
  const r = await C.modal({ title: "워치포인트 추가", okText: "추가", fields: [
    { name: "expr", label: "감시할 식 (변수·*주소·구조체 필드)", value: defaultExpr, placeholder: "예: counter, *(int *)0x601040, buf[2]" },
    { name: "mode", label: "언제 멈출까", type: "select", value: "write", options: [
      { value: "write", text: "값이 바뀔 때" }, { value: "read", text: "읽힐 때" }, { value: "access", text: "읽히거나 바뀔 때" }] },
    { name: "condition", label: "조건 (선택)", placeholder: "예: counter > 20" },
    { name: "at_address", label: "그 변수가 있는 ‘주소’를 감시 (지역 변수가 사라져도 유지)", type: "select", options: yesNo, value: "" },
    { name: "software", label: "소프트웨어 감시 (하드웨어를 못 쓸 때, 매우 느림)", type: "select", options: yesNo, value: "" },
  ] });
  if (!r || !r.expr.trim()) return;
  try {
    await api.dbgBreak({ op: "watch", expr: r.expr.trim(), mode: r.mode, condition: r.condition.trim() || null,
      at_address: !!r.at_address, software: !!r.software });
    await C.dbgRefresh();
  } catch (err) { C.toast("워치포인트 실패: " + err.message, "err"); }
}

// ---- 정지 이유 안내 -------------------------------------------------------------
export function stopSummary(st) {
  const s = st && st.stop;
  if (!s || st.status !== "stopped") return null;
  const w = s.watch, v = s.value || {};
  if (w) {
    const lab = { watch: "값이 바뀜", rwatch: "읽힘", awatch: "접근됨" }[w.kind] || "감시";
    let t = `워치포인트 #${w.number} ${w.exp} — ${lab}`;
    if (v.old !== undefined || v.new !== undefined) t += `: ${v.old ?? "?"} → ${v.new ?? "?"}`;
    else if (v.value !== undefined) t += `: ${v.value}`;
    return { kind: "watch", text: t };
  }
  switch (s.reason) {
    case "breakpoint-hit": return { kind: "bp", text: `중단점 #${s.bkptno}에서 멈춤` };
    case "end-stepping-range": return null;
    case "function-finished": return { kind: "info", text: "함수가 끝나 돌아옴" };
    case "watchpoint-scope": return { kind: "warn", text: "감시하던 변수의 범위를 벗어나 워치포인트가 해제됨" };
    case "no-history": return { kind: "warn", text: "기록의 끝에 도달했습니다 (더 이상 되감을 수 없음/재생할 기록이 없음)" };
    case "signal-received": return { kind: "warn", text: `시그널 ${s.signal || ""} 수신` };
    case "remote-connected": return { kind: "info", text: "원격 대상에 연결됨 — 대상이 시작 지점에서 멈춰 있습니다" };
    default: return null;
  }
}

// ---- 오른쪽 패널(탭) ------------------------------------------------------------
export function mountSide(right, buildStatePane) {
  const { el } = C;
  const tabs = el("div", { class: "dbgx-tabs" });
  const pane = el("div", { class: "dbgx-pane" });
  const names = [["state", "상태"], ["stack", "스택"], ["memory", "메모리"], ["watch", "감시식"]];
  const paint = () => {
    tabs.innerHTML = "";
    for (const [k, label] of names) {
      tabs.append(el("button", { class: "dbgx-tab" + (X.tab === k ? " active" : ""), onclick: () => { X.tab = k; paint(); } }, label));
    }
    pane.innerHTML = "";
    if (X.tab === "state") pane.append(buildStatePane());
    else if (X.tab === "stack") renderStack(pane);
    else if (X.tab === "memory") renderMemory(pane);
    else renderWatch(pane);
  };
  right.append(tabs, pane);
  paint();
}

function needStopped(pane, what) {
  const st = C.S.dbg.state || {};
  if (st.status === "stopped") return false;
  pane.append(C.el("div", { class: "empty-list", style: "padding:10px 12px" },
    st.status === "exited" ? "프로그램이 종료되었습니다." : `${what}은(는) 프로그램이 멈춰 있을 때만 볼 수 있습니다.`));
  return true;
}

// ---- 스택 탭 -------------------------------------------------------------------
async function selectFrame(level) {
  try { await api.dbgFrame(level); await C.dbgRefresh(); }
  catch (err) { C.toast("프레임 선택 실패: " + err.message, "err"); }
}

function renderStack(pane) {
  const { el, esc } = C;
  const d = C.S.dbg;
  if (needStopped(pane, "스택")) return;
  const sel = (d.state && d.state.selected_frame) || 0;
  pane.append(el("div", { class: "dbgws-head" }, "호출 스택 (클릭: 그 프레임으로 이동)"));
  const list = el("div", { class: "frame-list" });
  for (const f of d.stack) {
    list.append(el("div", { class: "frame" + (Number(f.level) === sel ? " sel" : ""), onclick: () => selectFrame(Number(f.level)) },
      el("span", { class: "fr-n" }, "#" + f.level),
      el("span", { class: "fr-f" }, f.func || "??"),
      el("span", { class: "fr-a" }, (f.addr || "").replace(/^0x0*/, "0x")),
      f.line ? el("span", { class: "fr-l" }, `:${f.line}`) : null));
  }
  pane.append(list);
  const head = el("div", { class: "dbgws-head" }, `스택 메모리 (프레임 #${sel}의 $sp부터)`);
  const cnt = el("select", { class: "mini-sel", onchange: (e) => { X.stackCount = +e.target.value; loadStack(box); } });
  for (const n of [16, 32, 48, 96, 192]) cnt.append(el("option", { value: n, ...(n === X.stackCount ? { selected: "" } : {}) }, `${n} 워드`));
  head.append(cnt);
  pane.append(head);
  const box = el("div", { class: "stack-dump" });
  pane.append(box);
  loadStack(box);
}

async function loadStack(box) {
  const { el } = C;
  box.innerHTML = "";
  box.append(el("div", { class: "empty-list", style: "padding:6px 12px" }, "읽는 중…"));
  let r;
  try { r = await api.dbgStackDump(X.stackCount); }
  catch (err) { box.innerHTML = ""; box.append(el("div", { class: "dbg-warn", style: "margin:6px" }, "스택을 읽지 못했습니다: " + err.message)); return; }
  box.innerHTML = "";
  if (!r.have_map) box.append(el("div", { class: "empty-list", style: "padding:4px 12px" }, "메모리 지도를 얻지 못해 주소 종류 표시는 생략합니다."));
  for (const row of r.rows) {
    const marks = (row.marks || []).map((m) => el("span", { class: "sd-mark" }, "← " + (m === "sp" ? "sp" : "fp")));
    const note = [];
    if (row.ret_to) note.push(`반환 주소 → #${row.ret_to.level} ${row.ret_to.func || ""}`);
    if (row.symbol) note.push(row.symbol);
    if (row.string) note.push(`"${row.string}"`);
    else if (row.kind !== "int" && !row.symbol && row.region) note.push(row.region);
    box.append(el("div", { class: "sd-row sd-" + row.kind + (row.ret_to ? " sd-ret" : "") },
      el("span", { class: "sd-off", title: row.addr }, "+" + row.offset.toString(16)),
      el("span", { class: "sd-val", title: row.kind === "int" ? "" : "클릭: 메모리 탭에서 이 주소 보기",
        onclick: () => { if (row.kind !== "int") gotoMemory(row.value); } }, row.value),
      el("span", { class: "sd-note" }, note.join(" · ")),
      ...marks));
  }
}

// ---- 메모리 탭 -----------------------------------------------------------------
export function gotoMemory(addr) {
  X.memAddr = addr; X.tab = "memory";
  C.openDebuggerTab();
}

function hexToBytes(h) {
  const out = [];
  for (let i = 0; i + 1 < h.length; i += 2) out.push(parseInt(h.slice(i, i + 2), 16));
  return out;
}
const addHex = (base, n) => "0x" + (BigInt(base) + BigInt(n)).toString(16);

async function editByte(base, i, cur) {
  const v = await C.modal({ title: `메모리 수정 — ${addHex(base, i)}`, okText: "쓰기", fields: [
    { name: "v", label: "바이트 값 (16진, 여러 바이트도 가능: 90 90 90)", value: cur } ] });
  if (!v) return;
  const hex = v.v.replace(/\s+/g, "").replace(/^0x/i, "");
  if (!/^([0-9a-fA-F]{2})+$/.test(hex)) { C.toast("16진수 바이트(두 자리씩)로 입력하세요.", "err"); return; }
  try { await api.dbgWriteMem(addHex(base, i), hex); await C.dbgRefresh(); }
  catch (err) { C.toast("쓰기 실패: " + err.message, "err"); }
}

function renderMemory(pane) {
  const { el } = C;
  if (needStopped(pane, "메모리")) return;
  const bar = el("div", { class: "mem-bar" });
  const inp = el("input", { type: "text", class: "mem-addr", spellcheck: "false", value: X.memAddr,
    title: "주소 또는 식 — 예: $sp, $rip, &buf, 0x7ffe...", onkeydown: (e) => { if (e.key === "Enter") go(); } });
  const len = el("select", { class: "mini-sel", onchange: (e) => { X.memLen = +e.target.value; go(); } });
  for (const n of [64, 128, 256, 512, 1024]) len.append(el("option", { value: n, ...(n === X.memLen ? { selected: "" } : {}) }, `${n} B`));
  const reg = el("select", { class: "mini-sel mem-reg", title: "메모리 영역으로 이동", onchange: (e) => { if (e.target.value) { inp.value = e.target.value; go(); } } },
    el("option", { value: "" }, "영역…"));
  bar.append(inp, len, el("button", { class: "mini-btn", onclick: go }, "이동"), reg);
  pane.append(bar);
  const view = el("div", { class: "mem-view" });
  pane.append(view);
  if (!X.regions) {
    api.dbgMaps().then((r) => {
      X.regions = r.regions || [];
      for (const g of X.regions) {
        if (!g.perms.includes("r")) continue;
        reg.append(el("option", { value: g.start }, `${g.name || "(익명)"} ${g.start} ${g.perms}`));
      }
    }).catch(() => { X.regions = []; });
  } else {
    for (const g of X.regions) if (g.perms.includes("r")) reg.append(el("option", { value: g.start }, `${g.name || "(익명)"} ${g.start} ${g.perms}`));
  }
  function go() { X.memAddr = inp.value.trim() || "$sp"; loadMemory(view); }
  loadMemory(view);
}

async function loadMemory(view) {
  const { el } = C;
  view.innerHTML = "";
  let r;
  try { r = await api.dbgMem(X.memAddr, X.memLen); }
  catch (err) { view.append(el("div", { class: "dbg-warn", style: "margin:6px" }, "읽을 수 없습니다: " + err.message)); return; }
  const bytes = hexToBytes(r.bytes || "");
  const prev = X.memPrev && X.memPrev.addr === r.addr ? X.memPrev.bytes : null;
  X.memPrev = { addr: r.addr, bytes };
  for (let off = 0; off < bytes.length; off += 16) {
    const row = el("div", { class: "mem-row" });
    row.append(el("span", { class: "mem-a" }, addHex(r.addr, off).replace(/^0x/, "").padStart(10, "0")));
    const cells = el("span", { class: "mem-b" });
    let ascii = "";
    for (let i = 0; i < 16; i++) {
      const k = off + i;
      if (k >= bytes.length) { cells.append(el("span", { class: "mem-x" }, "  ")); continue; }
      const b = bytes[k];
      const hx2 = b.toString(16).padStart(2, "0");
      const changed = prev && prev[k] !== undefined && prev[k] !== b;
      cells.append(el("span", { class: "mem-x" + (changed ? " chg" : "") + (b === 0 ? " zero" : ""), title: "더블클릭: 수정",
        ondblclick: () => editByte(r.addr, k, hx2) }, hx2));
      ascii += b >= 32 && b < 127 ? String.fromCharCode(b) : ".";
    }
    row.append(cells, el("span", { class: "mem-s" }, ascii));
    view.append(row);
  }
}

// ---- 감시식 탭 -----------------------------------------------------------------
export async function evalWatches() {
  if (!X.watch.length) return;
  const st = C.S.dbg.state || {};
  if (st.status !== "stopped") return;
  X.watchPrev = new Map(Array.from(X.watchVals, ([k, v]) => [k, v.value]));
  await Promise.all(X.watch.map(async (expr) => {
    try { X.watchVals.set(expr, { value: (await api.dbgEval(expr)).value }); }
    catch (err) { X.watchVals.set(expr, { error: err.message }); }
  }));
}

function renderWatch(pane) {
  const { el } = C;
  pane.append(el("div", { class: "dbgws-head" }, "감시식 — 멈출 때마다 다시 계산 (더블클릭: 값 수정)"));
  const inp = el("input", { type: "text", class: "mem-addr", spellcheck: "false", placeholder: "식 입력 후 Enter — 예: total, buf[0], *ptr, $rax + 8",
    onkeydown: async (e) => {
      if (e.key !== "Enter" || !inp.value.trim()) return;
      const ex = inp.value.trim();
      if (!X.watch.includes(ex)) X.watch.push(ex);
      inp.value = "";
      await evalWatches(); renderInto();
    } });
  pane.append(el("div", { class: "mem-bar" }, inp));
  const list = el("div", { class: "loc-list" });
  pane.append(list);
  function renderInto() {
    list.innerHTML = "";
    if (!X.watch.length) list.append(el("div", { class: "empty-list" }, "추가한 식이 없습니다."));
    for (const ex of X.watch) {
      const v = X.watchVals.get(ex);
      const changed = v && v.value !== undefined && X.watchPrev.has(ex) && X.watchPrev.get(ex) !== v.value;
      list.append(el("div", { class: "loc" + (changed ? " changed" : ""), title: "더블클릭: 값 수정",
        ondblclick: async () => {
          const nv = await C.modal({ title: "값 수정 (런타임 조작)", fields: [{ name: "v", label: `${ex} =`, value: v && v.value !== undefined ? v.value : "" }] });
          if (!nv) return;
          try { await api.dbgSetVar(ex, nv.v); await C.dbgRefresh(); } catch (err) { C.toast("실패: " + err.message, "err"); }
        } },
        el("span", { class: "loc-n" }, ex),
        el("span", { class: "loc-v" + (v && v.error ? " err" : "") }, v ? (v.error ? v.error : v.value) : "…"),
        el("button", { class: "bp-del", title: "삭제", onclick: (e) => { e.stopPropagation(); X.watch = X.watch.filter((x) => x !== ex); X.watchVals.delete(ex); renderInto(); } }, "×")));
    }
  }
  const st = C.S.dbg.state || {};
  if (st.status !== "stopped") list.append(el("div", { class: "empty-list", style: "padding:6px 12px" }, "멈춰 있을 때 값이 계산됩니다."));
  else evalWatches().then(renderInto);
  renderInto();
}

// ---- 기록·역방향 실행(툴바) ------------------------------------------------------
export function reverseButtons(st, mk) {
  const { el } = C;
  const out = [];
  const stopped = st.status === "stopped";
  if (!st.recording) {
    out.push(el("button", { class: "vt-btn", title: "지금부터의 실행을 기록합니다. 기록한 구간은 거꾸로 되돌아갈 수 있습니다(느려짐)",
      disabled: stopped ? null : "", onclick: toggleRecord }, "⏺ 기록 시작"));
    return out;
  }
  out.push(el("button", { class: "vt-btn rec-on", title: "기록 중 — 클릭하면 기록을 멈추고 지웁니다", onclick: toggleRecord }, "⏺ 기록 중"));
  out.push(mk("⏪", "거꾸로 계속 (직전 중단점/기록 처음까지)", "reverse-continue", !stopped));
  out.push(mk("◀↓", "거꾸로 한 줄 (안으로)", "reverse-step", !stopped));
  out.push(mk("◀⤼", "거꾸로 한 줄 (넘어)", "reverse-next", !stopped));
  out.push(mk("◀»", "거꾸로 한 명령", "reverse-stepi", !stopped));
  out.push(mk("◀⤴", "거꾸로 함수 시작까지", "reverse-finish", !stopped));
  return out;
}

async function toggleRecord() {
  const st = C.S.dbg.state || {};
  try {
    await api.dbgRecord(st.recording ? "stop" : "start");
    await C.dbgRefresh();
    C.toast(st.recording ? "기록을 멈췄습니다." : "기록을 시작했습니다. 이제 거꾸로 실행할 수 있습니다.", "ok");
  } catch (err) { C.toast("기록 실패: " + err.message, "err"); }
}

// 디스어셈블리에서 ‘여기까지 실행’
export async function runTo(addr) {
  try { await api.dbgExecUntil("*" + addr); await C.dbgRefresh(); }
  catch (err) { C.toast("실행 실패: " + err.message, "err"); }
}
