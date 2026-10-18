"""디버거 대상 실행 샌드박스(격리 실행).

디버깅은 대상 바이너리를 **실제로 실행**한다. 악성코드 같은 신뢰할 수 없는 파일을 호스트에서
그대로 돌리지 않도록, GDB 와 대상을 함께 bubblewrap(bwrap) 격리 환경 안에서 실행한다.
(gdb 가 악성 파일을 파싱하는 것도 격리 안에서 일어난다.)

층별 방어(모든 층이 서로를 보완하며, 하나가 없어도 나머지는 유지된다):
- 네임스페이스: user·pid·net·ipc·uts·cgroup. 호스트 프로세스가 보이지 않고, 네트워크는
  기본 차단(루프백만), 중첩 user namespace 생성 불가.
- 파일시스템: 허용 목록 방식의 **읽기 전용** 최소 루트(/usr 일부 + 합성 /etc 몇 개). 호스트의
  홈·/etc/shadow·소켓·데이터 디렉터리는 보이지 않는다. /tmp 는 크기 제한이 있는 빈 tmpfs.
  대상은 호스트 원본이 아니라 **읽기 전용 복사본**을 쓴다(원본이 잠기지 않고 변조도 안 된다).
- 환경 변수 전부 비움(API 키 등 비밀이 대상에 새지 않게), 모든 capability 제거, no-new-privs.
- 권한: 서버가 root 면 샌드박스 프로세스는 낮은 uid 로 낮춘다(탈출해도 root 가 아니고
  RLIMIT_NPROC 같은 한도가 root 면제 없이 적용된다).
- seccomp: 커널 공격면이 큰 시스템 콜(bpf·io_uring·userfaultfd·mount 계열·네임스페이스 생성
  등)을 차단(x86_64).
- 자원: rlimit(CPU·주소공간·프로세스·파일·FD) + 가능하면 cgroup(총 메모리·프로세스 수) +
  선택적 실행 시간 제한.
- 터미널: 호스트의 /dev/pts 전체가 아니라 **전용 pty 하나만** 넘긴다.

한계(정직하게): 커널 취약점을 쓰는 탈출은 막지 못한다(seccomp 로 공격면만 줄임). 완전한
격리가 필요하면 별도 VM 을 쓰라. Linux 전용이다(Windows 는 WSL2/VM 안에서 실행).
정적 분석(lief·capstone)은 이 서버 프로세스 안에서 일어나므로 이 샌드박스 범위 밖이다.
"""
from __future__ import annotations

import json
import os
import stat
import platform
import re
import resource
import shutil
import signal
import struct
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field, asdict


class SandboxError(Exception):
    pass


# ---- 정책 ------------------------------------------------------------------
MODES = ("on", "off")
NETWORKS = ("none", "host")
# 읽기 전용으로라도 노출하면 안 되는 경로(호스트 전체·커널 인터페이스·소켓 디렉터리)
FORBIDDEN_RO = ("/", "/proc", "/sys", "/dev", "/run", "/var/run", "/boot", "/root")

_INT_RANGES = {
    "memory_mb": (64, 65536),
    "cpu_seconds": (1, 86400),
    "max_processes": (8, 4096),
    "max_file_mb": (1, 4096),
    "max_open_files": (16, 65536),
    "tmp_mb": (1, 4096),
    "wall_seconds": (0, 604800),
    "run_as_uid": (1, 4294967293),
}
_BOOLS = ("seccomp", "drop_privileges")


@dataclass
class SandboxPolicy:
    mode: str = "on"                  # on: 격리 실행, off: 비격리(신뢰하는 파일만)
    network: str = "none"             # none: 차단(루프백만), host: 호스트 네트워크 공유(위험)
    memory_mb: int = 2048             # 프로세스당 주소공간 한도(MB). cgroup 이 있으면 총 메모리 한도도
    cpu_seconds: int = 600            # 프로세스당 CPU 시간(초)
    max_processes: int = 256          # 샌드박스가 추가로 만들 수 있는 프로세스 수
    max_file_mb: int = 256            # 파일 하나의 최대 크기(MB)
    max_open_files: int = 1024
    tmp_mb: int = 128                 # /tmp(tmpfs) 크기(MB)
    wall_seconds: int = 0             # 세션 최대 실행 시간(초). 0 은 제한 없음
    extra_ro: list = field(default_factory=list)   # 읽기 전용으로 추가 노출할 호스트 경로
    seccomp: bool = True
    drop_privileges: bool = True      # root 로 실행 중이면 낮은 uid 로 낮춤
    run_as_uid: int = 65534

    @property
    def enabled(self) -> bool:
        return self.mode == "on"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None, base: "SandboxPolicy | None" = None,
                  strict: bool = True) -> "SandboxPolicy":
        """data 의 값을 base(없으면 기본값) 위에 덮어 검증한다. 잘못된 값은 ValueError."""
        cur = (base or cls()).to_dict()
        data = data or {}
        if not isinstance(data, dict):
            raise ValueError("샌드박스 설정은 객체여야 합니다.")
        for k, v in data.items():
            if k not in cur:
                if strict:
                    raise ValueError(f"알 수 없는 샌드박스 설정: {k}")
                continue
            cur[k] = v
        if cur["mode"] not in MODES:
            raise ValueError(f"mode 는 {MODES} 중 하나여야 합니다.")
        if cur["network"] not in NETWORKS:
            raise ValueError(f"network 는 {NETWORKS} 중 하나여야 합니다.")
        for k, (lo, hi) in _INT_RANGES.items():
            v = cur[k]
            if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
                raise ValueError(f"{k} 는 {lo}~{hi} 사이의 정수여야 합니다.")
        for k in _BOOLS:
            if not isinstance(cur[k], bool):
                raise ValueError(f"{k} 는 true/false 여야 합니다.")
        cur["extra_ro"] = _validate_extra_ro(cur["extra_ro"])
        return cls(**cur)


