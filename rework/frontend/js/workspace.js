// rework — 작업 폴더(탐색기) · 소스 편집기(Monaco)
// app.js 가 initWorkspace(ctx) 로 필요한 도우미를 넘겨 준다. Monaco 는 첫 파일을 열 때 비로소 불러온다(시작 속도에 영향 없음).
import { api } from "/js/api.js";

let C = null;                       // app.js 가 넘겨 준 도우미({S,$,$$,el,toast,setMsg,prompt1,renderTabs,renderEditor,setSideView,openFunc,openPath,showPanel})
const W = {
  root: null, recent: [],
  cache: new Map(),                 // 상대 경로 → {entries, truncated}
  expanded: new Set(), selected: "",
  picker: null,                     // 폴더 고르는 중이면 {path}
  search: null,                     // {q, case, results}
  files: new Map(),                 // 상대 경로 → {model, encoding, mtime_ns, savedAlt, viewState, readonly, language}
  shown: null,                      // 지금 편집기에 보이는 파일(상대 경로)
};
let monacoP = null;
let editor = null;
let host = null;

export const wsState = W;
export function initWorkspace(ctx) {
  C = ctx;
  window.addEventListener("beforeunload", (e) => { if (anyDirty()) { e.preventDefault(); e.returnValue = ""; } });
  window.addEventListener("focus", () => { checkExternal().catch(() => {}); });
  document.addEventListener("click", () => closeMenu());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
  refreshState().catch(() => {});
}

// ---- 상태 --------------------------------------------------------------------
async function refreshState() {
  const s = await api.wsState();
  W.root = s.root; W.recent = s.recent || [];
}
const sep = () => (W.root && W.root.path.includes("\\") && !W.root.path.includes("/") ? "\\" : "/");
const absPath = (rel) => W.root.path.replace(/[\\/]+$/, "") + sep() + (sep() === "\\" ? rel.replaceAll("/", "\\") : rel);
const baseName = (rel) => rel.split("/").pop();
const dirName = (rel) => (rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : "");
const joinRel = (dir, name) => (dir ? dir + "/" + name : name);

export function anyDirty() {
  for (const f of W.files.values()) if (isDirty(f)) return true;
  return false;
}
function isDirty(f) { return !!f && f.model.getAlternativeVersionId() !== f.savedAlt; }

// ---- 폴더 열기·닫기 -----------------------------------------------------------------
export async function openFolder(path) {
  try {
    const s = await api.wsOpen(path);
    W.root = s.root; W.recent = s.recent || [];
    W.cache.clear(); W.expanded.clear(); W.search = null; W.picker = null; W.selected = "";
    C.toast(`작업 폴더: ${W.root.name}`, "ok");
    C.setSideView("explorer");
  } catch (err) { C.toast("폴더를 열지 못했습니다: " + err.message, "err"); }
}
async function closeFolder() {
  if (anyDirty() && !confirm("저장하지 않은 변경이 있는 파일이 있습니다. 그래도 작업 폴더를 닫을까요?")) return;
  for (const rel of [...W.files.keys()]) closeFileTab(rel, true);
  const s = await api.wsClose();
  W.root = s.root; W.cache.clear(); W.expanded.clear(); W.search = null;
  renderExplorer();
}

// ---- 탐색기 사이드바 ----------------------------------------------------------------
export async function renderExplorer() {
  const { el, $ } = C;
  const body = $("#side-body"); body.innerHTML = "";
  if (!W.root || W.picker) return renderPicker(body);
  const bar = el("div", { class: "ws-bar" },
    el("span", { class: "ws-root", title: W.root.path }, "📂 " + W.root.name),
    iconBtn("＋", "새 파일", () => newEntry("", "file")),
    iconBtn("▣", "새 폴더", () => newEntry("", "dir")),
    iconBtn("⟳", "새로 고침", () => refreshTree()),
    iconBtn("🔍", "폴더에서 검색", () => { W.search = W.search ? null : { q: "", case: false, results: null }; renderExplorer(); }),
    iconBtn("📂", "다른 폴더 열기", () => { W.picker = { path: "" }; renderExplorer(); }),
    iconBtn("✕", "폴더 닫기", closeFolder));
  body.append(bar);
  if (W.search) body.append(searchBox());
  const tree = el("div", { class: "ws-tree" });
  body.append(tree);
  tree.addEventListener("contextmenu", (e) => { if (e.target === tree) { e.preventDefault(); showMenu(e, "", true); } });
  await fillTree(tree);
}

function iconBtn(label, title, onclick) {
  return C.el("button", { class: "ws-btn", title, onclick }, label);
}

