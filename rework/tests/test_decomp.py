"""디컴파일러 차분 시험.

tests/decomp_corpus.c 를 여러 최적화 수준으로 컴파일하고, 각 함수의 의사코드를 다시 gcc 로 컴파일해 원본과 같은 입력에서
같은 값(과 같은 메모리 결과)을 내는지 비교한다. 의사코드가 '그럴듯해 보이는' 게 아니라 '실제로 같은 일을 하는지'를 확인하는 시험이다.

실행:  .venv/bin/python tests/test_decomp.py [-O0 -O2 ...]  [-k 함수이름]      (gcc 필요)
"""
import os, random, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
WORK = tempfile.mkdtemp(prefix="rework-decomp-")
os.environ["REWORK_DATA"] = os.path.join(WORK, "data")

from app.loader import load            # noqa: E402
from app.analysis import Program       # noqa: E402
from app.decompile import Decompiler   # noqa: E402

CORPUS = os.path.join(HERE, "decomp_corpus.c")

# 이름: (반환 종류, [인자 종류...])  — 반환: i int · u unsigned · l long · v void · D double · F float
# 인자: i 작은 int · n 0..12 · p 1..40 · u 0..80 unsigned · l long · b uchar · h short · P int[16] 버퍼 · Q 정렬된 int[16] · S 문자열 · C 글자 · T struct P
#        d double · f float · E double[16] 버퍼
SPECS = {
    "add": ("i", "ii"), "sub3": ("i", "iii"), "maxi": ("i", "ii"), "mini": ("i", "ii"), "absdiff": ("i", "ii"),
    "clampi": ("i", "iii"), "sign": ("i", "i"), "nested": ("i", "iii"), "shortc": ("i", "ii"), "andor": ("i", "iii"),
    "ternary": ("i", "iii"), "logic_ops": ("i", "ii"), "sum_to": ("i", "n"), "fact": ("i", "n"), "collatz": ("u", "u"),
    "gcd": ("i", "pp"), "fib": ("u", "n"), "dowhile": ("i", "n"), "breaks": ("i", "i"), "nestedloop": ("i", "n"),
    "popcnt": ("u", "u"), "cmp_chain": ("i", "i"), "classify": ("i", "i"), "sw_fall": ("i", "i"), "mul64": ("i", "ll"),
    "widen": ("l", "iu"), "shorts": ("i", "hh"), "uchars": ("i", "bb"), "div_ops": ("i", "ip"), "udiv_ops": ("u", "uu"),
    "div_const": ("i", "i"), "udiv_const": ("u", "u"), "shifts": ("i", "ii"), "bits": ("i", "ii"),
    "arr_sum": ("i", "Pn"), "arr_max": ("i", "Pm"), "arr_fill": ("v", "Pni"), "arr_rev": ("v", "Pn"), "local_arr": ("i", "i"),
    "str_len": ("i", "S"), "str_cmp": ("i", "SS"), "count_char": ("i", "SC"), "swap": ("v", "PP"), "ptr_out": ("i", "iP"),
    "calls": ("i", "ii"), "calls2": ("i", "ii"), "rec_fact": ("i", "n"), "rec_fib": ("i", "n"),
    "many_args": ("i", "iiiiiiii"), "use_many": ("i", "i"), "st_sum": ("i", "T"), "st_set": ("v", "Ti"), "st_local": ("i", "i"),
    "hash": ("u", "Sn"), "bsearch_": ("i", "QQi"), "isprime": ("i", "p"), "bubble": ("i", "Pn"), "lsum": ("l", "L"),
    "neg_not": ("i", "i"), "goto_like": ("i", "n"), "while_true": ("i", "p"),
    "w_copy": ("v", "WW"), "w_zero": ("v", "W"), "w_sum": ("l", "W"), "w_fill": ("v", "Wl"), "w_rot": ("v", "W"), "w_local": ("l", "l"),
    "big_copy": ("v", "BB"), "big_zero": ("v", "B"),
    "d_add": ("D", "dd"), "d_mix": ("D", "ddd"), "d_abs": ("D", "d"), "d_max": ("D", "dd"), "d_cmp": ("i", "dd"), "d_eq": ("i", "dd"),
    "d_ne": ("i", "dd"), "d2i": ("i", "d"), "i2d": ("D", "i"), "f_add": ("F", "ff"), "f2d": ("D", "f"), "d2f": ("F", "d"),
    "d_poly": ("D", "d"), "d_neg": ("D", "d"), "d2l": ("l", "d"), "l2d": ("D", "l"),
    "d_sum": ("D", "En"), "d_dot": ("D", "EEn"), "d_scale": ("v", "Enk"), "d_loop": ("D", "n"), "f_sum": ("F", "Gn"), "d_sqrt": ("D", "a"),
    "d_hyp": ("D", "aa"), "d_clamp": ("i", "d"), "d2u": ("u", "d"), "u2d": ("D", "u"), "d_calls": ("D", "dd"), "d_ifp": ("D", "id"),
    "d_store": ("v", "Ed"), "v_len2": ("D", "V"), "v_set": ("v", "Vd"),
    "bit_test": ("i", "un"), "bit_flag": ("i", "Ln"), "bit_set": ("u", "un"), "bswap32": ("u", "u"), "bswap64": ("l", "L"),
}
# 인자 이름이 겹치는 특수 처리: bsearch_(const int *a, int n, int key) → Q(정렬 배열), n(<=16), i(키)
SPECS["bsearch_"] = ("i", "Qni")
SPECS["hash"] = ("u", "Sn")


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def build(opt, extra, out):
    flags = opt.split(",") + ["-fno-math-errno"]                                  # "-O2,-fPIE,-pie,-fstack-protector-all" 처럼 쉼표로 이어 쓴다
    base = [f for f in ["-fno-pie", "-no-pie", "-fno-stack-protector"]
            if not (f == "-fno-pie" and any(x in flags for x in ("-fPIE", "-fpie", "-fPIC")))
            and not (f == "-no-pie" and any(x in flags for x in ("-pie",)))
            and not (f == "-fno-stack-protector" and any(x.startswith("-fstack-protector") for x in flags))]
    r = sh(["gcc"] + flags + base + ["-o", out, CORPUS] + extra)
    if r.returncode:
        raise SystemExit("corpus 컴파일 실패:\n" + r.stderr)


