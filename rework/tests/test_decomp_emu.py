"""디컴파일러 차분 시험(에뮬레이터판): 호스트와 다른 아키텍처(x86-32 · ARM64 · ARM32 · Windows x64 ABI)도 같은 방식으로 검증한다.

1) clang 으로 코퍼스(tests/decomp_corpus.c)를 대상 아키텍처의 자립(freestanding) 실행 파일로 컴파일한다.
2) 그 파일의 각 함수를 디컴파일해 C 로 되돌리고, 원본 + 의사코드(dec_*) 를 한 이미지로 다시 컴파일한다.
3) Unicorn 에뮬레이터로 같은 입력에서 원본과 의사코드 함수를 각각 실행해 반환값과 메모리 결과를 비교한다.

실행:  .venv/bin/python tests/test_decomp_emu.py [i386 arm64 arm win64 x64] [-O0 -O1 -O2] [-k 함수,...] [-v]
필요: clang · ld.lld · unicorn(pip install unicorn)
"""
import os, random, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, HERE)
WORK = tempfile.mkdtemp(prefix="rework-decomp-emu-")
os.environ["REWORK_DATA"] = os.path.join(WORK, "data")

import lief                                         # noqa: E402
from unicorn import (Uc, UC_ARCH_X86, UC_ARCH_ARM64, UC_ARCH_ARM, UC_MODE_32, UC_MODE_64, UC_MODE_ARM, UcError,   # noqa: E402
                     UC_PROT_ALL)
from unicorn import x86_const as X, arm64_const as A64, arm_const as A32      # noqa: E402

from app.loader import load                         # noqa: E402
from app.analysis import Program                    # noqa: E402
from app.decompile import Decompiler                # noqa: E402
from test_decomp import SPECS, CORPUS, c_param_list, HELPERS as _H   # noqa: E402

HELPERS = """#include <stdint.h>
#ifdef __SIZEOF_INT128__
static inline int64_t __mulhs64(int64_t a, int64_t b) { return (int64_t)(((__int128)a * b) >> 64); }
static inline uint64_t __mulhu64(uint64_t a, uint64_t b) { return (uint64_t)(((unsigned __int128)a * b) >> 64); }
#endif
static inline int32_t __mulhs32(int32_t a, int32_t b) { return (int32_t)(((int64_t)a * b) >> 32); }
static inline uint32_t __mulhu32(uint32_t a, uint32_t b) { return (uint32_t)(((uint64_t)a * b) >> 32); }
static uint64_t __stack_chk_guard;
"""

ARCH = {
    "i386": dict(target="i386-linux-gnu", cflags=[], uc=(UC_ARCH_X86, UC_MODE_32), ptr=4, long=4, abi="cdecl"),
    "arm64": dict(target="aarch64-linux-gnu", cflags=[], uc=(UC_ARCH_ARM64, UC_MODE_ARM), ptr=8, long=8, abi="aapcs64"),
    "arm": dict(target="armv7-linux-gnueabihf", cflags=["-mcpu=cortex-a15", "-marm", "-mfloat-abi=soft"],
                uc=(UC_ARCH_ARM, UC_MODE_ARM), ptr=4, long=4, abi="aapcs"),
    "x64": dict(target="x86_64-linux-gnu", cflags=[], uc=(UC_ARCH_X86, UC_MODE_64), ptr=8, long=8, abi="sysv"),
    "win64": dict(target="x86_64-linux-gnu", cflags=["-DMS_ABI"], uc=(UC_ARCH_X86, UC_MODE_64), ptr=8, long=8, abi="win64"),
}

STACK_BASE, STACK_SIZE = 0x7ff00000, 0x100000
DATA_BASE, DATA_SIZE = 0x30000000, 0x40000
SENTINEL = 0x40000000
BUF_STRIDE = 0x1000


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def cc(arch, opt, src, out, extra=()):
    a = ARCH[arch]
    cmd = ["clang", f"--target={a['target']}"] + a["cflags"] + [opt, "-ffreestanding", "-nostdlib", "-static", "-fuse-ld=lld", "-w",
                                                             "-fno-stack-protector", "-fno-pic", "-fwrapv", "-fno-builtin", "-o", out, src,
                                                             "-Wl,-e,main", "-Wl,--no-gc-sections"] + list(extra)
    r = sh(cmd)
    if r.returncode:
        raise SystemExit(f"{arch} {opt} 컴파일 실패:\n{r.stderr[:2000]}")


