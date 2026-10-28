"""디컴파일러 차분 시험(에뮬레이터판): 호스트와 다른 아키텍처(x86-32 · ARM64 · ARM32 · Windows x64 ABI)도 같은 방식으로 검증한다.

1) clang 으로 코퍼스(tests/decomp_corpus.c)를 대상 아키텍처의 자립(freestanding) 실행 파일로 컴파일한다.
2) 그 파일의 각 함수를 디컴파일해 C 로 되돌리고, 원본 + 의사코드(dec_*) 를 한 이미지로 다시 컴파일한다.
3) Unicorn 에뮬레이터로 같은 입력에서 원본과 의사코드 함수를 각각 실행해 반환값과 메모리 결과를 비교한다.

실행:  .venv/bin/python tests/test_decomp_emu.py [i386 i386gcc i386fast arm64 arm thumb win64 x64] [-O0 -O1 -O2] [-k 함수,...] [-v]
필요: clang · ld.lld · unicorn(pip install unicorn)
"""
import os, struct, random, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, HERE)
WORK = tempfile.mkdtemp(prefix="rework-decomp-emu-")
os.environ["REWORK_DATA"] = os.path.join(WORK, "data")

import lief                                         # noqa: E402
from unicorn import (Uc, UC_ARCH_X86, UC_ARCH_ARM64, UC_ARCH_ARM, UC_MODE_32, UC_MODE_64, UC_MODE_ARM, UC_MODE_THUMB, UcError,   # noqa: E402
                     UC_PROT_ALL)
from unicorn import x86_const as X, arm64_const as A64, arm_const as A32      # noqa: E402

from app.loader import load                         # noqa: E402
from app.analysis import Program                    # noqa: E402
from app.decompile import Decompiler                # noqa: E402
from test_decomp import SPECS, CORPUS, c_param_list, HELPERS as _H   # noqa: E402

HELPERS = """#include <stdint.h>
#if !defined(__clang__)
#define __builtin_bit_cast(T, x) ({ __typeof__(x) _bcs = (x); T _bcd; __builtin_memcpy(&_bcd, &_bcs, sizeof _bcd); _bcd; })
#endif
#define fma __builtin_fma
#define fmaf __builtin_fmaf
#define fmax __builtin_fmax
#define fmin __builtin_fmin
#define fmaxf __builtin_fmaxf
#define fminf __builtin_fminf
#define trunc __builtin_trunc
#define floor __builtin_floor
#define ceil __builtin_ceil
#define round __builtin_round
#define rint __builtin_rint
#define sqrt __builtin_sqrt
#define sqrtf __builtin_sqrtf
#define fabs __builtin_fabs
#define fabsf __builtin_fabsf
#ifndef NAN
#define NAN __builtin_nanf("")
#define INFINITY __builtin_inff()
#endif
#ifdef __SIZEOF_INT128__
static inline int64_t __mulhs64(int64_t a, int64_t b) { return (int64_t)(((__int128)a * b) >> 64); }
static inline uint64_t __mulhu64(uint64_t a, uint64_t b) { return (uint64_t)(((unsigned __int128)a * b) >> 64); }
#endif
static inline int32_t __mulhs32(int32_t a, int32_t b) { return (int32_t)(((int64_t)a * b) >> 32); }
static inline uint32_t __mulhu32(uint32_t a, uint32_t b) { return (uint32_t)(((uint64_t)a * b) >> 32); }
static uint64_t __stack_chk_guard;
static inline uint8_t __rol8(uint8_t x, unsigned k) { k &= 7; return (uint8_t)((x << k) | (x >> ((8 - k) & 7))); }
static inline uint8_t __ror8(uint8_t x, unsigned k) { return __rol8(x, 8 - (k & 7)); }
static inline uint16_t __rol16(uint16_t x, unsigned k) { k &= 15; return (uint16_t)((x << k) | (x >> ((16 - k) & 15))); }
static inline uint16_t __ror16(uint16_t x, unsigned k) { return __rol16(x, 16 - (k & 15)); }
static inline uint32_t __rol32(uint32_t x, unsigned k) { k &= 31; return (x << k) | (x >> ((32 - k) & 31)); }
static inline uint32_t __ror32(uint32_t x, unsigned k) { return __rol32(x, 32 - (k & 31)); }
static inline uint64_t __rol64(uint64_t x, unsigned k) { k &= 63; return (x << k) | (x >> ((64 - k) & 63)); }
static inline uint64_t __ror64(uint64_t x, unsigned k) { return __rol64(x, 64 - (k & 63)); }
static inline uint32_t __clz32(uint32_t x) { return x ? __builtin_clz(x) : 32; }
static inline uint32_t __rbit32(uint32_t x) { uint32_t r = 0; for (int i = 0; i < 32; i++) r |= ((x >> i) & 1u) << (31 - i); return r; }
static inline uint32_t __rev1632(uint32_t x) { return ((x & 0x00ff00ffu) << 8) | ((x >> 8) & 0x00ff00ffu); }
"""