async function fillTree(tree) {
  tree.innerHTML = "";
  await loadDir("");
  const rows = [];
  const walk = (rel, depth) => {
    const d = W.cache.get(rel);
    if (!d) { rows.push(C.el("div", { class: "ws-row dim", style: `padding-left:${8 + depth * 14}px` }, "불러오는 중…")); loadDir(rel).then(() => fillTree(tree)); return; }
    for (const e of d.entries) {
      const relp = joinRel(rel, e.name);
      const open = e.dir && W.expanded.has(relp);
      const f = W.files.get(relp);
      const row = C.el("div", { class: "ws-row" + (W.selected === relp ? " sel" : "") + (e.outside ? " dim" : ""), "data-rel": relp, style: `padding-left:${8 + depth * 14}px`,
                              title: e.outside ? "작업 폴더 밖을 가리키는 링크 — 열 수 없습니다" : relp },
        C.el("span", { class: "ws-ic" }, e.dir ? (open ? "▾" : "▸") : fileIcon(e.name)),
        C.el("span", { class: "ws-name" }, e.name + (e.link ? " ↪" : "")),
        f && isDirty(f) ? C.el("span", { class: "ws-dirty" }, "●") : null);
      if (!e.outside) {
        row.addEventListener("click", (ev) => { ev.stopPropagation(); W.selected = relp; e.dir ? toggleDir(relp, tree) : openFile(relp); });
        row.addEventListener("contextmenu", (ev) => { ev.preventDefault(); ev.stopPropagation(); W.selected = relp; showMenu(ev, relp, e.dir); });
      }
      rows.push(row);
      if (open) walk(relp, depth + 1);
    }
    if (d.truncated) rows.push(C.el("div", { class: "ws-row dim", style: `padding-left:${8 + depth * 14}px` }, "… 항목이 너무 많아 일부만 표시합니다"));
    if (!d.entries.length && depth === 0) rows.push(C.el("div", { class: "empty-list" }, "빈 폴더입니다. ＋ 로 새 파일을 만드세요."));
  };
  walk("", 0);
  tree.append(...rows);
}

function fileIcon(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["c", "h", "cpp", "cc", "hpp", "rs", "go"].includes(ext)) return "ⓒ";
  if (["py", "js", "ts", "sh", "ps1"].includes(ext)) return "ⓟ";
  if (["asm", "s", "nasm", "inc"].includes(ext)) return "ⓐ";
  if (["json", "yaml", "yml", "toml", "ini", "xml"].includes(ext)) return "ⓙ";
  if (["md", "txt", "log"].includes(ext)) return "ⓣ";
  return "▫";
}

async function loadDir(rel, force = false) {
  if (!force && W.cache.has(rel)) return;
  try { W.cache.set(rel, await api.wsTree(rel)); }
  catch (err) { C.toast("폴더를 읽지 못했습니다: " + err.message, "err"); W.cache.set(rel, { entries: [], truncated: false }); }
}
async function toggleDir(rel, tree) {
  if (W.expanded.has(rel)) W.expanded.delete(rel); else { W.expanded.add(rel); await loadDir(rel); }
  await fillTree(tree);
}
async function refreshTree(rels) {
  const targets = rels || ["", ...W.expanded];
  for (const r of targets) await loadDir(r, true);
  if (C.S.sideView === "explorer") await renderExplorer();
}

// ---- 폴더 고르기(작업 폴더가 없을 때) ----------------------------------------------------
async function renderPicker(body) {
  const { el } = C;
  body.innerHTML = "";
  body.append(el("div", { class: "dec-note", style: "margin:10px 12px" }, W.root ? "작업 폴더를 바꿉니다." : "소스·스크립트·메모 파일을 편집하려면 작업 폴더를 여세요. 폴더 안의 텍스트 파일만 읽고 쓸 수 있습니다."));
  const start = W.picker ? W.picker.path : "";
  let r;
  try { r = await api.fs(start); } catch (err) { C.toast("폴더 목록 실패: " + err.message, "err"); return; }
  W.picker = { path: r.path };
  const typed = el("input", { type: "text", class: "ws-path-input", placeholder: "폴더 경로를 직접 입력하고 Enter (예: /home/me/project)", spellcheck: "false" });
  typed.addEventListener("keydown", (e) => { if (e.key === "Enter" && typed.value.trim()) openFolder(typed.value.trim()); });
  body.append(el("div", { style: "padding:0 12px 6px" }, typed));
  body.append(el("button", { class: "primary", style: "margin:4px 12px;width:calc(100% - 24px)", onclick: () => openFolder(r.path) }, "✔ 이 폴더를 작업 폴더로 열기"));
  if (W.root) body.append(el("button", { class: "ws-btn wide", style: "margin:4px 12px;width:calc(100% - 24px)", onclick: () => { W.picker = null; renderExplorer(); } }, "취소"));
  if (W.recent.length && !W.root) {
    body.append(el("div", { class: "side-head" }, "최근 폴더"));
    for (const p of W.recent) body.append(el("div", { class: "list-item", title: p, onclick: () => openFolder(p) }, el("span", { class: "li-name" }, "📂 " + p)));
  }
  body.append(el("div", { class: "side-head" }, "폴더 고르기"));
  body.append(el("div", { class: "fs-path" }, r.path));
  const list = el("div", { class: "fs-list" });
  if (r.parent && r.parent !== r.path) list.append(el("div", { class: "list-item", onclick: () => { W.picker = { path: r.parent }; renderExplorer(); } }, el("span", { class: "li-name fs-up" }, ".. (상위 폴더)")));
  for (const d of r.drives || []) list.append(el("div", { class: "list-item", onclick: () => { W.picker = { path: d }; renderExplorer(); } }, el("span", { class: "li-name" }, "💽 " + d)));
  for (const e of r.entries.filter((x) => x.dir)) list.append(el("div", { class: "list-item", onclick: () => { W.picker = { path: e.path }; renderExplorer(); } }, el("span", { class: "li-name" }, "📁 " + e.name)));
  body.append(list);
}
export function pickFolder() { W.picker = W.picker || { path: "" }; C.setSideView("explorer"); }

