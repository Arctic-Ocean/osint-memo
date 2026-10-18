"""격리 실행(backend/app/sandbox.py) 회귀 테스트 — 탈출 시도 포함.

tests/sbxprobe.c 를 컴파일해 실제 격리 안에서 돌리고, 호스트에서 본 결과와 대조한다.
'격리를 끈 대조군'으로 같은 탐침이 호스트의 것을 보는 것을 확인해, 통과가 우연이 아님을 보인다.

- 정체성: 권한 0·no-new-privs·seccomp·낮춘 uid·빈 환경(비밀 변수가 새지 않음)
- 파일시스템: 호스트 파일·홈·데이터(API 키)·소켓 불가, 루트·/dev 읽기 전용, 대상은 읽기 전용 복사본
- 네트워크: 호스트 루프백·추상 유닉스 소켓·외부 통신 차단(opt-in 하면 열림), AF_PACKET 등 차단
- 시스템 콜: 금지 목록 전부 EPERM, 중첩 user ns·mount·chroot 차단, 정상 fork·mmap 은 동작
- 자원: 프로세스 수·메모리·CPU·파일 크기·/tmp 크기·열린 파일 수·실행 시간 한도
- 정리: 세션 종료 후 프로세스(setsid·SIGTERM 무시 포함)·작업 폴더·cgroup·fd 가 남지 않음,
  서버가 SIGKILL 로 죽어도 샌드박스가 남지 않음

실행:  .venv/bin/python tests/test_sandbox.py [시나리오 이름...]   (bwrap·gdb·gcc 필요, 없으면 건너뜀)
"""
import hashlib, os, re, shutil, signal, socket, subprocess, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
want = set(a for a in sys.argv[1:] if not a.startswith("-"))

from app import sandbox as sbx                    # noqa: E402
from app.debug import DebugSession, DebugError    # noqa: E402

RESULTS, SCEN = [], []
IS_ROOT = os.geteuid() == 0
SECRET = "leak-" + os.urandom(4).hex()
os.environ["REWORK_SECRET_TEST"] = SECRET          # 격리가 환경 변수를 비우는지 보려는 미끼
PROBE = {}


