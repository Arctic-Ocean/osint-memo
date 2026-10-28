/* 디컴파일러 차분 시험용 코퍼스: 이 파일을 gcc 로 컴파일한 뒤 각 함수의 의사코드를 다시 컴파일해 원본과 같은 값을 내는지 비교한다.
 * 모든 함수는 정의된 동작(부호 있는 오버플로·0 으로 나누기 없음)만 하도록 입력 범위를 harness 가 제한한다. */
#include <stdint.h>

#define FN __attribute__((noinline, noclone))

FN int add(int a, int b) { return a + b; }
FN int sub3(int a, int b, int c) { return a - b - c; }
FN int maxi(int a, int b) { return a > b ? a : b; }
FN int mini(int a, int b) { if (a < b) return a; return b; }
FN int absdiff(int a, int b) { if (a > b) return a - b; else return b - a; }
FN int clampi(int x, int lo, int hi) { if (x < lo) return lo; if (x > hi) return hi; return x; }
FN int sign(int x) { if (x > 0) return 1; if (x < 0) return -1; return 0; }
FN int nested(int a, int b, int c) {
    int r = 0;
    if (a > 0) { if (b > 0) r = 1; else r = 2; }
    else if (c > 0) r = 3;
    else r = 4;
    return r;
}
FN int shortc(int a, int b) {
    if (a > 0 && b > 0) return 1;
    if (a < 0 || b < 0) return 2;
    return 3;
}
FN int andor(int a, int b, int c) { if ((a > 3 && b < 5) || c == 7) return 10; return 20; }
FN int ternary(int a, int b, int c) { return a ? b : c; }
FN int logic_ops(int a, int b) { return (a && b) + (a || b) * 2 + (!a) * 4; }
FN int sum_to(int n) { int s = 0; for (int i = 1; i <= n; i++) s += i; return s; }
FN int fact(int n) { int r = 1; while (n > 1) { r *= n; n--; } return r; }
FN unsigned collatz(unsigned n) { unsigned steps = 0; while (n != 1) { if (n & 1) n = 3 * n + 1; else n /= 2; steps++; } return steps; }
FN int gcd(int a, int b) { while (b) { int t = a % b; a = b; b = t; } return a; }
FN unsigned fib(unsigned n) { unsigned a = 0, b = 1; for (unsigned i = 0; i < n; i++) { unsigned t = a + b; a = b; b = t; } return a; }
FN int dowhile(int n) { int s = 0; do { s += n; n--; } while (n > 0); return s; }
FN int breaks(int n) {
    int s = 0;
    for (int i = 0; i < 100; i++) { if (i == n) break; if (i % 3 == 0) continue; s += i; }
    return s;
}
FN int nestedloop(int n) { int c = 0; for (int i = 0; i < n; i++) for (int j = 0; j < i; j++) c += i * j; return c; }
FN unsigned popcnt(unsigned x) { unsigned c = 0; while (x) { c += x & 1; x >>= 1; } return c; }
FN int cmp_chain(int x) { if (x < 10) return 1; if (x < 100) return 2; if (x < 1000) return 3; return 4; }
FN int classify(int x) {
    switch (x) {
    case 0: return 10;
    case 1: return 20;
    case 2: return 35;
    case 3: return 41;
    case 4: return 59;
    case 7: return 70;
    default: return -1;
    }
}
FN int sw_fall(int x) {
    int r = 0;
    switch (x) {
    case 1: r += 1;
    case 2: r += 2; break;
    case 3: r += 4; break;
    case 5: r += 8;
    default: r += 16;
    }
    return r;
}
FN int mul64(long a, long b) { long r = a * b + (a >> 3); return (int)(r ^ (r >> 32)); }
FN long widen(int a, unsigned b) { return (long)a * 3 + (long)b; }
FN int shorts(short a, short b) { return a * b; }
FN int uchars(unsigned char a, unsigned char b) { return (unsigned char)(a + b) + (a > b); }
FN int div_ops(int a, int b) { if (b == 0) return 0; return a / b + a % b; }
FN unsigned udiv_ops(unsigned a, unsigned b) { return b ? a / b + a % b : 0; }
FN int div_const(int a) { return a / 7 + a % 5; }
FN unsigned udiv_const(unsigned a) { return a / 10 + a % 3; }
FN int shifts(int a, int n) { return (a << (n & 15)) + (a >> (n & 7)) + (int)((unsigned)a >> (n & 3)); }
FN int bits(int a, int b) { return (a & b) | (a ^ b) + (~a & 0xff) - (-b); }
FN int arr_sum(int *p, int n) { int s = 0; for (int i = 0; i < n; i++) s += p[i]; return s; }
FN int arr_max(int *p, int n) { int m = p[0]; for (int i = 1; i < n; i++) if (p[i] > m) m = p[i]; return m; }
FN void arr_fill(int *p, int n, int v) { for (int i = 0; i < n; i++) p[i] = v + i; }
FN void arr_rev(int *p, int n) { for (int i = 0, j = n - 1; i < j; i++, j--) { int t = p[i]; p[i] = p[j]; p[j] = t; } }
FN int local_arr(int n) { int a[8]; for (int i = 0; i < 8; i++) a[i] = i * n; int s = 0; for (int i = 0; i < 8; i++) s += a[i]; return s; }
FN int str_len(const char *s) { int n = 0; while (s[n]) n++; return n; }
FN int str_cmp(const char *a, const char *b) { while (*a && *a == *b) { a++; b++; } return (unsigned char)*a - (unsigned char)*b; }
FN int count_char(const char *s, char c) { int n = 0; for (; *s; s++) if (*s == c) n++; return n; }
FN void swap(int *a, int *b) { int t = *a; *a = *b; *b = t; }
FN int ptr_out(int a, int *out) { *out = a * 2; return a + 1; }
FN int calls(int a, int b) { return add(a, b) + maxi(a, b); }
FN int calls2(int a, int b) { int x = add(a, 1); int y = add(b, 2); return maxi(x, y) * 2; }
FN int rec_fact(int n) { if (n <= 1) return 1; return n * rec_fact(n - 1); }
FN int rec_fib(int n) { if (n < 2) return n; return rec_fib(n - 1) + rec_fib(n - 2); }
FN int many_args(int a, int b, int c, int d, int e, int f, int g, int h) { return a + 2 * b + 3 * c + 4 * d + 5 * e + 6 * f + 7 * g + 8 * h; }
FN int use_many(int a) { return many_args(a, a + 1, a + 2, a + 3, a + 4, a + 5, a + 6, a + 7); }

typedef struct { int x; int y; long z; } P;
FN int st_sum(const P *p) { return p->x + p->y + (int)p->z; }
FN void st_set(P *p, int a) { p->x = a; p->y = a * 2; p->z = a * 3L; }
FN int st_local(int a) { P p; p.x = a; p.y = a + 1; p.z = a + 2; return st_sum(&p); }

typedef struct { long a, b, c, d; } W;
FN void w_copy(W *dst, const W *src) { *dst = *src; }
FN void w_zero(W *w) { w->a = 0; w->b = 0; w->c = 0; w->d = 0; }
FN long w_sum(const W *w) { return w->a + w->b + w->c + w->d; }
FN void w_fill(W *w, long v) { w->a = v; w->b = v; w->c = v; w->d = v; }
FN void w_rot(W *w) { long t = w->a; w->a = w->b; w->b = w->c; w->c = w->d; w->d = t; }
FN long w_local(long v) { W t = {v, v + 1, v + 2, v + 3}; W u = t; return u.a * 2 + u.b + u.d; }

typedef struct { long v[16]; } Big;
FN void big_copy(Big *d, const Big *s) { *d = *s; }
FN void big_zero(Big *b) { Big z = {{0}}; *b = z; }

FN int bit_test(unsigned x, unsigned i) { return (x >> (i & 31)) & 1; }
FN int bit_flag(unsigned long m, int i) { return (m & (1UL << (i & 63))) != 0; }
FN unsigned bit_set(unsigned x, unsigned i) { return x | (1u << (i & 31)); }
FN unsigned bswap32(unsigned x) { return __builtin_bswap32(x * 2654435761u); }
FN unsigned long bswap64(unsigned long x) { return __builtin_bswap64(x * 11400714819323198485ul); }

#ifndef NO_FP                                   /* 소프트 플로트 런타임이 없는 독립 실행 ARM32 시험에서는 부동소수점 구역을 뺀다 */
FN double d_add(double a, double b) { return a + b; }
FN double d_mix(double a, double b, double c) { return a * b - c / 2.0; }
FN double d_abs(double x) { return x < 0 ? -x : x; }
FN double d_max(double a, double b) { return a > b ? a : b; }
FN int d_cmp(double a, double b) { if (a < b) return -1; if (a > b) return 1; return 0; }
FN int d_eq(double a, double b) { return a == b; }
FN int d_ne(double a, double b) { return a != b; }
FN int d2i(double x) { return (int)x; }
FN double i2d(int x) { return x * 0.5 + 1.25; }
FN float f_add(float a, float b) { return a + b * 2.0f; }
FN double f2d(float x) { return x * 3.0; }
FN float d2f(double x) { return (float)x - 1.0f; }
FN double d_poly(double x) { return ((x * 3.0 + 2.0) * x - 1.0) * x + 0.5; }
FN double d_neg(double x) { return -x; }
FN long d2l(double x) { return (long)x; }
FN double l2d(long x) { return (double)x / 4; }

FN double d_sum(const double *p, int n) { double s = 0; for (int i = 0; i < n; i++) s += p[i]; return s; }
FN double d_dot(const double *a, const double *b, int n) { double s = 0; for (int i = 0; i < n; i++) s += a[i] * b[i]; return s; }
FN void d_scale(double *p, int n, double k) { for (int i = 0; i < n; i++) p[i] *= k; }
FN double d_loop(int n) { double s = 0; for (int i = 1; i <= n; i++) s += 1.0 / i; return s; }
FN float f_sum(const float *p, int n) { float s = 0; for (int i = 0; i < n; i++) s += p[i]; return s; }
FN double d_sqrt(double x) { return __builtin_sqrt(x); }
FN double d_hyp(double a, double b) { return __builtin_sqrt(a * a + b * b); }
FN int d_clamp(double x) { if (x < 0) return 0; if (x > 255) return 255; return (int)x; }
FN unsigned d2u(double x) { return (unsigned)x; }
FN double u2d(unsigned x) { return x; }
FN double d_calls(double a, double b) { return d_add(a, b) * d_max(a, b); }
FN double d_ifp(int n, double x) { return n * x; }
FN void d_store(double *out, double x) { *out = x * 2.0 + 1.0; }
typedef struct { double x, y; int k; } V;
FN double v_len2(const V *v) { return v->x * v->x + v->y * v->y; }
FN void v_set(V *v, double a) { v->x = a; v->y = a * 2; v->k = 3; }
#endif

#ifdef X87_TESTS                                /* x87 전용(i386 시험에서만): 초월함수·반올림 모드·long double. 라이브러리 함수는 시험 지원 코드가 x87 어셈으로 제공한다 */
double sin(double), cos(double), tan(double), atan2(double, double), log2(double), log(double), exp2(double), fmod(double, double),
       floor(double), ceil(double), trunc(double), remainder(double, double);
