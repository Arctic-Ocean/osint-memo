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
import hashlib, json, os, re, shutil, signal, socket, subprocess, sys, tempfile, threading, time

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
    return [p for p in host_procs("/sandbox/sbxprobe", "--hostname rework-sandbox")
            if not p[1].startswith(("/bin/bash", "bash ", "sh -c"))]       # 이 테스트를 부르는 셸 자신은 제외


def wait_gone(secs=5.0):
    t0 = time.time()
    while time.time() - t0 < secs:
        if not sbx_leftovers():
            return []
        time.sleep(0.1)
    return sbx_leftovers()


def workdirs(owner_pid=None):
    """호스트에 남아 있는 작업 폴더. owner_pid 를 주면 그 서버가 만든 것만(폴더 안 .owner 에 서버 pid 가 있다)."""
    out = []
    for n in os.listdir(tempfile.gettempdir()):
        if not n.startswith("rework-sbx-"):
            continue
        if owner_pid is not None:
            try:
                if int(open(os.path.join(tempfile.gettempdir(), n, ".owner")).read().split()[0]) != owner_pid:
                    continue
            except (OSError, ValueError):
                continue
        out.append(n)
    return out


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
    sd = tempfile.mkdtemp()
    sock = os.path.join(sd, "s.sock")
    srv = socket.socket(socket.AF_UNIX); srv.bind(sock)
    try:
        for bad in (sock, sd):                       # 소켓 자체, 그리고 소켓이 '들어 있는' 폴더(network=none 우회 경로)
            try:
                sbx._validate_extra_ro([bad]); raise AssertionError(f"{bad} 노출이 거부돼야 함")
            except ValueError:
                pass
    finally:
        srv.close()
    os.mkfifo(os.path.join(d, "f.fifo"))
    try:
        sbx._validate_extra_ro([d]); raise AssertionError("FIFO 가 든 폴더가 거부돼야 함")
    except ValueError:
        pass
    os.unlink(os.path.join(d, "f.fifo"))
    assert sbx._validate_extra_ro([d + "/"]) == [d], "끝 슬래시 정규화"
    for p in ("/etc", "/home", "/usr", "/var", "/tmp", "/bin", "/lib"):      # 샌드박스가 정한 최소 루트를 통째로 되돌림
        try:
            sbx._validate_extra_ro([p]); raise AssertionError(f"{p} 노출이 거부돼야 함")
        except ValueError:
            pass
    assert sbx._validate_extra_ro(["/etc/hostname"]) == ["/etc/hostname"], "파일 하나는 허용"
    fake_home = tempfile.mkdtemp(); os.makedirs(os.path.join(fake_home, ".ssh")); os.makedirs(os.path.join(fake_home, "inputs"))
    old_home = os.environ.get("HOME"); os.environ["HOME"] = fake_home
    try:
        for p in (fake_home, os.path.join(fake_home, ".ssh")):
            try:
                sbx._validate_extra_ro([p]); raise AssertionError(f"{p} 노출이 거부돼야 함(자격 증명 폴더)")
            except ValueError:
                pass
        assert sbx._validate_extra_ro([os.path.join(fake_home, "inputs")]), "홈 아래 일반 폴더는 허용"
    finally:
        if old_home is None: os.environ.pop("HOME", None)
        else: os.environ["HOME"] = old_home
        shutil.rmtree(fake_home, ignore_errors=True)
    big = tempfile.mkdtemp()
    for i in range(8): open(os.path.join(big, f"f{i}"), "w").close()
    saved, sbx.WALK_MAX_ENTRIES = sbx.WALK_MAX_ENTRIES, 5
    try:
        sbx._validate_extra_ro([big]); raise AssertionError("너무 큰 폴더는 거부돼야 함")
    except ValueError:
        pass
    finally:
        sbx.WALK_MAX_ENTRIES = saved
        shutil.rmtree(big, ignore_errors=True); shutil.rmtree(sd, ignore_errors=True)
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
        wd = workdirs(os.getpid())
        assert len(wd) == 1, wd
        copy = os.path.join(tempfile.gettempdir(), wd[0], "sbxprobe")
        mode = os.stat(copy).st_mode & 0o777
        assert mode == 0o500, oct(mode)
        s.run("sleep")
        time.sleep(0.6)
        # 실행 중에도 원본은 교체할 수 있고(ETXTBSY 없음) 실행 중인 복사본은 영향받지 않는다
        tmp = exe + ".new"
        open(tmp, "wb").write(b"#!/bin/sh\nexit 0\n"); os.chmod(tmp, 0o755); os.replace(tmp, exe)
        assert s.snapshot()["status"] == "running", "원본 교체가 실행 중인 대상에 영향"
    finally:
        s.stop()
        PROBE.pop("exe", None)                        # 원본을 바꿨으니 다시 컴파일
    assert not workdirs(os.getpid()), "작업 폴더 잔존"
    assert before != hashlib.sha256(open(exe, "rb").read()).hexdigest()
    return "대상은 0500 복사본으로 실행, 원본 교체 가능, 종료 후 복사본 삭제"


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
        assert not wait_gone() and not workdirs(os.getpid())
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
    assert not workdirs(os.getpid()), "작업 폴더 잔존"
    assert nfds() == base_fds, f"fd 누수 {nfds() - base_fds}"
    cg = [p for base in ("/sys/fs/cgroup/memory", "/sys/fs/cgroup/pids", "/sys/fs/cgroup")
          if os.path.isdir(base) for p in os.listdir(base) if p.startswith(f"rework-sbx-{os.getpid()}-")]   # cgroup 이름만 pid 를 쓴다
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
    stale = workdirs(dead)
    sbx._SWEPT["done"] = False                              # 다음 시작 때의 청소를 재현
    sbx._sweep_stale()
    assert not workdirs(dead), "남은 작업 폴더가 치워지지 않음"
    return f"서버 SIGKILL 후 프로세스 0개, 남은 작업 폴더 {len(stale)}개는 다음 시작 때 청소됨"



