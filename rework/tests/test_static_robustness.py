"""정적 분석 경로(로더·분석·프로젝트 저장·API 입력 검증)의 견고성 회귀 테스트.

서버 안에서 도는(격리되지 않는) 이 경로는 낯선 바이너리를 직접 파싱하므로 '평범하게 만들어졌지만 모양이 특이한' 파일과 이상한 입력에
죽거나 멈추거나 값이 어긋나면 안 된다.
- 올바른 UTF-8 이 아닌 심볼·섹션·파일 이름(레거시 코드페이지 한글 포함)이 목록 API 를 500 으로 만들지 않는다
- 심볼이 아주 많은 파일(lief 의 통합 symbols 는 제곱 시간)과 크기 모르는 함수가 수만 개인 파일이 현실적인 시간에 열린다
- 수만 개의 명령어를 가진 함수는 의사코드를 만들지 않고 알린다
- 서로 다른 파일을 계속 열어도 열린 SQLite 연결(fd)이 늘지 않는다
- 첫 요청이 동시에 와도 xref 가 부풀지 않고, 동시 주석 저장이 예외 없이 캐시와 DB 를 일치시킨다
- 잘못된 JSON 입력(없는 키·범위 밖 주소·짝 없는 서로게이트·문자열이 아닌 경로)은 500 이 아니라 400

실행:  .venv/bin/python tests/test_static_robustness.py   (gcc·as·objcopy 필요, 없으면 건너뜀)
"""
import json, os, shutil, subprocess, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
DATA = tempfile.mkdtemp(prefix="rework-static-")
os.environ["REWORK_DATA"] = DATA

from fastapi import HTTPException            # noqa: E402
from app import main as M                    # noqa: E402
from app.loader import load                  # noqa: E402
from app.analysis import Program             # noqa: E402
from app.project import Project              # noqa: E402
from app.decompile import Decompiler        # noqa: E402

RESULTS = []
WORK = tempfile.mkdtemp(prefix="rework-static-w-")