_PROTECTED_DIRS: list = []          # 절대 노출하면 안 되는 디렉터리(데이터 디렉터리: API 키·프로젝트 DB)


def protect_dir(path: str):
    rp = os.path.realpath(path)
    if rp not in _PROTECTED_DIRS:
        _PROTECTED_DIRS.append(rp)


def _under(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip("/") + "/")


def _validate_extra_ro(paths) -> list:
    if paths is None:
        return []
    if not isinstance(paths, list) or len(paths) > 32:
        raise ValueError("extra_ro 는 경로 목록(최대 32개)이어야 합니다.")
    out = []
    forbidden = set()
    for p in FORBIDDEN_RO:
        forbidden.add(p)
        forbidden.add(os.path.realpath(p))
    forbidden.discard("/")                         # 루트는 아래에서 따로 막는다(모든 경로가 하위이므로)
    for p in paths:
        if not isinstance(p, str) or not p.strip():
            raise ValueError("extra_ro 의 각 항목은 비어 있지 않은 문자열이어야 합니다.")
        p = p.strip()
        if "\x00" in p or "\n" in p or "\r" in p or len(p) > 4096:
            raise ValueError("경로에 개행·NUL 을 쓸 수 없습니다.")
        if not p.startswith("/"):
            raise ValueError(f"절대 경로여야 합니다: {p}")
        norm = os.path.normpath(p)
        if norm.startswith("//"):            # normpath 는 선행 '//' 를 보존한다
            norm = "/" + norm.lstrip("/")
        real = os.path.realpath(norm)
        if norm == "/" or real == "/":
            raise ValueError(f"노출할 수 없는 경로입니다: {p}")
        for f in forbidden:                  # /proc/1, /run/docker.sock, /root/.ssh 같은 하위 경로도 거부
            if _under(norm, f) or _under(real, f):
                raise ValueError(f"노출할 수 없는 경로입니다: {p}")
        for d in _PROTECTED_DIRS:            # 데이터 디렉터리와 그 상위(통째로 노출되는 경우)도 거부
            if _under(norm, d) or _under(real, d) or _under(d, real):
                raise ValueError(f"rework 의 데이터(API 키·프로젝트)가 들어 있어 노출할 수 없습니다: {p}")
        try:
            st = os.stat(real)
        except OSError:
            raise ValueError(f"존재하지 않는 경로입니다: {p}")
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
            raise ValueError(f"파일이나 디렉터리만 노출할 수 있습니다(소켓·장치 불가): {p}")
        if norm not in out:
            out.append(norm)
    return out


def _accessible_as(uid: int, gid: int, path: str) -> str | None:
    """낮춘 uid/gid(보조 그룹 없음)로 path 를 읽을 수 있는지 본다. 막는 첫 구성 요소를 돌려준다."""
    comps = [c for c in path.split("/") if c]
    cur = "/"
    need_last = stat.S_IROTH | (stat.S_IXOTH if os.path.isdir(path) else 0)
    for i in range(len(comps) + 1):
        try:
            st = os.stat(cur)
        except OSError:
            return cur
        last = i == len(comps)
        shift = 6 if st.st_uid == uid else (3 if st.st_gid == gid else 0)
        need = (need_last if last else stat.S_IXOTH) & 0o7
        if uid != 0 and ((st.st_mode >> shift) & need) != need:      # 필요한 비트가 모두 있어야 한다
            return cur
        if not last:
            cur = os.path.join(cur, comps[i])
    return None