# ---- 레드팀 지적 사항의 회귀 테스트 --------------------------------------------
@scenario("gdb-unreachable-from-target")
def _():
    """대상이 같은 uid 의 gdb·init 의 메모리·fd·ptrace·pidfd_getfd 로 호스트 MI 파이프에 거짓 보고를 쓰지 못한다."""
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        info = s.start()["sandbox"]
        guard = [l for l in info["layers"] if l["id"] == "gdbguard"][0]
        if not sbx.probe().as_pid1:
            assert not guard["active"], "--as-pid-1 이 없으면 보호 층이 미적용으로 정직하게 표시돼야 함"
            return "bwrap 이 --as-pid-1 을 지원하지 않아 건너뜀(미적용 표시 확인)"
        assert guard["active"], f"gdb 보호가 확인되지 않음: {guard}"
        threading.Thread(target=lambda: s.run("gdbprobe stay"), daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < 15 and "forged=" not in s.target_output():
            time.sleep(0.1)
        out = s.target_output().replace("\r", "")
        kv = parse(out)
        assert kv.get("forged") == "0", out
        bad = {k: v for k, v in kv.items() if k.startswith(("open_fd[", "open_mem[", "ptrace_attach[", "pidfd_getfd[",
                                                            "process_vm_", "readlink[")) and v in ("OK", "STOLEN")}
        assert not bad, f"gdb·init 에 접근할 수 있음: {bad}"
        time.sleep(1.5)
        assert s.snapshot()["status"] == "running", "거짓 *stopped 보고가 세션 상태를 바꿈"
        assert "FORGED-BY-TARGET" not in s.merged_output(), "대상이 쓴 거짓 MI 레코드가 호스트에 도착함"
        return "gdb(pid 1)가 덤프 불가 — /proc/<gdb>/{mem,fd}·ptrace·pidfd_getfd·process_vm 모두 거부, 거짓 보고 도착 0"
    finally:
        s.stop()


@scenario("session-survives-starter-thread-exit")
def _():
    """--die-with-parent 는 Popen 을 호출한 '스레드'가 끝나면 발동한다. 웹 서버의 스레드풀 작업자가 놀다 사라져도 세션이 살아야 한다."""
    box = {}

    def starter():
        box["s"] = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
        box["s"].start()
    t = threading.Thread(target=starter); t.start(); t.join()            # 시작한 스레드가 끝난다
    s = box["s"]
    try:
        time.sleep(3.0)
        assert s.snapshot()["alive"] and s.proc.poll() is None, "시작한 스레드가 끝나자 샌드박스가 죽음"
        threading.Thread(target=lambda: s.run("sleep"), daemon=True).start()
        time.sleep(2.0)
        assert s.snapshot()["status"] == "running" and host_procs("/sandbox/sbxprobe")
        return "시작한 스레드가 끝난 뒤에도 세션이 살아 있음(Popen 은 장수 스레드에서)"
    finally:
        s.stop()


@scenario("namespace-pinned-no-cross-kill")
def _():
    """정리가 pid 네임스페이스 'inode 번호'로 프로세스를 찾는데, 네임스페이스가 사라지면 번호가 재사용되어 엉뚱한 세션을
    죽일 수 있었다. ns fd 를 열어 붙들면 번호가 고정된다."""
    A = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    B = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        A.start(); B.start()
        for x in (A, B):
            threading.Thread(target=lambda x=x: x.run("sleep"), daemon=True).start()
        time.sleep(1.5)
        la, lb = A._launch, B._launch
        assert la.ns_fd is not None and lb.ns_fd is not None, "네임스페이스를 붙들지 못함"
        assert la.ns_inode == os.fstat(la.ns_fd).st_ino and la.ns_inode != lb.ns_inode
        assert sbx.kill_pidns(sbx._pidns_inode(os.getpid())) == 0, "호스트 자신의 네임스페이스를 죽이려 함"
        gdb_pid = None                                   # A 의 gdb 가 밖에서 죽는다(대상이 gdb 를 죽인 경우와 같다)
        for c in [A.proc.pid] + sbx.descendants(A.proc.pid):
            try:
                nspid = open(f"/proc/{c}/status").read().split("NSpid:")[1].split("\n")[0].split()
                if nspid[-1] == "1" and len(nspid) >= 2:
                    gdb_pid = c
            except (OSError, IndexError):
                continue
        assert gdb_pid, "A 의 gdb 를 찾지 못함"
        os.kill(gdb_pid, signal.SIGKILL)
        t0 = time.time()
        while time.time() - t0 < 5 and A.snapshot()["alive"]:
            time.sleep(0.1)
        for i in range(60):                              # 그 사이 다른 pid 네임스페이스가 많이 생겼다 사라진다
            subprocess.run(["bwrap", "--unshare-user", "--unshare-pid", "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
                            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64", "--", "/usr/bin/true"],
                           capture_output=True)
            if i == 5 and la.ns_fd is not None:           # A 의 정리가 끝나기 전이라면 붙든 번호가 유지된다
                assert os.fstat(la.ns_fd).st_ino == la.ns_inode != lb.ns_inode, "붙든 네임스페이스의 번호가 바뀜"
        assert lb.ns_fd is not None and os.fstat(lb.ns_fd).st_ino == lb.ns_inode, "B 의 네임스페이스 고정이 풀림"
        A.stop()
        assert B.snapshot()["alive"] and B.snapshot()["status"] == "running", "A 를 정리하다 B 가 죽음"
        assert host_procs("/sandbox/sbxprobe"), "B 의 대상이 죽음"
        return "A 의 gdb 가 죽고 네임스페이스가 많이 오간 뒤 A.stop() 해도 B 는 그대로(번호가 fd 로 고정됨)"
    finally:
        A.stop(); B.stop()


@scenario("workdir-not-readable-by-others")
def _():
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        s.start()
        wd = workdirs(os.getpid())
        assert len(wd) == 1 and str(os.getpid()) not in wd[0], f"작업 폴더 이름에 서버 pid 가 있음: {wd}"
        path = os.path.join(tempfile.gettempdir(), wd[0]); copy = os.path.join(path, "sbxprobe")
        sc, sd = os.stat(copy), os.stat(path)
        assert sc.st_mode & 0o077 == 0, f"복사본이 다른 계정에 열려 있음: {oct(sc.st_mode)}"
        if IS_ROOT:
            assert sd.st_mode & 0o777 == 0o711 and sc.st_uid == 65534, (oct(sd.st_mode), sc.st_uid)
            if shutil.which("setpriv"):
                r = subprocess.run(["setpriv", "--reuid=54321", "--regid=54321", "--clear-groups", "cat", copy],
                                   capture_output=True)
                assert r.returncode != 0 and not r.stdout, "다른 계정이 실행 중인 대상 복사본을 읽음"
        else:
            assert sd.st_mode & 0o777 == 0o700, oct(sd.st_mode)
        return f"복사본 {oct(sc.st_mode & 0o777)}·폴더 {oct(sd.st_mode & 0o777)}, 이름에 pid 없음, 다른 계정은 읽지 못함"
    finally:
        s.stop()


@scenario("nproc-counts-threads")
def _():
    """RLIMIT_NPROC 는 프로세스가 아니라 태스크(스레드 포함)를 센다. 같은 uid 의 다중 스레드 프로세스가 있어도 시작·동작해야 한다."""
    uid = 65534 if IS_ROOT else os.geteuid()
    code = ("import threading,time\n"
            "for _ in range(250): threading.Thread(target=time.sleep,args=(90,),daemon=True).start()\n"
            "time.sleep(90)")
    cmd = (["setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups"] if IS_ROOT else []) + [sys.executable, "-c", code]
    holder = subprocess.Popen(cmd)
    try:
        t0 = time.time()
        while time.time() - t0 < 10 and sbx._count_uid_tasks(uid) < 250:
            time.sleep(0.1)
        n = sbx._count_uid_tasks(uid)
        assert n >= 250, f"스레드를 세지 못함: {n}"
        out, kv, snap = run_probe("forks 100", policy=sbx.SandboxPolicy(max_processes=40))
        made = int(kv["forked"])
        assert 5 <= made <= 60 and kv["fork_err"] == "Resource temporarily unavailable", kv
        return f"같은 uid 의 스레드 {n}개가 있어도 시작·동작, fork {made}개에서 EAGAIN(허용 40)"
    finally:
        holder.kill(); holder.wait()


@scenario("limit-total-cpu")
def _():
    """RLIMIT_CPU 는 프로세스마다 따로라 N 개로 fork 하면 N 배가 된다 — 총 CPU 시간 합계를 감시한다."""
    P = sbx.SandboxPolicy(cpu_seconds=100, cpu_total_seconds=3, max_processes=64)
    s = DebugSession(probe_exe(), sandbox=P)
    try:
        s.start()
        t0 = time.time()
        threading.Thread(target=lambda: s.run("cpuforks 4"), daemon=True).start()
        while time.time() - t0 < 25 and s.snapshot()["alive"]:
            time.sleep(0.2)
        dt = time.time() - t0
        assert not s.snapshot()["alive"], "총 CPU 한도를 넘겼는데 세션이 계속됨"
        assert "총 CPU 시간 한도" in s.merged_output(), s.merged_output()[-300:]
        assert dt < 20, f"{dt:.1f}s"
    finally:
        s.stop()
    assert not wait_gone()
    return f"프로세스 4개가 각자 한도(100초) 안에서 돌아도 합계 3초를 넘자 {dt:.1f}s 만에 세션 종료·정리"


@scenario("limit-total-memory-without-cgroup")
def _():
    """cgroup 이 없는 일반 데스크톱: RLIMIT_AS 는 프로세스마다 따로라 총 메모리는 N 배가 된다 — 합계를 감시한다."""
    real = sbx.CgroupLimit.create
    sbx.CgroupLimit.create = classmethod(lambda cls, *a, **k: None)      # cgroup 을 못 쓰는 환경을 흉내
    try:
        P = sbx.SandboxPolicy(memory_mb=64, max_processes=32)
        s = DebugSession(probe_exe(), sandbox=P)
        try:
            info = s.start()["sandbox"]
            ids = {l["id"]: l for l in info["layers"]}
            assert not ids["cgroup"]["active"] and "메모리 합계" in ids["total"]["detail"], ids["total"]
            t0 = time.time()
            threading.Thread(target=lambda: s.run("memforks 4 60"), daemon=True).start()
            while time.time() - t0 < 25 and s.snapshot()["alive"]:
                time.sleep(0.2)
            dt = time.time() - t0
            assert not s.snapshot()["alive"], "총 메모리 한도를 넘겼는데 세션이 계속됨"
            assert "총 메모리 한도" in s.merged_output(), s.merged_output()[-300:]
        finally:
            s.stop()
    finally:
        sbx.CgroupLimit.create = real
    assert not wait_gone()
    return f"프로세스 4개×60MB(=240MB)가 합계 한도 128MB 를 넘자 {dt:.1f}s 만에 세션 종료"


@scenario("output-flood-bounded")
def _():
    from app.debug import LOG_LIMIT_CHARS, LOG_TAIL_CHARS
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        s.start()
        threading.Thread(target=lambda: s.run("flood 60"), daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < 60 and "flood-done" not in s.merged_output(tail=4096):
            time.sleep(0.2)
        assert "flood-done" in s.merged_output(tail=4096), "폭주 출력이 끝나지 않음"
        held = {n: sum(len(x) for x in getattr(s, a))
                for n, a in (("merged", "merged"), ("target", "target_out"), ("console", "console"))}
        assert all(v <= LOG_LIMIT_CHARS * 5 // 4 + 70000 for v in held.values()), held
        tail = s.merged_output(tail=LOG_TAIL_CHARS)
        assert len(tail) <= LOG_TAIL_CHARS
        assert len(json.dumps({"merged": tail})) <= LOG_TAIL_CHARS * 6 + 100, "응답 크기가 한도를 넘음"
        t1 = time.time(); s.stop(); dt = time.time() - t1
        assert dt < 8, f"폭주 뒤 stop 이 {dt:.1f}s"
        return (f"60MB 출력 후 보관 {held['merged'] // 1000}K자(한도 {LOG_LIMIT_CHARS // 1000}K), "
                f"API 응답 ≤ {LOG_TAIL_CHARS // 1000}K자, stop {dt:.1f}s")
    finally:
        s.stop()


@scenario("config-concurrent-save")
def _():
    d = tempfile.mkdtemp()
    cfg = sbx.SandboxConfig(os.path.join(d, "sandbox.json"))
    errs = []

    def w(i):
        try:
            for _ in range(25):
                cfg.save({"memory_mb": 100 + i})
        except Exception as e:             # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=w, args=(i,)) for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs, errs[:2]
    data = json.load(open(os.path.join(d, "sandbox.json")))
    assert 100 <= data["memory_mb"] <= 107 and not [n for n in os.listdir(d) if ".tmp" in n], os.listdir(d)
    shutil.rmtree(d, ignore_errors=True)
    return "동시 저장 200회 — 예외 없음, 임시 파일 잔존 없음, 파일은 항상 올바른 JSON"


@scenario("extra-ro-nested-socket-blocked-at-start")
def _():
    d = tempfile.mkdtemp(); os.chmod(d, 0o755)
    open(os.path.join(d, "in.txt"), "w").write("x\n")
    srv = socket.socket(socket.AF_UNIX); srv.bind(os.path.join(d, "app.sock")); srv.listen(1)
    try:
        # 설정 저장 때의 검증을 건너뛰고(= 나중에 소켓이 생긴 경우) 시작 직전 검증이 막는지 본다
        P = sbx.SandboxPolicy(); P.extra_ro = [d]
        s = DebugSession(probe_exe(), sandbox=P)
        try:
            s.start(); raise AssertionError("소켓이 든 폴더로 시작되면 안 됨")
        except DebugError as e:
            assert "소켓" in str(e), str(e)
        assert not wait_gone() and not workdirs(os.getpid())
        return "시작 직전 재검증이 폴더 안 소켓을 거부(검증 뒤 생긴 소켓도 막음)"
    finally:
        srv.close(); shutil.rmtree(d, ignore_errors=True)



# ---- 2차 레드팀 지적 사항의 회귀 테스트 --------------------------------------------
@scenario("cgroup-really-contains-sandbox")
def _():
    """cgroup 을 만들어도 gdb·대상이 거기에 담기지 않던 문제(바깥 bwrap 만 들어가 한도가 아무것도 막지 못함)."""
    if not sbx.CgroupLimit.available():
        return "이 환경은 cgroup 을 쓸 수 없어 건너뜀(비 root 등)"
    ok_runs = 0
    for _ in range(4):                                      # 타이밍 문제였으므로 여러 번
        s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
        try:
            info = s.start()["sandbox"]
            layer = [l for l in info["layers"] if l["id"] == "cgroup"][0]
            threading.Thread(target=lambda: s.run("sleep"), daemon=True).start()
            time.sleep(1.0)
            root = s.proc.pid
            pids = [root] + sbx.descendants(root)
            outside = [p for p in pids if "rework-sbx" not in open(f"/proc/{p}/cgroup").read()]
            assert not outside, f"cgroup 밖에 있는 프로세스: {[(p, open(f'/proc/{p}/comm').read().strip()) for p in outside]}"
            assert s._launch.cg_ok and layer["active"], layer
            assert any("/sandbox/sbxprobe" in open(f"/proc/{p}/cmdline").read() for p in pids), "대상이 보이지 않음"
            ok_runs += 1
        finally:
            s.stop()
    # 보고서는 정직해야 한다: 담지 못했다면 활성으로 표시하지 않는다
    return f"{ok_runs}회 모두 바깥 bwrap·gdb·대상이 전부 cgroup 안에 있고 보고서도 활성"


@scenario("cpu-total-counts-short-lived-children")
def _():
    """표본 사이에 숨는 아주 짧은 자식으로 CPU 를 태워도 합계에 잡힌다(예전: 실제의 1/10 만 셈)."""
    P = sbx.SandboxPolicy(cpu_seconds=1000, cpu_total_seconds=4, max_processes=64)
    s = DebugSession(probe_exe(), sandbox=P)
    try:
        s.start()
        t0 = time.time()
        threading.Thread(target=lambda: s.run("cpuburst 40"), daemon=True).start()
        while time.time() - t0 < 25 and s.snapshot()["alive"]:
            time.sleep(0.2)
        dt = time.time() - t0
        assert not s.snapshot()["alive"], "짧게 사는 자식으로 CPU 를 태우는데도 총 CPU 한도가 작동하지 않음"
        assert "총 CPU 시간 한도" in s.merged_output(), s.merged_output()[-200:]
        assert dt < 14, f"{dt:.1f}s — 실제 CPU 소모(≈코어 1개)에 비해 너무 늦음"
    finally:
        s.stop()
    return f"40ms 자식 반복으로 합계 4초 한도를 {dt:.1f}s 만에 감지"


@scenario("memory-total-ignores-comm-rename")
def _():
    """대상이 스스로 comm 을 'gdb' 로 바꿔 메모리 합계에서 빠지려던 시도(예전엔 이름으로 gdb 를 구분했다)."""
    real = sbx.CgroupLimit.create
    sbx.CgroupLimit.create = classmethod(lambda cls, *a, **k: None)
    try:
        s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy(memory_mb=64, max_processes=32))
        try:
            s.start()
            t0 = time.time()
            threading.Thread(target=lambda: s.run("memcomm 4 60"), daemon=True).start()
            while time.time() - t0 < 25 and s.snapshot()["alive"]:
                time.sleep(0.2)
            assert not s.snapshot()["alive"], "comm 을 gdb 로 바꾼 프로세스가 메모리 합계를 피함"
            assert "총 메모리 한도" in s.merged_output()
        finally:
            s.stop()
    finally:
        sbx.CgroupLimit.create = real
    return f"comm='gdb' 로 위장한 4×60MB 도 합계 한도(128MB)에 걸려 {time.time() - t0:.1f}s 만에 종료"


@scenario("guard-required-fails-closed")
def _():
    """디버거 보호를 확인하지 못하면 시작하지 않는다(기본). 세션별로만 위험을 감수할 수 있다."""
    # (1) gdb 에 Python 이 없는 환경을 흉내: 보호 인자를 비운다 → 시작 후 호스트 검증이 실패해야 한다
    saved = sbx.GDB_GUARD_ARGS
    sbx.GDB_GUARD_ARGS = ["-iex", "maint set worker-threads 1"]
    try:
        s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
        try:
            s.start(); raise AssertionError("가드가 없는데 시작됨")
        except DebugError as e:
            assert "디버거 보호" in str(e), str(e)
        assert not wait_gone() and not workdirs(os.getpid())
        # (2) 명시적으로 끄면(세션별) 시작은 되지만 보고서는 미적용을 정직하게 표시한다
        s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy(require_gdb_guard=False))
        try:
            info = s.start()["sandbox"]
            g = [l for l in info["layers"] if l["id"] == "gdbguard"][0]
            assert g["active"] is False and "위조" in g["detail"], g
            assert "디버거 보호 미적용" in s.merged_output()
        finally:
            s.stop()
    finally:
        sbx.GDB_GUARD_ARGS = saved
    # (3) 사전 점검: 이 환경에서 보호를 쓸 수 없다고 알려진 경우 프로세스를 만들기 전에 이유와 함께 거부
    real = dict(sbx._PROBE_CACHE)
    st = sbx.probe()
    fake = sbx.SandboxStatus(**{**st.to_dict(), "gdb_python": False, "guard_ready": False})
    sbx._PROBE_CACHE.update(t=time.monotonic(), status=fake)
    try:
        s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
        try:
            s.start(); raise AssertionError("보호를 쓸 수 없는데 시작됨")
        except DebugError as e:
            assert "Python" in str(e) and "디버거 보호 없이" in str(e), str(e)
        assert not host_procs("gdb --quiet")
    finally:
        sbx._PROBE_CACHE.update(real)
    # 보호 요구를 끄는 설정은 저장할 수 없다(비격리와 같은 원칙)
    d = tempfile.mkdtemp()
    cfg = sbx.SandboxConfig(os.path.join(d, "sandbox.json"))
    try:
        cfg.save({"require_gdb_guard": False}); raise AssertionError("저장되면 안 됨")
    except ValueError:
        pass
    assert cfg.effective({"require_gdb_guard": False}).require_gdb_guard is False, "세션별로는 끌 수 있어야 함"
    shutil.rmtree(d, ignore_errors=True)
    return "가드 없으면 시작 거부(기본)·세션별 opt-in 은 허용하되 보고서에 ✗·사전 점검 거부·저장 불가"


@scenario("target-cannot-kill-gdb-with-signals")
def _():
    """대상이 pid 1(gdb)에 SIGTERM·SIGHUP 을 보내 디버거를 죽이는 anti-debug 가 통하지 않는다."""
    results = []
    # 15·1 은 무시, 6·7·8·11(ABRT·BUS·FPE·SEGV)은 gdb 가 스스로 핸들러를 거는 치명 신호, 9 는 pid 1 이라 무시, 2·3 은 gdb 가 처리
    for sig in (15, 1, 9, 2, 3, 6, 7, 8, 11):
        s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
        try:
            s.start()
            threading.Thread(target=lambda: s.run(f"killone {sig}"), daemon=True).start()
            t0 = time.time()
            while time.time() - t0 < 10 and "kill1[" not in s.target_output():
                time.sleep(0.1)
            assert f"kill1[{sig}]=SENT" in s.target_output().replace("\r", ""), s.target_output()
            time.sleep(1.5)
            snap = s.snapshot()
            assert snap["alive"] and snap["status"] == "running", f"시그널 {sig} 로 세션이 끝남: {snap['status']} {snap['stop_reason']}"
            results.append(sig)
        finally:
            s.stop()
    return f"시그널 {results} 를 pid 1 에 보내도 gdb 가 살아 있음"


@scenario("gdb-death-is-reported-and-cleaned")
def _():
    """gdb 가 대상 종료 보고 없이 죽으면(밖에서 SIGKILL) 정상 종료와 구분해 알리고 작업 폴더·cgroup·감시를 정리한다."""
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        s.start()
        threading.Thread(target=lambda: s.run("sleep"), daemon=True).start()
        time.sleep(1.0)
        launch = s._launch
        assert workdirs(os.getpid()), "작업 폴더가 있어야 함"
        os.kill(launch.init_host_pid, signal.SIGKILL)               # gdb(pid 1) 를 밖에서 죽인다
        t0 = time.time()
        while time.time() - t0 < 6 and not launch._cleaned:
            time.sleep(0.1)
        snap = s.snapshot()
        assert launch._cleaned, "gdb 가 죽었는데 정리되지 않음"
        assert not workdirs(os.getpid()), "작업 폴더가 남음"
        assert launch.watch is None and launch.ns_fd is None
        assert snap["stop_reason"] == "debugger-died" and not snap["alive"], snap
        assert "디버거(gdb)가 대상 종료 보고 없이 사라졌습니다" in s.merged_output()
        assert not wait_gone()
    finally:
        s.stop()
    return "gdb 사망을 'debugger-died' 로 구분해 알리고, stop() 호출 전에 작업 폴더·cgroup·감시 스레드·ns fd 정리"


@scenario("extra-ro-swap-after-validation-is-pinned")
def _():
    """검증한 뒤 bwrap 이 마운트하기 전에 경로의 대상을 바꿔치기(rename → 호스트 /etc 로 가는 심볼릭 링크)해도 검증한
    원래 폴더가 붙는다(fd 로 고정)."""
    base = tempfile.mkdtemp(); os.chmod(base, 0o755)
    d = os.path.join(base, "inputs"); os.makedirs(d); os.chmod(d, 0o755)
    open(os.path.join(d, "marker.txt"), "w").write("ORIGINAL"); os.chmod(os.path.join(d, "marker.txt"), 0o644)
    orig = sbx.shutil.copyfile
    swapped = {"n": 0}

    def evil_copyfile(src, dst, *a, **k):          # 검증(resolve_extra_ro)이 끝난 뒤, bwrap 시작 전의 틈에서 바꿔치기
        if not swapped["n"]:
            swapped["n"] = 1
            os.rename(d, d + ".orig")
            os.symlink("/etc", d)
        return orig(src, dst, *a, **k)
    sbx.shutil.copyfile = evil_copyfile
    try:
        out, kv, _s = run_probe(f"cat {d}/marker.txt {d}/hostname {d}/passwd",
                                policy=sbx.SandboxPolicy(extra_ro=[d]))
    finally:
        sbx.shutil.copyfile = orig
    assert swapped["n"] == 1, "바꿔치기가 일어나지 않음(테스트 전제)"
    assert kv.get(f"cat[{d}/marker.txt]") == "ORIGINAL", f"검증한 폴더가 붙지 않음: {kv}"
    assert "ERR" in kv.get(f"cat[{d}/passwd]", ""), f"바꿔치기한 호스트 /etc 가 보임: {kv}"
    shutil.rmtree(base, ignore_errors=True)
    return "검증 뒤 폴더를 /etc 심볼릭 링크로 바꿔도 원래 폴더가 붙고 호스트 /etc/passwd 는 보이지 않음"


@scenario("workdir-copy-locked-before-dir-opens")
def _():
    """폴더를 0711 로 열기 '전에' 복사본이 0500(샌드박스 uid 소유)으로 잠겨 있어야 한다(예전: 열린 폴더 안에서 0644 인 틈)."""
    if not IS_ROOT:
        return "root 서버에서만 해당(비 root 는 폴더가 0700): 건너뜀"
    order = []
    ochmod, ochown = os.chmod, os.chown
    wd_prefix = os.path.join(tempfile.gettempdir(), "rework-sbx-")

    def rec_chmod(path, mode, *a, **k):
        if str(path).startswith(wd_prefix):
            order.append(("chmod", "dir" if os.path.isdir(path) else "file", oct(mode)))
        return ochmod(path, mode, *a, **k)

    def rec_chown(path, uid, gid, *a, **k):
        if str(path).startswith(wd_prefix):
            order.append(("chown", "dir" if os.path.isdir(path) else "file", uid))
        return ochown(path, uid, gid, *a, **k)
    os.chmod, os.chown = rec_chmod, rec_chown
    try:
        s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
        try:
            s.start()
        finally:
            s.stop()
    finally:
        os.chmod, os.chown = ochmod, ochown
    idx = {k: i for i, k in enumerate(order)}
    assert ("chmod", "dir", "0o711") in idx and ("chmod", "file", "0o500") in idx, order
    assert idx[("chmod", "file", "0o500")] < idx[("chmod", "dir", "0o711")], f"폴더가 먼저 열림: {order}"
    return f"순서: {[o[0] + ':' + o[1] + ':' + str(o[2]) for o in order]}"


@scenario("sensitive-paths-cover-service-accounts")
def _():
    import pwd as _pwd
    home = tempfile.mkdtemp(); os.makedirs(os.path.join(home, ".ssh"))
    real_getpwall = _pwd.getpwall

    class E:                                           # uid 999(시스템·서비스 계정)의 홈
        pw_uid, pw_dir = 999, home
    _pwd.getpwall = lambda: [E()]
    try:
        try:
            sbx._validate_extra_ro([home]); raise AssertionError("서비스 계정 홈이 거부돼야 함")
        except ValueError:
            pass
    finally:
        _pwd.getpwall = real_getpwall
        shutil.rmtree(home, ignore_errors=True)
    return "uid 1~999 계정의 홈(.ssh 등)도 노출 거부"


@scenario("stale-sweep-is-robust")
def _():
    base = tempfile.gettempdir()
    own = sbx._own_ns()
    made = {}
    for name, owner in (("rework-sbx-zzbad1", "99999999999999999999"), ("rework-sbx-zzbad2", "not-a-number"),
                        ("rework-sbx-zzbad3", ""), ("rework-sbx-zzdead", f"2999999 {own}"),
                        ("rework-sbx-zzforeign", f"2999998 {own + 12345}"), ("rework-sbx-zzlive", f"{os.getpid()} {own}")):
        d = os.path.join(base, name); os.makedirs(d, exist_ok=True)
        open(os.path.join(d, ".owner"), "w").write(owner)
        made[name] = d
    link_target = tempfile.mkdtemp(); open(os.path.join(link_target, "keep"), "w").write("x")
    os.symlink(link_target, os.path.join(base, "rework-sbx-zzlink"))
    sbx._SWEPT["done"] = False
    try:
        sbx._sweep_stale()                              # 어떤 .owner 여도 예외 없이 끝나야 한다
        assert sbx._SWEPT["done"] is True
        assert not os.path.exists(made["rework-sbx-zzdead"]), "같은 네임스페이스에서 죽은 서버의 폴더는 치워야 함"
        for n in ("rework-sbx-zzbad1", "rework-sbx-zzbad2", "rework-sbx-zzbad3", "rework-sbx-zzforeign", "rework-sbx-zzlive"):
            assert os.path.exists(made[n]), f"{n}: 판단할 수 없거나 살아 있는/다른 네임스페이스의 것을 지움"
        assert os.path.exists(os.path.join(link_target, "keep")), "심볼릭 링크를 따라가 지움"
    finally:
        for n, d in made.items():
            shutil.rmtree(d, ignore_errors=True)
        os.unlink(os.path.join(base, "rework-sbx-zzlink")); shutil.rmtree(link_target, ignore_errors=True)
        sbx._SWEPT["done"] = True
    return "엉뚱한 .owner(거대한 수·문자·빈 값)에도 예외 없이 완료, 죽은 서버의 폴더만 삭제, 다른 네임스페이스·링크는 보존"


@scenario("bwrap-failure-is-explained-fast")
def _():
    """bwrap 이 시작 직후 실패하면 5초 기다린 뒤 '활성 상태가 아닙니다' 만 보이던 문제."""
    d = tempfile.mkdtemp(); os.chmod(d, 0o755)
    fake = os.path.join(d, "bwrap")
    open(fake, "w").write("#!/bin/sh\necho 'bwrap: FAKE-FAILURE: cannot do the thing' >&2\nexit 1\n"); os.chmod(fake, 0o755)
    real = dict(sbx._PROBE_CACHE)
    st = sbx.probe()
    sbx._PROBE_CACHE.update(t=time.monotonic(), status=sbx.SandboxStatus(**{**st.to_dict(), "bwrap": fake}))
    try:
        s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
        t0 = time.time()
        try:
            s.start(); raise AssertionError("실패해야 함")
        except DebugError as e:
            msg = str(e)
        dt = time.time() - t0
        assert "FAKE-FAILURE" in msg, msg
        assert dt < 3.0, f"{dt:.1f}s — 너무 오래 기다림"
        assert not wait_gone() and not workdirs(os.getpid())
    finally:
        sbx._PROBE_CACHE.update(real)
        shutil.rmtree(d, ignore_errors=True)
    return f"{dt:.1f}s 만에 bwrap 의 오류 메시지와 함께 실패"


@scenario("gdb-stdin-never-blocks-the-server")
def _():
    """gdb 가 stdin 을 읽지 않을 때(멈춘 gdb) 큰 명령 하나가 요청 스레드를 영원히 붙들던 문제."""
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        s.start()
        os.kill(s._launch.init_host_pid, signal.SIGSTOP) if False else None
        # 호스트에서 gdb(pid 1)를 SIGSTOP: 같은 네임스페이스 밖에서 보내므로 정지된다
        os.kill(s._launch.init_host_pid, signal.SIGSTOP)
        t0 = time.time()
        try:
            s._send("-data-evaluate-expression " + "1+" * 120000 + "1", timeout=3)
            raise AssertionError("멈춘 gdb 에 큰 명령이 성공할 수 없음")
        except DebugError as e:
            assert "응답" in str(e) or "시간 초과" in str(e), str(e)
        dt = time.time() - t0
        assert dt < 12, f"{dt:.1f}s"
        t1 = time.time(); s.stop(); dt2 = time.time() - t1          # 멈춘 gdb 에서도 stop() 이 끝난다
        assert dt2 < 8, f"stop {dt2:.1f}s"
    finally:
        try:
            os.kill(s._launch.init_host_pid, signal.SIGCONT)
        except Exception:                          # noqa: BLE001
            pass
        s.stop()
    assert not wait_gone()
    return f"멈춘 gdb 에 보낸 큰 명령은 {dt:.1f}s 안에 오류, stop() 은 {dt2:.1f}s 안에 끝남"


@scenario("pty-reader-works-with-high-fds")
def _():
    """select() 는 fd 번호 1024 이상에서 실패해 출력 읽기 스레드가 조용히 죽었다."""
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = 2200 if hard == resource.RLIM_INFINITY else min(2200, hard)
    if want < 1500:
        return "RLIMIT_NOFILE 상한이 낮아 건너뜀"
    resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    hold = []
    try:
        while len(hold) < 1200:
            hold.append(os.open("/dev/null", os.O_RDONLY))
        s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
        try:
            s.start()
            assert s._pty_master >= 1024, f"pty fd 가 {s._pty_master} 로 낮음(테스트 전제)"
            threading.Thread(target=lambda: s.run("id"), daemon=True).start()
            t0 = time.time()
            while time.time() - t0 < 10 and "envcount=" not in s.target_output():
                time.sleep(0.1)
            assert "envcount=" in s.target_output(), "높은 fd 에서 대상 출력을 읽지 못함"
            assert s._pty_reader.is_alive() or "envcount=" in s.target_output()
        finally:
            s.stop()
    finally:
        for fd in hold:
            os.close(fd)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    return "pty fd 번호가 1024 이상이어도 대상 출력을 정상 수신(poll 사용)"


@scenario("descendants-uses-children-files")
def _():
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        s.start()
        threading.Thread(target=lambda: s.run("daemon"), daemon=True).start()
        time.sleep(1.2)
        fast = set(sbx.descendants(s.proc.pid)); slow = set(sbx._descendants_slow(s.proc.pid))
        assert fast == slow and len(fast) >= 3, (fast, slow)
        # 비용이 호스트 전체가 아니라 자손 수에 비례한다: 호스트에 프로세스를 많이 늘려도 거의 같다
        t0 = time.time()
        for _ in range(200):
            sbx.descendants(s.proc.pid)
        fast_t = time.time() - t0
        t0 = time.time()
        for _ in range(200):
            sbx._descendants_slow(s.proc.pid)
        slow_t = time.time() - t0
        assert fast_t < slow_t, f"빠른 방식이 더 느림: {fast_t:.3f}s vs {slow_t:.3f}s"
        return f"자손 {len(fast)}개 동일, 200회 {fast_t * 1000:.0f}ms(children 파일) vs {slow_t * 1000:.0f}ms(전체 /proc 훑기)"
    finally:
        s.stop()


@scenario("noexec-tmp-falls-back-to-sandbox-copy")
def _():
    """임시 폴더가 noexec 로 마운트돼 있으면 bind 한 복사본을 실행하지 못한다 → 샌드박스 tmpfs 안에 내용을 복사해 실행."""
    if not IS_ROOT or not shutil.which("unshare"):
        return "root·unshare 가 필요해 건너뜀"
    d = tempfile.mkdtemp()
    code = f"""
import sys, os, time
sys.path.insert(0, {os.path.join(ROOT, 'backend')!r})
from app import sandbox as sbx
from app.debug import DebugSession
s = DebugSession({probe_exe()!r}, sandbox=sbx.SandboxPolicy())
s.start(); s.run("id")
t0 = time.time()
while time.time() - t0 < 12 and "envcount=" not in s.target_output(): time.sleep(0.1)
print("RESULT", "envcount=" in s.target_output())
s.stop()
"""
    script_py = os.path.join(d, "run_nx.py")
    open(script_py, "w").write(code)
    script = (f"mkdir -p {d}/nx && mount -t tmpfs -o noexec tmpfs {d}/nx && chmod 1777 {d}/nx && "
              f"TMPDIR={d}/nx {sys.executable} {script_py}")
    try:
        r = subprocess.run(["unshare", "-m", "sh", "-c", script], capture_output=True, text=True, timeout=60)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if "RESULT" not in r.stdout:
        return f"unshare/mount 를 쓸 수 없어 건너뜀: {(r.stderr or '')[-120:]}"
    assert "RESULT True" in r.stdout, f"noexec 임시 폴더에서 실행 실패: {r.stdout[-200:]} {r.stderr[-300:]}"
    return "noexec 로 마운트된 임시 폴더에서도 대상이 실행됨(샌드박스 tmpfs 로 복사)"


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


# ---- 3차 검토 반영 ------------------------------------------------------
@scenario("gdb-cpu-not-limited-by-target-limit")
def _():
    """프로세스당 CPU 한도는 대상 프로세스에만 걸린다. 디버거(gdb)가 그 한도를 넘게 CPU 를 써도(조건부 중단점·fork 많은 대상) 죽지 않는다."""
    P = sbx.SandboxPolicy(cpu_seconds=3)
    s = DebugSession(probe_exe(), sandbox=P)
    try:
        s.start()
        s.console_cmd('python import time; t = time.time(); exec("while time.time() - t < 5: pass")')   # gdb 가 5초 동안 CPU 를 쓴다
        snap = s.snapshot()
        assert snap["alive"], f"gdb 가 대상용 CPU 한도(3초)에 걸려 죽음: {snap}"
        assert not snap["end_reason"], snap["end_reason"]
        w = s._launch.watch
        time.sleep(1.0)
        assert w.gdb_cpu_seconds >= 3.5, f"gdb CPU 를 따로 집계하지 못함: {w.gdb_cpu_seconds}"
        assert w.cpu_seconds_used < 3, f"gdb 자신의 CPU 가 총 CPU 합계에 들어감: {w.cpu_seconds_used}"
        s.console_cmd("print 1")                                   # 여전히 응답한다
    finally:
        s.stop()
    assert not wait_gone()
    return "gdb 가 CPU 5초를 써도(한도 3초) 세션 유지, 총 CPU 합계에는 미포함"


@scenario("per-process-cpu-limit-kills-only-target")
def _():
    """프로세스당 CPU 한도를 넘은 대상은 감시 스레드가 SIGKILL 한다. 세션과 gdb 는 그대로이고 사유가 로그에 남는다."""
    P = sbx.SandboxPolicy(cpu_seconds=2)
    s = DebugSession(probe_exe(), sandbox=P)
    try:
        s.start()
        t0 = time.time()
        s.run("cpu")
        while time.time() - t0 < 12 and s.snapshot()["status"] != "exited":
            time.sleep(0.1)
        snap = s.snapshot()
        dt = time.time() - t0
        assert snap["status"] == "exited" and snap["alive"], snap
        assert dt < 8, f"{dt:.1f}s"
        m = s.merged_output()
        assert "프로세스당 CPU 시간 한도(2초)" in m, m[-400:]
        assert "SIGKILL" in m or "terminated with signal" in m, m[-400:]
        s.console_cmd("print 2")                                    # gdb 는 멀쩡하다
    finally:
        s.stop()
    assert not wait_gone()
    return f"CPU 2초 한도 → {dt:.1f}s 에 대상만 종료, 사유 기록, gdb 유지"


@scenario("persisting-protection-off-is-refused")
def _():
    """seccomp·권한 낮추기를 끄는 값도 mode=off·require_gdb_guard=false 처럼 저장되지 않는다(세션별 덮어쓰기로만)."""
    d = tempfile.mkdtemp()
    cfgf = os.path.join(d, "sandbox.json")
    cfg = sbx.SandboxConfig(cfgf)
    for patch in ({"seccomp": False}, {"drop_privileges": False}, {"mode": "off"}, {"require_gdb_guard": False}):
        try:
            cfg.save(patch); raise AssertionError(f"저장되면 안 됨: {patch}")
        except ValueError:
            pass
    with open(cfgf, "w") as fh:
        json.dump({"seccomp": False, "drop_privileges": False, "memory_mb": 300}, fh)
    p = sbx.SandboxConfig(cfgf).policy
    assert p.seccomp is True and p.drop_privileges is True and p.memory_mb == 300, p
    eff = cfg.effective({"seccomp": False})
    assert eff.seccomp is False and cfg.policy.seccomp is True                  # 이번 세션에만
    shutil.rmtree(d, ignore_errors=True)
    return "seccomp·drop_privileges 끄기는 저장·불러오기에서 무효, 세션별 덮어쓰기만 허용"


@scenario("leftover-count-is-fresh")
def _():
    """끝난 시점에 남은 프로세스 수를 새로 센다: 정상 종료는 0, 데몬을 남기면 정확히 그 수(감시 스레드의 마지막 표본을 쓰지 않는다)."""
    out, kv, snap = run_probe("id")
    assert snap["status"] == "exited"
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        s.start()
        s.run("id")
        t0 = time.time()
        while time.time() - t0 < 10 and s.snapshot()["status"] != "exited":
            time.sleep(0.05)
        snap = s.snapshot()
        assert snap["status"] == "exited" and snap["sandbox_leftover"] == 0, snap
    finally:
        s.stop()
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        s.start()
        s.run("daemon")
        t0 = time.time()
        while time.time() - t0 < 10 and s.snapshot()["status"] != "exited":
            time.sleep(0.05)
        time.sleep(0.3)
        snap = s.snapshot()
        assert snap["status"] == "exited" and snap["sandbox_leftover"] == 6, snap       # 데몬 3개 × (자신 + 자식)
    finally:
        s.stop()
    assert not wait_gone()
    return "정상 종료 → 남은 0개, 데몬 3쌍 → 정확히 6개"


@scenario("end-reason-and-log-survive-limit-kill")
def _():
    """한도로 세션이 끝나면 사유(end_reason)와 마지막 로그가 남아, 화면이 '왜 끝났는지' 보여 줄 수 있다."""
    P = sbx.SandboxPolicy(cpu_seconds=100, cpu_total_seconds=3, max_processes=64)
    s = DebugSession(probe_exe(), sandbox=P)
    try:
        s.start()
        threading.Thread(target=lambda: s.run("cpuforks"), daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < 15 and s.snapshot()["alive"]:
            time.sleep(0.1)
        snap = s.snapshot()
        assert not snap["alive"], "총 CPU 한도가 세션을 끝내지 못함"
        assert snap["end_reason"] and "총 CPU" in snap["end_reason"], snap
        assert "[샌드박스]" in s.merged_output() and "총 CPU" in s.merged_output()
    finally:
        s.stop()
    assert not wait_gone()
    return f"종료 사유 보존: {snap['end_reason']}"


@scenario("debugger-died-reason-and-log")
def _():
    """gdb 가 사라지면 end_reason 이 채워지고 로그가 남는다(공격으로 오인되지 않도록 CPU 받침 한도 사유는 따로 표시)."""
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        s.start()
        threading.Thread(target=lambda: s.run("sleep"), daemon=True).start()
        time.sleep(1.0)
        os.kill(s._launch.init_host_pid, signal.SIGKILL)
        t0 = time.time()
        while time.time() - t0 < 10 and s.snapshot()["alive"]:
            time.sleep(0.1)
        snap = s.snapshot()
        assert snap["stop_reason"] == "debugger-died" and "디버거(gdb)가" in (snap["end_reason"] or ""), snap
        assert "[샌드박스]" in s.merged_output(), s.merged_output()[-200:]
    finally:
        s.stop()
    return f"end_reason={snap['end_reason'][:40]}…"


@scenario("deep-stack-is-capped-and-fast")
def _():
    """무한 재귀 같은 아주 깊은 스택도 위쪽 STACK_FRAMES_MAX 개만 빠르게 돌려준다. 한도를 넘는 응답은 즉시 오류로 끝난다."""
    from app import debug as dbgmod
    s = DebugSession(probe_exe(), sandbox=sbx.SandboxPolicy())
    try:
        s.start()
        s.run("deep 20000")
        t0 = time.time()
        while time.time() - t0 < 20 and s.snapshot()["status"] != "stopped":
            time.sleep(0.05)
        assert s.snapshot()["status"] == "stopped", s.snapshot()
        t1 = time.time()
        frames = s.stack()
        dt = time.time() - t1
        assert len(frames) == dbgmod.STACK_FRAMES_MAX and s._stack_truncated, (len(frames), s._stack_truncated)
        assert dt < 3, f"{dt:.1f}s"
        # 상한 없이 요청하면 응답이 MI 한 줄 한도(1MiB)를 넘는다: 시간 초과(10초)를 기다리지 않고 바로 오류가 나야 한다
        t2 = time.time()
        try:
            s._send("-stack-list-frames", timeout=8)
            raise AssertionError("한도를 넘는 응답이 성공으로 처리됨")
        except DebugError as e:
            assert "너무 커서" in str(e), str(e)
        dt2 = time.time() - t2
        assert dt2 < 3, f"한도 초과 응답이 {dt2:.1f}s 뒤에야 실패"
        s.console_cmd("print 3")                                   # 이후에도 정상 동작
    finally:
        s.stop()
    assert not wait_gone()
    return f"깊이 20000 → {len(frames)}프레임 {dt:.2f}s 생략 표시, 한도 초과 응답은 {dt2:.2f}s 만에 즉시 오류"


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