# ---- 에뮬레이터 ----------------------------------------------------------------------------------
class Machine:
    def __init__(self, arch, exe):
        self.arch = arch
        self.a = ARCH[arch]
        arch_id, mode = self.a["uc"]
        self.uc = Uc(arch_id, mode)
        lb = lief.parse(exe)
        for seg in lb.segments:
            if seg.type != lief.ELF.Segment.TYPE.LOAD:
                continue
            start = seg.virtual_address & ~0xFFF
            end = (seg.virtual_address + seg.virtual_size + 0xFFF) & ~0xFFF
            for pg in range(start, end, 0x1000):
                try:
                    self.uc.mem_map(pg, 0x1000, UC_PROT_ALL)
                except UcError:
                    pass                                        # 이미 매핑된 쪽(세그먼트가 같은 쪽을 나눠 쓴다)
            self.uc.mem_write(seg.virtual_address, bytes(seg.content))
        self.uc.mem_map(STACK_BASE, STACK_SIZE, UC_PROT_ALL)
        self.uc.mem_map(DATA_BASE, DATA_SIZE, UC_PROT_ALL)
        self.syms = {s.name: s.value for s in lb.symbols if s.name}

    def reset_stack(self):
        self.uc.mem_write(STACK_BASE + STACK_SIZE - 0x4000, b"\x00" * 0x4000)

    def call(self, addr, args, ret_size):
        """args: [(값, 폭)] 정수 인자 목록. 반환: 정수(부호 없는 ret_size 바이트) 또는 None."""
        uc, a = self.uc, self.a
        sp = STACK_BASE + STACK_SIZE - 0x1000
        mask_ = lambda v, n: v & ((1 << (8 * n)) - 1)
        if a["abi"] == "cdecl":
            uc.reg_write(X.UC_X86_REG_ESP, sp - 4 * len(args) - 4)
            for i, (v, n) in enumerate(args):
                uc.mem_write(sp - 4 * len(args) - 4 + 4 + 4 * i, mask_(v, 4).to_bytes(4, "little"))
            uc.mem_write(sp - 4 * len(args) - 4, SENTINEL.to_bytes(4, "little"))
            uc.emu_start(addr, SENTINEL, count=3_000_000)
            r = uc.reg_read(X.UC_X86_REG_EAX)
        elif a["abi"] in ("sysv", "win64"):
            regs = ([X.UC_X86_REG_RDI, X.UC_X86_REG_RSI, X.UC_X86_REG_RDX, X.UC_X86_REG_RCX, X.UC_X86_REG_R8, X.UC_X86_REG_R9]
                    if a["abi"] == "sysv" else [X.UC_X86_REG_RCX, X.UC_X86_REG_RDX, X.UC_X86_REG_R8, X.UC_X86_REG_R9])
            stack_args = args[len(regs):]
            shadow = 32 if a["abi"] == "win64" else 0
            base = (sp - 8 * len(stack_args) - shadow - 8) & ~0xF
            base -= 8
            rng = random.Random(addr)
            for i, (v, n) in enumerate(args[:len(regs)]):
                junk = rng.getrandbits(64) & ~((1 << (8 * n)) - 1) if n < 8 else 0           # 좁은 인자의 윗부분은 쓰레기
                uc.reg_write(regs[i], (v & ((1 << (8 * n)) - 1)) | junk)
            for i, (v, n) in enumerate(stack_args):
                uc.mem_write(base + 8 + shadow + 8 * i, mask_(v, 8).to_bytes(8, "little"))
            uc.mem_write(base, SENTINEL.to_bytes(8, "little"))
            uc.reg_write(X.UC_X86_REG_RSP, base)
            uc.reg_write(X.UC_X86_REG_RAX, 0xDEADBEEF12345678)
            uc.emu_start(addr, SENTINEL, count=3_000_000)
            r = uc.reg_read(X.UC_X86_REG_RAX)
        elif a["abi"] == "aapcs64":
            regs = [A64.UC_ARM64_REG_X0 + i for i in range(8)]
            rng = random.Random(addr)
            for i, (v, n) in enumerate(args[:8]):
                junk = rng.getrandbits(64) & ~((1 << (8 * n)) - 1) if n < 8 else 0
                uc.reg_write(regs[i], (v & ((1 << (8 * n)) - 1)) | junk)
            for i in range(8, 18):
                uc.reg_write(A64.UC_ARM64_REG_X0 + i, rng.getrandbits(64))
            uc.reg_write(A64.UC_ARM64_REG_SP, sp & ~0xF)
            uc.reg_write(A64.UC_ARM64_REG_LR, SENTINEL)
            uc.emu_start(addr, SENTINEL, count=3_000_000)
            r = uc.reg_read(A64.UC_ARM64_REG_X0)
        elif a["abi"] == "aapcs":
            regs = [A32.UC_ARM_REG_R0, A32.UC_ARM_REG_R1, A32.UC_ARM_REG_R2, A32.UC_ARM_REG_R3]
            stack_args = args[4:]
            base = (sp - 4 * len(stack_args)) & ~0x7
            for i, (v, n) in enumerate(args[:4]):
                uc.reg_write(regs[i], mask_(v, 4))
            for i, (v, n) in enumerate(stack_args):
                uc.mem_write(base + 4 * i, mask_(v, 4).to_bytes(4, "little"))
            uc.reg_write(A32.UC_ARM_REG_SP, base)
            uc.reg_write(A32.UC_ARM_REG_LR, SENTINEL)
            uc.emu_start(addr, SENTINEL, count=3_000_000)
            r = uc.reg_read(A32.UC_ARM_REG_R0)
        else:
            raise SystemExit("알 수 없는 ABI")
        return None if ret_size == 0 else r & ((1 << (8 * ret_size)) - 1)