class SandboxConfig:
    """데이터 디렉터리의 sandbox.json 에 정책을 보관한다(ai.json 과 같은 방식)."""

    def __init__(self, path: str):
        self.path = path
        protect_dir(os.path.dirname(os.path.abspath(path)))
        self.policy = SandboxPolicy()
        self.load()

    def load(self):
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    self.policy = SandboxPolicy.from_dict(json.load(fh), strict=False)
            except Exception:
                self.policy = SandboxPolicy()   # 깨진 설정은 안전한 기본값(격리 켬)으로

    def save(self, patch: dict) -> SandboxPolicy:
        new = SandboxPolicy.from_dict(patch, base=self.policy)
        self.policy = new
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(new.to_dict(), fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        return new

    def effective(self, override: dict | None) -> SandboxPolicy:
        """저장된 정책 위에 이번 요청의 값을 덮어 쓴 정책(저장하지 않음)."""
        return SandboxPolicy.from_dict(override, base=self.policy)


# ---- seccomp (x86_64) --------------------------------------------------------
# 차단 목록 방식: 커널 공격면이 크거나 샌드박스를 느슨하게 만들 수 있는 시스템 콜을 막는다.
# gdb 는 ptrace·personality·wait4 등이 필요하므로 그것들은 막지 않는다.
_X86_64_DENY = {
    "_sysctl": 156, "acct": 163, "add_key": 248, "adjtimex": 159, "bpf": 321, "chroot": 161,
    "clock_adjtime": 305, "clock_settime": 227, "delete_module": 176, "fanotify_init": 300,
    "finit_module": 313, "fsconfig": 431, "fsmount": 432, "fsopen": 430, "fspick": 433,
    "init_module": 175, "io_uring_enter": 426, "io_uring_register": 427, "io_uring_setup": 425,
    "ioperm": 173, "iopl": 172, "keyctl": 250, "kexec_file_load": 320, "kexec_load": 246,
    "lookup_dcookie": 212, "mount": 165, "mount_setattr": 442, "move_mount": 429,
    "name_to_handle_at": 303, "nfsservctl": 180, "open_by_handle_at": 304, "open_tree": 428,
    "perf_event_open": 298, "pivot_root": 155, "quotactl": 179, "quotactl_fd": 443,
    "reboot": 169, "request_key": 249, "setns": 308, "settimeofday": 164, "swapoff": 168,
    "swapon": 167, "syslog": 103, "umount2": 166, "unshare": 272, "uselib": 134,
    "userfaultfd": 323, "vhangup": 153,
}
_NR_CLONE, _NR_CLONE3, _NR_SOCKET = 56, 435, 41
_CLONE_NEW_MASK = 0x00020000 | 0x02000000 | 0x04000000 | 0x08000000 | 0x10000000 \
    | 0x20000000 | 0x40000000 | 0x00000080          # NS·CGROUP·UTS·IPC·USER·PID·NET·TIME
_ALLOWED_SOCKET_FAMILIES = (1, 2, 10, 16)           # UNIX, INET, INET6, NETLINK
_AUDIT_ARCH_X86_64 = 0xC000003E
_EPERM, _ENOSYS, _EAFNOSUPPORT = 1, 38, 97

_BPF_LD_W_ABS = 0x20
_BPF_JEQ_K = 0x15
_BPF_JSET_K = 0x45
_BPF_RET_K = 0x06
_RET_ALLOW = 0x7FFF0000
_RET_KILL = 0x80000000
_RET_ERRNO = 0x00050000


def seccomp_supported() -> bool:
    return platform.machine() in ("x86_64", "AMD64")


def build_seccomp_bpf() -> bytes | None:
    """x86_64 용 seccomp 필터(고전 BPF)를 만든다. 다른 아키텍처는 None(미적용)."""
    if not seccomp_supported():
        return None
    ins: list = []        # ("op", code, k, jt_label, jf_label) / ("label", name)

    def st(code, k):
        ins.append(("op", code, k, None, None))

    def jmp(code, k, jt, jf):
        ins.append(("op", code, k, jt, jf))

    def label(name):
        ins.append(("label", name))

    def ret(v):
        st(_BPF_RET_K, v)

    # 아키텍처 확인(다른 ABI 로 필터를 우회하지 못하게)
    st(_BPF_LD_W_ABS, 4)
    jmp(_BPF_JEQ_K, _AUDIT_ARCH_X86_64, "arch_ok", "arch_bad")
    label("arch_bad"); ret(_RET_KILL)
    label("arch_ok")
    st(_BPF_LD_W_ABS, 0)                                   # nr
    jmp(_BPF_JSET_K, 0x40000000, "x32", "not_x32")         # x32 ABI 금지
    label("x32"); ret(_RET_ERRNO | _ENOSYS)
    label("not_x32")
    for name, nr in sorted(_X86_64_DENY.items(), key=lambda kv: kv[1]):
        jmp(_BPF_JEQ_K, nr, f"deny_{name}", f"next_{name}")
        label(f"deny_{name}"); ret(_RET_ERRNO | _EPERM)
        label(f"next_{name}")
    # clone3 는 플래그를 검사할 수 없으므로 ENOSYS → glibc 가 clone 으로 폴백
    jmp(_BPF_JEQ_K, _NR_CLONE3, "clone3", "not_clone3")
    label("clone3"); ret(_RET_ERRNO | _ENOSYS)
    label("not_clone3")
    # clone: 네임스페이스 생성 플래그 금지
    jmp(_BPF_JEQ_K, _NR_CLONE, "clone", "not_clone")
    label("clone")
    st(_BPF_LD_W_ABS, 16)                                  # args[0] 하위 32비트 = flags
    jmp(_BPF_JSET_K, _CLONE_NEW_MASK, "clone_ns", "allow")
    label("clone_ns"); ret(_RET_ERRNO | _EPERM)
    label("not_clone")
    # socket: 허용한 주소 체계만
    jmp(_BPF_JEQ_K, _NR_SOCKET, "socket", "allow")
    label("socket")
    st(_BPF_LD_W_ABS, 16)
    for fam in _ALLOWED_SOCKET_FAMILIES:
        jmp(_BPF_JEQ_K, fam, "allow", f"sock_next_{fam}")
        label(f"sock_next_{fam}")
    ret(_RET_ERRNO | _EAFNOSUPPORT)
    label("allow"); ret(_RET_ALLOW)

    # 라벨을 명령어 인덱스로 풀고 상대 점프 오프셋을 계산한다
    pos, idx = {}, 0
    for e in ins:
        if e[0] == "label":
            pos[e[1]] = idx
        else:
            idx += 1
    out, i = bytearray(), 0
    for e in ins:
        if e[0] == "label":
            continue
        _, code, k, jt, jf = e
        jt_off = jf_off = 0
        if jt is not None:
            jt_off, jf_off = pos[jt] - (i + 1), pos[jf] - (i + 1)
            if not (0 <= jt_off <= 255 and 0 <= jf_off <= 255):
                raise SandboxError("seccomp 필터 점프 오프셋이 범위를 벗어났습니다.")
        out += struct.pack("=HBBI", code, jt_off, jf_off, k)
        i += 1
    return bytes(out)


# ---- cgroup(가능할 때만) -----------------------------------------------------
class CgroupLimit:
    """샌드박스 전체의 메모리·프로세스 수를 cgroup 으로 제한한다(v1 또는 이미 위임된 v2).
    권한이 없거나 지원하지 않으면 만들지 못하고(None), 그 사실이 격리 보고서에 표시된다."""

    def __init__(self, dirs: list, mode: str):
        self.dirs = dirs
        self.mode = mode

    @staticmethod
    def _write(path: str, value: str) -> bool:
        try:
            with open(path, "w") as fh:
                fh.write(value)
            return True
        except OSError:
            return False

    @classmethod
    def _v1_base(cls, ctrl: str) -> str | None:
        """/proc/self/cgroup 에서 이 프로세스가 속한 v1 컨트롤러 디렉터리를 찾는다."""
        root = f"/sys/fs/cgroup/{ctrl}"
        if not os.path.isdir(root):
            return None
        sub = ""
        try:
            with open("/proc/self/cgroup") as fh:
                for line in fh:
                    n, ctrls, path = line.rstrip("\n").split(":", 2)
                    if ctrl in ctrls.split(","):
                        sub = path
        except OSError:
            pass
        base = os.path.normpath(root + "/" + sub.lstrip("/"))
        return base if os.path.isdir(base) and os.access(base, os.W_OK) else None

    @classmethod
    def _v2_base(cls) -> str | None:
        """v2 통합 계층에서 하위 cgroup 을 만들 수 있고 memory·pids 가 하위에 켜져 있는 경로."""
        if not os.path.isfile("/sys/fs/cgroup/cgroup.controllers"):
            return None
        try:
            with open("/proc/self/cgroup") as fh:
                rel = next((l.rstrip("\n").split(":", 2)[2] for l in fh if l.startswith("0::")), "/")
            base = os.path.normpath("/sys/fs/cgroup/" + rel.lstrip("/"))
            with open(os.path.join(base, "cgroup.subtree_control")) as fh:
                enabled = fh.read().split()
        except (OSError, StopIteration):
            return None
        if "memory" in enabled and "pids" in enabled and os.access(base, os.W_OK):
            return base
        return None

    @classmethod
    def available(cls) -> bool:
        return bool(cls._v2_base() or cls._v1_base("memory") or cls._v1_base("pids"))

    @classmethod
    def create(cls, name: str, mem_bytes: int, pids_max: int) -> "CgroupLimit | None":
        base2 = cls._v2_base()
        if base2:
            d = os.path.join(base2, name)
            try:
                os.mkdir(d)
            except OSError:
                return None
            ok = cls._write(os.path.join(d, "memory.max"), str(mem_bytes))
            cls._write(os.path.join(d, "memory.swap.max"), "0")
            ok = cls._write(os.path.join(d, "pids.max"), str(pids_max)) and ok
            if not ok:
                try:
                    os.rmdir(d)
                except OSError:
                    pass
                return None
            return cls([d], "v2")
        made = []
        for ctrl, fname, val in (("memory", "memory.limit_in_bytes", str(mem_bytes)),
                                 ("pids", "pids.max", str(pids_max))):
            base = cls._v1_base(ctrl)
            if not base:
                continue
            d = os.path.join(base, name)
            try:
                os.mkdir(d)
            except OSError:
                continue
            if not cls._write(os.path.join(d, fname), val):
                try:
                    os.rmdir(d)
                except OSError:
                    pass
                continue
            if ctrl == "memory":       # 스왑으로 한도를 우회하지 못하게(있을 때만)
                cls._write(os.path.join(d, "memory.memsw.limit_in_bytes"), str(mem_bytes))
            made.append(d)
        return cls(made, "v1") if made else None

    def add(self, pid: int):
        for d in self.dirs:
            self._write(os.path.join(d, "cgroup.procs"), str(pid))

    @classmethod
    def sweep_stale(cls):
        """서버가 비정상 종료(SIGKILL 등)해 남은 이전 실행의 cgroup 을 치운다(이름에 서버 pid 가 있다)."""
        bases = [b for b in (cls._v2_base(), cls._v1_base("memory"), cls._v1_base("pids")) if b]
        for base in bases:
            try:
                names = os.listdir(base)
            except OSError:
                continue
            for n in names:
                m = re.fullmatch(r"rework-sbx-(\d+)-\w+", n)
                if m and not _pid_alive(int(m.group(1))):
                    cls([os.path.join(base, n)], "stale").cleanup()

    def procs(self) -> list:
        out = []
        for d in self.dirs:
            try:
                with open(os.path.join(d, "cgroup.procs")) as fh:
                    out += [int(x) for x in fh.read().split()]
            except (OSError, ValueError):
                pass
        return sorted(set(out))

    def cleanup(self):
        # 남은 프로세스를 모두 죽이고(v2 는 cgroup.kill) 디렉터리를 지운다
        for d in self.dirs:
            self._write(os.path.join(d, "cgroup.kill"), "1")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            left = self.procs()
            if not left:
                break
            for pid in left:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            time.sleep(0.03)
        for d in self.dirs:
            for _ in range(20):
                try:
                    os.rmdir(d)
                    break
                except OSError:
                    time.sleep(0.05)


# ---- 상태(사용 가능 여부) -----------------------------------------------------
@dataclass
class SandboxStatus:
    available: bool
    reason: str = ""
    hint: str = ""
    bwrap: str = ""
    version: str = ""
    drop_ok: bool = False          # 낮은 uid 로 낮춘 채 격리를 만들 수 있는가
    running_as_root: bool = False
    seccomp: bool = False
    cgroup: bool = False
    disable_userns: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


_PROBE_LOCK = threading.Lock()
_PROBE_CACHE: dict = {"t": 0.0, "status": None}


def find_bwrap() -> str | None:
    return shutil.which("bwrap")


def _bwrap_version(path: str) -> str:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
        return (out.stdout or out.stderr).strip()
    except Exception:
        return ""


def _bwrap_supports(path: str, flag: str) -> bool:
    try:
        out = subprocess.run([path, "--help"], capture_output=True, text=True, timeout=5)
        return flag in (out.stdout + out.stderr)
    except Exception:
        return False


_INSTALL_HINT = ("bubblewrap 을 설치하세요: `sudo apt install bubblewrap` (Kali·Debian·Ubuntu), "
                 "`sudo dnf install bubblewrap` (Fedora).")
_USERNS_HINT = ("커널이 사용자 네임스페이스를 막고 있습니다. `sudo sysctl -w kernel.unprivileged_userns_clone=1` "
                "(Debian 계열), Ubuntu 24.04 는 `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` "
                "또는 root 로 실행하세요. 컨테이너 안이라면 컨테이너에 네임스페이스 권한이 필요합니다.")


def _drop_kwargs(policy: SandboxPolicy) -> dict:
    """서버가 root 면 샌드박스 프로세스를 낮은 uid 로 낮춘다."""
    if policy.drop_privileges and os.geteuid() == 0:
        uid = policy.run_as_uid
        return {"user": uid, "group": uid, "extra_groups": []}
    return {}


def probe(force: bool = False) -> SandboxStatus:
    """bwrap 이 있고 이 환경에서 실제로 격리를 만들 수 있는지 작은 샌드박스를 돌려 확인한다."""
    with _PROBE_LOCK:
        now = time.monotonic()
        if not force and _PROBE_CACHE["status"] is not None and now - _PROBE_CACHE["t"] < 30:
            return _PROBE_CACHE["status"]
        st = _probe_uncached()
        _PROBE_CACHE.update(t=now, status=st)
        return st


def _probe_uncached() -> SandboxStatus:
    if platform.system() != "Linux":
        return SandboxStatus(False, "Linux 가 아닙니다.", "격리 실행은 Linux 전용입니다. Windows 는 WSL2 나 VM 안에서 rework 를 실행하세요.")
    bw = find_bwrap()
    if not bw:
        return SandboxStatus(False, "bubblewrap(bwrap)이 설치되어 있지 않습니다.", _INSTALL_HINT)
    st = SandboxStatus(False, bwrap=bw, version=_bwrap_version(bw),
                       running_as_root=(os.geteuid() == 0), seccomp=seccomp_supported(),
                       cgroup=CgroupLimit.available(), disable_userns=_bwrap_supports(bw, "--disable-userns"))
    policy = SandboxPolicy()
    attempts = [("낮춘 uid", _drop_kwargs(policy))]
    if st.running_as_root:
        attempts.append(("현재 사용자(root)", {}))
    last_err = ""
    for label, kw in attempts:
        try:
            fds = _FdBag()
            try:
                argv = _bwrap_argv(policy, bw, command=["/usr/bin/true"], fds=fds, probing=True,
                                   disable_userns=st.disable_userns)
                r = subprocess.run(argv, capture_output=True, text=True, timeout=15,
                                   pass_fds=tuple(fds.fds), **kw)
            finally:
                fds.close()
        except Exception as e:                       # noqa: BLE001
            last_err = str(e)
            continue
        if r.returncode == 0:
            st.available = True
            st.drop_ok = bool(kw) or not st.running_as_root
            if st.running_as_root and not kw:
                st.reason = "낮은 uid 로 낮춘 격리가 실패해 root 로 격리합니다(탈출 시 root 권한이 되는 약점)."
            return st
        last_err = (r.stderr or r.stdout).strip()[-300:]
    st.reason = "이 환경에서 격리를 만들 수 없습니다: " + (last_err or "알 수 없는 오류")
    st.hint = _USERNS_HINT
    return st


# ---- bwrap 명령 구성 ----------------------------------------------------------
class _FdBag:
    """bwrap 에 넘길 임시 fd(합성 파일·seccomp 필터)를 모아 두었다가 닫는다."""

    def __init__(self):
        self.fds: list = []

    def data(self, payload: bytes) -> int:
        fd = os.memfd_create("rework-sbx", 0)
        os.write(fd, payload)
        os.lseek(fd, 0, os.SEEK_SET)
        os.set_inheritable(fd, True)
        self.fds.append(fd)
        return fd

    def close(self):
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds = []


_EXPOSED_USR = ("bin", "sbin", "lib", "lib32", "lib64", "libexec", "share")
_USRMERGE_LINKS = ("bin", "sbin", "lib", "lib32", "lib64")
_ETC_RO = ("ld.so.cache", "ld.so.conf", "ld.so.conf.d", "alternatives")
INNER_UID = 1000
GDB_AS_MARGIN_MB = 1024            # 주소공간 한도에 더하는 디버거(gdb) 몫 — gdb 는 기호가 큰 대상에서 수백 MB 를 쓴다
SB_TARGET_DIR = "/sandbox"
SB_TTY = "/dev/rework-tty"


def _bwrap_argv(policy: SandboxPolicy, bwrap: str, *, command: list, fds: _FdBag,
                target_src: str | None = None, target_name: str = "target",
                tty_src: str | None = None, seccomp_fd: int | None = None,
                block_fd: int | None = None, probing: bool = False,
                disable_userns: bool = False) -> list:
    # --unshare-all 의 user ns 는 "가능하면"(try)이라 --uid·--disable-userns 와 어울리지 않는다 → 명시적으로 요구
    a = [bwrap, "--die-with-parent", "--new-session", "--unshare-all", "--unshare-user"]
    if policy.network == "host":
        a.append("--share-net")
    a += ["--hostname", "rework-sandbox", "--uid", str(INNER_UID), "--gid", str(INNER_UID),
          "--cap-drop", "ALL"]
    if disable_userns:
        a.append("--disable-userns")      # 중첩 user namespace 를 커널 수준에서 금지(seccomp 이 없는 환경 대비)
    a.append("--clearenv")
    for k, v in (("PATH", "/usr/bin:/bin"), ("HOME", "/tmp"), ("TERM", "dumb"),
                 ("LANG", "C.UTF-8"), ("USER", "sandbox")):
        a += ["--setenv", k, v]

    # 읽기 전용 최소 루트: 허용 목록(/usr/local·홈·/var·/run 등은 보이지 않는다)
    merged = all(os.path.islink(f"/{n}") for n in ("bin", "lib"))
    for d in _EXPOSED_USR:
        p = f"/usr/{d}"
        if os.path.isdir(p):
            a += ["--ro-bind", p, p]
    for n in _USRMERGE_LINKS:
        if merged:
            if os.path.exists(f"/usr/{n}") and os.path.islink(f"/{n}"):
                a += ["--symlink", f"usr/{n}", f"/{n}"]
        elif os.path.isdir(f"/{n}") and not os.path.islink(f"/{n}"):
            a += ["--ro-bind", f"/{n}", f"/{n}"]
    for n in _ETC_RO:
        if os.path.exists(f"/etc/{n}"):
            a += ["--ro-bind", f"/etc/{n}", f"/etc/{n}"]
    # 합성 /etc: 대상이 사용자·호스트 이름을 조회해도 호스트 파일을 보지 않게
    for dst, text in (("/etc/passwd", f"sandbox:x:{INNER_UID}:{INNER_UID}:sandbox:/tmp:/bin/sh\n"),
                      ("/etc/group", f"sandbox:x:{INNER_UID}:\n"),
                      ("/etc/hosts", "127.0.0.1 localhost\n::1 localhost\n"),
                      ("/etc/nsswitch.conf", "passwd: files\ngroup: files\nhosts: files\n")):
        a += ["--ro-bind-data", str(fds.data(text.encode())), dst]
    a += ["--proc", "/proc", "--dev", "/dev",
          "--size", str(min(policy.tmp_mb, 64) * 1024 * 1024), "--tmpfs", "/dev/shm",
          "--size", str(policy.tmp_mb * 1024 * 1024), "--tmpfs", "/tmp", "--chdir", "/tmp"]
    if tty_src:
        a += ["--dev-bind", tty_src, SB_TTY]          # 호스트 pty 하나만 노출
    if target_src:
        a += ["--ro-bind", target_src, f"{SB_TARGET_DIR}/{target_name}"]
    for p in policy.extra_ro:
        a += ["--ro-bind", os.path.realpath(p), p]
    # bwrap 의 루트와 /dev 는 쓰기 가능한 tmpfs(크기 제한 없음) — 잠가야 RAM 을 무제한으로 쓰지 못한다.
    # 쓸 수 있는 곳은 크기가 정해진 /tmp 와 /dev/shm 뿐이다.
    a += ["--remount-ro", "/dev", "--remount-ro", "/"]
    if seccomp_fd is not None:
        a += ["--seccomp", str(seccomp_fd)]
    if block_fd is not None:
        a += ["--block-fd", str(block_fd)]
    return a + ["--"] + command


def resolve_gdb() -> str:
    """샌드박스 안에서 쓸 gdb 경로. /usr 밖(예: /usr/local, /opt)에 있으면 노출되지 않으므로 거부."""
    gdb = shutil.which("gdb")
    if not gdb:
        raise SandboxError("GDB 를 찾을 수 없습니다. 설치하세요 (예: apt install gdb).")
    real = os.path.realpath(gdb)
    if not (real.startswith("/usr/bin/") or real.startswith("/usr/sbin/") or real.startswith("/usr/lib")
            or real.startswith("/usr/libexec/")):
        raise SandboxError(f"gdb({real})가 /usr 밖에 있어 격리 환경에서 쓸 수 없습니다. "
                           "배포판 패키지의 gdb 를 설치하거나 격리를 끄세요.")
    return "gdb"


# ---- rlimit ------------------------------------------------------------------
def _count_uid_procs(uid: int) -> int:
    n = 0
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/status") as fh:
                for line in fh:
                    if line.startswith("Uid:"):
                        if int(line.split()[1]) == uid:
                            n += 1
                        break
        except OSError:
            continue
    return n


def _make_preexec(policy: SandboxPolicy, uid: int):
    """exec 직전에 자식에게 자원 한도를 건다(권한을 낮춘 뒤에 호출되므로 한도를 낮추기만 한다).
    NPROC 는 호스트의 같은 uid 프로세스도 함께 세므로 '현재 수 + 허용량' 으로 잡는다."""
    nproc = _count_uid_procs(uid) + policy.max_processes
    mb = 1024 * 1024
    limits = (
        (resource.RLIMIT_AS, (policy.memory_mb + GDB_AS_MARGIN_MB) * mb),   # gdb 자신의 몫을 더한다
        (resource.RLIMIT_CPU, policy.cpu_seconds),
        (resource.RLIMIT_FSIZE, policy.max_file_mb * mb),
        (resource.RLIMIT_NOFILE, policy.max_open_files),
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_NPROC, nproc),
    )

    def apply():
        for res, val in limits:
            try:
                resource.setrlimit(res, (val, val))
            except (ValueError, OSError):
                pass
    return apply


