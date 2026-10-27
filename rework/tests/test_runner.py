"""빌드·테스트 러너(backend/app/runner.py) 회귀·보안 시험.

실제 bubblewrap 격리 안에서 명령을 돌려 아래를 확인한다(bwrap 이 없으면 격리 시나리오는 건너뛴다).
- 컴파일·실행·진단 파싱·결과물 되가져오기
- 격리 경계: 호스트 파일·환경 변수·네트워크가 보이지 않음, 자원 한도(메모리·프로세스·출력 폭주), 시간 초과·중단 뒤 프로세스 정리
- 되가져오기 검증: 심볼릭 링크·`.git`·제어 문자 이름·큰 파일은 버리고, 실행 중에 원본이 바뀌면 덮어쓰지 않음, 삭제는 옵션일 때만
- 입력 검증, 동시 실행 거부, 격리 없는 실행에서도 환경 변수 비움

실행:  python tests/test_runner.py [시나리오 이름...]     pytest 로도 돌릴 수 있다(test_runner_suite)
"""
import os, shutil, socket, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app import runner as rn            # noqa: E402
from app import sandbox as sbx          # noqa: E402

want = set(a for a in sys.argv[1:] if not a.startswith("-"))
RESULTS, SCEN = [], []
HAVE_SBX = sbx.probe().available
HAVE_GCC = bool(shutil.which("gcc"))


def scenario(name, needs_sbx=True, needs_gcc=False):
    def deco(fn):
        def run():
            if want and name not in want:
                return
            if needs_sbx and not HAVE_SBX:
                RESULTS.append((name, "SKIP", "격리(bwrap) 없음")); return
            if needs_gcc and not HAVE_GCC:
                RESULTS.append((name, "SKIP", "gcc 없음")); return
            try:
                RESULTS.append((name, "PASS", fn() or ""))
            except Exception as e:
                RESULTS.append((name, "FAIL", f"{type(e).__name__}: {e}"))
        SCEN.append(run)
        return fn
    return deco


def ws(files: dict | None = None) -> str:
    d = tempfile.mkdtemp(prefix="rk-ws-")
    for rel, text in (files or {}).items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
    return d


def run(root, cmd, *, sandbox=True, policy=None, protected=(), timeout=60, sync_deletes=False, wait=True, runner=None):
    r = runner or rn.Runner()
    j = r.start(root, cmd, policy or sbx.SandboxPolicy(), list(protected), timeout=timeout, sandbox=sandbox, sync_deletes=sync_deletes)
    if not wait:
        return r, j
    t0 = time.time()
    while j.status == "running" and time.time() - t0 < timeout + 20:
        time.sleep(0.05)
    for _ in range(100):                                   # 마무리(되가져오기·정리)가 끝나 summary 가 채워질 때까지
        if j.ended is not None and j.summary:
            break
        time.sleep(0.05)
    return r, j


def text(j):
    return j.full_text()


@scenario("compile-run-copyback", needs_gcc=True)
def _():
    d = ws({"hello.c": '#include <stdio.h>\nint main(void){int u; puts("hi"); return 7;}\n'})
    _, j = run(d, "gcc -Wall -Wextra hello.c -o hello && ./hello")
    s = j.snapshot()
    assert s["status"] == "done" and s["exit_code"] == 7 and "hi" in s["output"], s
    assert s["sandboxed"] and any(p["file"] == "hello.c" and p["line"] == 2 and p["severity"] == "warning" for p in s["problems"]), s["problems"]
    new = [c for c in s["changes"] if c["status"] == "new"]
    assert [c["path"] for c in new] == ["hello"], s["changes"]
    assert os.access(os.path.join(d, "hello"), os.X_OK), "실행 권한이 보존돼야 함"
    shutil.rmtree(d)
    return f"종료 코드 7, 새 파일 hello, 경고 {len(s['problems'])}건"


