"""ARM 32비트 어셈블리 점프 테이블 복원 시험.

C 컴파일러가 잘 만들어 주지 않는 ARM 의 switch 꼴(ldr pc 주소 표 · add pc 분기표 · Thumb-2 tbb/tbh)을 어셈블해서,
(1) 디컴파일러가 `switch` 로 복원하는지 보고 (2) 복원한 의사코드를 컴파일해 원본(Unicorn 에뮬레이션)과 같은 값을 내는지 비교한다.

또한 동적 링크된 ARM/Thumb/ARM64 실행 파일의 PLT 스텁이 임포트 이름(`foo(3)`)으로 풀리는지 확인한다.

실행:  .venv/bin/python tests/test_decomp_arm.py        (clang · lld · unicorn 필요)
"""
import os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_decomp_emu as emu   # noqa: E402  (WORK 임시 폴더·REWORK_DATA 설정을 함께 가져온다)
from app.loader import load            # noqa: E402
from app.analysis import Program       # noqa: E402
from app.decompile import Decompiler   # noqa: E402

ASM = r"""
.syntax unified
.arm
.global sw1
.type sw1, %function
sw1:                          @ gcc 꼴: ldrls pc, [pc, r0, lsl #2]
    cmp r0, #3
    ldrls pc, [pc, r0, lsl #2]
    b .Ldef
    .word .Lc0
    .word .Lc1
    .word .Lc2
    .word .Lc3
.Lc0: mov r0, #10
    bx lr
.Lc1: mov r0, #20
    bx lr
.Lc2: mov r0, #35
    bx lr
.Lc3: mov r0, #47
    bx lr
.Ldef: mov r0, #0
    bx lr
.global sw2
.type sw2, %function
sw2:                          @ add pc, pc, r0, lsl #2 + 분기표
    cmp r0, #3
    bhi .Ldef2
    add pc, pc, r0, lsl #2
    nop
    b .Ld0
    b .Ld1
    b .Ld2
    b .Ld3
.Ld0: mov r0, #1
    bx lr
.Ld1: mov r0, #2
    bx lr
.Ld2: mov r0, #4
    bx lr
.Ld3: mov r0, #8
    bx lr
.Ldef2: mvn r0, #0
    bx lr
.thumb
.thumb_func
.global sw3
.type sw3, %function
sw3:                          @ Thumb-2 tbh (반 워드 표)
    cmp r0, #3
    bhi .Ldef3
    tbh [pc, r0, lsl #1]
.Ltab:
    .short (.Le0 - .Ltab)/2
    .short (.Le1 - .Ltab)/2
    .short (.Le2 - .Ltab)/2
    .short (.Le3 - .Ltab)/2
.Le0: movs r0, #100
    bx lr
.Le1: movs r0, #101
    bx lr
.Le2: movs r0, #103
    bx lr
.Le3: movs r0, #107
    bx lr
.Ldef3: movs r0, #0
    bx lr
.thumb_func
.global sw4
.type sw4, %function
sw4:                          @ Thumb-2 tbb (바이트 표) + 여러 케이스가 같은 곳으로
    cmp r0, #4
    bhi .Ldef4
    tbb [pc, r0]
.Ltab4:
    .byte (.Lf0 - .Ltab4)/2
    .byte (.Lf1 - .Ltab4)/2
    .byte (.Lf0 - .Ltab4)/2
    .byte (.Lf1 - .Ltab4)/2
    .byte (.Lf2 - .Ltab4)/2
    .align 1
.Lf0: movs r0, #5
    bx lr
.Lf1: movs r0, #6
    bx lr
.Lf2: movs r0, #9
    bx lr
.Ldef4: movs r0, #77
    bx lr
"""

def to_signed32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


FUNCS = {"sw1": "arm", "sw2": "arm", "sw3": "thumb", "sw4": "thumb"}
INPUTS = list(range(-2, 9))


PLT_LIB = "int foo(int x) { return x + 1; }\nint bar(int a, int b) { return a * b; }\n"
PLT_MAIN = "int foo(int x);\nint bar(int a, int b);\nint main(void) { int t = foo(3); return t + bar(2, 5) + foo(t); }\n"