FN double x_sin(double x) { return __builtin_sin(x) * 2.0; }
FN double x_cos(double x) { return __builtin_cos(x) + 1.0; }
FN double x_tan(double x) { return __builtin_tan(x); }
FN double x_atan2(double y, double x) { return __builtin_atan2(y, x); }
FN double x_log2(double x) { return __builtin_log2(x) + 0.5; }
FN double x_log(double x) { return __builtin_log(x); }
FN double x_exp2(double x) { return __builtin_exp2(x); }
FN double x_fmod(double a, double b) { return __builtin_fmod(a, b); }
FN double x_floor(double x) { return __builtin_floor(x); }
FN double x_ceil(double x) { return __builtin_ceil(x) * 0.5; }
FN double x_trunc(double x) { return __builtin_trunc(x); }
FN int x_lrint(double x) { return __builtin_lrint(x); }
FN int x_floor_i(double x) { return (int)__builtin_floor(x); }
FN float x_fsin(float x) { return __builtin_sinf(x) + 1.0f; }
#endif

#ifdef X87_TESTS                                /* naked 어셈 함수: 컴파일러가 거의 만들지 않는 x87 명령·피연산자 형태를 직접 시험한다(i386 만) */
#define NK __attribute__((naked, noinline))
#define ASM(...) __asm__(".intel_syntax noprefix\n" __VA_ARGS__ "\n.att_syntax")
NK double xa_fisttp(double x) { ASM("fld qword ptr [esp+4]\n sub esp, 4\n fisttp dword ptr [esp]\n fild dword ptr [esp]\n add esp, 4\n ret"); }
NK double xa_sincos(double x) { ASM("fld qword ptr [esp+4]\n fsincos\n fadd st(0), st(0)\n faddp st(1), st(0)\n ret"); }
NK double xa_prem1(double a, double b) { ASM("fld qword ptr [esp+12]\n fld qword ptr [esp+4]\n 1: fprem1\n fnstsw ax\n test ah, 4\n jnz 1b\n fstp st(1)\n ret"); }
NK int xa_ftst(double x) { ASM("fld qword ptr [esp+4]\n ftst\n fnstsw ax\n fstp st(0)\n sahf\n jz 2f\n jc 3f\n mov eax, 1\n ret\n 2: xor eax, eax\n ret\n 3: mov eax, -1\n ret"); }
NK int xa_fucomp_eq(double a, double b) { ASM("fld qword ptr [esp+12]\n fld qword ptr [esp+4]\n fucomp st(1)\n fnstsw ax\n fstp st(0)\n and ah, 0x45\n cmp ah, 0x40\n sete al\n movzx eax, al\n ret"); }
NK int xa_fucompp_np(double a, double b) { ASM("fld qword ptr [esp+12]\n fld qword ptr [esp+4]\n fucompp\n fnstsw ax\n test ah, 0x44\n setnp al\n movzx eax, al\n ret"); }
NK int xa_fcom_lt(double a, double b) { ASM("fld qword ptr [esp+12]\n fld qword ptr [esp+4]\n fcom st(1)\n fnstsw ax\n fstp st(0)\n fstp st(0)\n test ah, 1\n setne al\n movzx eax, al\n ret"); }
NK int xa_ficomp_lt7(double x) { ASM("push 7\n fld qword ptr [esp+8]\n ficomp dword ptr [esp]\n fnstsw ax\n add esp, 4\n test ah, 1\n setne al\n movzx eax, al\n ret"); }
#define FORMS2(N, BODY) NK double N(double a, double b) { ASM("fld qword ptr [esp+4]\n fld qword ptr [esp+12]\n " BODY "\n ret"); }
FORMS2(xa_subrp, "fsubrp st(1), st(0)")
FORMS2(xa_subp, "fsubp st(1), st(0)")
FORMS2(xa_divrp, "fdivrp st(1), st(0)")
FORMS2(xa_divp, "fdivp st(1), st(0)")
FORMS2(xa_sub_i0, "fsub st(1), st(0)\n fstp st(0)")
FORMS2(xa_subr_i0, "fsubr st(1), st(0)\n fstp st(0)")
FORMS2(xa_div_i0, "fdiv st(1), st(0)\n fstp st(0)")
FORMS2(xa_divr_i0, "fdivr st(1), st(0)\n fstp st(0)")
FORMS2(xa_sub_0i, "fsub st(0), st(1)\n fstp st(1)")
FORMS2(xa_subr_0i, "fsubr st(0), st(1)\n fstp st(1)")
FORMS2(xa_div_0i, "fdiv st(0), st(1)\n fstp st(1)")
FORMS2(xa_divr_0i, "fdivr st(0), st(1)\n fstp st(1)")
NK double xa_fxch3(double a, double b, double c) { ASM("fld qword ptr [esp+4]\n fld qword ptr [esp+12]\n fld qword ptr [esp+20]\n fxch st(2)\n fsubp st(1), st(0)\n fxch st(1)\n fdivp st(1), st(0)\n ret"); }
NK double xa_fldsti(double a, double b) { ASM("fld qword ptr [esp+4]\n fld qword ptr [esp+12]\n fld st(1)\n fst st(2)\n fmul st(0), st(1)\n fstp st(2)\n fsubp st(1), st(0)\n ret"); }
NK double xa_fiarith(double x, int n) { ASM("fld qword ptr [esp+4]\n fiadd dword ptr [esp+12]\n fimul dword ptr [esp+12]\n fidivr dword ptr [esp+12]\n fisub dword ptr [esp+12]\n ret"); }
#define CMOV2(N, CC) NK double N(double a, double b) { ASM("fld qword ptr [esp+12]\n fld qword ptr [esp+4]\n fucomi st(0), st(1)\n " CC " st(0), st(1)\n fstp st(1)\n ret"); }
CMOV2(xa_cmov_b, "fcmovb") CMOV2(xa_cmov_e, "fcmove") CMOV2(xa_cmov_be, "fcmovbe") CMOV2(xa_cmov_u, "fcmovu")
CMOV2(xa_cmov_nb, "fcmovnb") CMOV2(xa_cmov_ne, "fcmovne") CMOV2(xa_cmov_nbe, "fcmovnbe") CMOV2(xa_cmov_nu, "fcmovnu")
/* 반올림 모드: or ah,imm 로 만든 제어 워드(내림·올림)·상수로 쓴 제어 워드(0x077f 내림 · 0x0b7f 올림 · 0x0f7f 자름)·기본(가장 가까운) */
#define RND(N, SETUP) NK double N(double x) { ASM("sub esp, 8\n fnstcw word ptr [esp]\n " SETUP "\n fld qword ptr [esp+12]\n frndint\n fldcw word ptr [esp]\n add esp, 8\n ret"); }
RND(xa_floor_or, "mov ax, word ptr [esp]\n and ah, 0xf3\n or ah, 4\n mov word ptr [esp+2], ax\n fldcw word ptr [esp+2]")
RND(xa_ceil_or, "mov ax, word ptr [esp]\n and ah, 0xf3\n or ah, 8\n mov word ptr [esp+2], ax\n fldcw word ptr [esp+2]")
RND(xa_floor_imm, "mov word ptr [esp+2], 0x077f\n fldcw word ptr [esp+2]")
RND(xa_ceil_imm, "mov word ptr [esp+2], 0x0b7f\n fldcw word ptr [esp+2]")
RND(xa_trunc_imm, "mov word ptr [esp+2], 0x0f7f\n fldcw word ptr [esp+2]")
RND(xa_near, "nop")
NK int xa_fist_near(double x) { ASM("sub esp, 4\n fld qword ptr [esp+8]\n fist dword ptr [esp]\n fstp st(0)\n mov eax, dword ptr [esp]\n add esp, 4\n ret"); }
NK int xa_fist_down(double x) { ASM("sub esp, 8\n fnstcw word ptr [esp]\n mov word ptr [esp+2], 0x077f\n fldcw word ptr [esp+2]\n fld qword ptr [esp+12]\n fistp dword ptr [esp+4]\n fldcw word ptr [esp]\n mov eax, dword ptr [esp+4]\n add esp, 8\n ret"); }
#endif

#ifdef LD_TESTS                                 /* long double(x87 80비트): i386·x86-64 SysV 에서만 */
FN long double ld_add(long double a, long double b) { return a + b * 2.0L; }
FN long double ld_mix(long double a, double b, int n) { return a * b + n; }
FN double ld2d(long double x) { return (double)x * 0.5; }
FN long double d2ld(double x) { return x * 3.0; }
FN int ld_cmp(long double a, long double b) { if (a < b) return -1; if (a > b) return 1; return 0; }
FN long double ld_sum(const long double *p, int n) { long double s = 0; for (int i = 0; i < n; i++) s += p[i]; return s; }
FN void ld_scale(long double *p, int n, long double k) { for (int i = 0; i < n; i++) p[i] *= k; }
FN int ld2i(long double x) { return (int)x; }
FN long double ld_sqrt(long double x) { return __builtin_sqrtl(x) + 1.0L; }
FN long double ld_poly(long double x) { return (x * 3.0L + 2.0L) * x - 1.0L; }
FN long double ld_calls(long double a, long double b) { return ld_add(a, b) * ld_add(b, a); }
#endif

#if defined(__i386__)
#define STD __attribute__((stdcall))
#else
#define STD
#endif
FN STD int std_add(int a, int b) { return a * 2 + b; }
FN STD int std_three(int a, int b, int c) { return a - b + c * 3; }
FN int std_calls(int a, int b) { int x = std_add(a, b); int y = std_three(x, a, b); return x + y + std_add(y, x); }
#ifndef NO_FP
FN double bf_mix(float a, double b, float c) { return a * 2.0 + b - c; }                  /* ARM 하드 플로트: a=s0, b=d1, c=s1 (뒷칸 채우기). (float, float, double) 과 레지스터 배정이 같아 선언 순서는 복원할 수 없다 */
FN float ff_mix(float a, float b, float c, float d) { return a * b - c * d; }
#ifndef MS_ABI                                  /* Windows x64 는 위치가 레지스터를 정해서, 정수보다 앞선 실수 인자의 선언 순서를 아직 복원하지 못한다 */
FN double fd_mix(float a, double b, int n) { return a + b * n; }
#endif
#endif

#if defined(__i386__) && !defined(NO_FP)         /* 스택 인자는 순서가 남는다. 레지스터 규약(x64·ARM)은 정수/실수 레지스터 종류별로만 번호가 매겨져 (double, int, double) 순서를 복원할 수 없다 */
FN STD double std_dmix(double a, int n, double b) { return a * n + b; }
FN double std_dcalls(double a, double b) { return std_dmix(a, 3, b) * std_dmix(b, 2, a); }          /* 곱: 뺄셈의 상쇄로 x87 확장 정밀도 차이가 부풀지 않게 */
#endif