@scenario("original-untouched-until-end")
def _():
    d = ws({"a.txt": "orig"})
    _, j = run(d, "echo changed > a.txt; echo new > b.txt")
    assert open(os.path.join(d, "a.txt")).read() == "changed\n"          # 끝난 뒤에는 변경이 반영된다
    assert open(os.path.join(d, "b.txt")).read() == "new\n"
    st = {c["path"]: c["status"] for c in j.changes}
    assert st == {"a.txt": "modified", "b.txt": "new"}, st
    shutil.rmtree(d)
    return "수정·신규 반영"


@scenario("isolation-boundary")
def _():
    os.environ["REWORK_TOKEN"] = "super-secret-token-value"
    try:
        d = ws({"x": "1"})
        _, j = run(d, "id -u; echo token=[$REWORK_TOKEN]; ls /root /home 2>&1; cat /etc/shadow 2>&1; ls / | tr '\\n' ' '; echo; pwd")
    finally:
        del os.environ["REWORK_TOKEN"]
    out = text(j)
    assert "super-secret" not in out and "token=[]" in out, out
    assert "cannot access '/root'" in out and "cannot access '/home'" in out, out
    assert "No such file" in out and "root:" not in out, out
    assert out.strip().splitlines()[-1] == "/work", out
    shutil.rmtree(d)
    return "uid 1000·환경 변수 비움·호스트 홈/shadow 안 보임·/work"


@scenario("network-blocked-and-host-opt-in")
def _():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(4)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def accept():
        srv.settimeout(0.3)
        while not stop.is_set():
            try:
                c, _ = srv.accept(); c.close()
            except OSError:
                pass
    t = threading.Thread(target=accept, daemon=True); t.start()
    try:
        cmd = f"python3 -c \"import socket; socket.create_connection(('127.0.0.1', {port}), 2); print('CONNECTED')\" 2>&1 | tail -1"
        d = ws({"x": "1"})
        _, j = run(d, cmd)
        assert "CONNECTED" not in text(j), text(j)                         # 기본: 호스트의 루프백 서비스에 닿지 못한다
        pol = sbx.SandboxPolicy(network="host")
        _, j2 = run(d, cmd, policy=pol)
        assert "CONNECTED" in text(j2), text(j2)                           # 호스트 네트워크를 명시적으로 켜면 닿는다
    finally:
        stop.set(); t.join(); srv.close()
    shutil.rmtree(d)
    return "기본 차단, network=host 일 때만 연결"


@scenario("copyback-rejects-links-and-zones")
def _():
    d = ws({"src/a.c": "x", ".git/config": "[core]\n"})
    cmd = ("ln -s /etc/passwd evil_link; ln -s ../../etc sub_link; mkdir -p .git/hooks; echo 'evil' > .git/hooks/pre-commit; "
           "mkdir -p newdir/deeper; echo ok > newdir/deeper/f.txt; printf 'x' > \"$(printf 'bad\\nname')\"; printf 'y' > ok.txt")
    _, j = run(d, cmd)
    names = {c["path"]: c["status"] for c in j.changes}
    assert not os.path.lexists(os.path.join(d, "evil_link")) and not os.path.lexists(os.path.join(d, "sub_link")), names
    assert not os.path.exists(os.path.join(d, ".git", "hooks", "pre-commit")), "`.git` 에 쓰이면 안 됨"
    assert open(os.path.join(d, ".git", "config")).read() == "[core]\n"
    assert not any("\n" in k for k in os.listdir(d)), os.listdir(d)
    assert open(os.path.join(d, "newdir", "deeper", "f.txt")).read() == "ok\n" and open(os.path.join(d, "ok.txt")).read() == "y"
    assert names.get(".git/hooks/pre-commit") == "skipped" and names.get("evil_link") == "skipped", names
    shutil.rmtree(d)
    return "링크·.git·제어 문자 이름 차단, 정상 파일은 반영"


