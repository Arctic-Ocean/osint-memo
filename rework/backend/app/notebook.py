"""노트북(Jupyter 식 셀) 커널.

파이썬 코드 셀을 **상태가 유지되는 네임스페이스**에서 실행한다(앞 셀에서 만든 변수를 뒤 셀이 쓴다). 열어 둔 바이너리를 다루는 `rk` 객체가 미리 들어 있다.
IPython 처럼 셀의 마지막 줄이 식이면 그 값을 보여 주고(`Out`), `print` 출력은 실행하는 동안 조금씩 보인다.

- 실행은 별도 스레드에서 하고 화면은 출력 이벤트를 **폴링**한다(오래 걸리는 셀도 요청을 붙들지 않는다). 한 번에 셀 하나.
- 표준 출력은 스레드별로 갈라 받는다: 셀을 실행하는 스레드의 출력만 셀로 가고, 서버의 다른 스레드가 찍는 로그는 섞이지 않는다.
- 중단(interrupt)은 실행 스레드에 KeyboardInterrupt 를 넣는다(파이썬 코드가 도는 동안만 듣는다. C 확장 안에서 멈춘 것은 못 끊는다 — 그때는 재시작).
- 출력이 너무 많으면(1MB) 이후는 버리고 알린다.

한계(정직하게): 이 커널은 **서버 프로세스 안에서** 돈다. 스크립트 콘솔(`/api/script`)과 같은 신뢰 모델이라 격리하지 않는다 — 분석가 본인의 코드를 위한 것이다.
신뢰할 수 없는 코드를 돌리려면 빌드·테스트 러너(격리 실행)를 쓰라.
"""
from __future__ import annotations

import ast
import ctypes
import io
import sys
import threading
import time
import traceback

OUTPUT_LIMIT = 1_000_000          # 한 번 실행의 출력 글자 수 상한
MAX_CODE = 1_000_000
REPR_LIMIT = 200_000              # 결과 표현(repr)의 글자 수 상한


class KernelError(Exception):
    pass


class _Router(io.TextIOBase):
    """sys.stdout·sys.stderr 대신 들어가서, 셀을 실행하는 스레드의 출력만 그 셀로 보내고 나머지는 원래 스트림으로 보낸다."""

    def __init__(self, original, name: str):
        self._orig, self._name = original, name
        self.sinks: dict = {}

    def writable(self):
        return True

    def write(self, s):
        sink = self.sinks.get(threading.get_ident())
        if sink is not None:
            sink(self._name, s)
            return len(s)
        return self._orig.write(s)

    def flush(self):
        try:
            self._orig.flush()
        except Exception:                                  # noqa: BLE001
            pass

    def __getattr__(self, item):
        return getattr(self._orig, item)


class _StdinRouter(io.TextIOBase):
    """셀을 실행하는 스레드가 표준 입력을 읽으려 하면(`input()` 등) 서버의 stdin 에서 막히지 않고 바로 EOFError 를 낸다.
    (서버의 stdin 은 터미널·데스크톱 앱의 파이프일 수 있어, 읽다가 멈추면 중단(KeyboardInterrupt)도 듣지 못한다.) 다른 스레드는 원래 스트림을 그대로 쓴다."""

    MESSAGE = "노트북 셀에서는 input() 을 쓸 수 없습니다. 값은 코드에 직접 넣으세요."

    def __init__(self, original):
        self._orig = original
        self.blocked: set = set()

    def readable(self):
        return True

    def _check(self):
        if threading.get_ident() in self.blocked:
            raise EOFError(self.MESSAGE)

    def read(self, size=-1):
        self._check()
        return self._orig.read(size)

    def readline(self, size=-1):
        self._check()
        return self._orig.readline(size)

    def readlines(self, hint=-1):
        self._check()
        return self._orig.readlines(hint)

    def __next__(self):
        self._check()
        return next(self._orig)

    def __getattr__(self, item):
        return getattr(self._orig, item)


_INSTALL_LOCK = threading.Lock()
_ROUTERS: dict = {}