SUPPORT = """
void *memcpy(void *d, const void *s, unsigned long n) { char *a = d; const char *b = s; while (n--) *a++ = *b++; return d; }
void *memset(void *d, int c, unsigned long n) { char *a = d; while (n--) *a++ = (char)c; return d; }
"""

# x87 시험 지원: 컴파일러가 라이브러리 호출로 남기는 초월함수를 x87 명령으로 구현한다(인라인한 쪽과 같은 명령이라 결과가 같다)
SUPPORT_X87 = r"""
double sin(double x) { double r; __asm__(".intel_syntax noprefix; fsin; .att_syntax" : "=t"(r) : "0"(x)); return r; }
double cos(double x) { double r; __asm__(".intel_syntax noprefix; fcos; .att_syntax" : "=t"(r) : "0"(x)); return r; }
double tan(double x) { double r; __asm__(".intel_syntax noprefix; fptan; fstp st(0); .att_syntax" : "=t"(r) : "0"(x)); return r; }
float sinf(float x) { double r; __asm__(".intel_syntax noprefix; fsin; .att_syntax" : "=t"(r) : "0"((double)x)); return (float)r; }
double atan2(double y, double x) { double r; __asm__(".intel_syntax noprefix; fpatan; .att_syntax" : "=t"(r) : "0"(x), "u"(y) : "st(1)"); return r; }
double log2(double x) { double r; __asm__(".intel_syntax noprefix; fld1; fxch; fyl2x; .att_syntax" : "=t"(r) : "0"(x)); return r; }
double log(double x) { double r; __asm__(".intel_syntax noprefix; fldln2; fxch; fyl2x; .att_syntax" : "=t"(r) : "0"(x)); return r; }
double exp2(double x) { double r; __asm__(".intel_syntax noprefix; fld st(0); frndint; fxch; fsub st(0), st(1); f2xm1; fld1; faddp st(1), st(0); fscale; fstp st(1); .att_syntax" : "=t"(r) : "0"(x)); return r; }
double fmod(double a, double b) { double r; __asm__(".intel_syntax noprefix; 0: fprem; fnstsw ax; sahf; jp 0b; fstp st(1); .att_syntax" : "=t"(r) : "0"(a), "u"(b) : "ax", "cc", "st(1)"); return r; }
static double round_mode(double x, unsigned short rc) { unsigned short cw, cw2; double r; __asm__ volatile("fnstcw %0" : "=m"(cw)); cw2 = (cw & 0xf3ff) | rc;
  __asm__ volatile("fldcw %0" :: "m"(cw2)); __asm__("frndint" : "=t"(r) : "0"(x)); __asm__ volatile("fldcw %0" :: "m"(cw)); return r; }
double rint(double x) { double r; __asm__("frndint" : "=t"(r) : "0"(x)); return r; }
double floor(double x) { return round_mode(x, 0x400); }
double ceil(double x) { return round_mode(x, 0x800); }
double trunc(double x) { return round_mode(x, 0xc00); }
"""