@scenario("copyback-conflict-keeps-editor-changes")
def _():
    d = ws({"a.txt": "orig", "b.txt": "orig"})
    r, j = run(d, "sleep 1.5; echo job > a.txt; echo job > b.txt", wait=False)
    time.sleep(0.6)
    with open(os.path.join(d, "a.txt"), "w") as fh:            # 실행 중에 편집기에서 a.txt 를 먼저 고쳤다
        fh.write("edited-in-editor")
    while j.status == "running":
        time.sleep(0.05)
    time.sleep(0.3)
    st = {c["path"]: c["status"] for c in j.changes}
    assert st["a.txt"] == "conflict" and st["b.txt"] == "modified", st
    assert open(os.path.join(d, "a.txt")).read() == "edited-in-editor", "편집한 내용이 덮어써짐"
    assert open(os.path.join(d, "b.txt")).read() == "job\n"
    shutil.rmtree(d)
    return "먼저 바뀐 파일은 보존(conflict), 나머지는 반영"


@scenario("deletes-only-when-asked")
def _():
    d = ws({"keep.txt": "k", "gone.txt": "g", "edited.txt": "e"})
    _, j = run(d, "rm keep.txt gone.txt edited.txt")
    assert os.path.exists(os.path.join(d, "gone.txt")), "기본은 삭제를 되가져오지 않는다"
    r = rn.Runner()
    r2, j2 = run(d, "sleep 1; rm gone.txt edited.txt", sync_deletes=True, wait=False, runner=r)
    time.sleep(0.5)
    with open(os.path.join(d, "edited.txt"), "w") as fh:
        fh.write("changed meanwhile")
    while j2.status == "running":
        time.sleep(0.05)
    time.sleep(0.3)
    assert not os.path.exists(os.path.join(d, "gone.txt")), "옵션을 켜면 지운 파일이 반영돼야 함"
    assert os.path.exists(os.path.join(d, "edited.txt")), "그 사이 바뀐 파일은 지우면 안 됨"
    shutil.rmtree(d)
    return "기본은 보존, 옵션이면 안 바뀐 파일만 삭제"


@scenario("big-files-and-limits")
def _():
    d = ws({"a.txt": "a"})
    old = rn.MAX_FILE_BYTES
    rn.MAX_FILE_BYTES = 1024 * 1024
    try:
        with open(os.path.join(d, "huge.bin"), "wb") as fh:
            fh.write(b"\0" * (2 * 1024 * 1024))                 # 복사 단계에서 제외
        _, j = run(d, "ls -la | grep -c huge || true; head -c 3000000 /dev/zero > made.bin; echo done")
        assert "huge.bin" in " ".join(j.copy_info["skipped"]), j.copy_info["skipped"]
        assert any(c["path"] == "made.bin" and c["status"] == "skipped" for c in j.changes), j.changes
        assert not os.path.exists(os.path.join(d, "made.bin"))
    finally:
        rn.MAX_FILE_BYTES = old
    old2 = rn.MAX_COPY_FILES
    rn.MAX_COPY_FILES = 3
    try:
        d2 = ws({f"f{i}.txt": "x" for i in range(6)})
        try:
            run(d2, "true")
        except rn.RunError as e:
            assert "너무 큽니다" in str(e), e
        else:
            raise AssertionError("너무 큰 작업 폴더가 받아들여짐")
    finally:
        rn.MAX_COPY_FILES = old2
    shutil.rmtree(d); shutil.rmtree(d2)
    return "큰 파일 제외·되가져오기 거부·파일 수 초과 거부"