def c_param_list(sig):
    m = re.match(r"^\s*(.+?)\s+(\w+)\((.*)\)\s*$", sig)
    if not m:
        return None, None, []
    ret, name, params = m.group(1), m.group(2), m.group(3).strip()
    if params in ("", "void"):
        return ret, name, []
    out = []
    for p in params.split(","):
        p = p.strip()
        mm = re.match(r"^(.*?)(\w+)$", p)
        out.append(mm.group(1).strip() if mm else p)
    return ret, name, out


def gen_case(name, rng, kinds):
    """한 번의 호출을 위한 C 선언·인자식을 만든다. 반환: (선언 코드, 호출 인자(원본), 호출 인자(의사코드), 비교할 버퍼 이름들)"""
    decl, a_orig, a_dec, bufs = [], [], [], []
    nbuf = 0
    for i, k in enumerate(kinds):
        v = f"a{i}"
        if k in ("i", "n", "m", "p", "u", "l", "L", "b", "h"):
            rng_ = {"i": (-40, 40), "n": (0, 12), "m": (1, 12), "p": (1, 40), "u": (0, 80), "l": (-1000, 1000), "L": (0, 12), "b": (0, 255),
                    "h": (-200, 200)}[k]
            if name in ("div_ops",) and k == "p":
                rng_ = (1, 40)
            ty = {"i": "int", "n": "int", "m": "int", "p": "int", "u": "unsigned", "l": "long", "L": "long", "b": "unsigned char", "h": "short"}[k]
            val = rng.randint(*rng_)
            if name in ("collatz",):
                val = rng.randint(1, 60)
            if name == "bsearch_" and k == "n":
                val = 16
            if name == "hash" and k == "n":
                val = rng.randint(0, 12)
            if name in ("goto_like", "nestedloop") and k == "n":
                val = rng.randint(0, 8)
            if name in ("fact", "rec_fact") and k == "n":
                val = rng.randint(0, 10)
            if name in ("rec_fib",) and k == "n":
                val = rng.randint(0, 14)
            if name in ("fib",) and k == "n":
                val = rng.randint(0, 20)
            decl.append(f"{ty} {v} = {val}{'L' if k in 'lL' else ''};")
            a_orig.append(v)
            a_dec.append(v)
        elif k in ("P", "Q"):
            nbuf += 1
            vals = [rng.randint(-50, 50) for _ in range(16)]
            if k == "Q":
                vals = sorted(vals)
            init = "{" + ",".join(map(str, vals)) + "}"
            decl.append(f"int {v}o[16] = {init}; int {v}d[16] = {init};")
            a_orig.append(f"{v}o")
            a_dec.append(f"{v}d")
            bufs.append((f"{v}o", f"{v}d", "int", 16))
        elif k == "S":
            n = rng.randint(0, 10)
            s = "".join(rng.choice("abcab") for _ in range(n))
            decl.append(f'char {v}o[32] = "{s}"; char {v}d[32] = "{s}";')
            a_orig.append(f"{v}o")
            a_dec.append(f"{v}d")
            bufs.append((f"{v}o", f"{v}d", "char", 32))
        elif k == "C":
            ch = rng.choice("abc")
            decl.append(f"char {v} = '{ch}';")
            a_orig.append(v)
            a_dec.append(v)
        elif k == "T":
            decl.append(f"P {v}o = {{{rng.randint(-9, 9)}, {rng.randint(-9, 9)}, {rng.randint(-9, 9)}}}; P {v}d = {v}o;")
            a_orig.append(f"&{v}o")
            a_dec.append(f"&{v}d")
            bufs.append((f"{v}o", f"{v}d", "P", 1))
        elif k in ("a", "k"):                      # a: 0 이상 double(제곱근) · k: 작은 double
            val = repr(round(rng.uniform(0, 50) if k == "a" else rng.uniform(-3, 3), 3))
            decl.append(f"double {v} = {val};")
            a_orig.append(v)
            a_dec.append(v)
        elif k == "E":
            vals = ", ".join(repr(round(rng.uniform(-9, 9), 3)) for _ in range(16))
            decl.append(f"double {v}o[16] = {{{vals}}}; double {v}d[16]; memcpy({v}d, {v}o, sizeof {v}o);")
            a_orig.append(f"{v}o")
            a_dec.append(f"{v}d")
            bufs.append((f"{v}o", f"{v}d", "double", 16))
        elif k == "G":
            vals = ", ".join(f"{rng.randint(-80, 80) / 8}f" for _ in range(16))
            decl.append(f"float {v}o[16] = {{{vals}}}; float {v}d[16]; memcpy({v}d, {v}o, sizeof {v}o);")
            a_orig.append(f"{v}o")
            a_dec.append(f"{v}d")
            bufs.append((f"{v}o", f"{v}d", "float", 16))
        elif k == "V":
            decl.append(f"V {v}o = {{{round(rng.uniform(-9, 9), 3)}, {round(rng.uniform(-9, 9), 3)}, {rng.randint(-9, 9)}}}; V {v}d = {v}o;")
            a_orig.append(f"&{v}o")
            a_dec.append(f"&{v}d")
            bufs.append((f"{v}o", f"{v}d", "V", 1))
        elif k in ("d", "f"):
            if k == "d":
                val = repr(round(rng.uniform(-50, 50), 3))
                decl.append(f"double {v} = {val};")
            else:
                decl.append(f"float {v} = {rng.randint(-400, 400) / 8}f;")
            a_orig.append(v)
            a_dec.append(v)
        elif k == "B":
            decl.append(f"Big {v}o = {{{{{', '.join(str(rng.randint(-99, 99)) for _ in range(16))}}}}}; Big {v}d = {v}o;")
            a_orig.append(f"&{v}o")
            a_dec.append(f"&{v}d")
            bufs.append((f"{v}o", f"{v}d", "Big", 1))
        elif k == "W":
            decl.append(f"W {v}o = {{{', '.join(str(rng.randint(-99, 99)) for _ in range(4))}}}; W {v}d = {v}o;")
            a_orig.append(f"&{v}o")
            a_dec.append(f"&{v}d")
            bufs.append((f"{v}o", f"{v}d", "W", 1))
    return decl, a_orig, a_dec, bufs


