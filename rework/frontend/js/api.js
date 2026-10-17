// rework 프론트엔드 — 백엔드 REST 호출 래퍼
async function j(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(url, opt);
  const text = await r.text();
  let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    const msg = (data && data.detail) ? (typeof data.detail === "string" ? data.detail
                 : JSON.stringify(data.detail)) : `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return data;
}

export const api = {
  open:       (path) => j("POST", "/api/open", { path }),
  upload:     (file) => {
    const fd = new FormData(); fd.append("file", file);
    return fetch("/api/upload", { method: "POST", body: fd }).then(async r => {
      const d = await r.json(); if (!r.ok) throw new Error(d.detail || "업로드 실패"); return d;
    });
  },
  info:       () => j("GET", "/api/info"),
  sections:   () => j("GET", "/api/sections"),
  functions:  (query = "", offset = 0, limit = 300) =>
                j("GET", `/api/functions?query=${encodeURIComponent(query)}&offset=${offset}&limit=${limit}`),
  func:       (addr) => j("GET", `/api/function/${addr}`),
  decompile:  (addr) => j("GET", `/api/decompile/${addr}`),
  strings:    (query = "", offset = 0, limit = 500) =>
                j("GET", `/api/strings?query=${encodeURIComponent(query)}&offset=${offset}&limit=${limit}`),
  symbols:    (query = "", offset = 0, limit = 500) =>
                j("GET", `/api/symbols?query=${encodeURIComponent(query)}&offset=${offset}&limit=${limit}`),
  imports:    () => j("GET", "/api/imports"),
  exports:    () => j("GET", "/api/exports"),
  xrefs:      (addr) => j("GET", `/api/xrefs/${addr}`),
  hex:        (addr, length = 256) => j("GET", `/api/hex?addr=${addr}&length=${length}`),
  navigate:   (addr) => j("GET", `/api/navigate?addr=${addr}`),
  rename:     (addr, name) => j("POST", "/api/rename", { addr, name }),
  comment:    (addr, text) => j("POST", "/api/comment", { addr, text }),
  varname:    (func, slot, name) => j("POST", "/api/varname", { func, slot, name }),
  bookmark:   (addr, label = "") => j("POST", "/api/bookmark", { addr, label }),
  bookmarks:  () => j("GET", "/api/bookmarks"),
  script:     (code) => j("POST", "/api/script", { code }),
  fs:         (path = "") => j("GET", `/api/fs?path=${encodeURIComponent(path)}`),
  aiConfigGet:() => j("GET", "/api/ai/config"),
  aiConfigSet:(cfg) => j("POST", "/api/ai/config", cfg),
  aiChat:     (messages, system = "") => j("POST", "/api/ai/chat", { messages, system }),

  // 디버거 (GDB/MI)
  dbgStart:   (breakpoints = []) => j("POST", "/api/debug/start", { breakpoints }),
  dbgStop:    () => j("POST", "/api/debug/stop"),
  dbgState:   () => j("GET", "/api/debug/state"),
  dbgExec:    (action, args = "") => j("POST", "/api/debug/exec", { action, args }),
  dbgBreak:   (payload) => j("POST", "/api/debug/break", payload),
  dbgBreaks:  () => j("GET", "/api/debug/breakpoints"),
  dbgRegs:    () => j("GET", "/api/debug/registers"),
  dbgStack:   () => j("GET", "/api/debug/stack"),
  dbgLocals:  () => j("GET", "/api/debug/locals"),
  dbgDisasm:  () => j("GET", "/api/debug/disasm"),
  dbgEval:    (expr) => j("POST", "/api/debug/eval", { expr }),
  dbgSetVar:  (expr, value) => j("POST", "/api/debug/setvar", { expr, value }),
  dbgSetReg:  (name, value) => j("POST", "/api/debug/setreg", { name, value }),
  dbgMem:     (addr, count = 128) => j("GET", `/api/debug/mem?addr=${encodeURIComponent(addr)}&count=${count}`),
  dbgWriteMem:(addr, hex) => j("POST", "/api/debug/writemem", { addr, hex }),
  dbgConsole: (cmd) => j("POST", "/api/debug/console", { cmd }),
  dbgOutput:  (clear = false) => j("GET", `/api/debug/output?clear=${clear}`),
};