@scenario("zones-not-copied-in")
def _():
    prot = tempfile.mkdtemp(prefix="rk-data-")
    d = ws({"a.c": "x", ".git/HEAD": "ref", "node_modules/x/y.js": "z", "secret_zone/key.txt": "KEY"})
    link_target = tempfile.mkdtemp(prefix="rk-outside-")
    open(os.path.join(link_target, "outside.txt"), "w").write("outside")
    os.symlink(link_target, os.path.join(d, "linked"))
    _, j = run(d, "find . -type f | sort; ls -la linked 2>&1 | head -1", protected=[os.path.join(d, "secret_zone")])
    out = text(j)
    assert "./a.c" in out and ".git" not in out and "node_modules" not in out and "key.txt" not in out and "outside.txt" not in out, out
    shutil.rmtree(d); shutil.rmtree(prot); shutil.rmtree(link_target)
    return ".git·node_modules·보호 폴더·폴더 밖 링크는 복사에서 제외"


@scenario("resource-limits")
def _():
    d = ws({"x": "1"})
    pol = sbx.SandboxPolicy(memory_mb=256)
    _, j = run(d, "python3 -c \"x = bytearray(2*1024*1024*1024); print('GOT%d' % len(x))\" 2>&1 | tail -2", policy=pol)
    assert "GOT2147483648" not in text(j) and "MemoryError" in text(j), text(j)                             # 주소공간 한도로 실패해야 한다
    pol2 = sbx.SandboxPolicy(max_processes=24)
    _, j2 = run(d, "for i in $(seq 1 200); do sleep 30 & done; sleep 1; echo spawned", policy=pol2, timeout=20)
    time.sleep(0.5)
    assert not _leftover("sleep 30"), "한도를 넘긴 뒤에도 프로세스가 남음"
    # 출력 폭주
    _, j3 = run(d, "yes 0123456789abcdef | head -c 20000000")
    s3 = j3.snapshot(0)
    assert s3["status"] == "done" and len(s3["output"]) <= rn.OUTPUT_LIMIT + 16384, (s3["status"], len(s3["output"]))
    shutil.rmtree(d)
    return f"메모리 한도, 프로세스 폭주 정리, 출력 {len(s3['output']) >> 10}KB 로 제한"


def _leftover(marker: str) -> list:
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if marker in cmd and "bwrap" not in cmd and "test_runner" not in cmd:
            out.append((pid, cmd))
    return out


@scenario("timeout-and-stop-reap-processes")
def _():
    d = ws({"x": "1"})
    marker = f"sleep 9{os.getpid() % 1000:03d}"
    _, j = run(d, f"({marker} &) ; {marker}; echo never", timeout=3)
    assert j.status == "timeout" and "never" not in text(j), (j.status, text(j))
    time.sleep(0.6)
    assert not _leftover(marker), _leftover(marker)
    r, j2 = run(d, f"{marker}", wait=False)
    time.sleep(1.0)
    assert r.stop() is True
    while j2.status == "running":
        time.sleep(0.05)
    time.sleep(0.6)
    assert j2.status == "stopped" and not _leftover(marker), (j2.status, _leftover(marker))
    assert r.stop() is False
    shutil.rmtree(d)
    return "시간 초과·사용자 중단 모두 자식·고아 프로세스까지 정리"


@scenario("one-job-at-a-time-and-validation")
def _():
    d = ws({"x": "1"})
    r, j = run(d, "sleep 5", wait=False)
    try:
        r.start(d, "true", sbx.SandboxPolicy(), [])
    except rn.RunError as e:
        assert "이미 실행 중" in str(e)
    else:
        raise AssertionError("동시 실행이 허용됨")
    r.stop()
    while j.status == "running":
        time.sleep(0.05)
    time.sleep(0.4)
    for bad in ["", "   ", "a\0b", "x" * (rn.MAX_COMMAND + 1), None, 123]:
        try:
            rn.Runner().start(d, bad, sbx.SandboxPolicy(), [])
        except rn.RunError:
            continue
        raise AssertionError(f"받아들여짐: {bad!r}")
    shutil.rmtree(d)
    return "동시 실행·잘못된 명령 거부"