def make_driver(entries, seed, iterations=40):
    """entries: [(name, ret, kinds, dec_params)] → C 소스"""
    rng = random.Random(seed)
    out = ['#include <stdio.h>', '#include <string.h>', '#include <stdint.h>', '#include <unistd.h>', '#include <sys/wait.h>',
           '#define main corpus_main', '#include "decomp_corpus.c"', '#undef main', '#include "dec.c"', '']
    out.append("static int g_fail;")
    for name, ret, kinds, dparams in entries:
        out.append(f"static int run_{name}(void) {{ int bad = 0;")
        for it in range(iterations):
            decl, ao, ad, bufs = gen_case(name, rng, kinds)
            cast_args = []
            for j, a in enumerate(ad):
                pt = dparams[j] if j < len(dparams) else "int64_t"
                if "*" in pt:
                    cast_args.append(f"({pt}){a}")
                elif kinds[j] in ("P", "Q", "S", "T", "W", "B", "E", "G", "V"):
                    cast_args.append(f"({pt})(intptr_t){a}")
                else:
                    cast_args.append(f"({pt}){a}")
            out.append("  {")
            out.extend("    " + d for d in decl)
            rt = {"i": "int", "u": "unsigned", "l": "long", "D": "double", "F": "float"}.get(ret)
            if rt and ret in "DF":
                out.append(f"    {rt} ro = {name}({', '.join(ao)});")
                out.append(f"    {rt} rd = dec_{name}({', '.join(cast_args)});")
                out.append(f'    if (memcmp(&ro, &rd, sizeof ro)) {{ bad++; if (bad < 3) printf("MISMATCH {name} it={it}: orig=%.17g dec=%.17g\\n", (double)ro, (double)rd); }}')
            elif rt:
                out.append(f"    {rt} ro = ({rt}){name}({', '.join(ao)});")
                out.append(f"    {rt} rd = ({rt})dec_{name}({', '.join(cast_args)});")
                out.append(f'    if (ro != rd) {{ bad++; if (bad < 3) printf("MISMATCH {name} it={it}: orig=%ld dec=%ld\\n", (long)ro, (long)rd); }}')
            else:
                out.append(f"    {name}({', '.join(ao)});")
                out.append(f"    dec_{name}({', '.join(cast_args)});")
            for bo, bd, ty, n in bufs:
                out.append(f'    if (memcmp(&{bo}, &{bd}, sizeof({bo}))) {{ bad++; if (bad < 3) printf("MISMATCH {name} it={it}: memory {bo}\\n"); }}')
            out.append("  }")
        out.append("  return bad; }")
    out.append("int main(void) { int total = 0; setvbuf(stdout, NULL, _IONBF, 0);")
    for name, *_ in entries:
        out.append(f'  {{ pid_t p = fork(); if (p == 0) {{ alarm(10); _exit(run_{name}() ? 1 : 0); }} int st = 0; waitpid(p, &st, 0);')
        out.append(f'    if (WIFSIGNALED(st)) {{ printf("CRASH {name} sig=%d\\n", WTERMSIG(st)); total++; }}')
        out.append(f'    else if (WEXITSTATUS(st)) {{ printf("FAIL {name}\\n"); total++; }} else printf("PASS {name}\\n"); }}')
    out.append('  printf("== %d failed ==\\n", total); return total ? 1 : 0; }')
    return "\n".join(out)


