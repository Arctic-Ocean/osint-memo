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