@scenario("direct-mode-scrubs-env", needs_sbx=False)
def _():
    os.environ["REWORK_TOKEN"] = "direct-secret-token"
    os.environ["ANTHROPIC_API_KEY"] = "sk-direct-secret"
    try:
        d = ws({"a.txt": "a"})
        _, j = run(d, "echo t=[$REWORK_TOKEN] k=[$ANTHROPIC_API_KEY]; pwd; echo hi > made.txt", sandbox=False)
    finally:
        del os.environ["REWORK_TOKEN"]; del os.environ["ANTHROPIC_API_KEY"]
    out = text(j)
    assert "secret" not in out and "t=[]" in out and "k=[]" in out, out
    assert "격리 없이" in out and not j.sandboxed and open(os.path.join(d, "made.txt")).read() == "hi\n"
    shutil.rmtree(d)
    return "격리 없는 실행도 서버 비밀은 비우고 경고를 남김"


@scenario("sandbox-off-policy-refused")
def _():
    d = ws({"x": "1"})
    try:
        rn.Runner().start(d, "true", sbx.SandboxPolicy(mode="off"), [], sandbox=True)
    except rn.RunError as e:
        assert "꺼져" in str(e)
        shutil.rmtree(d)
        return "격리 정책이 꺼져 있으면 격리 실행 요청을 거부"
    raise AssertionError("정책이 꺼져 있는데 격리 실행이 시작됨")


@scenario("detect-tasks-and-problem-parser", needs_sbx=False)
def _():
    d = ws({"Makefile": "all: app\napp: app.c\n\tgcc app.c -o app\ntest: app\n\t./app\nclean:\n\trm -f app\n", "app.c": "int main(){return 0;}\n",
            "tests/test_a.py": "def test_x(): pass\n", "run.py": "print(1)\n"})
    tasks = rn.detect_tasks(d)
    labels = {t["label"]: t["kind"] for t in tasks}
    assert labels.get("make") == "build" and labels.get("make test") == "test" and labels.get("pytest") == "test" and labels.get("실행: run.py") == "run", labels
    d2 = ws({"one.c": "int main(){return 0;}\n"})
    cmds = [t["command"] for t in rn.detect_tasks(d2)]
    assert any(c.startswith("gcc") and c.endswith("-o one") for c in cmds) and any(c.endswith("./one") for c in cmds), cmds
    pr = rn.parse_problems("make[1]: Entering directory '/work/src'\nfoo.c:3:5: error: bad\n/usr/include/x.h:1:1: error: sys\n"
                           "/work/lib/x.c:9:1: warning: w\nbad.c:(.text+0x1): undefined reference to `f'\n  File \"/work/app.py\", line 4, in <module>\n")
    got = [(p["file"], p["line"], p["severity"]) for p in pr]
    assert ("src/foo.c", 3, "error") in got and ("lib/x.c", 9, "warning") in got and ("bad.c", 1, "error") in got and ("app.py", 4, "trace") in got, got
    assert not any(p["file"].startswith("/usr") for p in pr), got
    shutil.rmtree(d); shutil.rmtree(d2)
    return f"작업 {len(tasks)}개 감지, 진단 {len(pr)}건 파싱(시스템 헤더 제외)"


def main():
    for fn in SCEN:
        fn()
    if not RESULTS:
        print("실행할 시나리오가 없습니다."); return 1
    w = max(len(n) for n, _, _ in RESULTS)
    bad = 0
    for n, st, out in RESULTS:
        print(f"{st:4} {n:<{w}}  {out}")
        bad += st == "FAIL"
    print(f"\n== {sum(1 for _, s, _ in RESULTS if s == 'PASS')}/{len(RESULTS)} 통과 (건너뜀 {sum(1 for _, s, _ in RESULTS if s == 'SKIP')}) ==")
    return bad


def test_runner_suite():
    RESULTS.clear()
    assert main() == 0


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