ARCH = {
    "i386": dict(target="i386-linux-gnu", cflags=["-DX87_TESTS"], uc=(UC_ARCH_X86, UC_MODE_32), ptr=4, long=4, abi="cdecl"),
    # gcc -m32: clang 과 다른 x87 관용구(fnstsw+sahf·fcmov·fld1 …)를 시험한다. i386fast 는 인라인 초월함수(fsin·fpatan·fprem …)까지 쓰게 한다
    "i386gcc": dict(target="i386-linux-gnu", cc="gcc", cflags=["-m32", "-march=i686", "-DX87_TESTS"], uc=(UC_ARCH_X86, UC_MODE_32), ptr=4, long=4, abi="cdecl"),
    "i386fast": dict(target="i386-linux-gnu", cc="gcc", cflags=["-m32", "-march=i686", "-ffast-math", "-mfpmath=387", "-DX87_TESTS"], uc=(UC_ARCH_X86, UC_MODE_32),
                     ptr=4, long=4, abi="cdecl"),
    "arm64": dict(target="aarch64-linux-gnu", cflags=[], uc=(UC_ARCH_ARM64, UC_MODE_ARM), ptr=8, long=8, abi="aapcs64"),
    "arm": dict(target="armv7-linux-gnueabi", cflags=["-mcpu=cortex-a15", "-marm", "-mfloat-abi=soft", "-DNO_FP"],
                uc=(UC_ARCH_ARM, UC_MODE_ARM), ptr=4, long=4, abi="aapcs"),
    "thumb": dict(target="thumbv7-linux-gnueabi", cflags=["-mcpu=cortex-a15", "-mthumb", "-mfloat-abi=soft", "-DNO_FP"],
                  uc=(UC_ARCH_ARM, UC_MODE_THUMB), ptr=4, long=4, abi="aapcs", thumb=True),
    "x64": dict(target="x86_64-linux-gnu", cflags=[], uc=(UC_ARCH_X86, UC_MODE_64), ptr=8, long=8, abi="sysv"),
    "win64": dict(target="x86_64-linux-gnu", cflags=["-DMS_ABI"], uc=(UC_ARCH_X86, UC_MODE_64), ptr=8, long=8, abi="win64"),
}

STACK_BASE, STACK_SIZE = 0x7ff00000, 0x100000
DATA_BASE, DATA_SIZE = 0x30000000, 0x40000
SENTINEL = 0x40000000
BUF_STRIDE = 0x1000


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def compiler_cmd(a):
    """(컴파일러 실행 인자, 형식에 맞는 기본 플래그). gcc 변형은 호스트 gcc -m32 를 쓴다."""
    if a.get("cc") == "gcc":
        return ["gcc"] + a["cflags"], ["-no-pie"]
    return ["clang", f"--target={a['target']}"] + a["cflags"], ["-fuse-ld=lld"]