FN unsigned hash(const unsigned char *s, int n) { unsigned h = 5381; for (int i = 0; i < n; i++) h = h * 33 + s[i]; return h; }
FN int bsearch_(const int *a, int n, int key) {
    int lo = 0, hi = n - 1;
    while (lo <= hi) { int mid = lo + (hi - lo) / 2; if (a[mid] == key) return mid; if (a[mid] < key) lo = mid + 1; else hi = mid - 1; }
    return -1;
}
FN int isprime(int n) { if (n < 2) return 0; for (int i = 2; i * i <= n; i++) if (n % i == 0) return 0; return 1; }
FN int bubble(int *a, int n) {
    int swaps = 0;
    for (int i = 0; i < n; i++)
        for (int j = 0; j + 1 < n - i; j++)
            if (a[j] > a[j + 1]) { int t = a[j]; a[j] = a[j + 1]; a[j + 1] = t; swaps++; }
    return swaps;
}
FN long lsum(long n) { long s = 0; for (long i = 0; i < n; i++) s += i * i; return s; }
FN int neg_not(int a) { return -a + ~a; }
FN int goto_like(int n) {
    int s = 0;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            if (i * j > 20) goto done;
            s += i + j;
        }
    }
done:
    return s;
}
FN int while_true(int n) { int c = 0; while (1) { c += 3; if (c > n) break; } return c; }

/* 실전 꼴: 비트 연산·정렬·파싱·회전·오버플로 검사·상태 기계 */
static inline __attribute__((always_inline)) unsigned rotl(unsigned x, int k) { return (x << k) | (x >> (32 - k)); }      /* -O0 에서도 인라인: 호출 규약이 다른 정적 함수가 끼지 않게 */
FN unsigned rotr13(unsigned x, unsigned k) { return (x >> 13) | (x << 19) | (k << (k & 7)) >> 11; }
FN unsigned rotv(unsigned x, unsigned k) { k &= 31; return k ? (x << k) | (x >> (32 - k)) : x; }
FN unsigned crc32_bits(const unsigned char *s, int n) {
    unsigned c = 0xFFFFFFFFu;
    for (int i = 0; i < n; i++) { c ^= s[i]; for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & -(c & 1)); }
    return ~c;
}
FN void isort(int *a, int n) {
    for (int i = 1; i < n; i++) { int v = a[i], j = i - 1; while (j >= 0 && a[j] > v) { a[j + 1] = a[j]; j--; } a[j + 1] = v; }
}
FN int parse_int(const char *s) {
    int sign = 1, v = 0;
    while (*s == ' ' || *s == '\t') s++;
    if (*s == '-') { sign = -1; s++; } else if (*s == '+') s++;
    while (*s >= '0' && *s <= '9') { v = v * 10 + (*s - '0'); s++; }
    return sign * v;
}
FN unsigned rotmix(unsigned a, unsigned b, unsigned c) {
    for (int i = 0; i < 5; i++) { a = rotl(a + (b ^ c) + 0x9e3779b9u, 5) + b; c = rotl(c, 11) ^ a; b += c; }
    return a ^ b ^ c;
}
FN int sat_add(int a, int b) {
    int r;
    if (__builtin_add_overflow(a * 40000000, b * 40000000, &r)) return a < 0 ? -2147483647 - 1 : 2147483647;
    return r;
}
FN unsigned usub_sat(unsigned a, unsigned b) { unsigned r; return __builtin_sub_overflow(a * 3000000u, b * 3000000u, &r) ? 0 : r; }
FN int isqrt(unsigned n) {
    unsigned r = 0, bit = 1u << 30;
    n *= 1234567u;
    while (bit > n) bit >>= 2;
    while (bit) { if (n >= r + bit) { n -= r + bit; r = (r >> 1) + bit; } else r >>= 1; bit >>= 2; }
    return (int)r;
}
FN unsigned powmod(unsigned b, unsigned e, unsigned m) {
    unsigned r = 1;
    b %= m;
    while (e) { if (e & 1) r = r * b % m; b = b * b % m; e >>= 1; }
    return r;
}
FN unsigned u16swap(unsigned x) { x *= 2654435761u; return ((x & 0xff) << 8) | ((x >> 8) & 0xff); }
FN int word_count(const char *s) {
    int n = 0, in = 0;
    for (; *s; s++) {
        switch (*s) {
        case ' ': case '\t': case '-': in = 0; break;
        default: if (!in) { in = 1; n++; }
        }
    }
    return n;
}

int main(int argc, char **argv) { (void)argv; return add(argc, 1) + fib(3) + (int)sum_to(2); }

#if defined(VFP_TESTS) && defined(__ARM_PCS_VFP)            /* naked 어셈 함수: 컴파일러가 거의 만들지 않는 VFP 명령·피연산자 형태를 직접 시험한다(ARM·Thumb 하드 플로트) */
#define NK __attribute__((naked, noinline))
#ifdef __thumb__
#define IT(c) "it " #c "\n"
#define ITE(c) "ite " #c "\n"
#else
#define IT(c) ""
#define ITE(c) ""
#endif
#define VASM(...) __asm__(".syntax unified\n" __VA_ARGS__ "\nbx lr")
#define VOP3D(N, OP) NK double N(double a, double b, double c) { VASM(OP " d0, d1, d2"); }
#define VOP3F(N, OP) NK float N(float a, float b, float c) { VASM(OP " s0, s1, s2"); }
VOP3D(va_vmla, "vmla.f64") VOP3D(va_vmls, "vmls.f64") VOP3D(va_vnmla, "vnmla.f64") VOP3D(va_vnmls, "vnmls.f64")
VOP3D(va_vfms, "vfms.f64") VOP3D(va_vfnma, "vfnma.f64") VOP3D(va_vfnms, "vfnms.f64") VOP3D(va_vfma, "vfma.f64")
VOP3F(vf_vmla, "vmla.f32") VOP3F(vf_vmls, "vmls.f32") VOP3F(vf_vnmla, "vnmla.f32") VOP3F(vf_vnmls, "vnmls.f32")
VOP3F(vf_vfms, "vfms.f32") VOP3F(vf_vfnma, "vfnma.f32") VOP3F(vf_vfnms, "vfnms.f32") VOP3F(vf_vfma, "vfma.f32")
NK double va_vnmul(double a, double b) { VASM("vnmul.f64 d0, d0, d1"); }
NK double va_vabs(double a) { VASM("vabs.f64 d0, d0"); }
NK double va_vneg(double a) { VASM("vneg.f64 d0, d0"); }
NK double va_vsqrt(double a) { VASM("vabs.f64 d0, d0\n vsqrt.f64 d0, d0"); }
NK float vf_vabs(float a) { VASM("vabs.f32 s0, s0"); }
NK float vf_vneg(float a) { VASM("vneg.f32 s0, s0"); }
NK float vf_vsqrt(float a) { VASM("vabs.f32 s0, s0\n vsqrt.f32 s0, s0"); }
NK float vf_vdiv(float a, float b) { VASM("vdiv.f32 s0, s0, s1"); }
NK double va_cmpsel(double a, double b) { VASM("vcmpe.f64 d0, d1\n vmrs APSR_nzcv, fpscr\n " IT(lt) " vmovlt.f64 d0, d1\n vabs.f64 d0, d0"); }
NK int va_cmp0(double a) { VASM("vcmp.f64 d0, #0\n vmrs APSR_nzcv, fpscr\n mov r0, #0\n " IT(gt) " movgt r0, #1\n " IT(mi) " mvnmi r0, #0"); }
NK int vf_cmp(float a, float b) { VASM("vcmp.f32 s0, s1\n vmrs APSR_nzcv, fpscr\n mov r0, #0\n " ITE(eq) " moveq r0, #5\n movne r0, #9\n " IT(mi) " addmi r0, r0, #20"); }
NK float va_f2f(double a) { VASM("vcvt.f32.f64 s0, d0"); }
NK double va_f2d(float a) { VASM("vcvt.f64.f32 d0, s0"); }
NK int va_d2s32(double a) { VASM("vcvt.s32.f64 s0, d0\n vmov r0, s0"); }
NK unsigned va_d2u32(double a) { VASM("vabs.f64 d0, d0\n vcvt.u32.f64 s0, d0\n vmov r0, s0"); }
NK int vf_f2s32(float a) { VASM("vcvt.s32.f32 s0, s0\n vmov r0, s0"); }
NK unsigned vf_f2u32(float a) { VASM("vabs.f32 s0, s0\n vcvt.u32.f32 s0, s0\n vmov r0, s0"); }
NK double va_s32tod(int a) { VASM("vmov s0, r0\n vcvt.f64.s32 d0, s0"); }
NK double va_u32tod(unsigned a) { VASM("vmov s0, r0\n vcvt.f64.u32 d0, s0"); }
NK float vf_s32tof(int a) { VASM("vmov s0, r0\n vcvt.f32.s32 s0, s0"); }
NK float vf_u32tof(unsigned a) { VASM("vmov s0, r0\n vcvt.f32.u32 s0, s0"); }
NK int va_vcvtr(double a) { VASM("vcvtr.s32.f64 s0, d0\n vmov r0, s0"); }
NK float vf_fixed_in(int a) { VASM("vmov s0, r0\n vcvt.f32.s32 s0, s0, #4"); }
NK int vf_fixed_out(float a) { VASM("vcvt.s32.f32 s0, s0, #4\n vmov r0, s0"); }
NK double va_movcore(int a, int b) { VASM("vmov d0, r0, r1\n vadd.f64 d0, d0, d0"); }
NK int va_movcore2(double a) { VASM("vmov r0, r1, d0\n eor r0, r0, r1"); }
NK int vf_movs(float a) { VASM("vmov r0, s0\n add r0, r0, #1"); }
NK float vf_movs2(int a) { VASM("vmov s0, r0\n vmov.f32 s1, #1.0\n vadd.f32 s0, s0, s1"); }
NK int va_lane1(double a) { VASM("vmov.32 r0, d0[1]"); }
NK int va_lane0(double a) { VASM("vmov.32 r0, d0[0]"); }
NK double va_setlane(double a, int b) { VASM("vmov.32 d0[1], r0\n vadd.f64 d0, d0, d0"); }
NK double va_imm(double a) { VASM("vmov.f64 d1, #1.5\n vadd.f64 d0, d0, d1\n vmov.f64 d2, #-0.25\n vmul.f64 d0, d0, d2"); }
NK float vf_imm(float a) { VASM("vmov.f32 s1, #3.0\n vsub.f32 s0, s0, s1"); }
NK double va_ldst(double *p, double x) { VASM("vldr d1, [r0, #8]\n vadd.f64 d0, d0, d1\n vstr d0, [r0]"); }
NK double va_ldm(double *p, double x) { VASM("vldmia r0!, {d1, d2}\n vadd.f64 d0, d0, d1\n vsub.f64 d0, d0, d2\n vstr d0, [r0]"); }
NK void va_stm(double *p, double x, double y) { VASM("vstmia r0!, {d0, d1}\n vadd.f64 d2, d0, d1\n vstmia r0, {d2}"); }
NK void va_stmdb(double *p, double x, double y) { VASM("add r0, r0, #32\n vstmdb r0!, {d0, d1}"); }
NK float vf_ldm(float *p, float x) { VASM("vldmia r0!, {s2, s3, s4}\n vadd.f32 s0, s0, s2\n vsub.f32 s0, s0, s3\n vmla.f32 s0, s3, s4"); }
NK double va_pushpop(double a, double b) { VASM("vpush {d8, d9}\n vmov.f64 d8, d0\n vmov.f64 d9, d1\n vadd.f64 d0, d8, d9\n vmul.f64 d0, d0, d8\n vpop {d8, d9}"); }
NK float vf_pushpop(float a, float b) { VASM("vpush {s16, s17}\n vmov.f32 s16, s0\n vmov.f32 s17, s1\n vsub.f32 s0, s16, s17\n vpop {s16, s17}"); }
NK double va_lit(double a) { __asm__(".syntax unified\n vldr d1, 1f\n vadd.f64 d0, d0, d1\n bx lr\n .balign 8\n 1: .double 2.5"); }
NK double va_hiregs(double a, double b) { VASM("vmov.f64 d16, d0\n vmov.f64 d17, d1\n vmul.f64 d18, d16, d17\n vadd.f64 d0, d18, d16"); }
NK double va_alias_sd(float a) { VASM("vcvt.f64.f32 d1, s0\n vadd.f64 d0, d1, d1"); }
NK float va_alias_ds(double a) { VASM("vcvt.f32.f64 s2, d0\n vadd.f32 s0, s2, s2"); }
NK float va_alias3(float a, float b) { VASM("vmov.f32 s2, s1\n vsub.f32 s0, s0, s2"); }
NK double fp9(double a, double b, double c, double d, double e, double f, double g, double h, double i) {
    VASM("vadd.f64 d0, d0, d1\n vsub.f64 d0, d0, d2\n vmla.f64 d0, d3, d4\n vadd.f64 d0, d0, d5\n vmls.f64 d0, d6, d7\n vldr d1, [sp]\n vadd.f64 d0, d0, d1"); }