// ---- 검색 ---------------------------------------------------------------------------
function searchBox() {
  const { el } = C;
  const wrap = el("div", { class: "ws-search" });
  const inp = el("input", { type: "text", placeholder: "폴더에서 텍스트 검색 (Enter)", value: W.search.q });
  const cs = el("input", { type: "checkbox", ...(W.search.case ? { checked: "" } : {}) });
  const results = el("div", { class: "ws-results" });
  const run = async () => {
    W.search.q = inp.value; W.search.case = cs.checked;
    if (!inp.value.trim()) { W.search.results = null; results.innerHTML = ""; return; }
    results.innerHTML = '<div class="empty-list">검색 중…</div>';
    try { W.search.results = await api.wsSearch(inp.value, cs.checked); }
    catch (err) { results.innerHTML = ""; C.toast("검색 실패: " + err.message, "err"); return; }
    paint();
  };
  const paint = () => {
    results.innerHTML = "";
    const r = W.search.results;
    if (!r) return;
    results.append(el("div", { class: "fs-path" }, `${r.results.length}건 · 파일 ${r.files}개 검사` + (r.truncated ? " · 일부만 표시" : "")));
    for (const m of r.results) {
      results.append(el("div", { class: "list-item ws-hit", title: `${m.path}:${m.line}`, onclick: () => openFile(m.path, { line: m.line, col: m.col }) },
        el("span", { class: "li-name" }, `${m.path}:${m.line}`), el("span", { class: "li-sub" }, m.text)));
    }
  };
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  cs.addEventListener("change", run);
  wrap.append(inp, el("label", { class: "ws-case" }, cs, "대소문자"), results);
  setTimeout(() => { if (!W.search.results) inp.focus(); }, 0);
  if (W.search.results) paint();
  return wrap;
}

// ---- 컨텍스트 메뉴·파일 조작 ----------------------------------------------------------------
let menuEl = null;
function closeMenu() { if (menuEl) { menuEl.remove(); menuEl = null; } }
function showMenu(ev, rel, isDir) {
  closeMenu();
  const items = [];
  const base = isDir ? rel : dirName(rel);
  items.push(["새 파일", () => newEntry(base, "file")], ["새 폴더", () => newEntry(base, "dir")]);
  if (rel) items.push(["이름 바꾸기", () => renameEntry(rel)], ["삭제", () => deleteEntry(rel, isDir)], ["상대 경로 복사", () => copyText(rel)], ["전체 경로 복사", () => copyText(absPath(rel))]);
  menuEl = C.el("div", { class: "ctx-menu", style: `left:${Math.min(ev.clientX, window.innerWidth - 180)}px;top:${Math.min(ev.clientY, window.innerHeight - 40 - items.length * 28)}px` });
  for (const [label, fn] of items) menuEl.append(C.el("div", { class: "ctx-item", onclick: (e) => { e.stopPropagation(); closeMenu(); fn(); } }, label));
  document.body.append(menuEl);
}
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); C.toast("복사했습니다", "ok"); } catch { C.toast("복사하지 못했습니다", "err"); }
}
async function newEntry(dir, kind) {
  const name = await C.prompt1(kind === "dir" ? "새 폴더" : "새 파일", `${dir || "(작업 폴더)"} 안에 만들 이름`, "");
  if (!name) return;
  const rel = joinRel(dir, name.trim());
  try {
    await api.wsOp({ op: "create", path: rel, kind });
    if (dir) W.expanded.add(dir);
    await refreshTree([dir]);
    if (kind === "file") await openFile(rel);
  } catch (err) { C.toast("만들지 못했습니다: " + err.message, "err"); }
}
async function renameEntry(rel) {
  const name = await C.prompt1("이름 바꾸기", "새 이름(같은 폴더 안)", baseName(rel));
  if (!name || name.trim() === baseName(rel)) return;
  const to = joinRel(dirName(rel), name.trim());
  try {
    await api.wsOp({ op: "rename", path: rel, to });
    // 열려 있던 탭(그 안의 파일들)을 새 이름으로 옮긴다
    for (const [old, f] of [...W.files.entries()]) {
      if (old === rel || old.startsWith(rel + "/")) {
        const nrel = to + old.slice(rel.length);
        W.files.delete(old); W.files.set(nrel, f);
        const t = C.S.tabs.find((x) => x.kind === "file" && x.path === old);
        if (t) { t.path = nrel; t.addr = "file:" + nrel; }
        if (W.shown === old) W.shown = nrel;
      }
    }
    if (W.expanded.delete(rel)) W.expanded.add(to);
    await refreshTree([dirName(rel), dirName(to)]);
    C.renderTabs();
  } catch (err) { C.toast("이름을 바꾸지 못했습니다: " + err.message, "err"); }
}
async function deleteEntry(rel, isDir) {
  if (!confirm(`‘${rel}’ ${isDir ? "폴더(안의 모든 내용 포함)" : "파일"}를 삭제할까요?\n되돌릴 수 없습니다.`)) return;
  try {
    try { await api.wsOp({ op: "delete", path: rel, recursive: false }); }
    catch (err) {
      if (!/비어 있지 않은/.test(err.message)) throw err;
      if (!confirm(`‘${rel}’ 폴더는 비어 있지 않습니다. 안의 모든 파일까지 삭제할까요?\n되돌릴 수 없습니다.`)) return;
      await api.wsOp({ op: "delete", path: rel, recursive: true });
    }
    for (const old of [...W.files.keys()]) if (old === rel || old.startsWith(rel + "/")) closeFileTab(old, true);
    W.expanded.delete(rel);
    await refreshTree([dirName(rel)]);
  } catch (err) { C.toast("삭제하지 못했습니다: " + err.message, "err"); }
}