def scenario(name):
    def deco(fn):
        def run():
            if want and name not in want:
                return
            try:
                RESULTS.append((name, "PASS", fn() or ""))
            except Exception as e:                # noqa: BLE001
                import traceback
                RESULTS.append((name, "FAIL", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"))
        run.__name__ = name
        SCEN.append(run)
        return fn
    return deco


def probe_exe():
    if "exe" not in PROBE:
        d = tempfile.mkdtemp(prefix="sbxprobe-")
        os.chmod(d, 0o755)
        exe = os.path.join(d, "sbxprobe")
        subprocess.check_call(["gcc", "-g", "-O0", "-o", exe, os.path.join(HERE, "sbxprobe.c")])
        PROBE["exe"] = exe
    return PROBE["exe"]


def parse(out: str) -> dict:
    kv = {}
    for ln in out.replace("\r", "").split("\n"):
        if "=" in ln and not ln.startswith(("[", "Breakpoint", "warning")):
            k, v = ln.split("=", 1)
            kv.setdefault(k, v)
    return kv


def run_probe(args: str, policy=None, wait=20.0, gdb=()):
    """격리(기본)에서 탐침을 실행해 (출력, 키-값, 최종 스냅샷)을 돌려준다."""
    s = DebugSession(probe_exe(), sandbox=policy or sbx.SandboxPolicy())
    try:
        s.start()
        for c in gdb:
            s.console_cmd(c)
        s.run(args)
        t0 = time.time()
        while time.time() - t0 < wait and s.snapshot()["status"] not in ("exited", "error"):
            time.sleep(0.05)
        time.sleep(0.15)
        out = s.target_output()
        snap = s.snapshot()
        snap["merged"] = s.merged_output()
    finally:
        s.stop()
    return out.replace("\r", ""), parse(out), snap


def host_procs(*markers):
    """호스트에서 보이는, 명령줄에 markers 중 하나가 든 프로세스 (pid, cmdline)."""
    found = []
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) == os.getpid():
            continue
        try:
            cmd = open(f"/proc/{d}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if any(m in cmd for m in markers):
            found.append((int(d), cmd[:120]))
    return found


def sbx_leftovers():
    return host_procs("/sandbox/sbxprobe", "rework-sbx", "rework-sandbox")


def wait_gone(secs=5.0):
    t0 = time.time()
    while time.time() - t0 < secs:
        if not sbx_leftovers():
            return []
        time.sleep(0.1)
    return sbx_leftovers()


def workdirs():
    return [n for n in os.listdir(tempfile.gettempdir()) if n.startswith("rework-sbx-")]


def nfds():
    return len(os.listdir("/proc/self/fd"))


# ---- 정책·설정 --------------------------------------------------------------
@scenario("policy-validation")
def _():
    P = sbx.SandboxPolicy
    assert P().mode == "on" and P().network == "none", "기본값은 격리 켬·네트워크 없음이어야 함"
    for bad in ({"mode": "maybe"}, {"network": "lan"}, {"memory_mb": 1}, {"memory_mb": True},
                {"memory_mb": "100"}, {"cpu_seconds": 0}, {"seccomp": "yes"}, {"run_as_uid": 0},
                {"wall_seconds": -1}, {"nope": 1}, {"extra_ro": "/tmp"}, {"extra_ro": [1]},
                {"extra_ro": ["relative/path"]}, {"extra_ro": ["/tmp\n/etc"]}):
        try:
            P.from_dict(bad)
            raise AssertionError(f"{bad} 가 거부돼야 함")
        except ValueError:
            pass
    sbx.protect_dir(os.path.join(ROOT, "data"))
    for p in ("/", "//", "/proc", "/proc/1/environ", "/sys/kernel", "/dev/null", "/run/docker.sock",
              "/var/run/docker.sock", "/root", "/root/.ssh", "/boot", ROOT, os.path.join(ROOT, "data"),
              os.path.join(ROOT, "data", "ai.json"), "/proc/../proc", "/tmp/../proc"):
        try:
            sbx._validate_extra_ro([p])
            raise AssertionError(f"{p} 노출이 거부돼야 함")
        except ValueError:
            pass
    d = tempfile.mkdtemp()
    sock = os.path.join(d, "s.sock")
    srv = socket.socket(socket.AF_UNIX); srv.bind(sock)
    try:
        sbx._validate_extra_ro([sock]); raise AssertionError("소켓 노출이 거부돼야 함")
    except ValueError:
        pass
    finally:
        srv.close()
    assert sbx._validate_extra_ro([d + "/"]) == [d], "끝 슬래시 정규화"
    assert P.from_dict({"nope": 1}, strict=False).mode == "on", "저장 파일의 미지 키는 무시"
    # 설정 파일: 깨진 파일은 안전한 기본값, 저장은 원자적 교체, 요청별 덮어쓰기는 저장하지 않음
    cfgf = os.path.join(d, "sandbox.json"); open(cfgf, "w").write("{broken")
    cfg = sbx.SandboxConfig(cfgf)
    assert cfg.policy.mode == "on", "깨진 설정은 격리 켬으로"
    cfg.save({"memory_mb": 512, "network": "host"})
    assert sbx.SandboxConfig(cfgf).policy.memory_mb == 512 and not os.path.exists(cfgf + ".tmp")
    eff = cfg.effective({"mode": "off"})
    assert eff.mode == "off" and cfg.policy.mode == "on", "effective 는 저장하지 않음"
    try:
        cfg.save({"memory_mb": -1}); raise AssertionError
    except ValueError:
        assert cfg.policy.memory_mb == 512, "잘못된 저장은 기존 값을 유지"
    shutil.rmtree(d, ignore_errors=True)
    return "잘못된 값 14종·노출 금지 경로 16종·소켓 거부, 깨진 설정은 격리 켬으로 복구"


@scenario("seccomp-table-matches-kernel")
def _():
    hdr = "/usr/include/x86_64-linux-gnu/asm/unistd_64.h"
    if not os.path.exists(hdr):
        return "커널 헤더 없음: 건너뜀"
    nr = {m.group(1): int(m.group(2)) for m in re.finditer(r"#define __NR_(\w+) (\d+)", open(hdr).read())}
    bad = [(k, v, nr.get(k)) for k, v in sbx._X86_64_DENY.items() if nr.get(k) != v]
    assert not bad, f"시스템 콜 번호 불일치: {bad}"
    bpf = sbx.build_seccomp_bpf()
    assert bpf and len(bpf) % 8 == 0 and len(bpf) // 8 <= 4096, "BPF 길이"
    return f"금지 {len(sbx._X86_64_DENY)}개 번호가 커널 헤더와 일치, BPF {len(bpf) // 8}명령"


# ---- 정체성 ---------------------------------------------------------------
@scenario("identity-and-env")
def _():
    out, kv, snap = run_probe("id")
    assert kv.get("uid") == "1000" and kv.get("euid") == "1000", out
    assert kv.get("capeff") == "0" * 16 and kv.get("capbnd") == "0" * 16, "권한이 남아 있음"
    assert kv.get("nonewprivs") == "1", "no-new-privs"
    assert kv.get("seccomp") == "2", "seccomp 필터 미적용"
    assert kv.get("hostname") == "rework-sandbox"
    assert int(kv["pids"]) <= 6, f"호스트 프로세스가 보임: {kv['pids']}"
    names = set(re.findall(r"^env:(\w+)=", out, re.M))
    assert names <= {"PATH", "HOME", "TERM", "LANG", "USER", "PWD", "LINES", "COLUMNS"}, names
    assert SECRET not in out, "비밀 환경 변수가 대상에게 새어 나감"
    assert "API" not in out and "KEY" not in out
    return f"uid=1000 caps=0 NNP=1 seccomp=2 pids={kv['pids']} env={sorted(names)}"


@scenario("host-sees-dropped-uid")
def _():
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        s.start(); s.run("sleep")
        t0 = time.time(); procs = []
        while time.time() - t0 < 6 and not procs:
            procs = [p for p in host_procs("/sandbox/sbxprobe") if p[1].startswith("/sandbox/sbxprobe")]
            time.sleep(0.1)
        assert procs, "대상 프로세스가 호스트에서 보이지 않음"
        st = open(f"/proc/{procs[0][0]}/status").read()
        uid = int(re.search(r"^Uid:\s+(\d+)", st, re.M).group(1))
        assert re.search(r"^NoNewPrivs:\s+1", st, re.M)
        if IS_ROOT:
            assert uid == 65534, f"호스트에서 본 uid={uid} (root 로 실행되면 탈출 시 root 권한)"
        return f"호스트에서 본 uid={uid}"
    finally:
        s.stop()


# ---- 파일시스템 ----------------------------------------------------------
@scenario("filesystem-isolation")
def _():
    readme = os.path.join(ROOT, "README.md")
    data_file = os.path.join(ROOT, "data", "sbx-secret-test.json")
    os.makedirs(os.path.dirname(data_file), exist_ok=True)
    open(data_file, "w").write('{"api_key":"sk-test"}')
    try:
        paths = [readme, data_file, "/etc/shadow", "/etc/hostname", "/etc/os-release", "/root", "/home",
                 "/var/run/docker.sock", "/run", "/sys/kernel", f"/proc/{os.getpid()}/environ", "/mnt", "/opt"]
        probes = ["/usr/rework-w", "/etc/rework-w", "/rework-w", "/dev/rework-w", "/dev/shm/rework-w", "/tmp/rework-w"]
        snapshot = lambda: {p: (os.stat(p).st_mtime_ns if os.path.exists(p) else None) for p in probes}
        before = snapshot()                       # 호스트에 같은 이름의 파일이 이미 있어도 '바뀌지 않았는지'로 본다
        out, kv, _ = run_probe("fs " + " ".join(paths))
        assert snapshot() == before, "샌드박스의 쓰기 시도가 호스트 파일시스템을 바꿈"
        leaked = [p for p in paths if kv.get(f"open[{p}]") == "OK"]
        assert not leaked, f"호스트 경로가 보임: {leaked}\n{out}"
        for w in ("/usr/rework-w", "/etc/rework-w", "/rework-w", "/sandbox/rework-w", "/dev/rework-w"):
            assert kv.get(f"open[{w}]") == "Read-only file system", (w, kv.get(f"open[{w}]"))
        assert kv.get("open[/tmp/rework-w]") == "OK" and kv.get("open[/dev/shm/rework-w]") == "OK"
        assert kv.get("open[/sandbox/sbxprobe]") != "OK", "대상 복사본이 쓰기 가능"
        top = set(kv["ls:/"].strip(",").split(","))
        assert top <= {"sandbox", "tmp", "dev", "proc", "etc", "lib64", "lib", "sbin", "bin", "usr"}, top
        etc = set(kv["ls:/etc"].strip(",").split(","))
        assert etc <= {"passwd", "group", "hosts", "nsswitch.conf", "ld.so.cache", "ld.so.conf",
                       "ld.so.conf.d", "alternatives"}, etc
        assert set(kv["ls:/sandbox"].strip(",").split(",")) == {"sbxprobe"}, kv["ls:/sandbox"]
        pw = re.findall(r"^passwd:(.*)$", out, re.M)
        assert pw == ["sandbox:x:1000:1000:sandbox:/tmp:/bin/sh"], f"호스트 사용자 목록이 보임: {pw}"
        dev = set(kv["ls:/dev"].strip(",").split(","))
        assert dev <= {"rework-tty", "ptmx", "pts", "shm", "core", "fd", "stderr", "stdout", "stdin",
                       "tty", "urandom", "random", "full", "zero", "null"}, dev
        # 대조군: 격리를 끄면 같은 탐침이 호스트 파일을 본다
        _, kv2, snap2 = run_probe("fsro " + readme + " /etc/hostname", policy=sbx.SandboxPolicy(mode="off"))
        assert kv2.get(f"open[{readme}]") == "OK", f"대조군이 호스트 파일을 못 봄: {kv2}"
        assert snap2["sandbox"]["enabled"] is False
        return f"호스트 파일 {len(paths)}종 비가시, 루트·/dev·/usr 읽기 전용, 대조군은 호스트 파일을 봄"
    finally:
        os.unlink(data_file)


@scenario("target-is-readonly-copy")
def _():
    exe = probe_exe()
    before = hashlib.sha256(open(exe, "rb").read()).hexdigest()
    s = DebugSession(exe, sandbox=sbx.SandboxPolicy())
    try:
        s.start()
        wd = [n for n in workdirs() if n.startswith(f"rework-sbx-{os.getpid()}-")]
        assert len(wd) == 1, wd
        copy = os.path.join(tempfile.gettempdir(), wd[0], "sbxprobe")
        mode = os.stat(copy).st_mode & 0o777
        assert mode == 0o555, oct(mode)
        s.run("sleep")
        time.sleep(0.6)
        # 실행 중에도 원본은 교체할 수 있고(ETXTBSY 없음) 실행 중인 복사본은 영향받지 않는다
        tmp = exe + ".new"
        open(tmp, "wb").write(b"#!/bin/sh\nexit 0\n"); os.chmod(tmp, 0o755); os.replace(tmp, exe)
        assert s.snapshot()["status"] == "running", "원본 교체가 실행 중인 대상에 영향"
    finally:
        s.stop()
        PROBE.pop("exe", None)                        # 원본을 바꿨으니 다시 컴파일
    assert not [n for n in workdirs() if n.startswith(f"rework-sbx-{os.getpid()}-")], "작업 폴더 잔존"
    assert before != hashlib.sha256(open(exe, "rb").read()).hexdigest()
    return "대상은 0555 복사본으로 실행, 원본 교체 가능, 종료 후 복사본 삭제"


# ---- 네트워크 ----------------------------------------------------------
@scenario("network-isolation")
def _():
    lsock = socket.socket(); lsock.bind(("127.0.0.1", 0)); lsock.listen(8); lsock.settimeout(0.2)
    port = lsock.getsockname()[1]
    name = "rework-sbx-test-" + os.urandom(3).hex()
    usock = socket.socket(socket.AF_UNIX); usock.bind("\0" + name); usock.listen(8); usock.settimeout(0.2)
    try:
        out, kv, _ = run_probe(f"net {port} {name}")
        assert kv["connect_host_loopback"] != "CONNECTED", "호스트 루프백에 연결됨"
        assert kv["connect_external"] != "CONNECTED", "외부 네트워크에 연결됨"
        assert kv["connect_host_abstract"] != "CONNECTED", "호스트 추상 유닉스 소켓에 연결됨"
        for k in ("af_packet", "af_vsock", "raw_icmp"):
            assert kv[k] not in ("OPENED",), (k, kv[k])
        assert kv["own_loopback"] == "OK", "샌드박스 자신의 루프백은 동작해야 함"
        for ls in (lsock, usock):
            try:
                ls.accept(); raise AssertionError("호스트 소켓이 연결을 받음")
            except socket.timeout:
                pass
        # 대조군: network=host 를 명시하면 호스트 루프백에 닿는다(= 위 차단은 네임스페이스 덕분)
        out2, kv2, _ = run_probe(f"net {port} {name}", policy=sbx.SandboxPolicy(network="host"))
        assert kv2["connect_host_loopback"] == "CONNECTED", f"대조군 실패: {kv2}"
        assert kv2["af_packet"] != "OPENED", "network=host 에서도 AF_PACKET 은 막혀야 함"
        return (f"차단: 루프백={kv['connect_host_loopback']}, 외부={kv['connect_external']}, "
                f"추상소켓={kv['connect_host_abstract']}; 대조군(network=host)은 연결됨")
    finally:
        lsock.close(); usock.close()


# ---- 시스템 콜·프로세스 ---------------------------------------------------
@scenario("syscall-denylist")
def _():
    args = " ".join(f"{k}:{v}" for k, v in sbx._X86_64_DENY.items())
    out, kv, _ = run_probe("sys " + args)
    res = {k[4:]: v for k, v in kv.items() if k.startswith("sys:")}
    assert len(res) == len(sbx._X86_64_DENY), (len(res), out[-300:])
    bad = {k: v for k, v in res.items() if v != "Operation not permitted"}
    assert not bad, f"금지 시스템 콜이 EPERM 이 아님: {bad}"
    for k in ("unshare_newuser", "unshare_newns", "unshare_newnet", "mount", "chroot", "clone_newuser"):
        assert kv[k] == "Operation not permitted", (k, kv[k])
    assert kv["clone3"] == "Function not implemented", kv["clone3"]
    assert kv["plain_fork"] == "OK" and kv["mmap_rwx"] == "OK", "정상 fork·mmap 이 막힘"
    # 대조군: seccomp 를 끄면 같은 호출의 결과가 달라진다(필터가 실제로 일한다는 증거)
    _, kv2, _ = run_probe("sys " + args, policy=sbx.SandboxPolicy(seccomp=False))
    res2 = {k[4:]: v for k, v in kv2.items() if k.startswith("sys:")}
    differ = [k for k in res if res[k] != res2.get(k)]
    assert len(differ) >= 5, f"필터 유무가 결과를 거의 바꾸지 않음: {differ}"
    if sbx.probe().disable_userns:                    # seccomp 없이도 커널이 중첩 user ns 를 막는다
        assert kv2["unshare_newuser"] != "OK" and kv2["clone_newuser"] != "OK", kv2
    return (f"금지 {len(res)}개 전부 EPERM, ns 생성·mount·chroot 차단, clone3=ENOSYS; "
            f"필터를 끄면 {len(differ)}개의 결과가 달라짐")


@scenario("host-processes-invisible")
def _():
    victim = subprocess.Popen(["sleep", "60"])             # 같은 uid(65534)가 아니어도 보이면 안 된다
    try:
        pids = [os.getpid(), os.getppid(), victim.pid]
        out, kv, _ = run_probe("kill " + " ".join(map(str, pids)))
        for pid in pids:
            assert kv[f"kill[{pid}]"] == "No such process", (pid, kv)
        assert victim.poll() is None
    finally:
        victim.kill(); victim.wait()
    return "호스트 프로세스(서버·부모·다른 프로세스)가 보이지 않아 시그널을 보낼 수 없음"


@scenario("fd-hygiene")
def _():
    keep = socket.socket(); keep.bind(("127.0.0.1", 0))      # 서버가 들고 있는 소켓·파일이 새는지 보려는 미끼
    fh = open(os.path.join(tempfile.gettempdir(), "sbx-fd-bait"), "w")
    try:
        out, kv, _ = run_probe("fds")
        targets = [v for k, v in kv.items() if k.startswith("fd:")]
        assert targets, out
        bad = [t for t in targets if not (t.startswith(("/dev/rework-tty", "/proc/")) or t.startswith("pipe:"))]
        assert not bad, f"예상 밖 fd: {bad}"
        assert not any("memfd" in t or "socket" in t or "sbx-fd-bait" in t for t in targets), targets
        return f"대상의 fd {sorted(targets)}"
    finally:
        keep.close(); fh.close(); os.unlink(fh.name)


@scenario("tty-and-stdin")
def _():
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        s.start(); s.run("tty")
        t0 = time.time()
        while time.time() - t0 < 5 and "tty1=" not in s.target_output():
            time.sleep(0.05)
        s.send_input("안녕 hello")
        t0 = time.time()
        while time.time() - t0 < 5 and "got:" not in s.target_output():
            time.sleep(0.05)
        out = s.target_output().replace("\r", "")
        kv = parse(out)
        assert kv["tty0"] == kv["tty1"] and kv["tty0"].startswith("/dev/"), kv
        assert "got:안녕 hello" in out, out
        return f"stdin/stdout 이 전용 pty({kv['tty0']}), 한글 입력 전달"
    finally:
        s.stop()


# ---- 자원 한도 ------------------------------------------------------------
@scenario("limit-processes")
def _():
    P = sbx.SandboxPolicy(max_processes=20)
    out, kv, snap = run_probe("forks 300", policy=P)
    n = int(kv["forked"])
    assert 5 <= n <= 30, f"fork 가능 수 {n} (한도 20)"
    assert kv["fork_err"] == "Resource temporarily unavailable", kv
    assert snap["status"] == "exited" and str(snap["exit_code"]) == "0", snap["status"]
    assert not wait_gone(), "fork 폭탄 이후 프로세스가 남음"
    return f"fork {n}개에서 EAGAIN (한도 20), 세션은 정상 종료·정리됨"


@scenario("limit-memory")
def _():
    P = sbx.SandboxPolicy(memory_mb=256)              # 주소공간 한도 = 256MB + 디버거 몫
    _, ok, _s = run_probe("mem 100", policy=P)
    assert ok["mem"] == "OK", ok
    _, bad, _s = run_probe("mem 3000", policy=P)
    assert bad["mem"] == "Cannot allocate memory", bad
    return "100MB 할당 성공, 3000MB 는 ENOMEM"


@scenario("limit-cpu")
def _():
    P = sbx.SandboxPolicy(cpu_seconds=2)
    t0 = time.time()
    out, kv, snap = run_probe("cpu", policy=P, wait=15)
    dt = time.time() - t0
    assert snap["status"] == "exited", f"CPU 한도를 넘긴 대상이 계속 실행 중: {snap['status']}"
    assert dt < 12, f"{dt:.1f}s"
    assert "terminated with signal" in snap["merged"], snap["merged"][-300:]
    assert not wait_gone()
    return f"CPU 2초 한도 → {dt:.1f}s 만에 시그널로 종료·정리"


@scenario("limit-files-and-tmp")
def _():
    nostop = ("handle SIGXFSZ nostop noprint pass",)
    out, kv, _s = run_probe("write 10", policy=sbx.SandboxPolicy(max_file_mb=2), gdb=nostop)
    assert kv["written_mb"] == "2" and kv["write_err"] == "File too large", kv
    out, kv, _s = run_probe("write 40", policy=sbx.SandboxPolicy(tmp_mb=8, max_file_mb=100), gdb=nostop)
    assert int(kv["written_mb"]) <= 8 and kv["write_err"] == "No space left on device", kv
    out, kv, _s = run_probe("write 40 /dev/shm/big", policy=sbx.SandboxPolicy(tmp_mb=8, max_file_mb=100), gdb=nostop)
    assert int(kv["written_mb"]) <= 8 and kv["write_err"] == "No space left on device", ("shm", kv)
    out, kv, _s = run_probe("open", policy=sbx.SandboxPolicy(max_open_files=64))
    assert int(kv["opened"]) < 64 and kv["open_err"] == "Too many open files", kv
    return "파일 2MB·/tmp 8MB·/dev/shm 8MB·열린 파일 64개 한도에서 오류"


@scenario("limit-wall-clock")
def _():
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy(wall_seconds=3))
    try:
        s.start()
        t0 = time.time()
        threading.Thread(target=lambda: s.run("sleep"), daemon=True).start()   # run() 은 최대 8초 기다린다
        time.sleep(0.8)
        assert s.snapshot()["alive"] and host_procs("/sandbox/sbxprobe"), "시작 직후 살아 있어야 함"
        while time.time() - t0 < 10 and s.snapshot()["alive"]:
            time.sleep(0.1)
        dt = time.time() - t0
        assert not s.snapshot()["alive"], "실행 시간 제한이 세션을 끝내지 않음"
        assert dt < 6, f"{dt:.1f}s"
    finally:
        s.stop()
    assert not wait_gone()
    return f"3초 제한 → 시작 {dt:.1f}s 뒤 세션 종료·정리"


# ---- extra_ro / 시작 실패 ------------------------------------------------
@scenario("extra-readonly-paths")
def _():
    d = tempfile.mkdtemp(); os.chmod(d, 0o755)
    f = os.path.join(d, "in.txt"); open(f, "w").write("input\n"); os.chmod(f, 0o644)
    out, kv, _s = run_probe(f"fsro {f} {d}/new", policy=sbx.SandboxPolicy(extra_ro=[d]))
    assert kv[f"open[{f}]"] == "OK", kv
    out, kv, _s = run_probe(f"create {d}/new {f}", policy=sbx.SandboxPolicy(extra_ro=[d]))
    assert kv[f"open[{d}/new]"] == "Read-only file system", kv                     # 새 파일을 만들 수 없다
    assert kv[f"open[{f}]"] in ("Read-only file system", "Permission denied"), kv  # 기존 파일에 쓸 수 없다
    assert not os.path.exists(os.path.join(d, "new")), "읽기 전용 노출 경로에 파일이 만들어짐"
    assert open(f).read() == "input\n"
    if IS_ROOT:                                           # 낮춘 uid 가 못 읽는 경로는 시작 전에 설명하며 거부
        d2 = tempfile.mkdtemp(); os.chmod(d2, 0o700)
        s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy(extra_ro=[d2]))
        try:
            s.start(); raise AssertionError("읽을 수 없는 경로가 거부돼야 함")
        except DebugError as e:
            assert "읽을 수 없습니다" in str(e) and "chmod" in str(e), str(e)
        assert not wait_gone() and not [n for n in workdirs() if n.startswith(f"rework-sbx-{os.getpid()}-")]
        shutil.rmtree(d2, ignore_errors=True)
    shutil.rmtree(d, ignore_errors=True)
    return "추가 노출 경로는 읽기 전용, 읽을 수 없는 경로는 원인을 설명하며 거부"