def cc(arch, opt, src, out, extra=()):
    a = ARCH[arch]
    base, link = compiler_cmd(a)
    cmd = base + [opt, "-ffreestanding", "-nostdlib", "-static"] + link + ["-w",
                                                             "-fno-stack-protector", "-fno-pic", "-fwrapv", "-fno-builtin", "-fno-math-errno", "-o", out, src,
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

    def _x87_result(self, ret_size):
        """ST(0) 을 `fstp tbyte` 스텁으로 메모리에 꺼내 float(4)·double(8) 비트 패턴으로 바꾼다. 스택이 비어 있으면 None."""
        uc = self.uc
        tag = uc.reg_read(X.UC_X86_REG_FPTAG)
        stub, res = DATA_BASE + DATA_SIZE - 0x100, DATA_BASE + DATA_SIZE - 0x80
        uc.mem_write(stub, bytes([0xdb, 0x3d]) + res.to_bytes(4, "little"))              # fstp tbyte ptr [res]
        uc.emu_start(stub, stub + 6)
        raw = bytes(uc.mem_read(res, 10))
        mant, se = int.from_bytes(raw[:8], "little"), int.from_bytes(raw[8:], "little")
        sign, exp = se >> 15, se & 0x7FFF
        if exp == 0x7FFF:
            v = float("nan") if mant << 1 & ((1 << 64) - 1) else float("-inf" if sign else "inf")
        elif exp == 0 and mant == 0:
            v = -0.0 if sign else 0.0
        else:
            import math
            v = math.ldexp(mant, exp - 16383 - 63) * (-1 if sign else 1)
        return struct.unpack("<Q", struct.pack("<d", v))[0] if ret_size == 8 else struct.unpack("<I", struct.pack("<f", v))[0]

    def reset_stack(self):
        self.uc.mem_write(STACK_BASE + STACK_SIZE - 0x4000, b"\x00" * 0x4000)

    def call(self, addr, args, ret_size, ret_fp=False):
        """args: 위치 순서의 인자 목록. 정수는 (값, 폭), 부동소수점은 ("fp", 폭, 비트). 반환: 정수(부호 없는 ret_size 바이트) 또는 None.
        부동소수점 반환(ret_fp)은 xmm0 의 아래쪽 ret_size 바이트."""
        uc, a = self.uc, self.a
        sp = STACK_BASE + STACK_SIZE - 0x1000
        mask_ = lambda v, n: v & ((1 << (8 * n)) - 1)
        if a["abi"] == "cdecl":
            words = []                                          # 스택에 쌓이는 4바이트 단위(실수 인자: float 1칸 · double 2칸)
            for arg in args:
                if arg[0] == "fp":
                    raw = arg[2] & ((1 << (8 * arg[1])) - 1)
                    words += [(raw >> (32 * k)) & 0xFFFFFFFF for k in range(arg[1] // 4)]
                else:
                    words.append(mask_(arg[0], 4))
            top = sp - 4 * len(words) - 4
            uc.reg_write(X.UC_X86_REG_ESP, top)
            for i, w in enumerate(words):
                uc.mem_write(top + 4 + 4 * i, w.to_bytes(4, "little"))
            uc.mem_write(top, SENTINEL.to_bytes(4, "little"))
            uc.reg_write(X.UC_X86_REG_FPCW, 0x37F)               # Unicorn 은 FPU 제어 워드를 0(단정밀도·예외 해제)으로 시작한다: 리눅스 기본(확장 정밀도·예외 가림)으로
            uc.reg_write(X.UC_X86_REG_FPSW, 0)
            uc.reg_write(X.UC_X86_REG_FPTAG, 0xFFFF)             # x87 스택 비어 있음
            uc.emu_start(addr, SENTINEL, count=3_000_000)
            r = uc.reg_read(X.UC_X86_REG_EAX)
            if ret_fp:
                r = self._x87_result(ret_size)
        elif a["abi"] in ("sysv", "win64"):
            regs = ([X.UC_X86_REG_RDI, X.UC_X86_REG_RSI, X.UC_X86_REG_RDX, X.UC_X86_REG_RCX, X.UC_X86_REG_R8, X.UC_X86_REG_R9]
                    if a["abi"] == "sysv" else [X.UC_X86_REG_RCX, X.UC_X86_REG_RDX, X.UC_X86_REG_R8, X.UC_X86_REG_R9])
            win = a["abi"] == "win64"
            xregs = [X.UC_X86_REG_XMM0 + i for i in range(8)]
            int_args, ii, fi = [], 0, 0
            rng = random.Random(addr)
            for pos, arg in enumerate(args):
                if arg[0] == "fp":
                    xi = pos if win else fi
                    fi += 1
                    raw = arg[2] & ((1 << (8 * arg[1])) - 1)
                    junk = (rng.getrandbits(128) >> (8 * arg[1])) << (8 * arg[1]) if arg[1] < 16 else 0     # xmm 윗부분은 쓰레기
                    uc.reg_write(xregs[xi], (raw | junk) & ((1 << 128) - 1))
                else:
                    int_args.append((pos, arg))
            stack_args = []
            slot = 0
            for pos, (v, n) in int_args:
                ri = pos if win else slot
                slot += 1
                if ri < len(regs):
                    junk = rng.getrandbits(64) & ~((1 << (8 * n)) - 1) if n < 8 else 0           # 좁은 인자의 윗부분은 쓰레기
                    uc.reg_write(regs[ri], (v & ((1 << (8 * n)) - 1)) | junk)
                else:
                    stack_args.append((v, n))
            shadow = 32 if win else 0
            base = (sp - 8 * len(stack_args) - shadow - 8) & ~0xF
            base -= 8
            for i, (v, n) in enumerate(stack_args):
                uc.mem_write(base + 8 + shadow + 8 * i, mask_(v, 8).to_bytes(8, "little"))
            uc.mem_write(base, SENTINEL.to_bytes(8, "little"))
            uc.reg_write(X.UC_X86_REG_RSP, base)
            uc.reg_write(X.UC_X86_REG_RAX, 0xDEADBEEF12345678)
            uc.emu_start(addr, SENTINEL, count=3_000_000)
            r = uc.reg_read(X.UC_X86_REG_XMM0 if ret_fp else X.UC_X86_REG_RAX)
        elif a["abi"] == "aapcs64":
            regs = [A64.UC_ARM64_REG_X0 + i for i in range(8)]
            rng = random.Random(addr)
            ii = fi = 0
            for arg in args:
                if arg[0] == "fp":
                    raw = arg[2] & ((1 << (8 * arg[1])) - 1)
                    junk = (rng.getrandbits(128) >> (8 * arg[1])) << (8 * arg[1])            # v 레지스터의 윗부분은 쓰레기
                    uc.reg_write(A64.UC_ARM64_REG_Q0 + fi, (raw | junk) & ((1 << 128) - 1))
                    fi += 1
                else:
                    v, n = arg
                    junk = rng.getrandbits(64) & ~((1 << (8 * n)) - 1) if n < 8 else 0
                    uc.reg_write(regs[ii], (v & ((1 << (8 * n)) - 1)) | junk)
                    ii += 1
            for i in range(8, 18):
                uc.reg_write(A64.UC_ARM64_REG_X0 + i, rng.getrandbits(64))
            uc.reg_write(A64.UC_ARM64_REG_SP, sp & ~0xF)
            uc.reg_write(A64.UC_ARM64_REG_LR, SENTINEL)
            uc.emu_start(addr, SENTINEL, count=3_000_000)
            r = uc.reg_read(A64.UC_ARM64_REG_Q0 if ret_fp else A64.UC_ARM64_REG_X0)
        elif a["abi"] == "aapcs":
            regs = [A32.UC_ARM_REG_R0, A32.UC_ARM_REG_R1, A32.UC_ARM_REG_R2, A32.UC_ARM_REG_R3]
            stack_args = args[4:]
            base = (sp - 4 * len(stack_args)) & ~0x7
            for i, (v, n) in enumerate(args[:4]):
                uc.reg_write(regs[i], mask_(v, 4))
            for i, (v, n) in enumerate(stack_args):
                uc.mem_write(base + 4 * i, mask_(v, 4).to_bytes(4, "little"))
            uc.reg_write(A32.UC_ARM_REG_SP, base)
            th = 1 if a.get("thumb") else 0                      # Thumb 는 진입·복귀 주소의 최하위 비트로 모드를 알린다
            uc.reg_write(A32.UC_ARM_REG_LR, SENTINEL | th)
            uc.emu_start(addr | th, SENTINEL, count=3_000_000)
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
        elif k == "N":
            n = rng.randint(0, 12)
            out.append((k, "".join(rng.choice(" \t-+0123456789  ab") for _ in range(n))))
        elif k == "C":
            out.append((k, ord(rng.choice("abc"))))
        elif k == "T":
            out.append((k, [rng.randint(-9, 9), rng.randint(-9, 9), rng.randint(-9, 9)]))
        elif k in ("d", "a", "k"):
            out.append((k, round(rng.uniform(0, 50) if k == "a" else (rng.uniform(-3, 3) if k == "k" else rng.uniform(-50, 50)), 3)))
        elif k == "f":
            out.append((k, rng.randint(-400, 400) / 8))
        elif k == "E":
            out.append((k, [round(rng.uniform(-9, 9), 3) for _ in range(16)]))
        elif k == "G":
            out.append((k, [rng.randint(-80, 80) / 8 for _ in range(16)]))
        elif k == "V":
            out.append((k, [round(rng.uniform(-9, 9), 3), round(rng.uniform(-9, 9), 3), rng.randint(-9, 9)]))
        elif k == "W":
            out.append((k, [rng.randint(-99, 99) for _ in range(4)]))
        elif k == "B":
            out.append((k, [rng.randint(-99, 99) for _ in range(16)]))
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
        elif k in ("d", "a", "k"):
            args.append(("fp", 8, struct.unpack("<Q", struct.pack("<d", v))[0]))
        elif k == "f":
            args.append(("fp", 4, struct.unpack("<I", struct.pack("<f", v))[0]))
        else:
            base = DATA_BASE + buf_slots[idx]
            if k in ("P", "Q"):
                data = b"".join((x & 0xFFFFFFFF).to_bytes(4, "little") for x in v)
            elif k in ("S", "N"):
                data = v.encode() + b"\x00" * (32 - len(v))
            elif k == "E":
                data = b"".join(struct.pack("<d", x) for x in v)
            elif k == "G":
                data = b"".join(struct.pack("<f", x) for x in v)
            elif k == "V":                                          # V: struct { double x, y; int k; }
                data = struct.pack("<ddi", v[0], v[1], v[2]) + b"\x00" * 4
            elif k in ("W", "B"):                                   # W: struct { long a, b, c, d; } · B: struct { long v[16]; }
                data = b"".join((x & ((1 << (8 * a["long"])) - 1)).to_bytes(a["long"], "little") for x in v)
            else:                                                   # T: struct { int x; int y; long z; }
                data = (v[0] & 0xFFFFFFFF).to_bytes(4, "little") + (v[1] & 0xFFFFFFFF).to_bytes(4, "little") + \
                    (v[2] & ((1 << (8 * a["long"])) - 1)).to_bytes(a["long"], "little")
            uc.mem_write(base, b"\x00" * 0x1000)
            uc.mem_write(base, data)
            args.append((base, a["ptr"]))
            bufs.append((base, len(data)))
    rs = {"i": 4, "u": 4, "l": a["long"], "v": 0, "D": 8, "F": 4}[ret]
    r = m.call(addr, args, rs, ret_fp=ret in ("D", "F"))
    outs = [bytes(uc.mem_read(b, n)) for b, n in bufs]
    return r, outs


def _ord(bits, n):
    """부동소수점 비트 패턴을 크기 순서와 같은 정수로(두 값 사이의 ULP 거리를 재기 위해)."""
    sign = bits >> (8 * n - 1)
    mag = bits & ((1 << (8 * n - 1)) - 1)
    return -mag if sign else mag


def fp_close(a, b, n, ulps):
    """두 비트 패턴이 같거나(NaN 끼리 포함) ULP 거리가 ulps 이하인가."""
    if a == b:
        return True
    fa = struct.unpack("<d" if n == 8 else "<f", a.to_bytes(n, "little"))[0]
    fb = struct.unpack("<d" if n == 8 else "<f", b.to_bytes(n, "little"))[0]
    if fa != fa and fb != fb:
        return True
    return abs(_ord(a, n) - _ord(b, n)) <= ulps


FP_BUF = {"E": ("<d", 8), "G": ("<f", 4), "V": None}


def same_result(arch, ret, ro, rd, bo, bd, case):
    """원본과 의사코드의 결과가 같은가. x87(i386)은 80비트 확장 정밀도로 계산해 double/float 연산과 마지막 자리에서 달라질 수 있어 ULP 허용치를 둔다."""
    if ro == rd and bo == bd:
        return True
    if arch != "i386":
        return False
    if ro != rd:
        if ret not in ("D", "F") or not fp_close(ro, rd, 8 if ret == "D" else 4, ULPS):
            return False
    if bo != bd:
        kinds = [k for k, _v in case if k not in ("i", "n", "m", "p", "u", "l", "L", "b", "h", "C", "d", "a", "k", "f")]
        for kind, x, y in zip(kinds, bo, bd):
            if x == y:
                continue
            if kind == "E":
                step = 8
            elif kind == "G":
                step = 4
            elif kind == "V":                                     # struct { double x, y; int k; }: 앞 두 double 만 허용치, 나머지는 그대로
                if x[16:] != y[16:] or not all(fp_close(int.from_bytes(x[o:o + 8], "little"), int.from_bytes(y[o:o + 8], "little"), 8, ULPS) for o in (0, 8)):
                    return False
                continue
            else:
                return False
            if len(x) != len(y) or not all(fp_close(int.from_bytes(x[o:o + step], "little"), int.from_bytes(y[o:o + step], "little"), step, ULPS)
                                           for o in range(0, len(x) - step + 1, step)):
                return False
    return True


ULPS = 4


def run_level(arch, opt, only, verbose, seed=1, iterations=30):
    a = ARCH[arch]
    orig = os.path.join(WORK, f"orig_{arch}_{opt.strip('-')}")
    src_text = open(CORPUS).read()
    if arch == "win64":                                           # ELF 위에서 Windows x64 호출 규약(ms_abi)으로 컴파일한 코퍼스
        src_text = src_text.replace("#define FN __attribute__((noinline, noclone))", "#define FN __attribute__((noinline, noclone, ms_abi))")
    src_text += SUPPORT                                           # 독립 실행 환경에는 libc 가 없으므로 컴파일러가 만들어 내는 memcpy·memset 호출을 받아 준다
    if "-DX87_TESTS" in a["cflags"]:
        src_text += SUPPORT_X87
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
        if arch == "win64" and name in ("big_copy", "big_zero"):
            continue                    # 컴파일러가 만드는 memcpy/memset 호출은 SysV 규약이라, ELF 위의 ms_abi 모의 환경에서는 호출 규약이 섞여 의미가 없다
        d = Decompiler(prog).decompile(by[name])
        lines = d["lines"]
        lits = [int(x, 16) for x in re.findall(r"\b0x[0-9a-f]{4,}\b", "".join(l["text"] for l in lines))]
        if d.get("incomplete") or any(model.section_for(v) for v in lits) or "대체했습니다" in d.get("note", ""):
            why = "미해석 " + ",".join(d.get("incomplete") or []) if d.get("incomplete") else ("데이터 주소 리터럴" if any(model.section_for(v) for v in lits) else "예전 방식으로 대체")
            results.append((name, "SKIP", why))
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
        r = sh(compiler_cmd(a)[0] + ["-c", "-w", "-ffreestanding", "-I", WORK, "-o", os.devnull, one])
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
            if not same_result(arch, ret, ro, rd, bo, bd, case):
                bad += 1
                first = first or f"it={it} 입력={[v for _k, v in case]} 원본={ro} 의사코드={rd}" + ("" if bo == bd else " (메모리 불일치)")
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
    archs = archs or ["i386", "arm64", "arm", "thumb", "x64", "win64"]          # i386gcc·i386fast 는 이름을 주면 돈다
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
                if verbose:
                    for n, s, d in res:
                        if s == "SKIP":
                            print(f"    {n}: {d}")
            for n, s, d in res:
                if s not in ("PASS", "SKIP"):
                    print(f"  {s} {n}: {d}")
                    fails += 1
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