def check(name, ok, detail=""):
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def guarded(name):
    def deco(fn):
        def run():
            try:
                fn()
            except Exception as e:           # noqa: BLE001
                import traceback
                check(name, False, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")
        run.__name__ = name
        SCEN.append(run)
        return fn
    return deco


SCEN = []


def build_c(name, src):
    c = os.path.join(WORK, name + ".c")
    out = os.path.join(WORK, name)
    open(c, "w").write(src)
    subprocess.check_call(["gcc", "-O0", "-o", out, c])
    return out


def build_asm(name, text):
    s = os.path.join(WORK, name + ".s")
    out = os.path.join(WORK, name)
    open(s, "w").write(text)
    subprocess.check_call(["gcc", "-nostdlib", "-static", "-o", out, s])
    return out


def open_via_api(path):
    return M.api_open({"path": path})


def raises400(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except HTTPException as e:
        return e.status_code == 400
    except Exception:                        # noqa: BLE001
        return False
    return False


# ---- 이름 ------------------------------------------------------------------
@guarded("non-utf8-names")
def _():
    cp949 = "한글".encode("cp949")
    exe = build_c("names", "int fn(void){return 1;} int main(void){return fn();}")
    open(os.path.join(WORK, "syms"), "wb").write(b"fn fn_" + cp949 + b"\n")
    bad = os.path.join(WORK, "names_bad")
    subprocess.check_call(["objcopy", "--redefine-syms", os.path.join(WORK, "syms"), exe, bad])
    sect = os.path.join(WORK, "names_bad2")
    open(os.path.join(WORK, "sec"), "wb").write(b"")
    subprocess.check_call(["objcopy", "--rename-section", ".comment=.cm", bad, sect])
    # 섹션 이름도 CP949 바이트로: objcopy 는 인자를 바이트 그대로 받으므로 argv 에 직접 넣는다
    subprocess.check_call([b"objcopy", b"--rename-section", b".data=.d" + cp949, sect.encode(), (sect + "3").encode()])
    open_via_api(sect + "3")
    funcs = M.api_functions(query="", offset=0, limit=100000)
    syms = M.api_symbols(query="", offset=0, limit=100000)
    syms_q = M.api_symbols(query="fn_", offset=0, limit=100)
    secs = M.api_sections()
    exps = M.api_exports()
    for o in (funcs, syms, syms_q, secs, exps, M.api_info(), M.api_imports()):
        json.dumps(o, ensure_ascii=False).encode("utf-8")          # 예외 없이 직렬화
    names = [s["name"] for s in syms["items"]]
    check("비 UTF-8 심볼 이름도 목록 API 가 정상 응답(CP949 한글은 복원)", any("한글" in n for n in names) and syms_q["total"] >= 1,
          str([n for n in names if n.startswith("fn_")][:3]))
    check("비 UTF-8 섹션 이름도 정상 응답", any("한글" in s["name"] or s["name"].startswith(".d") for s in secs), str([s["name"] for s in secs][-3:]))


@guarded("non-utf8-filename-listing")
def _():
    d = os.path.join(WORK, "fsdir")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(os.fsencode(d), b"ok.bin"), "wb").write(b"x")
    open(os.path.join(os.fsencode(d), b"bad_\xb0\xa1.bin"), "wb").write(b"x")       # CP949 '가' — 올바른 UTF-8 이 아님
    r = M.api_fs(d)
    json.dumps(r, ensure_ascii=False).encode("utf-8")
    names = [e["name"] for e in r["entries"]]
    check("비 UTF-8 파일 이름이 있어도 폴더 목록이 정상(그 항목만 건너뜀)", "ok.bin" in names and not any("bad_" in n for n in names), str(names))
    check("경로의 NUL 문자는 400", raises400(M.api_fs, "/tmp/a\0b"))


# ---- 규모 ------------------------------------------------------------------
@guarded("many-symbols-load-fast")
def _():
    n = 150000
    lines = [".globl _start", ".text", "_start: ret", ".data"] + [f"sym{i}: .byte 0" for i in range(n)]
    exe = build_asm("manysyms", "\n".join(lines) + "\n")
    t0 = time.time()
    model, raw, b = load(exe)
    dt = time.time() - t0
    check(f"심볼 {n}개 파일 로드가 현실적인 시간(제곱 시간 아님)", dt < 8 and len(model.symbols) >= n, f"{dt:.1f}s symbols={len(model.symbols)}")


@guarded("many-sizeless-functions")
def _():
    n = 30000
    lines = [".globl _start", ".text", "_start:"]
    for i in range(n):                                 # .size 가 없는 함수 n 개(함수 경계를 선형 해석으로 구해야 한다)
        lines += [f".globl f{i}", f".type f{i}, @function", f"f{i}:", "  xorl %eax, %eax", "  ret"]
    exe = build_asm("manyfuncs", "\n".join(lines) + "\n")
    open_via_api(exe)
    t0 = time.time()
    r = M.api_functions(query="", offset=0, limit=100000)
    dt = time.time() - t0
    check(f"크기 모르는 함수 {n}개의 목록이 현실적인 시간(제곱 시간 아님)", dt < 30 and r["total"] >= n, f"{dt:.1f}s total={r['total']}")
    check("limit 은 서버가 상한을 둔다", len(M.api_functions(query="", offset=0, limit=10**9)["items"]) <= M.FUNCTIONS_PAGE_MAX)
    check("음수 offset·limit 도 안전", M.api_functions(query="", offset=-5, limit=-1)["items"] == [])


@guarded("huge-function-decompile-cap")
def _():
    n = 25000
    lines = [".globl _start", ".type _start, @function", ".text", "_start:", ".rept %d" % n, "  xorl %eax, %eax", ".endr", "  ret",
             ".size _start, .-_start"]
    exe = build_asm("hugefn", "\n".join(lines) + "\n")
    open_via_api(exe)
    t0 = time.time()
    r = M.api_decompile(hex(M.STATE.prog().model.entrypoint))
    dt = time.time() - t0
    check("명령어가 수만 개인 함수는 의사코드를 만들지 않고 이유를 알린다", dt < 20 and "너무 커서" in json.dumps(r, ensure_ascii=False), f"{dt:.1f}s")


@guarded("branchy-function-decompile-time")
def _():
    n = 3500
    lines = [".globl _start", ".type _start, @function", ".text", "_start:"]
    for i in range(n):
        lines += ["  cmpl $%d, %%eax" % i, f"  jne L{i}", "  incl %ebx", f"L{i}:"]
    lines += ["  ret", ".size _start, .-_start"]
    exe = build_asm("branchy", "\n".join(lines) + "\n")
    open_via_api(exe)
    t0 = time.time()
    r = M.api_decompile(hex(M.STATE.prog().model.entrypoint))
    dt = time.time() - t0
    check(f"분기 {n}개짜리 함수의 의사코드가 현실적인 시간(기본블록 슬라이스가 제곱 시간 아님)", dt < 30 and r["lines"], f"{dt:.1f}s lines={len(r['lines'])}")


# ---- 자원·동시성 -------------------------------------------------------------
@guarded("program-cache-bounded")
def _():
    base = build_c("lru", "int main(void){return 0;}")
    raw = open(base, "rb").read()
    fds0 = len(os.listdir("/proc/self/fd"))
    n = M.PROGRAMS_KEEP * 3
    for i in range(n):
        p = os.path.join(WORK, f"lru_{i}")
        open(p, "wb").write(raw + b"\0" * (i + 1))                      # sha256 이 다른 서로 다른 파일
        os.chmod(p, 0o755)
        open_via_api(p)
    fds1 = len(os.listdir("/proc/self/fd"))
    check("서로 다른 파일을 계속 열어도 보관 수가 한도 안", len(M.STATE.programs) <= M.PROGRAMS_KEEP, f"{len(M.STATE.programs)}개")
    check("열린 fd 가 파일 수만큼 늘지 않는다", fds1 - fds0 <= M.PROGRAMS_KEEP + 4, f"{fds0}→{fds1} ({n}개 열기)")
    cur = M.STATE.prog()
    cur.project.set_name(0x1000, "still_ok")                           # 현재 파일의 DB 는 멀쩡하다
    check("현재 파일 프로젝트는 닫히지 않았다", cur.project.get_name(0x1000) == "still_ok")


@guarded("xref-build-race")
def _():
    src = "void f10(void){}\n" + "".join(f"void g{i}(void){{f10();}}\n" for i in range(3)) + "int main(void){f10();return 0;}\n"
    exe = build_c("xr", src)

    def fresh():
        model, raw, b = load(exe)
        return Program(model, raw, b, None)
    seq = fresh()
    seq.build_xrefs()
    want = {k: len(v) for k, v in seq.xref_to.items()}
    bad = []
    for round_ in range(6):
        prog = fresh()
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            prog.build_xrefs()
        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        got = {k: len(v) for k, v in prog.xref_to.items()}
        if got != want:
            bad.append(round_)
    check("동시에 처음 요청해도 xref 가 부풀지 않는다(6라운드 × 8스레드)", not bad, f"어긋난 라운드: {bad}")


@guarded("project-concurrent-writes")
def _():
    db = os.path.join(WORK, "proj.sqlite")
    pr = Project(db, "ab" * 32, meta={"name": "t"})
    errors = []

    def worker(k):
        try:
            for i in range(120):
                a = 0x1000 + (i % 7)
                pr.set_name(a, f"n{k}_{i}")
                pr.set_comment(a, f"c{k}_{i}")
                if i % 5 == 0:
                    pr.toggle_bookmark(a, "b")
        except Exception as e:               # noqa: BLE001
            errors.append(repr(e))
    ts = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    mem_n, mem_c = dict(pr._names), dict(pr._comments)
    pr._reload_cache()
    check("동시 주석 저장이 예외 없이 끝난다", not errors, str(errors[:2]))
    check("동시 저장 뒤 메모리 캐시 == DB", mem_n == pr._names and mem_c == pr._comments, f"{len(mem_n)}/{len(mem_c)}")


# ---- 입력 검증 ---------------------------------------------------------------
@guarded("api-input-validation")
def _():
    exe = build_c("val", "int main(void){return 0;}")
    open_via_api(exe)
    cases = {
        "rename: addr 키 없음": lambda: M.api_rename({}),
        "rename: 주소가 2^70": lambda: M.api_rename({"addr": 2 ** 70, "name": "x"}),
        "rename: 음수 주소": lambda: M.api_rename({"addr": -1, "name": "x"}),
        "rename: 짝 없는 서로게이트 이름": lambda: M.api_rename({"addr": 4096, "name": "\udcff"}),
        "rename: 이름이 문자열 아님": lambda: M.api_rename({"addr": 4096, "name": ["x"]}),
        "comment: 텍스트가 너무 김": lambda: M.api_comment({"addr": 4096, "text": "a" * 70000}),
        "bookmark: addr 불리언": lambda: M.api_bookmark({"addr": True}),
        "varname: func 키 없음": lambda: M.api_varname({"slot": "a", "name": "b"}),
        "open: path 가 정수": lambda: M.api_open({"path": 123}),
        "open: path 가 목록": lambda: M.api_open({"path": ["/bin/ls"]}),
        "open: path 에 NUL": lambda: M.api_open({"path": "/bin/l\0s"}),
        "open: 짝 없는 서로게이트 경로": lambda: M.api_open({"path": "/tmp/\udcff"}),
        "주소 문자열이 쓰레기": lambda: M.parse_addr("zzz"),
    }
    failed = [n for n, fn in cases.items() if not raises400(fn)]
    check(f"이상한 입력 {len(cases)}종은 모두 500 이 아니라 400", not failed, str(failed))
    r = M.api_rename({"addr": 4096, "name": "정상 이름"})
    check("정상 입력은 그대로 동작", r["name"] == "정상 이름", str(r))


def main():
    for tool in ("gcc", "objcopy"):
        if shutil.which(tool) is None:
            print(f"SKIP  {tool} 가 없어 건너뜁니다."); return 0
    try:
        for fn in SCEN:
            fn()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
        shutil.rmtree(DATA, ignore_errors=True)
    w = max(len(n) for n, _, _ in RESULTS)
    bad = 0
    for n, s, d in RESULTS:
        print(f"{s:4} {n:<{w}}  {d}")
        bad += s != "PASS"
    print(f"\n== {len(RESULTS) - bad}/{len(RESULTS)} 통과 ==")
    return bad


def test_static_robustness_suite():
    RESULTS.clear()
    assert main() == 0


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