@scenario("unavailable-fails-closed")
def _():
    real = dict(sbx._PROBE_CACHE)
    sbx._PROBE_CACHE.update(t=time.monotonic(), status=sbx.SandboxStatus(False, "bwrap 이 없습니다.", "apt install bubblewrap"))
    base_fds, base_thr = nfds(), threading.active_count()
    try:
        for _ in range(5):
            s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
            try:
                s.start(); raise AssertionError("격리를 쓸 수 없으면 시작하지 않아야 함(비격리로 조용히 폴백 금지)")
            except DebugError as e:
                assert "bubblewrap" in str(e) and "격리" in str(e), str(e)
        assert nfds() == base_fds and threading.active_count() == base_thr, "실패 시작이 fd·스레드를 새게 함"
        assert not host_procs("gdb --quiet"), "gdb 가 남음"
    finally:
        sbx._PROBE_CACHE.update(real)
    return "격리 불가 → 안내와 함께 시작 거부(폴백 없음), fd·스레드·프로세스 누수 0"


# ---- 정리 ---------------------------------------------------------------
@scenario("cleanup-kills-everything")
def _():
    base_fds = nfds()
    for _ in range(6):
        s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
        s.start(); s.run("daemon"); time.sleep(0.7)
        left = host_procs("/sandbox/sbxprobe")
        s.stop()
    assert len(left) >= 3, f"탐침이 데몬을 만들지 못함: {left}"      # 정리가 일하는지 보이는 전제
    gone = wait_gone()
    assert not gone, f"세션 종료 후 프로세스가 남음(setsid·SIGTERM 무시 포함): {gone}"
    assert not [n for n in workdirs() if n.startswith(f"rework-sbx-{os.getpid()}-")], "작업 폴더 잔존"
    assert nfds() == base_fds, f"fd 누수 {nfds() - base_fds}"
    cg = [p for base in ("/sys/fs/cgroup/memory", "/sys/fs/cgroup/pids", "/sys/fs/cgroup")
          if os.path.isdir(base) for p in os.listdir(base) if p.startswith(f"rework-sbx-{os.getpid()}-")]
    assert not cg, f"cgroup 잔존: {cg}"
    return f"데몬 {len(left)}개(setsid·SIGTERM 무시)까지 6회 모두 정리, 작업 폴더·cgroup·fd 누수 0"