def make_case(rng, name, kinds, a):
    """한 번의 호출 입력: (스칼라 값 목록 / 버퍼 지정). 반환: [(종류, 값)]"""
    out = []
    for k in kinds:
        if k in ("i", "n", "m", "p", "u", "l", "L", "b", "h"):
            lo, hi = {"i": (-40, 40), "n": (0, 12), "m": (1, 12), "p": (1, 40), "u": (0, 80), "l": (-1000, 1000), "L": (0, 12), "b": (0, 255),
                      "h": (-200, 200)}[k]
            v = rng.randint(lo, hi)
            if name == "collatz":
                v = rng.randint(1, 60)
            if name == "bsearch_" and k == "n":
                v = 16
            if name in ("hash", "goto_like", "nestedloop") and k == "n":
                v = rng.randint(0, 8 if name != "hash" else 12)
            if name in ("fact", "rec_fact") and k == "n":
                v = rng.randint(0, 10)
            if name == "rec_fib" and k == "n":
                v = rng.randint(0, 14)
            if name == "fib" and k == "n":
                v = rng.randint(0, 20)
            out.append((k, v))
        elif k in ("P", "Q"):
            vals = [rng.randint(-50, 50) for _ in range(16)]
            out.append((k, sorted(vals) if k == "Q" else vals))
        elif k == "S":
            n = rng.randint(0, 10)
            out.append((k, "".join(rng.choice("abcab") for _ in range(n))))
        elif k == "C":
            out.append((k, ord(rng.choice("abc"))))
        elif k == "T":
            out.append((k, [rng.randint(-9, 9), rng.randint(-9, 9), rng.randint(-9, 9)]))
    return out


SCALAR_SIZE = {"i": 4, "n": 4, "m": 4, "p": 4, "u": 4, "b": 1, "h": 2, "C": 1}