def _routers():
    """sys.stdout·stderr·stdin 에 라우터를 건다(이미 걸려 있으면 그대로). 다른 코드(테스트 도구 등)가 스트림 하나를 바꿔 놓았다면
    **그 스트림만** 다시 건다 — 라우터가 라우터를 감싸면 쓸 때마다 끝없이 되돌아간다."""
    with _INSTALL_LOCK:
        if not _ROUTERS:
            _ROUTERS["stdout"] = _Router(sys.stdout, "stdout")
            _ROUTERS["stderr"] = _Router(sys.stderr, "stderr")
            _ROUTERS["stdin"] = _StdinRouter(sys.stdin)
        for name in ("stdout", "stderr", "stdin"):
            router, cur = _ROUTERS[name], getattr(sys, name)
            if cur is not router:
                router._orig = cur
                setattr(sys, name, router)
    return _ROUTERS["stdout"], _ROUTERS["stderr"], _ROUTERS["stdin"]


def _raise_in_thread(tid: int, exc) -> bool:
    n = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), ctypes.py_object(exc))
    if n > 1:                                              # 하나보다 많은 스레드에 걸렸다면 되돌린다
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), None)
        return False
    return n == 1


def split_last_expr(code: str):
    """(앞부분 코드 객체|None, 마지막 식 코드 객체|None). 마지막 문장이 식이 아니면 (전체, None)."""
    tree = ast.parse(code, "<cell>", "exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body.pop()
        head = compile(tree, "<cell>", "exec") if tree.body else None
        expr = compile(ast.Expression(last.value), "<cell>", "eval")
        return head, expr
    return compile(tree, "<cell>", "exec"), None


class Kernel:
    def __init__(self, make_namespace):
        """make_namespace: 새 전역 네임스페이스(dict)를 만드는 함수(재시작 때마다 부른다)."""
        self._make = make_namespace
        self._lock = threading.RLock()
        self.ns = self._make()
        self.execution_count = 0
        self.events: list = []            # 현재(또는 마지막) 실행의 출력 이벤트(nbformat 의 outputs 와 같은 모양)
        self.running = False
        self.status = "idle"              # idle | running | ok | error | interrupted
        self.started = None
        self.ended = None
        self._thread = None
        self._chars = 0
        self._overflow = False
        self._sent = 0                    # 화면이 가져간 이벤트 수
        self._gen = 0                     # 실행 세대: 재시작 뒤에 낡은 스레드의 출력이 섞이지 않게

    # ---- 출력 모으기 ----
    def _emit(self, ev: dict, gen: int):
        with self._lock:
            if gen != self._gen:
                return
            self.events.append(ev)

    def _stream(self, gen: int):
        def sink(name: str, text: str):
            if not text:
                return
            with self._lock:
                if gen != self._gen:
                    return
                if self._chars >= OUTPUT_LIMIT:
                    if not self._overflow:
                        self._overflow = True
                        self.events.append({"output_type": "stream", "name": "stderr", "text": "\n[출력이 너무 많아 이후는 버립니다(1MB)]\n"})
                    return
                room = OUTPUT_LIMIT - self._chars
                text = text[:room]
                self._chars += len(text)
                last = self.events[-1] if self.events else None
                # 같은 스트림의 조각은 붙이되, 화면이 아직 못 받은 이벤트에만 붙인다(이미 보낸 이벤트를 고치면 폴링이 놓친다)
                if last and last["output_type"] == "stream" and last["name"] == name and len(self.events) - 1 >= self._sent \
                        and len(last["text"]) < 65536:
                    last["text"] += text
                else:
                    self.events.append({"output_type": "stream", "name": name, "text": text})
        return sink

    # ---- 실행 ----
    def start(self, code: str) -> dict:
        if not isinstance(code, str) or len(code) > MAX_CODE:
            raise KernelError("코드가 올바르지 않거나 너무 깁니다.")
        with self._lock:
            if self.running:
                raise KernelError("이미 실행 중인 셀이 있습니다. 끝나길 기다리거나 중단하세요.")
            self.running = True
            self.status = "running"
            self.events = []
            self._sent = 0
            self._chars = 0
            self._overflow = False
            self.execution_count += 1
            count = self.execution_count
            self.started, self.ended = time.time(), None
            gen = self._gen
        t = threading.Thread(target=self._run, args=(code, count, gen), daemon=True, name="rework-nb-exec")
        self._thread = t
        t.start()
        return {"execution_count": count}

    def _run(self, code: str, count: int, gen: int):
        out, err, inp = _routers()
        tid = threading.get_ident()
        sink = self._stream(gen)
        out.sinks[tid] = err.sinks[tid] = sink
        inp.blocked.add(tid)
        status = "ok"
        try:
            try:
                head, expr = split_last_expr(code)
            except SyntaxError:
                self._emit(_error_event(*sys.exc_info(), skip_frames=0), gen)
                status = "error"
            else:
                try:
                    if head is not None:
                        exec(head, self.ns)
                    if expr is not None:
                        val = eval(expr, self.ns)
                        if val is not None:
                            self.ns["_"] = val
                            try:
                                text = repr(val)
                            except Exception as e:                    # noqa: BLE001
                                text = f"<repr 실패: {e}>"
                            if len(text) > REPR_LIMIT:
                                text = text[:REPR_LIMIT] + f"\n… (+{len(text) - REPR_LIMIT}자 생략)"
                            self._emit({"output_type": "execute_result", "execution_count": count, "metadata": {},
                                        "data": {"text/plain": text}}, gen)
                except KeyboardInterrupt:
                    status = "interrupted"
                    self._emit({"output_type": "error", "ename": "KeyboardInterrupt", "evalue": "사용자가 중단했습니다", "traceback": []}, gen)
                except SystemExit as e:
                    status = "error"
                    self._emit({"output_type": "error", "ename": "SystemExit", "evalue": f"exit({e.code})", "traceback": []}, gen)
                except BaseException:                                 # noqa: BLE001
                    status = "error"
                    self._emit(_error_event(*sys.exc_info(), skip_frames=1), gen)
        finally:
            out.sinks.pop(tid, None)
            err.sinks.pop(tid, None)
            inp.blocked.discard(tid)
            with self._lock:
                if gen == self._gen:
                    self.running = False
                    self.status = status
                    self.ended = time.time()

    def poll(self, since: int = 0) -> dict:
        """since 번째 이후의 이벤트(각 이벤트는 한 번 보내면 바뀌지 않는다)."""
        with self._lock:
            evs = [{"index": i, **self.events[i]} for i in range(max(0, since), len(self.events))]
            self._sent = max(self._sent, len(self.events))
            return {"running": self.running, "status": self.status, "execution_count": self.execution_count,
                    "events": evs, "total": len(self.events), "elapsed": round((self.ended or time.time()) - self.started, 2) if self.started else 0}

    def interrupt(self) -> bool:
        t = self._thread
        with self._lock:
            if not self.running or t is None or not t.is_alive():
                return False
        return _raise_in_thread(t.ident, KeyboardInterrupt)

    def restart(self):
        """네임스페이스를 새로 만든다. 실행 중이던 셀은 중단을 시도하고 그 출력은 버린다."""
        t = self._thread
        with self._lock:
            self._gen += 1                       # 낡은 스레드의 출력·종료 처리는 이제 무시된다
            was_running = self.running
            self.running = False
            self.status = "idle"
            self.events = []
            self._sent = 0
            self._chars = 0
            self.execution_count = 0
            self.ns = self._make()
        if was_running and t is not None and t.is_alive():
            _raise_in_thread(t.ident, KeyboardInterrupt)

    def info(self) -> dict:
        with self._lock:
            return {"running": self.running, "status": self.status, "execution_count": self.execution_count}


def _error_event(etype, value, tb, skip_frames: int = 0) -> dict:
    """사용자 코드 부분만 남긴 오류 이벤트(커널 내부 프레임은 뺀다)."""
    frames = traceback.extract_tb(tb)
    if issubclass(etype, SyntaxError):
        keep = []                                     # 컴파일 단계의 오류: 파일·줄·^ 표시만 보여 준다(커널 내부 프레임은 의미 없다)
    else:
        keep = [f for f in frames if f.filename.startswith("<cell>")] or frames[skip_frames:]
    lines = []
    if keep:
        lines.append("Traceback (most recent call last):\n")
        lines.extend(traceback.format_list(keep))
    lines.extend(traceback.format_exception_only(etype, value))
    return {"output_type": "error", "ename": etype.__name__, "evalue": str(value)[:2000], "traceback": ["".join(lines)]}