// ---- Monaco ---------------------------------------------------------------------------
function ensureMonaco() {
  if (monacoP) return monacoP;
  monacoP = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "/vendor/monaco/vs/loader.js";
    s.onerror = () => reject(new Error("편집기(Monaco)를 불러오지 못했습니다. `python3 scripts/vendor_monaco.py` 로 받아 두세요."));
    s.onload = () => {
      window.require.config({ paths: { vs: "/vendor/monaco/vs" }, "vs/nls": { availableLanguages: { "*": "ko" } } });
      window.MonacoEnvironment = { getWorkerUrl: () => "/vendor/monaco/worker.js" };
      window.require(["vs/editor/editor.main"], () => { setup(window.monaco); resolve(window.monaco); }, reject);
    };
    document.head.append(s);
  });
  monacoP.catch(() => { monacoP = null; });
  return monacoP;
}

const X86_MNEM = "mov movzx movsx movsxd lea push pop xchg add sub adc sbb inc dec neg mul imul div idiv and or xor not shl shr sal sar rol ror rcl rcr cmp test jmp je jne jz jnz ja jae jb jbe jg jge jl jle js jns jo jno jp jnp jcxz call ret retn leave nop int int3 syscall sysenter hlt cld std rep repe repne movs movsb movsw movsd movsq stos stosb stosw stosd stosq lods scas cmps cmpsb cmpxchg xadd bswap bt bts btr btc bsf bsr setz setnz sete setne seta setb setg setl cmove cmovne cmova cmovb cmovg cmovl cdq cqo cwde cdqe cbw cwd lock endbr64 endbr32 pause cpuid rdtsc ud2 movaps movups movdqa movdqu movq movd pxor paddd psubd punpcklbw pshufd addss subss mulss divss addsd subsd mulsd divsd cvtsi2sd cvttsd2si ucomisd comisd sqrtsd fld fstp fadd fmul fdiv fxch fild fistp".split(" ");
const X86_REGS = "rax rbx rcx rdx rsi rdi rbp rsp rip r8 r9 r10 r11 r12 r13 r14 r15 eax ebx ecx edx esi edi ebp esp eip ax bx cx dx si di bp sp al bl cl dl ah bh ch dh sil dil bpl spl r8d r9d r10d r11d r12d r13d r14d r15d r8w r9w r8b r9b cs ds es fs gs ss eflags rflags xmm0 xmm1 xmm2 xmm3 xmm4 xmm5 xmm6 xmm7 ymm0 ymm1 st0 st1".split(" ");
const ASM_DIRS = "section segment global globl extern byte word dword qword db dw dd dq resb resw resd resq equ times align ascii asciz string text data bss type size include macro endm if endif else define org bits use32 use64 intel_syntax att_syntax noprefix".split(" ");
const C_KW = "auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while bool true false NULL class namespace template typename public private protected virtual override nullptr using new delete try catch throw constexpr uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t size_t ssize_t".split(" ");
const C_LIB = "printf fprintf sprintf snprintf scanf sscanf puts putchar getchar gets fgets fputs fopen fclose fread fwrite fseek ftell fflush malloc calloc realloc free memcpy memmove memset memcmp memchr strlen strcpy strncpy strcat strncat strcmp strncmp strchr strrchr strstr strtok strdup atoi atol strtol strtoul abs exit abort atexit getenv system rand srand time sleep usleep read write open close lseek mmap munmap mprotect socket connect bind listen accept send recv fork execve waitpid kill signal ptrace dlopen dlsym dlclose pthread_create pthread_join pthread_mutex_lock pthread_mutex_unlock isalpha isdigit isspace toupper tolower qsort bsearch".split(" ");
const PY_KW = "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield print len range int str bytes bytearray list dict set tuple open enumerate zip map filter sorted min max sum abs hex bin ord chr isinstance hasattr getattr struct".split(" ");
const RK_API = [["rk.functions()", "모든 함수 목록"], ["rk.disasm(addr, size=64)", "주소에서 디스어셈블"], ["rk.func(addr)", "함수 디스어셈블(화면용)"], ["rk.read(addr, size)", "가상 주소의 바이트 읽기"],
  ["rk.name(addr)", "주소의 표시 이름"], ["rk.rename(addr, name)", "이름 변경(프로젝트에 저장)"], ["rk.comment(addr, text)", "주석 달기"], ["rk.strings()", "문자열 목록"], ["rk.xrefs(addr)", "상호 참조"], ["rk.model", "정규화된 바이너리 모델"], ["rk.program", "분석 Program 객체"]];