def run_one(m, addr, kinds_vals, ret, a, buf_slots):
    """버퍼를 채우고 함수를 실행한 뒤 (반환값, 버퍼 내용) 을 돌려준다."""
    uc = m.uc
    m.reset_stack()
    args = []
    bufs = []
    for idx, (k, v) in enumerate(kinds_vals):
        if k in SCALAR_SIZE:
            # clang 은 좁은 정수 인자를 호출자가 32비트로 확장해 준다고 가정한다 → 32비트까지는 올바르게 확장하고 그 위는 쓰레기로 둔다
            sz_ = SCALAR_SIZE[k]
            if sz_ < 4:
                bits_ = 8 * sz_
                v = (v & ((1 << bits_) - 1)) if k == "b" else ((v & ((1 << bits_) - 1)) | (-(v >> (bits_ - 1) & 1) << bits_))
            args.append((v & 0xFFFFFFFF, 4))
        elif k in ("l", "L"):
            args.append((v, a["long"]))
        else:
            base = DATA_BASE + buf_slots[idx]
            if k in ("P", "Q"):
                data = b"".join((x & 0xFFFFFFFF).to_bytes(4, "little") for x in v)
            elif k == "S":
                data = v.encode() + b"\x00" * (32 - len(v))
            else:                                                   # T: struct { int x; int y; long z; }
                data = (v[0] & 0xFFFFFFFF).to_bytes(4, "little") + (v[1] & 0xFFFFFFFF).to_bytes(4, "little") + \
                    (v[2] & ((1 << (8 * a["long"])) - 1)).to_bytes(a["long"], "little")
            uc.mem_write(base, b"\x00" * 0x1000)
            uc.mem_write(base, data)
            args.append((base, a["ptr"]))
            bufs.append((base, len(data)))
    rs = {"i": 4, "u": 4, "l": a["long"], "v": 0}[ret]
    r = m.call(addr, args, rs)
    outs = [bytes(uc.mem_read(b, n)) for b, n in bufs]
    return r, outs