def plt_check() -> int:
    """공유 라이브러리를 임포트하는 동적 실행 파일을 만들어, PLT 스텁 호출이 `foo(3)`·`bar(2, 5)` 로 풀리는지 본다."""
    work = emu.WORK
    open(os.path.join(work, "foo.c"), "w").write(PLT_LIB)
    open(os.path.join(work, "pmain.c"), "w").write(PLT_MAIN)
    fails = 0
    for tag, target, flag in (("arm", "armv7-linux-gnueabi", "-marm"), ("thumb", "armv7-linux-gnueabi", "-mthumb"), ("a64", "aarch64-linux-gnu", "-O1")):
        lib = os.path.join(work, f"libfoo_{tag}.so")
        exe = os.path.join(work, f"pmain_{tag}")
        base = ["clang", f"--target={target}", flag, "-O1", "-nostdlib", "-fuse-ld=lld"]
        r1 = subprocess.run(base + ["-fPIC", "-shared", os.path.join(work, "foo.c"), "-o", lib], capture_output=True, text=True)
        r2 = subprocess.run(base + ["-fno-pic", os.path.join(work, "pmain.c"), "-o", exe, "-L", work, f"-l:libfoo_{tag}.so", "-Wl,-e,main",
                                    "-Wl,--dynamic-linker=/lib/ld.so.1"], capture_output=True, text=True)
        if r1.returncode or r2.returncode:
            print(f"PLT {tag}: 빌드 실패", (r1.stderr + r2.stderr)[:300])
            fails += 1
            continue
        model, raw, lb = load(exe)
        prog = Program(model, raw, lb)
        by = {(prog.name_for(f.vaddr) or f.display_name): f.vaddr for f in model.functions}
        d = Decompiler(prog).decompile(by["main"])
        text = "\n".join(l["text"] for l in d["lines"])
        ok = "foo(3)" in text and "bar(2, 5)" in text
        print(f"PLT {tag}: 임포트 이름 {'O' if ok else 'X'}")
        if not ok:
            print(text)
            fails += 1
    return fails


def main():
    work = emu.WORK
    asm = os.path.join(work, "jt.s")
    obj = os.path.join(work, "jt.o")
    exe = os.path.join(work, "jt")
    open(asm, "w").write(ASM)
    for cmd in (["clang", "--target=armv7-linux-gnueabi", "-mcpu=cortex-a15", "-c", asm, "-o", obj],
                ["clang", "--target=armv7-linux-gnueabi", "-nostdlib", "-static", "-fuse-ld=lld", "-Wl,-e,sw1", obj, "-o", exe]):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            sys.exit("어셈블/링크 실패:\n" + r.stderr[:1500])
    machines = {a: emu.Machine(a, exe) for a in ("arm", "thumb")}
    model, raw, lb = load(exe)
    prog = Program(model, raw, lb)
    by = {(prog.name_for(f.vaddr) or f.display_name): f.vaddr for f in model.functions}
    fails = 0
    decs = []
    for name, mode in FUNCS.items():
        d = Decompiler(prog).decompile(by[name])
        text = "\n".join("    " * l["indent"] + l["text"] for l in d["lines"])
        ok_struct = "switch (" in text and not d.get("incomplete")
        want = [to_signed32(machines[mode].call(machines[mode].syms[name], [(v & 0xFFFFFFFF, 4)], 4)) for v in INPUTS]
        decs.append((name, text, ok_struct, want))
        print(f"{name}: switch 복원 {'O' if ok_struct else 'X'}")
        if not ok_struct:
            print(text)
            fails += 1
    # 복원한 의사코드를 호스트에서 컴파일해 같은 값을 내는지 본다
    src = emu.HELPERS + "\n#include <stdio.h>\n" + "\n".join(re.sub(r"\b" + n + r"\(", "dec_" + n + "(", t, count=1) for n, t, _, _ in decs)
    src += "\nint main(void){\n"
    for n, _, _, _ in decs:
        src += f'  for (int i = -2; i <= 8; i++) printf("{n} %d\\n", (int)dec_{n}(i));\n'
    src += "  return 0;\n}\n"
    c = os.path.join(work, "jt_dec.c")
    open(c, "w").write(src)
    r = subprocess.run(["clang", "-w", "-o", os.path.join(work, "jt_dec"), c], capture_output=True, text=True)
    if r.returncode:
        sys.exit("의사코드 컴파일 실패:\n" + r.stderr[:1500])
    out = subprocess.run([os.path.join(work, "jt_dec")], capture_output=True, text=True).stdout.split("\n")
    got = {}
    for line in out:
        if line:
            n, v = line.split()
            got.setdefault(n, []).append(int(v))
    for n, _, _, want in decs:
        same = got.get(n) == want
        print(f"{n}: 에뮬레이션과 {'일치' if same else '불일치'}")
        if not same:
            print("  원본", want, "\n  의사코드", got.get(n))
            fails += 1
    fails += plt_check()
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