#endif

#if defined(NEON_ASM) && defined(__aarch64__)           /* naked 어셈 함수(생성): 자동 벡터화가 만들지 않는 NEON 명령을 직접 시험한다. v0=*p, v1=*q, v2=p[4..7] → 결과는 v2 를 *q 에 */
#define NK __attribute__((naked, noinline))
#define AASM(...) __asm__(__VA_ARGS__ "\nret")
NK void nvi_add_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n add v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_add_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n add v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_add_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n add v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_add_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n add v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_add_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n add v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_add_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n add v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_add_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n add v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sub_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sub v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sub_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sub v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sub_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sub v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sub_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sub v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sub_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sub v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sub_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sub v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sub_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sub v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smax_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smax v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smax_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smax v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smax_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smax v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smax_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smax v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smax_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smax v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smax_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smax v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smin_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smin v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smin_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smin v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smin_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smin v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smin_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smin v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smin_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smin v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smin_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smin v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umax_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umax v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umax_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umax v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umax_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umax v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umax_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umax v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umax_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umax v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umax_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umax v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umin_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umin v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umin_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umin v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umin_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umin v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umin_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umin v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umin_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umin v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umin_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umin v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sabd_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sabd v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sabd_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sabd v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sabd_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sabd v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sabd_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sabd v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sabd_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sabd v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sabd_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sabd v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uabd_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uabd v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uabd_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uabd v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uabd_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uabd v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uabd_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uabd v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uabd_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uabd v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uabd_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uabd v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sqadd_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sqadd v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sqadd_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sqadd v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sqadd_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sqadd v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sqadd_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sqadd v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sqadd_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sqadd v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sqadd_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sqadd v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uqadd_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uqadd v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uqadd_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uqadd v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uqadd_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uqadd v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uqadd_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uqadd v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uqadd_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uqadd v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uqadd_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uqadd v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sqsub_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sqsub v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sqsub_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sqsub v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sqsub_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sqsub v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sqsub_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sqsub v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sqsub_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sqsub v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sqsub_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sqsub v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uqsub_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uqsub v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uqsub_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uqsub v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uqsub_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uqsub v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uqsub_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uqsub v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uqsub_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uqsub v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uqsub_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uqsub v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shadd_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shadd v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shadd_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shadd v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shadd_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shadd v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shadd_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shadd v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shadd_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shadd v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shadd_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shadd v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uhadd_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uhadd v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uhadd_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uhadd v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uhadd_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uhadd v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uhadd_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uhadd v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uhadd_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uhadd v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uhadd_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uhadd v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_srhadd_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n srhadd v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_srhadd_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n srhadd v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_srhadd_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n srhadd v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_srhadd_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n srhadd v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_srhadd_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n srhadd v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_srhadd_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n srhadd v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_urhadd_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n urhadd v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_urhadd_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n urhadd v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_urhadd_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n urhadd v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_urhadd_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n urhadd v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_urhadd_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n urhadd v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_urhadd_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n urhadd v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshl_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshl v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshl_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshl v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshl_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshl v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshl_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshl v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshl_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshl v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshl_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshl v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushl_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushl v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushl_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushl v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushl_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushl v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushl_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushl v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushl_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushl v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushl_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushl v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeq_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeq_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeq_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeq_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeq_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeq_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeq_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgt_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgt_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgt_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgt_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgt_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgt_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgt_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmge_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmge_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmge_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmge_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmge_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmge_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmge_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhi_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhi v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhi_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhi v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhi_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhi v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhi_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhi v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhi_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhi v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhi_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhi v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhi_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhi v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhs_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhs v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhs_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhs v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhs_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhs v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhs_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhs v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhs_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhs v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhs_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhs v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmhs_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmhs v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmtst_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmtst v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmtst_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmtst v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmtst_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmtst v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmtst_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmtst v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmtst_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmtst v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmtst_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmtst v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmtst_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmtst v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_addp_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n addp v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_addp_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n addp v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_addp_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n addp v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_addp_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n addp v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_addp_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n addp v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_addp_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n addp v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_addp_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n addp v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smaxp_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smaxp v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smaxp_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smaxp v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smaxp_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smaxp v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smaxp_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smaxp v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smaxp_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smaxp v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smaxp_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smaxp v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umaxp_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umaxp v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umaxp_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umaxp v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umaxp_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umaxp v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umaxp_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umaxp v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umaxp_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umaxp v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umaxp_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umaxp v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sminp_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sminp v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sminp_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sminp v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sminp_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sminp v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sminp_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sminp v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sminp_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sminp v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sminp_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sminp v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uminp_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uminp v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uminp_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uminp v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uminp_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uminp v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uminp_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uminp v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uminp_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uminp v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uminp_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uminp v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_neg_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n neg v2.16b, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_neg_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n neg v2.8h, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_neg_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n neg v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_neg_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n neg v2.8b, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_neg_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n neg v2.4h, v0.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_neg_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n neg v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_neg_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n neg v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_abs_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n abs v2.16b, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_abs_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n abs v2.8h, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_abs_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n abs v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_abs_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n abs v2.8b, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_abs_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n abs v2.4h, v0.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_abs_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n abs v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_abs_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n abs v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_clz_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n clz v2.16b, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_clz_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n clz v2.8h, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_clz_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n clz v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_clz_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n clz v2.8b, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_clz_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n clz v2.4h, v0.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_clz_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n clz v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cnt_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cnt v2.16b, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cnt_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cnt v2.8b, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rbit_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rbit v2.16b, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rbit_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rbit v2.8b, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_not_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n not v2.16b, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_not_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n not v2.8b, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rev64_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rev64 v2.16b, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rev64_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rev64 v2.8h, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rev64_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rev64 v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rev64_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rev64 v2.8b, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rev64_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rev64 v2.4h, v0.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rev64_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rev64 v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rev32_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rev32 v2.16b, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rev32_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rev32 v2.8h, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rev32_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rev32 v2.8b, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rev32_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rev32 v2.4h, v0.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rev16_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rev16 v2.16b, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_rev16_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n rev16 v2.8b, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeqz_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.16b, v0.16b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeqz_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.8h, v0.8h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeqz_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.4s, v0.4s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeqz_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.8b, v0.8b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeqz_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.4h, v0.4h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeqz_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.2s, v0.2s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmeqz_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmeq v2.2d, v0.2d, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgtz_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.16b, v0.16b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgtz_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.8h, v0.8h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgtz_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.4s, v0.4s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgtz_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.8b, v0.8b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgtz_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.4h, v0.4h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgtz_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.2s, v0.2s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgtz_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmgt v2.2d, v0.2d, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgez_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.16b, v0.16b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgez_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.8h, v0.8h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgez_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.4s, v0.4s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgez_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.8b, v0.8b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgez_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.4h, v0.4h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgez_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.2s, v0.2s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmgez_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmge v2.2d, v0.2d, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmlez_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmle v2.16b, v0.16b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmlez_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmle v2.8h, v0.8h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmlez_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmle v2.4s, v0.4s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmlez_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmle v2.8b, v0.8b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmlez_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmle v2.4h, v0.4h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmlez_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmle v2.2s, v0.2s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmlez_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmle v2.2d, v0.2d, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmltz_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmlt v2.16b, v0.16b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmltz_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmlt v2.8h, v0.8h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmltz_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmlt v2.4s, v0.4s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmltz_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmlt v2.8b, v0.8b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmltz_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmlt v2.4h, v0.4h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmltz_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmlt v2.2s, v0.2s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_cmltz_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n cmlt v2.2d, v0.2d, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_16b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.16b, v0.16b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_16b_4(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.16b, v0.16b, #4\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_16b_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.16b, v0.16b, #7\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_8h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.8h, v0.8h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_8h_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.8h, v0.8h, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_8h_15(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.8h, v0.8h, #15\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_4s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.4s, v0.4s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_4s_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.4s, v0.4s, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_4s_31(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.4s, v0.4s, #31\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_8b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.8b, v0.8b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_8b_4(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.8b, v0.8b, #4\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_8b_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.8b, v0.8b, #7\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_4h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.4h, v0.4h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_4h_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.4h, v0.4h, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_4h_15(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.4h, v0.4h, #15\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_2s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.2s, v0.2s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_2s_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.2s, v0.2s, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_2s_31(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.2s, v0.2s, #31\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_2d_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.2d, v0.2d, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_2d_32(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.2d, v0.2d, #32\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_shl_2d_63(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n shl v2.2d, v0.2d, #63\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_16b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.16b, v0.16b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_16b_4(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.16b, v0.16b, #4\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_16b_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.16b, v0.16b, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_8h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.8h, v0.8h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_8h_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.8h, v0.8h, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_8h_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.8h, v0.8h, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_4s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.4s, v0.4s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_4s_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.4s, v0.4s, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_4s_32(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.4s, v0.4s, #32\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_8b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.8b, v0.8b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_8b_4(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.8b, v0.8b, #4\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_8b_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.8b, v0.8b, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_4h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.4h, v0.4h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_4h_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.4h, v0.4h, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_4h_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.4h, v0.4h, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_2s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.2s, v0.2s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_2s_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.2s, v0.2s, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_2s_32(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.2s, v0.2s, #32\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_2d_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.2d, v0.2d, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_2d_32(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.2d, v0.2d, #32\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshr_2d_64(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshr v2.2d, v0.2d, #64\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_16b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.16b, v0.16b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_16b_4(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.16b, v0.16b, #4\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_16b_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.16b, v0.16b, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_8h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.8h, v0.8h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_8h_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.8h, v0.8h, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_8h_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.8h, v0.8h, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_4s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.4s, v0.4s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_4s_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.4s, v0.4s, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_4s_32(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.4s, v0.4s, #32\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_8b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.8b, v0.8b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_8b_4(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.8b, v0.8b, #4\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_8b_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.8b, v0.8b, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_4h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.4h, v0.4h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_4h_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.4h, v0.4h, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_4h_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.4h, v0.4h, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_2s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.2s, v0.2s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_2s_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.2s, v0.2s, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_2s_32(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.2s, v0.2s, #32\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_2d_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.2d, v0.2d, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_2d_32(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.2d, v0.2d, #32\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushr_2d_64(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushr v2.2d, v0.2d, #64\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_16b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.16b, v0.16b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_16b_4(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.16b, v0.16b, #4\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_16b_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.16b, v0.16b, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_8h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.8h, v0.8h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_8h_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.8h, v0.8h, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_8h_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.8h, v0.8h, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_4s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.4s, v0.4s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_4s_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.4s, v0.4s, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_4s_32(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.4s, v0.4s, #32\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_8b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.8b, v0.8b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_8b_4(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.8b, v0.8b, #4\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_8b_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.8b, v0.8b, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_4h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.4h, v0.4h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_4h_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.4h, v0.4h, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_4h_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.4h, v0.4h, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_2s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.2s, v0.2s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_2s_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.2s, v0.2s, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_2s_32(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.2s, v0.2s, #32\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_2d_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.2d, v0.2d, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_2d_32(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.2d, v0.2d, #32\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssra_2d_64(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ssra v2.2d, v0.2d, #64\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_16b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.16b, v0.16b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_16b_4(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.16b, v0.16b, #4\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_16b_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.16b, v0.16b, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_8h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.8h, v0.8h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_8h_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.8h, v0.8h, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_8h_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.8h, v0.8h, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_4s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.4s, v0.4s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_4s_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.4s, v0.4s, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_4s_32(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.4s, v0.4s, #32\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_8b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.8b, v0.8b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_8b_4(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.8b, v0.8b, #4\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_8b_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.8b, v0.8b, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_4h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.4h, v0.4h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_4h_8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.4h, v0.4h, #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_4h_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.4h, v0.4h, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_2s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.2s, v0.2s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_2s_16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.2s, v0.2s, #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_2s_32(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.2s, v0.2s, #32\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_2d_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.2d, v0.2d, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_2d_32(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.2d, v0.2d, #32\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usra_2d_64(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n usra v2.2d, v0.2d, #64\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_e4s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.4s, v0.4s, v1.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_e4s_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.4s, v0.4s, v1.s[3]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_e4s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.4s, v0.4s, v1.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_e4s_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.4s, v0.4s, v1.s[3]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_e4s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.4s, v0.4s, v1.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_e4s_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.4s, v0.4s, v1.s[3]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_e2s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.2s, v0.2s, v1.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_e2s_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.2s, v0.2s, v1.s[3]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_e2s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.2s, v0.2s, v1.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_e2s_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.2s, v0.2s, v1.s[3]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_e2s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.2s, v0.2s, v1.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_e2s_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.2s, v0.2s, v1.s[3]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_e8h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.8h, v0.8h, v1.h[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_e8h_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.8h, v0.8h, v1.h[7]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_e8h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.8h, v0.8h, v1.h[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_e8h_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.8h, v0.8h, v1.h[7]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_e8h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.8h, v0.8h, v1.h[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_e8h_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.8h, v0.8h, v1.h[7]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_e4h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.4h, v0.4h, v1.h[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mul_e4h_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mul v2.4h, v0.4h, v1.h[7]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_e4h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.4h, v0.4h, v1.h[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mla_e4h_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mla v2.4h, v0.4h, v1.h[7]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_e4h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.4h, v0.4h, v1.h[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mls_e4h_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mls v2.4h, v0.4h, v1.h[7]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_and_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n and v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_and_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n and v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_orr_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n orr v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_orr_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n orr v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_eor_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n eor v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_eor_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n eor v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_bic_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n bic v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_bic_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n bic v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_orn_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n orn v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_orn_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n orn v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_bsl_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n bsl v2.16b, v1.16b, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_bsl_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n bsl v2.8b, v1.8b, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_bit_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n bit v2.16b, v1.16b, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_bit_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n bit v2.8b, v1.8b, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_bif_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n bif v2.16b, v1.16b, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_bif_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n bif v2.8b, v1.8b, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fadd_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fadd v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fadd_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fadd v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fadd_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fadd v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fsub_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fsub v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fsub_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fsub v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fsub_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fsub v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmul_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmul v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmul_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmul v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmul_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmul v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fdiv_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fdiv v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fdiv_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fdiv v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fdiv_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fdiv v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmax_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmax v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmax_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmax v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmax_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmax v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmin_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmin v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmin_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmin v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmin_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmin v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmaxnm_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmaxnm v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmaxnm_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmaxnm v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmaxnm_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmaxnm v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fminnm_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fminnm v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fminnm_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fminnm v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fminnm_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fminnm v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fabd_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fabd v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fabd_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fabd v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fabd_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fabd v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmeq_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmeq v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmeq_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmeq v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fcmeq_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmeq v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmgt_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmgt v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmgt_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmgt v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fcmgt_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmgt v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmge_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmge v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmge_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmge v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fcmge_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmge v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_facgt_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n facgt v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_facgt_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n facgt v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_facgt_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n facgt v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_facge_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n facge v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_facge_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n facge v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_facge_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n facge v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_faddp_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n faddp v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_faddp_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n faddp v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_faddp_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n faddp v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmaxp_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmaxp v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmaxp_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmaxp v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmaxp_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmaxp v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fminp_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fminp v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fminp_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fminp v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fminp_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fminp v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmla_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmla v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmla_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmla v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmla_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmla v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmls_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmls v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmls_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmls v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmls_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmls v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fneg_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fneg v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fneg_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fneg v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fneg_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fneg v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fabs_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fabs v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fabs_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fabs v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fabs_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fabs v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fsqrt_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fsqrt v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fsqrt_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fsqrt v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fsqrt_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fsqrt v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_frintn_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintn v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_frintn_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintn v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_frintn_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintn v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_frintm_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintm v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_frintm_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintm v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_frintm_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintm v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_frintp_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintp v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_frintp_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintp v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_frintp_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintp v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_frintz_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintz v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_frintz_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintz v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_frintz_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintz v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_frinta_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frinta v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_frinta_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frinta v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_frinta_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frinta v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_frintx_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintx v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_frintx_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintx v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_frintx_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n frintx v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_scvtf_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n scvtf v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_scvtf_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n scvtf v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_scvtf_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n scvtf v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcvtzs_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcvtzs v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcvtzs_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcvtzs v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fcvtzs_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcvtzs v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcvtzu_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcvtzu v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcvtzu_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcvtzu v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fcvtzu_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcvtzu v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ucvtf_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ucvtf v2.4s, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ucvtf_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ucvtf v2.2s, v0.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ucvtf_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ucvtf v2.2d, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmeqz_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmeq v2.4s, v0.4s, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmeqz_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmeq v2.2s, v0.2s, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fcmeqz_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmeq v2.2d, v0.2d, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmgtz_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmgt v2.4s, v0.4s, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmgtz_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmgt v2.2s, v0.2s, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fcmgtz_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmgt v2.2d, v0.2d, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmgez_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmge v2.4s, v0.4s, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmgez_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmge v2.2s, v0.2s, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fcmgez_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmge v2.2d, v0.2d, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmlez_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmle v2.4s, v0.4s, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmlez_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmle v2.2s, v0.2s, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fcmlez_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmle v2.2d, v0.2d, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmltz_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmlt v2.4s, v0.4s, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fcmltz_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmlt v2.2s, v0.2s, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fcmltz_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fcmlt v2.2d, v0.2d, #0.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmul_e4s_0(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmul v2.4s, v0.4s, v1.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmul_e4s_3(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmul v2.4s, v0.4s, v1.s[3]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmul_e2s_0(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmul v2.2s, v0.2s, v1.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmul_e2s_1(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmul v2.2s, v0.2s, v1.s[1]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmul_e2d_0(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmul v2.2d, v0.2d, v1.d[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmul_e2d_1(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmul v2.2d, v0.2d, v1.d[1]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmla_e4s_0(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmla v2.4s, v0.4s, v1.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmla_e4s_3(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmla v2.4s, v0.4s, v1.s[3]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmla_e2s_0(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmla v2.2s, v0.2s, v1.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmla_e2s_1(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmla v2.2s, v0.2s, v1.s[1]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmla_e2d_0(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmla v2.2d, v0.2d, v1.d[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmla_e2d_1(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmla v2.2d, v0.2d, v1.d[1]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmls_e4s_0(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmls v2.4s, v0.4s, v1.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmls_e4s_3(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmls v2.4s, v0.4s, v1.s[3]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmls_e2s_0(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmls v2.2s, v0.2s, v1.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmls_e2s_1(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmls v2.2s, v0.2s, v1.s[1]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmls_e2d_0(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmls v2.2d, v0.2d, v1.d[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmls_e2d_1(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmls v2.2d, v0.2d, v1.d[1]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip1_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip1 v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip1_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip1 v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip1_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip1 v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip1_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip1 v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip1_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip1 v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip1_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip1 v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip1_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip1 v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip2 v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip2 v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip2 v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip2_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip2 v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip2_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip2 v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip2_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip2 v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_zip2_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n zip2 v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp1_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp1 v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp1_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp1 v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp1_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp1 v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp1_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp1 v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp1_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp1 v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp1_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp1 v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp1_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp1 v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp2 v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp2 v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp2 v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp2_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp2 v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp2_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp2 v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp2_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp2 v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uzp2_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uzp2 v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn1_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn1 v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn1_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn1 v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn1_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn1 v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn1_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn1 v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn1_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn1 v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn1_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn1 v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn1_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn1 v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn2 v2.16b, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn2 v2.8h, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn2 v2.4s, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn2_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn2 v2.8b, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn2_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn2 v2.4h, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn2_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn2 v2.2s, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_trn2_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n trn2 v2.2d, v0.2d, v1.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ext_16b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ext v2.16b, v0.16b, v1.16b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ext_16b_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ext v2.16b, v0.16b, v1.16b, #3\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ext_16b_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ext v2.16b, v0.16b, v1.16b, #7\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ext_16b_15(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ext v2.16b, v0.16b, v1.16b, #15\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ext_8b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ext v2.8b, v0.8b, v1.8b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ext_8b_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ext v2.8b, v0.8b, v1.8b, #3\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ext_8b_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ext v2.8b, v0.8b, v1.8b, #7\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupe_4s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n dup v2.4s, v0.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupe_4s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n dup v2.4s, v0.s[1]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupe_4s_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n dup v2.4s, v0.s[3]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupe_2s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n dup v2.2s, v0.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupe_2s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n dup v2.2s, v0.s[1]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupe_8h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n dup v2.8h, v0.h[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupe_8h_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n dup v2.8h, v0.h[3]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupe_8h_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n dup v2.8h, v0.h[7]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupe_16b_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n dup v2.16b, v0.b[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupe_16b_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n dup v2.16b, v0.b[7]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupe_16b_15(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n dup v2.16b, v0.b[15]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupe_2d_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n dup v2.2d, v0.d[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupe_2d_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n dup v2.2d, v0.d[1]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupg_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr w5, [x1]\n dup v2.4s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupg_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr w5, [x1]\n dup v2.2s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupg_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr w5, [x1]\n dup v2.8h, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupg_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr w5, [x1]\n dup v2.16b, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupg_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr w5, [x1]\n dup v2.8b, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_dupg_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr w5, [x1]\n dup v2.2d, x5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_insg_s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr x5, [x1]\n mov v2.16b, v0.16b\n ins v2.s[0], w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_inse_s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n ins v2.s[0], v1.s[3]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_insg_s_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr x5, [x1]\n mov v2.16b, v0.16b\n ins v2.s[3], w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_inse_s_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n ins v2.s[3], v1.s[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_insg_h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr x5, [x1]\n mov v2.16b, v0.16b\n ins v2.h[0], w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_inse_h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n ins v2.h[0], v1.h[7]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_insg_h_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr x5, [x1]\n mov v2.16b, v0.16b\n ins v2.h[7], w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_inse_h_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n ins v2.h[7], v1.h[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_insg_b_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr x5, [x1]\n mov v2.16b, v0.16b\n ins v2.b[0], w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_inse_b_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n ins v2.b[0], v1.b[15]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_insg_b_15(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr x5, [x1]\n mov v2.16b, v0.16b\n ins v2.b[15], w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_inse_b_15(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n ins v2.b[15], v1.b[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_insg_d_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr x5, [x1]\n mov v2.16b, v0.16b\n ins v2.d[0], x5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_inse_d_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n ins v2.d[0], v1.d[1]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_insg_d_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ldr x5, [x1]\n mov v2.16b, v0.16b\n ins v2.d[1], x5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_inse_d_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n ins v2.d[1], v1.d[0]\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umov_s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umov w5, v0.s[0]\n dup v2.4s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smov_s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smov x5, v0.s[0]\n dup v2.4s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umov_s_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umov w5, v0.s[3]\n dup v2.4s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smov_s_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smov x5, v0.s[3]\n dup v2.4s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umov_h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umov w5, v0.h[0]\n dup v2.4s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smov_h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smov w5, v0.h[0]\n dup v2.4s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umov_h_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umov w5, v0.h[7]\n dup v2.4s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smov_h_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smov w5, v0.h[7]\n dup v2.4s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umov_b_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umov w5, v0.b[0]\n dup v2.4s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smov_b_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smov w5, v0.b[0]\n dup v2.4s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umov_b_15(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umov w5, v0.b[15]\n dup v2.4s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smov_b_15(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smov w5, v0.b[15]\n dup v2.4s, w5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umov_d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umov x5, v0.d[1]\n dup v2.2d, x5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_movi_4s_5(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n movi v2.4s, #5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mvni_4s_5(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mvni v2.4s, #5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_movi_4s_5_lsl8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n movi v2.4s, #5, lsl #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mvni_4s_5_lsl8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mvni v2.4s, #5, lsl #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_movi_4s_5_lsl16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n movi v2.4s, #5, lsl #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mvni_4s_5_lsl16(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mvni v2.4s, #5, lsl #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_movi_4s_7_msl8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n movi v2.4s, #7, msl #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mvni_4s_7_msl8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mvni v2.4s, #7, msl #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_movi_2s_9_lsl24(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n movi v2.2s, #9, lsl #24\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mvni_2s_9_lsl24(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mvni v2.2s, #9, lsl #24\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_movi_8h_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n movi v2.8h, #3\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mvni_8h_3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mvni v2.8h, #3\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_movi_8h_3_lsl8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n movi v2.8h, #3, lsl #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_mvni_8h_3_lsl8(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mvni v2.8h, #3, lsl #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_movi_16b_77(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n movi v2.16b, #77\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_movi_8b_200(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n movi v2.8b, #200\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_movi_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n movi v2.2d, #0xff00ff00ff00ff00\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_movi_2d0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n movi v2.2d, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmov_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmov v2.4s, #1.5\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmov_2s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmov v2.2s, #-0.25\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_fmov_2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmov v2.2d, #-2.0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_orri_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n orr v2.4s, #3, lsl #8\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_bici_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n bic v2.4s, #3, lsl #16\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_orri_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v0.16b\n orr v2.8h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll_8b_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll v2.8h, v0.8b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll2_16b_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll2 v2.8h, v0.16b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll_8b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll v2.8h, v0.8b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll2_16b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll2 v2.8h, v0.16b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll_8b_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll v2.8h, v0.8b, #7\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll2_16b_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll2 v2.8h, v0.16b, #7\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll_4h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll v2.4s, v0.4h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll2_8h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll2 v2.4s, v0.8h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll_4h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll v2.4s, v0.4h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll2_8h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll2 v2.4s, v0.8h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll_4h_15(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll v2.4s, v0.4h, #15\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll2_8h_15(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll2 v2.4s, v0.8h, #15\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll_2s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll v2.2d, v0.2s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll2_4s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll2 v2.2d, v0.4s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll_2s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll v2.2d, v0.2s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll2_4s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll2 v2.2d, v0.4s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll_2s_31(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll v2.2d, v0.2s, #31\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sshll2_4s_31(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sshll2 v2.2d, v0.4s, #31\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll_8b_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll v2.8h, v0.8b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll2_16b_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll2 v2.8h, v0.16b, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll_8b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll v2.8h, v0.8b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll2_16b_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll2 v2.8h, v0.16b, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll_8b_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll v2.8h, v0.8b, #7\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll2_16b_7(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll2 v2.8h, v0.16b, #7\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll_4h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll v2.4s, v0.4h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll2_8h_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll2 v2.4s, v0.8h, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll_4h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll v2.4s, v0.4h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll2_8h_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll2 v2.4s, v0.8h, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll_4h_15(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll v2.4s, v0.4h, #15\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll2_8h_15(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll2 v2.4s, v0.8h, #15\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll_2s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll v2.2d, v0.2s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll2_4s_0(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll2 v2.2d, v0.4s, #0\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll_2s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll v2.2d, v0.2s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll2_4s_1(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll2 v2.2d, v0.4s, #1\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll_2s_31(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll v2.2d, v0.2s, #31\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ushll2_4s_31(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n ushll2 v2.2d, v0.4s, #31\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_xtn_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n xtn v2.8b, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_xtn2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n xtn2 v2.16b, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_xtn_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n xtn v2.4h, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_xtn2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n xtn2 v2.8h, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_xtn_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n xtn v2.2s, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_xtn2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n xtn2 v2.4s, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddl_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n saddl v2.8h, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddl2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n saddl2 v2.8h, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddl_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n saddl v2.4s, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddl2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n saddl2 v2.4s, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddl_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n saddl v2.2d, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddl2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n saddl2 v2.2d, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddl_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n uaddl v2.8h, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddl2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n uaddl2 v2.8h, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddl_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n uaddl v2.4s, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddl2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n uaddl2 v2.4s, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddl_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n uaddl v2.2d, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddl2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n uaddl2 v2.2d, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssubl_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n ssubl v2.8h, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssubl2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n ssubl2 v2.8h, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssubl_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n ssubl v2.4s, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssubl2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n ssubl2 v2.4s, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssubl_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n ssubl v2.2d, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ssubl2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n ssubl2 v2.2d, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usubl_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n usubl v2.8h, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usubl2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n usubl2 v2.8h, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usubl_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n usubl v2.4s, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usubl2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n usubl2 v2.4s, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usubl_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n usubl v2.2d, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_usubl2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n usubl2 v2.2d, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smull_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smull v2.8h, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smull2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smull2 v2.8h, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smull_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smull v2.4s, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smull2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smull2 v2.4s, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smull_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smull v2.2d, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smull2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smull2 v2.2d, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umull_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umull v2.8h, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umull2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umull2 v2.8h, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umull_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umull v2.4s, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umull2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umull2 v2.4s, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umull_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umull v2.2d, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umull2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umull2 v2.2d, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smlal_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smlal v2.8h, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smlal2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smlal2 v2.8h, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smlal_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smlal v2.4s, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smlal2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smlal2 v2.4s, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smlal_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smlal v2.2d, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smlal2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smlal2 v2.2d, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umlal_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umlal v2.8h, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umlal2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umlal2 v2.8h, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umlal_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umlal v2.4s, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umlal2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umlal2 v2.4s, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umlal_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umlal v2.2d, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umlal2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umlal2 v2.2d, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smlsl_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smlsl v2.8h, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smlsl2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smlsl2 v2.8h, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smlsl_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smlsl v2.4s, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smlsl2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smlsl2 v2.4s, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smlsl_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smlsl v2.2d, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smlsl2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n smlsl2 v2.2d, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umlsl_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umlsl v2.8h, v0.8b, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umlsl2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umlsl2 v2.8h, v0.16b, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umlsl_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umlsl v2.4s, v0.4h, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umlsl2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umlsl2 v2.4s, v0.8h, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umlsl_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umlsl v2.2d, v0.2s, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umlsl2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n mov v2.16b, v1.16b\n umlsl2 v2.2d, v0.4s, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddw_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n saddw v2.8h, v0.8h, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddw2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n saddw2 v2.8h, v0.8h, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddw_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n saddw v2.4s, v0.4s, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddw2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n saddw2 v2.4s, v0.4s, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddw_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n saddw v2.2d, v0.2d, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddw2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n saddw2 v2.2d, v0.2d, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddw_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uaddw v2.8h, v0.8h, v1.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddw2_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uaddw2 v2.8h, v0.8h, v1.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddw_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uaddw v2.4s, v0.4s, v1.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddw2_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uaddw2 v2.4s, v0.4s, v1.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddw_2s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uaddw v2.2d, v0.2d, v1.2s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddw2_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uaddw2 v2.2d, v0.2d, v1.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_addv_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n addv b2, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_addv_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n addv b2, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_addv_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n addv h2, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_addv_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n addv h2, v0.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_addv_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n addv s2, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smaxv_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smaxv b2, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smaxv_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smaxv b2, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smaxv_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smaxv h2, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smaxv_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smaxv h2, v0.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_smaxv_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n smaxv s2, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sminv_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sminv b2, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sminv_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sminv b2, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sminv_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sminv h2, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sminv_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sminv h2, v0.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_sminv_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n sminv s2, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umaxv_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umaxv b2, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umaxv_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umaxv b2, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umaxv_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umaxv h2, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umaxv_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umaxv h2, v0.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_umaxv_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n umaxv s2, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uminv_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uminv b2, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uminv_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uminv b2, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uminv_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uminv h2, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uminv_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uminv h2, v0.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uminv_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uminv s2, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddlv_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n saddlv h2, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddlv_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n saddlv h2, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddlv_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n saddlv s2, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddlv_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n saddlv s2, v0.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_saddlv_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n saddlv d2, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddlv_16b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uaddlv h2, v0.16b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddlv_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uaddlv h2, v0.8b\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddlv_8h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uaddlv s2, v0.8h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddlv_4h(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uaddlv s2, v0.4h\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_uaddlv_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n uaddlv d2, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvd_faddp_s2d(double *p, double *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n faddp d2, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_addp_s2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n addp d2, v0.2d\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmaxv_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmaxv s2, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fminv_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fminv s2, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fmaxnmv_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fmaxnmv s2, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvf_fminnmv_4s(float *p, float *q) { AASM("ldr q0, [x0]\n ldr q1, [x1]\n ldr q2, [x0, #16]\n fminnmv s2, v0.4s\n str q0, [x0]\n str q2, [x1]"); }
NK void nvi_ld1x1_16b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.16b}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_st1x1_16b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n st1 {v0.16b}, [x1]"); }
NK void nvi_ld1x2_16b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.16b, v1.16b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st1x2_16b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st1 {v0.16b, v1.16b}, [x1]"); }
NK void nvi_ld2_16b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2 {v0.16b, v1.16b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st2_16b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st2 {v0.16b, v1.16b}, [x1]"); }
NK void nvi_ld1x3_16b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.16b, v1.16b, v2.16b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st1x3_16b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st1 {v0.16b, v1.16b, v2.16b}, [x1]"); }
NK void nvi_ld3_16b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3 {v0.16b, v1.16b, v2.16b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st3_16b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st3 {v0.16b, v1.16b, v2.16b}, [x1]"); }
NK void nvi_ld1x4_16b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.16b, v1.16b, v2.16b, v3.16b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st1x4_16b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st1 {v0.16b, v1.16b, v2.16b, v3.16b}, [x1]"); }
NK void nvi_ld4_16b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4 {v0.16b, v1.16b, v2.16b, v3.16b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st4_16b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st4 {v0.16b, v1.16b, v2.16b, v3.16b}, [x1]"); }
NK void nvi_ld1r_16b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1r {v0.16b}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_ld2r_16b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2r {v0.16b, v1.16b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_ld3r_16b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3r {v0.16b, v1.16b, v2.16b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_ld4r_16b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4r {v0.16b, v1.16b, v2.16b, v3.16b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_ld1x1_8h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.8h}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_st1x1_8h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n st1 {v0.8h}, [x1]"); }
NK void nvi_ld1x2_8h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.8h, v1.8h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st1x2_8h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st1 {v0.8h, v1.8h}, [x1]"); }
NK void nvi_ld2_8h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2 {v0.8h, v1.8h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st2_8h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st2 {v0.8h, v1.8h}, [x1]"); }
NK void nvi_ld1x3_8h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.8h, v1.8h, v2.8h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st1x3_8h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st1 {v0.8h, v1.8h, v2.8h}, [x1]"); }
NK void nvi_ld3_8h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3 {v0.8h, v1.8h, v2.8h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st3_8h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st3 {v0.8h, v1.8h, v2.8h}, [x1]"); }
NK void nvi_ld1x4_8h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.8h, v1.8h, v2.8h, v3.8h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st1x4_8h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st1 {v0.8h, v1.8h, v2.8h, v3.8h}, [x1]"); }
NK void nvi_ld4_8h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4 {v0.8h, v1.8h, v2.8h, v3.8h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st4_8h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st4 {v0.8h, v1.8h, v2.8h, v3.8h}, [x1]"); }
NK void nvi_ld1r_8h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1r {v0.8h}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_ld2r_8h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2r {v0.8h, v1.8h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_ld3r_8h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3r {v0.8h, v1.8h, v2.8h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_ld4r_8h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4r {v0.8h, v1.8h, v2.8h, v3.8h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_ld1x1_4s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.4s}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_st1x1_4s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n st1 {v0.4s}, [x1]"); }
NK void nvi_ld1x2_4s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.4s, v1.4s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st1x2_4s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st1 {v0.4s, v1.4s}, [x1]"); }
NK void nvi_ld2_4s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2 {v0.4s, v1.4s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st2_4s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st2 {v0.4s, v1.4s}, [x1]"); }
NK void nvi_ld1x3_4s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.4s, v1.4s, v2.4s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st1x3_4s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st1 {v0.4s, v1.4s, v2.4s}, [x1]"); }
NK void nvi_ld3_4s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3 {v0.4s, v1.4s, v2.4s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st3_4s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st3 {v0.4s, v1.4s, v2.4s}, [x1]"); }
NK void nvi_ld1x4_4s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.4s, v1.4s, v2.4s, v3.4s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st1x4_4s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st1 {v0.4s, v1.4s, v2.4s, v3.4s}, [x1]"); }
NK void nvi_ld4_4s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4 {v0.4s, v1.4s, v2.4s, v3.4s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st4_4s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st4 {v0.4s, v1.4s, v2.4s, v3.4s}, [x1]"); }
NK void nvi_ld1r_4s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1r {v0.4s}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_ld2r_4s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2r {v0.4s, v1.4s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_ld3r_4s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3r {v0.4s, v1.4s, v2.4s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_ld4r_4s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4r {v0.4s, v1.4s, v2.4s, v3.4s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_ld1x1_2d(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.2d}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_st1x1_2d(int *p, int *q) { AASM("ldr q0, [x0, #0]\n st1 {v0.2d}, [x1]"); }
NK void nvi_ld1x2_2d(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.2d, v1.2d}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st1x2_2d(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st1 {v0.2d, v1.2d}, [x1]"); }
NK void nvi_ld2_2d(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2 {v0.2d, v1.2d}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st2_2d(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st2 {v0.2d, v1.2d}, [x1]"); }
NK void nvi_ld1x3_2d(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.2d, v1.2d, v2.2d}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st1x3_2d(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st1 {v0.2d, v1.2d, v2.2d}, [x1]"); }
NK void nvi_ld3_2d(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3 {v0.2d, v1.2d, v2.2d}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st3_2d(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st3 {v0.2d, v1.2d, v2.2d}, [x1]"); }
NK void nvi_ld1x4_2d(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.2d, v1.2d, v2.2d, v3.2d}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st1x4_2d(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st1 {v0.2d, v1.2d, v2.2d, v3.2d}, [x1]"); }
NK void nvi_ld4_2d(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4 {v0.2d, v1.2d, v2.2d, v3.2d}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st4_2d(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st4 {v0.2d, v1.2d, v2.2d, v3.2d}, [x1]"); }
NK void nvi_ld1r_2d(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1r {v0.2d}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_ld2r_2d(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2r {v0.2d, v1.2d}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_ld3r_2d(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3r {v0.2d, v1.2d, v2.2d}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_ld4r_2d(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4r {v0.2d, v1.2d, v2.2d, v3.2d}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_ld1x1_8b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.8b}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_st1x1_8b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n st1 {v0.8b}, [x1]"); }
NK void nvi_ld1x2_8b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.8b, v1.8b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st1x2_8b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st1 {v0.8b, v1.8b}, [x1]"); }
NK void nvi_ld2_8b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2 {v0.8b, v1.8b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st2_8b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st2 {v0.8b, v1.8b}, [x1]"); }
NK void nvi_ld1x3_8b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.8b, v1.8b, v2.8b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st1x3_8b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st1 {v0.8b, v1.8b, v2.8b}, [x1]"); }
NK void nvi_ld3_8b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3 {v0.8b, v1.8b, v2.8b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st3_8b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st3 {v0.8b, v1.8b, v2.8b}, [x1]"); }
NK void nvi_ld1x4_8b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.8b, v1.8b, v2.8b, v3.8b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st1x4_8b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st1 {v0.8b, v1.8b, v2.8b, v3.8b}, [x1]"); }
NK void nvi_ld4_8b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4 {v0.8b, v1.8b, v2.8b, v3.8b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st4_8b(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st4 {v0.8b, v1.8b, v2.8b, v3.8b}, [x1]"); }
NK void nvi_ld1r_8b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1r {v0.8b}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_ld2r_8b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2r {v0.8b, v1.8b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_ld3r_8b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3r {v0.8b, v1.8b, v2.8b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_ld4r_8b(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4r {v0.8b, v1.8b, v2.8b, v3.8b}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_ld1x1_4h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.4h}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_st1x1_4h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n st1 {v0.4h}, [x1]"); }
NK void nvi_ld1x2_4h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.4h, v1.4h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st1x2_4h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st1 {v0.4h, v1.4h}, [x1]"); }
NK void nvi_ld2_4h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2 {v0.4h, v1.4h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st2_4h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st2 {v0.4h, v1.4h}, [x1]"); }
NK void nvi_ld1x3_4h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.4h, v1.4h, v2.4h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st1x3_4h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st1 {v0.4h, v1.4h, v2.4h}, [x1]"); }
NK void nvi_ld3_4h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3 {v0.4h, v1.4h, v2.4h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st3_4h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st3 {v0.4h, v1.4h, v2.4h}, [x1]"); }
NK void nvi_ld1x4_4h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.4h, v1.4h, v2.4h, v3.4h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st1x4_4h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st1 {v0.4h, v1.4h, v2.4h, v3.4h}, [x1]"); }
NK void nvi_ld4_4h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4 {v0.4h, v1.4h, v2.4h, v3.4h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st4_4h(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st4 {v0.4h, v1.4h, v2.4h, v3.4h}, [x1]"); }
NK void nvi_ld1r_4h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1r {v0.4h}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_ld2r_4h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2r {v0.4h, v1.4h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_ld3r_4h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3r {v0.4h, v1.4h, v2.4h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_ld4r_4h(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4r {v0.4h, v1.4h, v2.4h, v3.4h}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_ld1x1_2s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.2s}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_st1x1_2s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n st1 {v0.2s}, [x1]"); }
NK void nvi_ld1x2_2s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.2s, v1.2s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st1x2_2s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st1 {v0.2s, v1.2s}, [x1]"); }
NK void nvi_ld2_2s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2 {v0.2s, v1.2s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_st2_2s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n st2 {v0.2s, v1.2s}, [x1]"); }
NK void nvi_ld1x3_2s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.2s, v1.2s, v2.2s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st1x3_2s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st1 {v0.2s, v1.2s, v2.2s}, [x1]"); }
NK void nvi_ld3_2s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3 {v0.2s, v1.2s, v2.2s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_st3_2s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n st3 {v0.2s, v1.2s, v2.2s}, [x1]"); }
NK void nvi_ld1x4_2s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1 {v0.2s, v1.2s, v2.2s, v3.2s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st1x4_2s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st1 {v0.2s, v1.2s, v2.2s, v3.2s}, [x1]"); }
NK void nvi_ld4_2s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4 {v0.2s, v1.2s, v2.2s, v3.2s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_st4_2s(int *p, int *q) { AASM("ldr q0, [x0, #0]\n ldr q1, [x0, #16]\n ldr q2, [x0, #32]\n ldr q3, [x0, #48]\n st4 {v0.2s, v1.2s, v2.2s, v3.2s}, [x1]"); }
NK void nvi_ld1r_2s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld1r {v0.2s}, [x0]\n str q0, [x1, #0]\n "); }
NK void nvi_ld2r_2s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld2r {v0.2s, v1.2s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n "); }
NK void nvi_ld3r_2s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld3r {v0.2s, v1.2s, v2.2s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n "); }
NK void nvi_ld4r_2s(int *p, int *q) { AASM("movi v0.2d, #0\n movi v1.2d, #0\n movi v2.2d, #0\n movi v3.2d, #0\n ld4r {v0.2s, v1.2s, v2.2s, v3.2s}, [x0]\n str q0, [x1, #0]\n str q1, [x1, #16]\n str q2, [x1, #32]\n str q3, [x1, #48]\n "); }
NK void nvi_ld1post_4s(int *p, int *q) { AASM("ld1 {v0.4s}, [x0], #16\n ldr q1, [x0]\n str q0, [x1]\n str q1, [x1, #16]"); }
NK void nvi_st1post_4s(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x0, #16]\n st1 {v0.4s}, [x1], #16\n str q1, [x1]"); }
NK void nvi_ld1postr_4s(int *p, int *q) { AASM("mov x2, #16\n ld1 {v0.4s}, [x0], x2\n ldr q1, [x0]\n str q0, [x1]\n str q1, [x1, #16]"); }
NK void nvi_ld1post_8b(int *p, int *q) { AASM("ld1 {v0.8b}, [x0], #8\n ldr q1, [x0]\n str q0, [x1]\n str q1, [x1, #16]"); }
NK void nvi_st1post_8b(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x0, #16]\n st1 {v0.8b}, [x1], #8\n str q1, [x1]"); }
NK void nvi_ld1postr_8b(int *p, int *q) { AASM("mov x2, #8\n ld1 {v0.8b}, [x0], x2\n ldr q1, [x0]\n str q0, [x1]\n str q1, [x1, #16]"); }
NK void nvi_ld1post_2d(int *p, int *q) { AASM("ld1 {v0.2d}, [x0], #16\n ldr q1, [x0]\n str q0, [x1]\n str q1, [x1, #16]"); }
NK void nvi_st1post_2d(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x0, #16]\n st1 {v0.2d}, [x1], #16\n str q1, [x1]"); }
NK void nvi_ld1postr_2d(int *p, int *q) { AASM("mov x2, #16\n ld1 {v0.2d}, [x0], x2\n ldr q1, [x0]\n str q0, [x1]\n str q1, [x1, #16]"); }
NK void nvi_ld2post(int *p, int *q) { AASM("ld2 {v0.4s, v1.4s}, [x0], #32\n ldr q2, [x0]\n str q0, [x1]\n str q1, [x1, #16]\n str q2, [x1, #32]"); }
NK void nvi_ld1lane_b0(int *p, int *q) { AASM("ldr q0, [x1]\n ld1 {v0.b}[0], [x0]\n str q0, [x1]"); }
NK void nvi_st1lane_b0(int *p, int *q) { AASM("ldr q0, [x0]\n st1 {v0.b}[0], [x1]"); }
NK void nvi_ld1lane_b7(int *p, int *q) { AASM("ldr q0, [x1]\n ld1 {v0.b}[7], [x0]\n str q0, [x1]"); }
NK void nvi_st1lane_b7(int *p, int *q) { AASM("ldr q0, [x0]\n st1 {v0.b}[7], [x1]"); }
NK void nvi_ld1lane_b15(int *p, int *q) { AASM("ldr q0, [x1]\n ld1 {v0.b}[15], [x0]\n str q0, [x1]"); }
NK void nvi_st1lane_b15(int *p, int *q) { AASM("ldr q0, [x0]\n st1 {v0.b}[15], [x1]"); }
NK void nvi_ld1lane_h0(int *p, int *q) { AASM("ldr q0, [x1]\n ld1 {v0.h}[0], [x0]\n str q0, [x1]"); }
NK void nvi_st1lane_h0(int *p, int *q) { AASM("ldr q0, [x0]\n st1 {v0.h}[0], [x1]"); }
NK void nvi_ld1lane_h3(int *p, int *q) { AASM("ldr q0, [x1]\n ld1 {v0.h}[3], [x0]\n str q0, [x1]"); }
NK void nvi_st1lane_h3(int *p, int *q) { AASM("ldr q0, [x0]\n st1 {v0.h}[3], [x1]"); }
NK void nvi_ld1lane_h7(int *p, int *q) { AASM("ldr q0, [x1]\n ld1 {v0.h}[7], [x0]\n str q0, [x1]"); }
NK void nvi_st1lane_h7(int *p, int *q) { AASM("ldr q0, [x0]\n st1 {v0.h}[7], [x1]"); }
NK void nvi_ld1lane_s0(int *p, int *q) { AASM("ldr q0, [x1]\n ld1 {v0.s}[0], [x0]\n str q0, [x1]"); }
NK void nvi_st1lane_s0(int *p, int *q) { AASM("ldr q0, [x0]\n st1 {v0.s}[0], [x1]"); }
NK void nvi_ld1lane_s1(int *p, int *q) { AASM("ldr q0, [x1]\n ld1 {v0.s}[1], [x0]\n str q0, [x1]"); }
NK void nvi_st1lane_s1(int *p, int *q) { AASM("ldr q0, [x0]\n st1 {v0.s}[1], [x1]"); }
NK void nvi_ld1lane_s3(int *p, int *q) { AASM("ldr q0, [x1]\n ld1 {v0.s}[3], [x0]\n str q0, [x1]"); }
NK void nvi_st1lane_s3(int *p, int *q) { AASM("ldr q0, [x0]\n st1 {v0.s}[3], [x1]"); }
NK void nvi_ld1lane_d0(int *p, int *q) { AASM("ldr q0, [x1]\n ld1 {v0.d}[0], [x0]\n str q0, [x1]"); }
NK void nvi_st1lane_d0(int *p, int *q) { AASM("ldr q0, [x0]\n st1 {v0.d}[0], [x1]"); }
NK void nvi_ld1lane_d1(int *p, int *q) { AASM("ldr q0, [x1]\n ld1 {v0.d}[1], [x0]\n str q0, [x1]"); }
NK void nvi_st1lane_d1(int *p, int *q) { AASM("ldr q0, [x0]\n st1 {v0.d}[1], [x1]"); }
NK void nvi_ld2lane_s1(int *p, int *q) { AASM("ldr q0, [x1]\n ldr q1, [x1, #16]\n ld2 {v0.s, v1.s}[1], [x0]\n str q0, [x1]\n str q1, [x1, #16]"); }
NK void nvi_st2lane_h3(int *p, int *q) { AASM("ldr q0, [x0]\n ldr q1, [x0, #16]\n st2 {v0.h, v1.h}[3], [x1]"); }
NK void nvi_ld3lane_b5(int *p, int *q) { AASM("ldr q0, [x1]\n ldr q1, [x1, #16]\n ldr q2, [x1, #32]\n ld3 {v0.b, v1.b, v2.b}[5], [x0]\n str q0, [x1]\n str q1, [x1, #16]\n str q2, [x1, #32]"); }
NK void nvi_ldr_q(int *p, int *q) { AASM("ldr q0, [x0, #16]\n str q0, [x1]"); }
NK void nvi_ldr_d(int *p, int *q) { AASM("movi v0.2d, #0xffffffffffffffff\n ldr d0, [x0, #8]\n str q0, [x1]"); }
NK void nvi_ldr_s(int *p, int *q) { AASM("movi v0.2d, #0xffffffffffffffff\n ldr s0, [x0, #4]\n str q0, [x1]"); }
NK void nvi_ldr_h(int *p, int *q) { AASM("movi v0.2d, #0xffffffffffffffff\n ldr h0, [x0, #2]\n str q0, [x1]"); }
NK void nvi_ldr_b(int *p, int *q) { AASM("movi v0.2d, #0xffffffffffffffff\n ldr b0, [x0, #3]\n str q0, [x1]"); }
NK void nvi_str_d(int *p, int *q) { AASM("ldr q0, [x0]\n str d0, [x1, #8]"); }
NK void nvi_str_s(int *p, int *q) { AASM("ldr q0, [x0]\n str s0, [x1, #4]"); }
NK void nvi_str_h(int *p, int *q) { AASM("ldr q0, [x0]\n str h0, [x1, #2]"); }
NK void nvi_str_b(int *p, int *q) { AASM("ldr q0, [x0]\n str b0, [x1, #3]"); }
NK void nvi_ldur_q(int *p, int *q) { AASM("ldur q0, [x0, #5]\n stur q0, [x1, #3]"); }
NK void nvi_ldr_q_post(int *p, int *q) { AASM("ldr q0, [x0], #16\n ldr q1, [x0]\n str q0, [x1]\n str q1, [x1, #16]"); }
NK void nvi_ldr_q_pre(int *p, int *q) { AASM("ldr q0, [x0, #16]!\n ldr q1, [x0]\n str q0, [x1]\n str q1, [x1, #16]"); }
NK void nvi_str_q_post(int *p, int *q) { AASM("ldr q0, [x0]\n str q0, [x1], #16\n str q0, [x1]"); }
NK void nvi_str_q_pre(int *p, int *q) { AASM("ldr q0, [x0]\n str q0, [x1, #16]!\n str q0, [x1, #16]"); }
NK void nvi_ldr_q_reg(int *p, int *q) { AASM("mov x2, #16\n ldr q0, [x0, x2]\n str q0, [x1]"); }
NK void nvi_ldr_q_regsh(int *p, int *q) { AASM("mov x2, #1\n ldr q0, [x0, x2, lsl #4]\n str q0, [x1]"); }
NK void nvi_ldr_d_regw(int *p, int *q) { AASM("mov w2, #2\n ldr d0, [x0, w2, uxtw #3]\n str q0, [x1]"); }
NK void nvi_ldp_q(int *p, int *q) { AASM("ldp q0, q1, [x0]\n stp q1, q0, [x1]"); }
NK void nvi_ldp_q_post(int *p, int *q) { AASM("ldp q0, q1, [x0], #32\n ldr q2, [x0]\n stp q0, q1, [x1]\n str q2, [x1, #32]"); }
NK void nvi_ldp_d(int *p, int *q) { AASM("ldp d0, d1, [x0, #8]\n stp d1, d0, [x1]"); }
NK void nvi_ldp_s(int *p, int *q) { AASM("ldp s0, s1, [x0, #4]\n stp s1, s0, [x1]"); }
NK void nvi_stp_q_pre(int *p, int *q) { AASM("ldp q0, q1, [x0]\n stp q0, q1, [x1, #16]!\n str q0, [x1, #32]"); }
NK void nvi_fmov_x_d(int *p, int *q) { AASM("ldr q0, [x0]\n fmov x5, d0\n str x5, [x1]"); }
NK void nvi_fmov_d_x(int *p, int *q) { AASM("ldr x5, [x0]\n fmov d2, x5\n str q2, [x1]"); }
NK void nvi_fmov_x_dh(int *p, int *q) { AASM("ldr q0, [x0]\n fmov x5, v0.d[1]\n str x5, [x1]"); }
NK void nvi_fmov_dh_x(int *p, int *q) { AASM("ldr q2, [x1]\n ldr x5, [x0]\n fmov v2.d[1], x5\n str q2, [x1]"); }
NK void nvi_fmov_w_s(int *p, int *q) { AASM("ldr q0, [x0]\n fmov w5, s0\n str w5, [x1]"); }
NK void nvi_fmov_s_w(int *p, int *q) { AASM("ldr w5, [x0]\n fmov s2, w5\n str q2, [x1]"); }
#endif