# ---- 프로세스 트리 ------------------------------------------------------------
def _ppid_map() -> dict:
    m = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as fh:
                s = fh.read()
            m[int(d)] = int(s[s.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
    return m


def descendants(pid: int) -> list:
    pm = _ppid_map()
    out, frontier = [], [pid]
    while frontier:
        cur = frontier.pop()
        kids = [p for p, pp in pm.items() if pp == cur]
        out += kids
        frontier += kids
    return out


def _pidns_inode(pid: int) -> int | None:
    try:
        return os.stat(f"/proc/{pid}/ns/pid").st_ino
    except OSError:
        return None


def kill_pidns(inode: int, exclude: int | None = None) -> int:
    """pid 네임스페이스 inode 가 같은 모든 프로세스를 죽인다(남은 것이 없을 때까지)."""
    killed = 0
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        found = 0
        for d in os.listdir("/proc"):
            if not d.isdigit() or int(d) == exclude:
                continue
            if _pidns_inode(int(d)) == inode:
                found += 1
                try:
                    os.kill(int(d), signal.SIGKILL)
                    killed += 1
                except OSError:
                    pass
        if not found:
            break
        time.sleep(0.03)
    return killed


# ---- 실행 준비 ----------------------------------------------------------------
@dataclass
class Launch:
    argv: list
    pass_fds: tuple
    popen_kwargs: dict
    tty_target: str
    target_path: str
    workdir: str
    uid: int | None                    # 낮춘 호스트 uid(없으면 None)
    info: dict
    cgroup: CgroupLimit | None = None
    block_w: int | None = None
    ns_inode: int | None = None
    _fds: _FdBag | None = None
    _blocked: bool = False

    def attach(self, proc):
        """Popen 직후 호출: cgroup 에 넣고 샌드박스를 풀어 준다(--block-fd)."""
        try:
            if self.cgroup:
                pids = [proc.pid] + descendants(proc.pid)
                for p in pids:
                    self.cgroup.add(p)
        finally:
            if self.block_w is not None:
                try:
                    os.write(self.block_w, b"x")
                finally:
                    os.close(self.block_w)
                    self.block_w = None
        # 샌드박스 pid 네임스페이스 inode(정리 때 남은 프로세스를 찾는 데 쓴다)
        deadline = time.monotonic() + 2.0
        outer = _pidns_inode(proc.pid)
        while time.monotonic() < deadline and self.ns_inode is None:
            for c in descendants(proc.pid):
                ino = _pidns_inode(c)
                if ino and ino != outer:
                    self.ns_inode = ino
                    break
            else:
                time.sleep(0.02)

    def cleanup(self, outer_pid: int | None = None):
        if self.ns_inode:
            kill_pidns(self.ns_inode, exclude=os.getpid())
        if self.cgroup:
            self.cgroup.cleanup()
            self.cgroup = None
        if self.block_w is not None:
            try:
                os.close(self.block_w)
            except OSError:
                pass
            self.block_w = None
        if self._fds:
            self._fds.close()
        shutil.rmtree(self.workdir, ignore_errors=True)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


_SWEPT = {"done": False}


def _sweep_stale():
    """이전 서버가 비정상 종료해 남긴 작업 폴더·cgroup 을 한 번 치운다."""
    if _SWEPT["done"]:
        return
    _SWEPT["done"] = True
    base = tempfile.gettempdir()
    try:
        for n in os.listdir(base):
            m = re.fullmatch(r"rework-sbx-(\d+)-\w+", n)
            if m and not _pid_alive(int(m.group(1))):
                shutil.rmtree(os.path.join(base, n), ignore_errors=True)
    except OSError:
        pass
    CgroupLimit.sweep_stale()


def prepare(policy: SandboxPolicy, target_path: str, tty_src: str, gdb_args: list) -> Launch:
    """격리 실행에 필요한 모든 것을 준비한다. 사용할 수 없으면 SandboxError.

    - 대상은 읽기 전용 복사본으로 만들어 /sandbox/<name> 으로 넣는다.
    - gdb_args 는 gdb 인자 목록(마지막에 샌드박스 안의 대상 경로가 붙는다).
    """
    status = probe()
    if not status.available:
        raise SandboxError(status.reason + (" " + status.hint if status.hint else ""))
    gdb = resolve_gdb()
    kw = _drop_kwargs(policy) if status.drop_ok else {}
    uid = kw.get("user")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(target_path)) or "target"

    if uid is not None:                       # 낮춘 uid 가 읽지 못하는 경로는 시작 전에 알려 준다
        for p in policy.extra_ro:
            bad = _accessible_as(uid, kw.get("group", uid), os.path.realpath(p))
            if bad:
                raise SandboxError(f"추가 노출 경로 {p} 를 격리 사용자(uid {uid})가 읽을 수 없습니다"
                                   f"({bad} 의 권한). `chmod o+rx` 후 다시 시도하세요.")

    _sweep_stale()
    workdir = tempfile.mkdtemp(prefix=f"rework-sbx-{os.getpid()}-")
    fds = _FdBag()
    cg = None
    block_r = block_w = None
    try:
        os.chmod(workdir, 0o755)              # 낮춘 uid 가 지나갈 수 있어야 한다
        copy = os.path.join(workdir, name)
        shutil.copyfile(target_path, copy)
        os.chmod(copy, 0o555)

        seccomp_fd = None
        if policy.seccomp and seccomp_supported():
            bpf = build_seccomp_bpf()
            if bpf:
                seccomp_fd = fds.data(bpf)
        cg = CgroupLimit.create(f"rework-sbx-{os.getpid()}-{os.path.basename(workdir).rsplit('-', 1)[-1]}",
                                policy.memory_mb * 1024 * 1024 * 2, policy.max_processes + 8)
        if cg:
            block_r, block_w = os.pipe()
            os.set_inheritable(block_r, True)
            fds.fds.append(block_r)

        argv = _bwrap_argv(policy, status.bwrap, fds=fds, target_src=copy, target_name=name,
                           tty_src=tty_src, seccomp_fd=seccomp_fd, block_fd=block_r,
                           disable_userns=status.disable_userns,
                           command=[gdb] + gdb_args + [f"{SB_TARGET_DIR}/{name}"])
        host_uid = uid if uid is not None else os.geteuid()
        kw = dict(kw)
        kw["preexec_fn"] = _make_preexec(policy, host_uid)
        info = describe(policy, status, seccomp=seccomp_fd is not None, cgroup=cg, uid=uid)
        launch = Launch(argv=argv, pass_fds=tuple(fds.fds), popen_kwargs=kw, tty_target=SB_TTY,
                        target_path=f"{SB_TARGET_DIR}/{name}", workdir=workdir, uid=uid, info=info,
                        cgroup=cg, block_w=block_w, _fds=fds)
        return launch
    except Exception:
        for fd in (block_w,):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if cg:
            cg.cleanup()
        fds.close()
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def describe(policy: SandboxPolicy, status: SandboxStatus | None = None, *, seccomp: bool | None = None,
             cgroup=None, uid: int | None = None, potential: bool = False) -> dict:
    """UI 에 보여 줄 격리 보고서: 어떤 층이 실제로 켜져 있는가."""
    if not policy.enabled:
        return {"enabled": False, "summary": "비격리 — 대상이 이 컴퓨터에서 그대로 실행됩니다",
                "layers": []}
    if potential and uid is None and status is not None and status.drop_ok \
            and policy.drop_privileges and os.geteuid() == 0:
        uid = policy.run_as_uid                      # 시작하면 이 uid 로 낮춰질 것이다
    sec_on = seccomp if seccomp is not None else (policy.seccomp and seccomp_supported())
    cg_on = bool(cgroup) if not potential else bool(status and status.cgroup)
    root = os.geteuid() == 0
    layers = [
        {"id": "ns", "label": "네임스페이스(프로세스·네트워크·IPC·호스트명·사용자)", "active": True,
         "detail": "호스트 프로세스가 보이지 않고 중첩 user namespace 를 만들 수 없음"},
        {"id": "fs", "label": "읽기 전용 최소 파일시스템", "active": True,
         "detail": f"/usr 일부만 읽기 전용, /tmp 는 빈 tmpfs({policy.tmp_mb}MB), 호스트 홈·/etc·데이터는 안 보임"
                   + (f", 추가 노출 {len(policy.extra_ro)}개" if policy.extra_ro else "")},
        {"id": "copy", "label": "대상은 읽기 전용 복사본", "active": True,
         "detail": "원본 파일이 잠기거나 변조되지 않음"},
        {"id": "net", "label": "네트워크 차단" if policy.network == "none" else "호스트 네트워크 공유(위험)",
         "active": policy.network == "none", "detail": "루프백만 존재" if policy.network == "none"
         else "대상이 이 컴퓨터의 네트워크로 통신할 수 있음"},
        {"id": "env", "label": "환경 변수 비움", "active": True, "detail": "API 키 등 비밀이 대상에 새지 않음"},
        {"id": "caps", "label": "권한(capability) 전부 제거", "active": True, "detail": "no-new-privs"},
        {"id": "uid", "label": "낮은 권한 uid 로 실행" if (uid or not root) else "root 로 실행(낮추지 못함)",
         "active": bool(uid) or not root,
         "detail": (f"uid {uid}" if uid else ("현재 사용자 권한" if not root else "탈출 시 root 권한 — 비권장"))},
        {"id": "seccomp", "label": "위험 시스템 콜 차단(seccomp)", "active": bool(sec_on),
         "detail": (f"{len(_X86_64_DENY) + 2}개 시스템 콜·네임스페이스 생성·비표준 소켓 차단" if sec_on
                    else ("정책에서 끔" if not policy.seccomp else "이 아키텍처는 미지원"))},
        {"id": "rlimit", "label": "자원 한도(rlimit)", "active": True,
         "detail": f"CPU {policy.cpu_seconds}초, 주소공간 {policy.memory_mb}MB(+디버거 {GDB_AS_MARGIN_MB}MB)/프로세스, "
                   f"프로세스 +{policy.max_processes}, 파일 {policy.max_file_mb}MB"},
        {"id": "cgroup", "label": "cgroup 총 메모리·프로세스 한도", "active": cg_on,
         "detail": (f"메모리 {policy.memory_mb * 2}MB, 프로세스 {policy.max_processes + 8}" if cg_on
                    else "권한이 없거나 지원하지 않아 미적용(rlimit 로만 제한)")},
    ]
    if policy.wall_seconds:
        layers.append({"id": "wall", "label": "실행 시간 제한", "active": True,
                       "detail": f"{policy.wall_seconds}초 후 자동 종료"})
    inactive = [l["label"] for l in layers if not l["active"]]
    summary = "격리 실행" + (f" — 주의: {', '.join(inactive)}" if inactive else "")
    return {"enabled": True, "summary": summary, "layers": layers, "network": policy.network}