HELPERS = """#include <stdint.h>
#include <math.h>
#if !defined(__clang__)
#define __builtin_bit_cast(T, x) ({ __typeof__(x) _bcs = (x); T _bcd; __builtin_memcpy(&_bcd, &_bcs, sizeof _bcd); _bcd; })
#endif
static inline int32_t __mulhs32(int32_t a, int32_t b) { return (int32_t)(((int64_t)a * b) >> 32); }
static inline uint32_t __mulhu32(uint32_t a, uint32_t b) { return (uint32_t)(((uint64_t)a * b) >> 32); }
static inline int64_t __mulhs64(int64_t a, int64_t b) { return (int64_t)(((__int128)a * b) >> 64); }
static inline uint64_t __mulhu64(uint64_t a, uint64_t b) { return (uint64_t)(((unsigned __int128)a * b) >> 64); }
static uint64_t __stack_chk_guard;
"""


def run_level(opt, only=None, verbose=False, seed=1):
    exe = os.path.join(WORK, "corpus" + re.sub(r"[^A-Za-z0-9]", "_", opt))
    build(opt, [], exe)
    model, raw, lb = load(exe)
    prog = Program(model, raw, lb)
    by = {(prog.name_for(f.vaddr) or f.display_name): f.vaddr for f in model.functions}
    results = []
    decs = []
    entries = []
    stats = {"goto": 0, "total": 0, "fallback": 0}
    texts = {}
    for name, (ret, kinds) in SPECS.items():
        if only and name not in only:
            continue
        if name not in by:
            continue
        d = Decompiler(prog).decompile(by[name])
        lines = d["lines"]
        lits = [int(x, 16) for x in re.findall(r"\b0x[0-9a-f]{4,}\b", "".join(l["text"] for l in lines))]
        if d.get("incomplete") or any(model.section_for(v) for v in lits):
            results.append((name, "SKIP", "해석하지 못한 명령어 또는 바이너리 데이터 참조"))
            continue
        text = "\n".join("    " * l["indent"] + l["text"] for l in lines)
        if "대체했습니다" in d.get("note", ""):
            stats["fallback"] += 1
        sig = lines[0]["text"]
        rty, fname, dparams = c_param_list(sig)
        text = re.sub(r"\b" + re.escape(fname) + r"\(", "dec_" + fname + "(", text, count=1)
        texts[name] = text
        decs.append(text)
        entries.append((name, ret, kinds, dparams))
        stats["total"] += 1
        if any(l["kind"] == "goto" for l in lines):
            stats["goto"] += 1
    with open(os.path.join(WORK, "dec.c"), "w") as fh:
        fh.write(HELPERS + "\n" + "\n\n".join(decs) + "\n")
    drv = make_driver(entries, seed)
    with open(os.path.join(WORK, "drv.c"), "w") as fh:
        fh.write(drv)
    for hdr in ("decomp_corpus.c",):
        sh(["cp", CORPUS, os.path.join(WORK, hdr)])
    out_exe = os.path.join(WORK, "drv" + re.sub(r"[^A-Za-z0-9]", "_", opt))
    # 함수마다 (의사코드 + 그 함수의 시험 코드) 가 컴파일되는지 따로 확인해, 안 되는 것만 COMPILE 로 분류한다
    ok_entries = []
    for e, t in zip(entries, decs):
        one = os.path.join(WORK, "one.c")
        drv1 = make_driver([e], seed).split('#include "dec.c"')[1].split("int main(void)")[0]
        with open(one, "w") as fh:
            fh.write(HELPERS + '#include <stdio.h>\n#include <string.h>\n#define main corpus_main\n#include "decomp_corpus.c"\n#undef main\n'
                     + t + "\n" + drv1)
        rr = sh(["gcc", "-c", "-w", "-I", WORK, "-o", os.devnull, one])
        if rr.returncode:
            err = [l for l in rr.stderr.splitlines() if "error" in l][:3]
            results.append((e[0], "COMPILE", "\n".join(err)))
            if verbose:
                print("----", e[0], "COMPILE\n" + t + "\n" + "\n".join(err))
        else:
            ok_entries.append((e, t))
    with open(os.path.join(WORK, "dec.c"), "w") as fh:
        fh.write(HELPERS + "\n" + "\n\n".join(t for _, t in ok_entries) + "\n")
    with open(os.path.join(WORK, "drv.c"), "w") as fh:
        fh.write(make_driver([e for e, _ in ok_entries], seed))
    r = sh(["gcc", "-O1", "-fwrapv", "-w", "-fno-strict-aliasing", "-I", WORK, "-o", out_exe, os.path.join(WORK, "drv.c"), "-lm"])
    if r.returncode:
        print(r.stderr[:2000])
        return results, stats
    r = sh([out_exe], timeout=120)
    detail = {}
    for line in r.stdout.splitlines():
        if line.startswith(("PASS", "FAIL", "CRASH")):
            parts = line.split()
            results.append((parts[1], parts[0], parts[2] if len(parts) > 2 else ""))
        elif line.startswith("MISMATCH"):
            nm = line.split()[1]
            detail.setdefault(nm, []).append(line)
    results = [(n, s, ("\n".join(detail.get(n, [])) if s == "FAIL" else d)) for n, s, d in results]
    if verbose:
        for n, s, d in results:
            if s != "PASS":
                print("----", n, s, "\n" + d)
                print(texts.get(n, ""))
    return results, stats


def main():
    args = sys.argv[1:]
    only = None
    verbose = False
    levels = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-k":
            only = set(args[i + 1].split(","))
            i += 1
        elif a == "-v":
            verbose = True
        elif a.startswith("-O"):
            levels.append(a)
        i += 1
    levels = levels or ["-O0", "-O1", "-O2"]
    total_fail = 0
    for opt in levels:
        res, stats = run_level(opt, only, verbose)
        passed = sum(1 for _, s, _ in res if s == "PASS")
        skipped = sum(1 for _, s, _ in res if s == "SKIP")
        print(f"=== {opt}: {passed}/{len(res) - skipped} 통과  (건너뜀 {skipped}, goto 가 남은 함수 {stats['goto']}/{stats['total']}, 대체 {stats['fallback']})")
        for n, s, d in res:
            if s == "SKIP":
                print(f"  SKIP {n}")
            elif s != "PASS":
                print(f"  {s} {n}: {d.splitlines()[0] if d else ''}")
                total_fail += 1
    sys.exit(1 if total_fail else 0)


if __name__ == "__main__":
    main()