@scenario("server-killed-leaves-nothing")
def _():
    code = f"""
import sys, os, time, signal
sys.path.insert(0, {os.path.join(ROOT, 'backend')!r})
from app import sandbox as sbx
from app.debug import DebugSession
s = DebugSession({probe_exe()!r}, sandbox=sbx.SandboxPolicy())
s.start(); s.run("sleep"); time.sleep(0.8)
print("READY", os.getpid(), flush=True)
os.kill(os.getpid(), signal.SIGKILL)
"""
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert "READY" in p.stdout, p.stderr[-400:]
    dead = int(p.stdout.split()[1])
    gone = wait_gone(8)
    assert not gone, f"서버가 SIGKILL 로 죽은 뒤 샌드박스가 남음: {gone}"
    stale = [n for n in workdirs() if n.startswith(f"rework-sbx-{dead}-")]
    sbx._SWEPT["done"] = False                              # 다음 시작 때의 청소를 재현
    sbx._sweep_stale()
    assert not [n for n in workdirs() if n.startswith(f"rework-sbx-{dead}-")], "남은 작업 폴더가 치워지지 않음"
    return f"서버 SIGKILL 후 프로세스 0개, 남은 작업 폴더 {len(stale)}개는 다음 시작 때 청소됨"


@scenario("describe-report")
def _():
    st = sbx.probe()
    rep = sbx.describe(sbx.SandboxPolicy(), st, potential=True)
    ids = {l["id"]: l["active"] for l in rep["layers"]}
    assert rep["enabled"] and ids["ns"] and ids["fs"] and ids["net"] and ids["seccomp"], ids
    off = sbx.describe(sbx.SandboxPolicy(mode="off"))
    assert off["enabled"] is False and "비격리" in off["summary"]
    host_net = sbx.describe(sbx.SandboxPolicy(network="host"), st, potential=True)
    assert [l for l in host_net["layers"] if l["id"] == "net"][0]["active"] is False
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        info = s.start()["sandbox"]
        assert info["enabled"] and any(l["id"] == "uid" for l in info["layers"]), info
    finally:
        s.stop()
    return "격리 보고서가 실제 적용된 층을 정직하게 나열(비격리·host 네트워크 표시 포함)"


def main():
    for tool in ("gdb", "gcc", "bwrap"):
        if shutil.which(tool) is None:
            print(f"SKIP  {tool} 가 없어 격리 실행 테스트를 건너뜁니다."); return 0
    st = sbx.probe(force=True)
    if not st.available:
        print(f"SKIP  이 환경에서는 격리를 만들 수 없어 건너뜁니다: {st.reason}"); return 0
    for fn in SCEN:
        fn()
    if not RESULTS:
        print("실행할 시나리오가 없습니다."); return 1
    w = max(len(n) for n, _, _ in RESULTS)
    bad = 0
    for n, s, out in RESULTS:
        print(f"{s:4} {n:<{w}}  {out}")
        bad += s != "PASS"
    print(f"\n== {len(RESULTS) - bad}/{len(RESULTS)} 통과 ==")
    return bad


def test_sandbox_suite():
    RESULTS.clear()
    assert main() == 0


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