def run_level(arch, opt, only, verbose, seed=1, iterations=30):
    a = ARCH[arch]
    orig = os.path.join(WORK, f"orig_{arch}_{opt.strip('-')}")
    src_text = open(CORPUS).read()
    if arch == "win64":                                           # ELF 위에서 Windows x64 호출 규약(ms_abi)으로 컴파일한 코퍼스
        src_text = src_text.replace("#define FN __attribute__((noinline, noclone))", "#define FN __attribute__((noinline, noclone, ms_abi))")
    corpus_path = os.path.join(WORK, f"corpus_{arch}.c")
    with open(corpus_path, "w") as fh:
        fh.write(src_text)
    cc(arch, opt, corpus_path, orig)
    model, raw, lb = load(orig)
    if arch == "win64":
        model.fmt = "PE"                                        # 호출 규약 선택용(실제 파일은 ELF)
    prog = Program(model, raw, lb)
    by = {(prog.name_for(f.vaddr) or f.display_name): f.vaddr for f in model.functions}
    decs, entries, results = [], [], []
    for name, (ret, kinds) in SPECS.items():
        if only and name not in only:
            continue
        if name not in by:
            continue
        d = Decompiler(prog).decompile(by[name])
        lines = d["lines"]
        lits = [int(x, 16) for x in re.findall(r"\b0x[0-9a-f]{4,}\b", "".join(l["text"] for l in lines))]
        if d.get("incomplete") or any(model.section_for(v) for v in lits) or "대체했습니다" in d.get("note", ""):
            results.append((name, "SKIP", ""))
            continue
        sig = lines[0]["text"]
        _, fname, dparams = c_param_list(sig)
        text = "\n".join("    " * l["indent"] + l["text"] for l in lines)
        text = re.sub(r"\b" + re.escape(fname) + r"\(", "dec_" + fname + "(", text, count=1)
        decs.append(text)
        entries.append((name, ret, kinds, dparams))
    allc = os.path.join(WORK, f"all_{arch}_{opt.strip('-')}.c")
    ms = "#define FN_MS\n" if arch == "win64" else ""
    with open(os.path.join(WORK, "decomp_corpus.c"), "w") as fh:
        fh.write(src_text)
    # 의사코드 함수도 같은 호출 규약으로(win64): 함수 정의 앞에 속성을 붙인다
    dec_texts = []
    for t in decs:
        if arch == "win64":
            t = "__attribute__((ms_abi)) " + t
        dec_texts.append(t)
    with open(allc, "w") as fh:
        fh.write("#define main corpus_main\n#include \"decomp_corpus.c\"\n#undef main\n" + HELPERS + "\n" + "\n\n".join(dec_texts)
                 + "\nint main(void){return 0;}\n")
    # 함수별로 컴파일 가능 여부를 확인
    good = []
    for e, t in zip(entries, dec_texts):
        one = os.path.join(WORK, "one.c")
        with open(one, "w") as fh:
            fh.write("#define main corpus_main\n#include \"decomp_corpus.c\"\n#undef main\n" + HELPERS + "\n" + t + "\n")
        r = sh(["clang", f"--target={a['target']}"] + a["cflags"] + ["-c", "-w", "-ffreestanding", "-I", WORK, "-o", os.devnull, one])
        if r.returncode:
            err = [l for l in r.stderr.splitlines() if "error" in l][:2]
            results.append((e[0], "COMPILE", " / ".join(err)))
            if verbose:
                print("----", e[0], "COMPILE\n" + t + "\n" + "\n".join(err))
        else:
            good.append((e, t))
    with open(allc, "w") as fh:
        fh.write("#define main corpus_main\n#include \"decomp_corpus.c\"\n#undef main\n" + HELPERS + "\n" + "\n\n".join(t for _, t in good)
                 + "\nint main(void){return 0;}\n")
    exe = os.path.join(WORK, f"all_{arch}_{opt.strip('-')}")
    cc(arch, opt, allc, exe, extra=["-I", WORK])
    m = Machine(arch, exe)
    rng = random.Random(seed)
    for (name, ret, kinds, dparams), t in good:
        o_addr, d_addr = m.syms.get(name), m.syms.get("dec_" + name)
        if o_addr is None or d_addr is None:
            results.append((name, "COMPILE", "심볼 없음"))
            continue
        bad = 0
        first = ""
        for it in range(iterations):
            case = make_case(rng, name, kinds, a)
            slots = {i: 0x2000 + 0x1000 * i for i in range(len(case))}
            try:
                ro, bo = run_one(m, o_addr, case, ret, a, slots)
            except UcError:
                bad = -1
                break
            try:
                rd, bd = run_one(m, d_addr, case, ret, a, slots)
            except UcError as ex:
                bad += 1
                first = first or f"의사코드 실행 오류 {ex}"
                continue
            if ro != rd or bo != bd:
                bad += 1
                first = first or f"it={it} 원본={ro} 의사코드={rd}" + ("" if bo == bd else " (메모리 불일치)")
        if bad < 0:
            results.append((name, "SKIP", "원본 실행 불가"))
        elif bad:
            results.append((name, "FAIL", first))
            if verbose:
                print("----", name, "FAIL", first, "\n" + t)
        else:
            results.append((name, "PASS", ""))
    return results


def main():
    args = sys.argv[1:]
    archs, levels, only, verbose = [], [], None, False
    i = 0
    while i < len(args):
        x = args[i]
        if x == "-k":
            only = set(args[i + 1].split(","))
            i += 1
        elif x == "-v":
            verbose = True
        elif x.startswith("-O"):
            levels.append(x)
        elif x in ARCH:
            archs.append(x)
        i += 1
    archs = archs or ["i386", "arm64", "arm", "x64", "win64"]
    levels = levels or ["-O0", "-O1", "-O2"]
    fails = 0
    for arch in archs:
        for opt in levels:
            res = run_level(arch, opt, only, verbose)
            passed = sum(1 for _, s, _ in res if s == "PASS")
            skipped = sum(1 for _, s, _ in res if s == "SKIP")
            print(f"=== {arch} {opt}: {passed}/{len(res) - skipped} 통과 (건너뜀 {skipped})")
            sk = [n for n, s, _ in res if s == "SKIP"]
            if sk:
                print("  건너뜀:", ", ".join(sk))
            for n, s, d in res:
                if s not in ("PASS", "SKIP"):
                    print(f"  {s} {n}: {d}")
                    fails += 1
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