function setup(monaco) {
  const root = getComputedStyle(document.documentElement);
  const v = (n, d) => (root.getPropertyValue(n) || d).trim();
  monaco.editor.defineTheme("rework-dark", { base: "vs-dark", inherit: true, rules: [],
    colors: { "editor.background": "#0b1522", "editor.lineHighlightBackground": "#13243a", "editorLineNumber.foreground": "#536479", "editorLineNumber.activeForeground": "#9fb3cf",
              "editor.selectionBackground": "#173a5e", "editorWidget.background": "#0f1c2e", "editorSuggestWidget.background": "#0f1c2e", "editorGutter.background": "#0b1522" } });
  monaco.editor.defineTheme("rework-light", { base: "vs", inherit: true, rules: [],
    colors: { "editor.background": "#ffffff", "editor.lineHighlightBackground": "#eaf1f9", "editor.selectionBackground": "#cfe3fb" } });
  // x86 어셈블리(Intel·AT&T)
  monaco.languages.register({ id: "x86asm", extensions: [".asm", ".s", ".S", ".nasm", ".inc", ".masm"], aliases: ["어셈블리(x86)", "asm"] });
  monaco.languages.setMonarchTokensProvider("x86asm", {
    ignoreCase: true, defaultToken: "",
    mnemonics: X86_MNEM, registers: X86_REGS, directives: ASM_DIRS,
    tokenizer: { root: [
      [/^\s*[A-Za-z_.$][\w.$@]*:/, "type.identifier"],
      [/[;#].*$/, "comment"], [/\/\/.*$/, "comment"], [/\/\*/, "comment", "@comment"],
      [/"([^"\\]|\\.)*"/, "string"], [/'([^'\\]|\\.)*'/, "string"],
      [/%[a-z][a-z0-9]*/, "variable.predefined"],
      [/\$-?(0x[0-9a-f]+|\d+)/, "number"],
      [/\b(0x[0-9a-f]+|[0-9][0-9a-f]*h|\d+)\b/, "number"],
      [/\.[a-z_]\w*/, "keyword.directive"],
      [/[a-z_.$][\w.$@]*/, { cases: { "@mnemonics": "keyword", "@registers": "variable.predefined", "@directives": "keyword.directive", "@default": "identifier" } }],
    ], comment: [[/[^*/]+/, "comment"], [/\*\//, "comment", "@pop"], [/./, "comment"]] },
  });
  monaco.languages.setLanguageConfiguration("x86asm", { comments: { lineComment: ";" }, brackets: [["[", "]"], ["(", ")"]], autoClosingPairs: [{ open: "[", close: "]" }, { open: "(", close: ")" }, { open: '"', close: '"' }], folding: { markers: { start: /^\s*;\s*#?region\b/, end: /^\s*;\s*#?endregion\b/ } } });
  const binNames = (range) => {
    const out = [];
    for (const [name, addr] of C.S.funcNameToAddr) out.push({ label: name, kind: monaco.languages.CompletionItemKind.Function, insertText: name, range, detail: `열린 바이너리의 함수 0x${Number(addr).toString(16)}`, sortText: "5" + name });
    return out;
  };
  const wordList = (lang, words, kind, range, sortPrefix) => words.map((w) => ({ label: w, kind, insertText: w, range, sortText: sortPrefix + w }));
  const provider = (lang, build) => monaco.languages.registerCompletionItemProvider(lang, {
    triggerCharacters: lang === "python" ? ["."] : [],
    provideCompletionItems(model, pos) {
      const w = model.getWordUntilPosition(pos);
      const range = { startLineNumber: pos.lineNumber, endLineNumber: pos.lineNumber, startColumn: w.startColumn, endColumn: w.endColumn };
      return { suggestions: build(model, pos, range) };
    },
  });
  const K = monaco.languages.CompletionItemKind;
  provider("x86asm", (m, p, r) => [...wordList("x86asm", X86_MNEM, K.Keyword, r, "1"), ...wordList("x86asm", X86_REGS, K.Variable, r, "2"), ...wordList("x86asm", ASM_DIRS.map((d) => d), K.Property, r, "3"), ...binNames(r)]);
  for (const lang of ["c", "cpp"]) provider(lang, (m, p, r) => [...wordList(lang, C_KW, K.Keyword, r, "1"), ...wordList(lang, C_LIB, K.Function, r, "2"), ...binNames(r),
    { label: "for", kind: K.Snippet, insertText: "for (int ${1:i} = 0; ${1:i} < ${2:n}; ${1:i}++) {\n\t$0\n}", insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, range: r, detail: "반복문", sortText: "0for" },
    { label: "main", kind: K.Snippet, insertText: "int main(int argc, char **argv) {\n\t$0\n\treturn 0;\n}", insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet, range: r, detail: "main 함수", sortText: "0main" }]);
  provider("python", (m, p, r) => {
    const line = m.getLineContent(p.lineNumber).slice(0, p.column - 1);
    if (/\brk\.\w*$/.test(line)) return RK_API.map(([sig, doc]) => ({ label: sig, filterText: sig.replace(/^rk\./, ""), kind: K.Method, insertText: sig.replace(/^rk\./, ""), range: r, detail: doc }));
    return [...wordList("python", PY_KW, K.Keyword, r, "1"), { label: "rk", kind: K.Variable, insertText: "rk", range: r, detail: "스크립트 콘솔의 분석 API", sortText: "0rk" }, ...binNames(r)];
  });
}

function themeName() { return document.body.dataset.theme === "light" ? "rework-light" : "rework-dark"; }
export function onThemeChange() { if (window.monaco) window.monaco.editor.setTheme(themeName()); }

function getEditor(monaco) {
  if (editor) return editor;
  host = C.el("div", { class: "ws-host" });
  const mono = getComputedStyle(document.documentElement).getPropertyValue("--mono").trim() || "monospace";
  editor = monaco.editor.create(host, {
    theme: themeName(), automaticLayout: true, fontFamily: mono, fontSize: 13, folding: true, showFoldingControls: "mouseover", foldingStrategy: "auto",
    minimap: { enabled: true }, wordBasedSuggestions: "allDocuments", quickSuggestions: { other: true, comments: false, strings: false },
    renderWhitespace: "selection", bracketPairColorization: { enabled: true }, scrollBeyondLastLine: false, smoothScrolling: true, tabSize: 4,
    detectIndentation: true, contextmenu: true, mouseWheelZoom: true, accessibilitySupport: "off",
  });
  editor.onDidChangeCursorPosition(updateStatus);
  editor.onDidChangeModel(updateStatus);
  editor.addAction({ id: "rework.gotoFunction", label: "열린 바이너리에서 이 이름의 함수 찾기", contextMenuGroupId: "navigation", contextMenuOrder: 1.5,
    run: (ed) => {
      const w = ed.getModel().getWordAtPosition(ed.getPosition());
      const addr = w && C.S.funcNameToAddr.get(w.word);
      if (addr == null) { C.toast(w ? `‘${w.word}’ 함수를 열린 바이너리에서 찾지 못했습니다.` : "이름 위에 커서를 놓으세요.", "err"); return; }
      C.openFunc(addr, "decompile");
    } });
  editor.addAction({ id: "rework.runScript", label: "rework 스크립트로 실행(Python)", contextMenuGroupId: "navigation", contextMenuOrder: 1.6,
    precondition: "editorLangId == python", keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter],
    run: (ed) => runAsScript(ed.getSelection().isEmpty() ? ed.getValue() : ed.getModel().getValueInRange(ed.getSelection())) });
  // Ctrl+S 는 문서 전체의 keydown 처리기(app.js)가 맡는다 — 여기에도 키를 걸면 한 번 눌러 저장이 두 번 일어나 둘째가 충돌로 보인다
  editor.addAction({ id: "rework.save", label: "저장 (Ctrl+S)", contextMenuGroupId: "9_cutcopypaste", run: () => { saveCurrent(); } });
  return editor;
}

async function runAsScript(code) {
  C.showPanel("output");
  const out = C.$("#panel-output");
  out.append(C.el("div", { class: "out-cmd" }, ">>> 편집기의 스크립트 실행"));
  try {
    const r = await api.script(code);
    if (r.output) out.append(C.el("div", {}, r.output));
    if (!r.ok && r.error) out.append(C.el("div", { class: "out-err" }, r.error));
  } catch (err) { out.append(C.el("div", { class: "out-err" }, err.message)); }
  out.scrollTop = out.scrollHeight;
}

function languageFor(monaco, name) {
  const lower = name.toLowerCase();
  const dot = lower.lastIndexOf(".");
  const ext = dot >= 0 ? lower.slice(dot) : "";
  if ([".asm", ".s", ".nasm", ".inc", ".masm"].includes(ext)) return "x86asm";
  if (lower === "dockerfile") return "dockerfile";
  if (lower === "makefile" || lower === "gnumakefile") return "shell";
  for (const l of monaco.languages.getLanguages()) {
    if ((l.extensions || []).some((e) => e.toLowerCase() === ext) || (l.filenames || []).some((f) => f.toLowerCase() === lower)) return l.id;
  }
  return "plaintext";
}
const LANG_LABEL = { x86asm: "어셈블리(x86)", plaintext: "일반 텍스트", cpp: "C++", c: "C", python: "Python", javascript: "JavaScript", typescript: "TypeScript", json: "JSON", markdown: "Markdown", shell: "Shell", rust: "Rust", go: "Go", yaml: "YAML", xml: "XML", html: "HTML", css: "CSS", java: "Java" };

// ---- 파일 탭 --------------------------------------------------------------------------------
export async function openFile(rel, pos) {
  const S = C.S;
  const tabAddr = "file:" + rel;
  let idx = S.tabs.findIndex((t) => t.addr === tabAddr);
  if (idx < 0) {
    let r;
    try { r = await api.wsFile(rel); }
    catch (err) {
      if (err.binary && confirm(`‘${rel}’ 은(는) 텍스트가 아닙니다.\n분석(디스어셈블·디컴파일)으로 열까요?`)) { await C.openPath(absPath(rel)); return; }
      if (!err.binary) C.toast("파일을 열지 못했습니다: " + err.message, "err");
      return;
    }
    let monaco;
    try { monaco = await ensureMonaco(); } catch (err) { C.toast(err.message, "err"); return; }
    const lang = languageFor(monaco, r.name);
    const model = monaco.editor.createModel(r.text, lang, monaco.Uri.from({ scheme: "file", path: "/" + rel }));
    const f = { model, encoding: r.encoding, mtime_ns: r.mtime_ns, savedAlt: model.getAlternativeVersionId(), viewState: null, readonly: r.readonly, language: lang, lastDirty: false };
    model.onDidChangeContent(() => {
      updateStatus();
      const d = isDirty(f);
      if (d !== f.lastDirty) { f.lastDirty = d; C.renderTabs(); updateDirtyDot(rel); }          // 수정됨 표시가 바뀔 때만 탭·트리를 갱신(글자마다 다시 그리지 않는다)
    });
    W.files.set(rel, f);
    S.tabs.push({ addr: tabAddr, kind: "file", path: rel });
    idx = S.tabs.length - 1;
  }
  S.active = idx;
  W.selected = rel;
  C.renderTabs();
  await C.renderEditor();
  if (pos && editor) { editor.revealLineInCenter(pos.line); editor.setPosition({ lineNumber: pos.line, column: pos.col || 1 }); editor.focus(); }
  if (S.sideView === "explorer") highlightSelected();
}

function updateDirtyDot(rel) {
  const f = W.files.get(rel);
  const row = C.$(`.ws-row[data-rel="${CSS.escape(rel)}"]`);
  if (!row) return;
  const dot = row.querySelector(".ws-dirty");
  if (isDirty(f) && !dot) row.append(C.el("span", { class: "ws-dirty" }, "●"));
  else if (!isDirty(f) && dot) dot.remove();
}
function highlightSelected() {
  C.$$(".ws-row.sel").forEach((r) => r.classList.remove("sel"));
  const row = C.$(`.ws-row[data-rel="${CSS.escape(W.selected)}"]`);
  if (row) row.classList.add("sel");
}

export async function renderFileTab(ed, tab) {
  const { el } = C;
  const f = W.files.get(tab.path);
  if (!f) { ed.append(el("div", { class: "dec-note" }, "파일 정보를 찾을 수 없습니다.")); return; }
  ed.style.display = "flex"; ed.style.flexDirection = "column"; ed.style.overflow = "hidden";
  const tb = el("div", { class: "view-toolbar" },
    el("span", { class: "vt-title", title: absPath(tab.path) }, tab.path),
    f.readonly ? el("span", { class: "dbg-badge exited" }, "읽기 전용") : null,
    el("span", { class: "vt-spacer" }),
    el("button", { class: "vt-btn", title: "디스크에서 다시 불러오기", onclick: () => reloadFromDisk(tab.path) }, "↻ 다시 불러오기"),
    el("button", { class: "vt-btn", title: "인코딩 바꿔 저장", onclick: () => pickEncoding(tab.path) }, "인코딩"),
    el("button", { class: "vt-btn primary-ish", title: "저장 (Ctrl+S)", onclick: () => saveCurrent() }, "💾 저장"));
  ed.append(tb);
  let monaco;
  try { monaco = await ensureMonaco(); } catch (err) { ed.append(el("div", { class: "dec-note" }, err.message)); return; }
  const e = getEditor(monaco);
  if (W.shown && W.files.has(W.shown) && e.getModel() === W.files.get(W.shown).model) W.files.get(W.shown).viewState = e.saveViewState();
  ed.append(host);
  e.updateOptions({ readOnly: f.readonly });
  e.setModel(f.model);
  if (f.viewState) e.restoreViewState(f.viewState);
  W.shown = tab.path;
  e.layout();
  e.focus();
  updateStatus();
}

export function clearStatus() {
  const s = C.$("#sb-ed");
  if (s) s.textContent = "";
}
function updateStatus() {
  const s = C.$("#sb-ed");
  const t = C.S.tabs[C.S.active];
  if (!s || !editor || !t || t.kind !== "file") return;
  const f = W.files.get(t.path);
  if (!f || editor.getModel() !== f.model) return;
  const p = editor.getPosition();
  const eol = f.model.getEOL() === "\r\n" ? "CRLF" : "LF";
  s.textContent = `줄 ${p.lineNumber}, 열 ${p.column} · ${LANG_LABEL[f.language] || f.language} · ${f.encoding.toUpperCase()} · ${eol}${isDirty(f) ? " · 수정됨" : ""}`;
}

export function isFileTabDirty(tab) { return tab.kind === "file" && isDirty(W.files.get(tab.path)); }

/** 탭 닫기(저장 안 한 변경은 확인). 닫았으면 true. */
export function closeFileTab(rel, force = false) {
  const S = C.S;
  const f = W.files.get(rel);
  if (f && !force && isDirty(f) && !confirm(`‘${rel}’ 에 저장하지 않은 변경이 있습니다. 버리고 닫을까요?`)) return false;
  const i = S.tabs.findIndex((t) => t.kind === "file" && t.path === rel);
  if (i >= 0) {
    S.tabs.splice(i, 1);
    if (S.active >= S.tabs.length) S.active = S.tabs.length - 1;
  }
  if (f) {
    if (editor && editor.getModel() === f.model) editor.setModel(null);
    f.model.dispose();
    W.files.delete(rel);
  }
  if (W.shown === rel) W.shown = null;
  C.renderTabs(); C.renderEditor();
  return true;
}

// ---- 저장·충돌·인코딩·외부 변경 ----------------------------------------------------------------
export async function saveCurrent() {
  const t = C.S.tabs[C.S.active];
  if (!t || t.kind !== "file") return false;
  return saveFile(t.path);
}
async function saveFile(rel, force = false) {
  const f = W.files.get(rel);
  if (!f) return false;
  if (f.readonly) { C.toast("읽기 전용 파일입니다.", "err"); return false; }
  if (f.saving) return f.saving;                           // 저장 중에 또 눌러도 한 번만(두 번째가 낡은 mtime 으로 충돌하지 않게)
  f.saving = doSave(rel, f, force).finally(() => { f.saving = null; });
  return f.saving;
}
async function doSave(rel, f, force) {
  try {
    const r = await api.wsSave({ path: rel, text: f.model.getValue(), encoding: f.encoding, expected_mtime_ns: f.mtime_ns, force });
    f.mtime_ns = r.mtime_ns; f.savedAlt = f.model.getAlternativeVersionId(); f.lastDirty = false;
    C.toast(`저장했습니다: ${baseName(rel)}`, "ok");
    C.renderTabs(); updateStatus(); updateDirtyDot(rel);
    return true;
  } catch (err) {
    if (err.conflict) {
      if (confirm(`‘${rel}’ 이(가) 열어 본 뒤 디스크에서 바뀌었습니다.\n내 편집으로 덮어쓸까요? (취소하면 저장하지 않습니다 — ‘다시 불러오기’로 디스크 내용을 볼 수 있습니다)`)) return doSave(rel, f, true);
      return false;
    }
    if (err.encoding_error) {
      if (confirm(`${f.encoding.toUpperCase()} 로 표현할 수 없는 문자가 있습니다.\nUTF-8 로 바꿔 저장할까요?`)) { f.encoding = "utf-8"; return doSave(rel, f, force); }
      return false;
    }
    C.toast("저장하지 못했습니다: " + err.message, "err");
    return false;
  }
}
async function reloadFromDisk(rel) {
  const f = W.files.get(rel);
  if (!f) return;
  if (isDirty(f) && !confirm("저장하지 않은 변경이 사라집니다. 디스크 내용으로 다시 불러올까요?")) return;
  try {
    const r = await api.wsFile(rel);
    f.model.setValue(r.text);
    f.encoding = r.encoding; f.mtime_ns = r.mtime_ns; f.readonly = r.readonly;
    f.model.pushStackElement();
    f.savedAlt = f.model.getAlternativeVersionId(); f.lastDirty = false;
    C.renderTabs(); updateStatus(); updateDirtyDot(rel);
    C.toast("디스크 내용으로 다시 불러왔습니다.", "ok");
  } catch (err) { C.toast("다시 불러오지 못했습니다: " + err.message, "err"); }
}
async function pickEncoding(rel) {
  const f = W.files.get(rel);
  if (!f) return;
  const v = await C.prompt1("저장 인코딩", "utf-8 · utf-8-sig(BOM) · cp949(한글 레거시) · utf-16", f.encoding);
  if (!v) return;
  const e = v.trim().toLowerCase();
  if (!["utf-8", "utf-8-sig", "cp949", "utf-16"].includes(e)) { C.toast("지원하지 않는 인코딩입니다.", "err"); return; }
  f.encoding = e; updateStatus();
  C.toast(`다음 저장부터 ${e.toUpperCase()} 로 저장합니다.`, "ok");
}
async function checkExternal() {
  const t = C.S.tabs[C.S.active];
  if (W.root && C.S.sideView === "explorer") refreshTree().catch(() => {});
  if (!t || t.kind !== "file") return;
  const f = W.files.get(t.path);
  if (!f) return;
  let st;
  try { st = await api.wsStat(t.path); } catch { return; }
  if (st.mtime_ns === f.mtime_ns) return;
  if (!isDirty(f)) {                                       // 내가 안 고쳤다면 조용히 새 내용으로
    const r = await api.wsFile(t.path);
    f.model.setValue(r.text); f.encoding = r.encoding; f.mtime_ns = r.mtime_ns; f.savedAlt = f.model.getAlternativeVersionId(); f.lastDirty = false;
    C.renderTabs(); updateStatus(); updateDirtyDot(t.path);
    C.toast(`${baseName(t.path)}: 디스크에서 바뀐 내용을 불러왔습니다.`, "ok");
  } else {
    C.toast(`${baseName(t.path)}: 디스크에서도 바뀌었습니다. 저장하면 충돌 확인이 뜹니다.`, "err");
  }
}

// ---- 분석 연동 ---------------------------------------------------------------------------------
/** AI 에 첨부할 현재 파일(선택 영역이 있으면 그것만). */
export function currentFileContext() {
  const t = C.S.tabs[C.S.active];
  if (!t || t.kind !== "file" || !editor) return null;
  const f = W.files.get(t.path);
  if (!f) return null;
  const sel = editor.getSelection();
  const text = sel && !sel.isEmpty() ? f.model.getValueInRange(sel) : f.model.getValue();
  return { name: t.path, text: text.slice(0, 12000), truncated: text.length > 12000 };
}

/** 의사코드를 작업 폴더의 .c 파일로 저장하고 편집기로 연다. */
export async function savePseudo(name, header, text) {
  if (!W.root) { C.toast("먼저 탐색기에서 작업 폴더를 여세요.", "err"); pickFolder(); return; }
  const safe = (name || "func").replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 80) || "func";
  let rel = `${safe}.c`;
  for (let i = 2; i < 100; i++) {
    try { await api.wsOp({ op: "create", path: rel, kind: "file" }); break; }
    catch (err) { if (!/이미 있습니다/.test(err.message)) { C.toast("파일을 만들지 못했습니다: " + err.message, "err"); return; } rel = `${safe}_${i}.c`; }
  }
  try {
    const cur = await api.wsFile(rel);
    await api.wsSave({ path: rel, text: header + text, encoding: "utf-8", expected_mtime_ns: cur.mtime_ns });
    await refreshTree([""]);
    await openFile(rel);
    C.toast(`의사코드를 ${rel} 로 저장했습니다.`, "ok");
  } catch (err) { C.toast("저장하지 못했습니다: " + err.message, "err"); }
}
export const hasWorkspace = () => !!W.root;
