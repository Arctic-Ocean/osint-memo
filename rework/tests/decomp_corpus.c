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

int main(int argc, char **argv) { (void)argv; return add(argc, 1) + fib(3) + (int)sum_to(2); }
