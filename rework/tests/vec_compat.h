/* x86 에서 의사코드의 벡터 식(`vadd_s32`·`vmax_s32`·`vdup_n_s32` …)을 컴파일하기 위한 시험용 호환 헤더.
 * 디컴파일러는 NEON 벡터 형·내장 함수 이름(ACLE)을 그대로 x86 SIMD 에도 쓴다. 여기서는 그 이름을 clang 벡터 확장과 원소별 반복문으로 구현한다.
 * 속도가 아니라 '의미가 맞는가'를 확인하는 용도라서 원소마다 반복한다. */
#ifndef REWORK_VEC_COMPAT_H
#define REWORK_VEC_COMPAT_H
#include <stdint.h>

typedef int8_t int8x8_t __attribute__((vector_size(8)));
typedef uint8_t uint8x8_t __attribute__((vector_size(8)));
typedef int16_t int16x4_t __attribute__((vector_size(8)));
typedef uint16_t uint16x4_t __attribute__((vector_size(8)));
typedef int32_t int32x2_t __attribute__((vector_size(8)));
typedef uint32_t uint32x2_t __attribute__((vector_size(8)));
typedef float float32x2_t __attribute__((vector_size(8)));
typedef int64_t int64x1_t __attribute__((vector_size(8)));
typedef uint64_t uint64x1_t __attribute__((vector_size(8)));

/* 128비트(q) 형: 벡터를 값으로 주고받는 매개변수·반환이 쓰는 uint64x2_t 와, 그 위에서 필요한 형들 */
typedef int8_t int8x16_t __attribute__((vector_size(16)));
typedef uint8_t uint8x16_t __attribute__((vector_size(16)));
typedef int16_t int16x8_t __attribute__((vector_size(16)));
typedef uint16_t uint16x8_t __attribute__((vector_size(16)));
typedef int32_t int32x4_t __attribute__((vector_size(16)));
typedef uint32_t uint32x4_t __attribute__((vector_size(16)));
typedef float float32x4_t __attribute__((vector_size(16)));
typedef int64_t int64x2_t __attribute__((vector_size(16)));
typedef uint64_t uint64x2_t __attribute__((vector_size(16)));
typedef double float64x2_t __attribute__((vector_size(16)));

#define VC_N(T) ((int)(sizeof(T) / sizeof(((T){0})[0])))

/* 정수 형 하나(sfx: 접미사, ET: 원소형, UT: 부호 없는 원소형, VT: 벡터형, UV: 부호 없는 벡터형, SV: 부호 있는 벡터형, BITS: 원소 비트 수) */
#define VC_INT(sfx, ET, UT, VT, UV, SV, BITS) \
static inline VT vdup_n_##sfx(ET x) { VT r; for (int i = 0; i < VC_N(VT); i++) r[i] = x; return r; } \
static inline VT vadd_##sfx(VT a, VT b) { VT r; for (int i = 0; i < VC_N(VT); i++) r[i] = (ET)((UT)a[i] + (UT)b[i]); return r; } \
static inline VT vsub_##sfx(VT a, VT b) { VT r; for (int i = 0; i < VC_N(VT); i++) r[i] = (ET)((UT)a[i] - (UT)b[i]); return r; } \
static inline VT vmax_##sfx(VT a, VT b) { VT r; for (int i = 0; i < VC_N(VT); i++) r[i] = a[i] > b[i] ? a[i] : b[i]; return r; } \
static inline VT vmin_##sfx(VT a, VT b) { VT r; for (int i = 0; i < VC_N(VT); i++) r[i] = a[i] < b[i] ? a[i] : b[i]; return r; } \
static inline VT vabd_##sfx(VT a, VT b) { VT r; for (int i = 0; i < VC_N(VT); i++) r[i] = a[i] > b[i] ? (ET)((UT)a[i] - (UT)b[i]) : (ET)((UT)b[i] - (UT)a[i]); return r; } \
static inline VT vhadd_##sfx(VT a, VT b) { VT r; for (int i = 0; i < VC_N(VT); i++) r[i] = (ET)(((long long)a[i] + (long long)b[i]) >> 1); return r; } \
static inline VT vrhadd_##sfx(VT a, VT b) { VT r; for (int i = 0; i < VC_N(VT); i++) r[i] = (ET)(((long long)a[i] + (long long)b[i] + 1) >> 1); return r; } \
static inline VT vqadd_##sfx(VT a, VT b) { VT r; for (int i = 0; i < VC_N(VT); i++) { long long s = (long long)a[i] + (long long)b[i]; long long lo = (ET)-1 < 0 ? -((long long)1 << (BITS - 1)) : 0, hi = (ET)-1 < 0 ? ((long long)1 << (BITS - 1)) - 1 : (((long long)1 << BITS) - 1); r[i] = (ET)(s < lo ? lo : s > hi ? hi : s); } return r; } \
static inline VT vqsub_##sfx(VT a, VT b) { VT r; for (int i = 0; i < VC_N(VT); i++) { long long s = (long long)a[i] - (long long)b[i]; long long lo = (ET)-1 < 0 ? -((long long)1 << (BITS - 1)) : 0, hi = (ET)-1 < 0 ? ((long long)1 << (BITS - 1)) - 1 : (((long long)1 << BITS) - 1); r[i] = (ET)(s < lo ? lo : s > hi ? hi : s); } return r; } \
static inline VT vabs_##sfx(VT a) { VT r; for (int i = 0; i < VC_N(VT); i++) r[i] = a[i] < 0 ? (ET)(0 - (UT)a[i]) : a[i]; return r; } \
static inline UV vceq_##sfx(VT a, VT b) { UV r; for (int i = 0; i < VC_N(VT); i++) r[i] = a[i] == b[i] ? (UT)-1 : 0; return r; } \
static inline UV vcgt_##sfx(VT a, VT b) { UV r; for (int i = 0; i < VC_N(VT); i++) r[i] = a[i] > b[i] ? (UT)-1 : 0; return r; } \
static inline UV vcge_##sfx(VT a, VT b) { UV r; for (int i = 0; i < VC_N(VT); i++) r[i] = a[i] >= b[i] ? (UT)-1 : 0; return r; } \
static inline UV vtst_##sfx(VT a, VT b) { UV r; for (int i = 0; i < VC_N(VT); i++) r[i] = (a[i] & b[i]) ? (UT)-1 : 0; return r; } \
static inline VT vshl_##sfx(VT a, SV b) { VT r; for (int i = 0; i < VC_N(VT); i++) { int s = (int8_t)b[i]; \
    if (s >= BITS) r[i] = 0; else if (s >= 0) r[i] = (ET)((UT)a[i] << s); else if (-s >= BITS) r[i] = a[i] < 0 ? (ET)-1 : 0; else r[i] = (ET)(a[i] >> -s); } return r; } \
static inline VT vshr_n_##sfx(VT a, int n) { VT r; for (int i = 0; i < VC_N(VT); i++) r[i] = n >= BITS ? ((ET)-1 < 0 ? (a[i] < 0 ? (ET)-1 : 0) : 0) : (ET)(a[i] >> n); return r; } \
static inline VT vsra_n_##sfx(VT acc, VT a, int n) { return vadd_##sfx(acc, vshr_n_##sfx(a, n)); } \
static inline VT vpadd_##sfx(VT a, VT b) { VT r; int n = VC_N(VT); for (int i = 0; i < n / 2; i++) { r[i] = (ET)((UT)a[2 * i] + (UT)a[2 * i + 1]); r[n / 2 + i] = (ET)((UT)b[2 * i] + (UT)b[2 * i + 1]); } return r; } \
static inline VT vpmax_##sfx(VT a, VT b) { VT r; int n = VC_N(VT); for (int i = 0; i < n / 2; i++) { r[i] = a[2 * i] > a[2 * i + 1] ? a[2 * i] : a[2 * i + 1]; r[n / 2 + i] = b[2 * i] > b[2 * i + 1] ? b[2 * i] : b[2 * i + 1]; } return r; } \
static inline VT vpmin_##sfx(VT a, VT b) { VT r; int n = VC_N(VT); for (int i = 0; i < n / 2; i++) { r[i] = a[2 * i] < a[2 * i + 1] ? a[2 * i] : a[2 * i + 1]; r[n / 2 + i] = b[2 * i] < b[2 * i + 1] ? b[2 * i] : b[2 * i + 1]; } return r; } \
static inline VT vld1_##sfx(const ET *p) { VT r; __builtin_memcpy(&r, p, sizeof r); return r; } \
static inline void vst1_##sfx(ET *p, VT v) { __builtin_memcpy(p, &v, sizeof v); } \
static inline VT vset_lane_##sfx(ET x, VT v, int i) { v[i] = x; return v; } \
static inline ET vaddv_##sfx(VT a) { UT s = 0; for (int i = 0; i < VC_N(VT); i++) s += (UT)a[i]; return (ET)s; } \
static inline ET vmaxv_##sfx(VT a) { ET s = a[0]; for (int i = 1; i < VC_N(VT); i++) if (a[i] > s) s = a[i]; return s; } \
static inline ET vminv_##sfx(VT a) { ET s = a[0]; for (int i = 1; i < VC_N(VT); i++) if (a[i] < s) s = a[i]; return s; }

VC_INT(s8, int8_t, uint8_t, int8x8_t, uint8x8_t, int8x8_t, 8)
VC_INT(u8, uint8_t, uint8_t, uint8x8_t, uint8x8_t, int8x8_t, 8)
VC_INT(s16, int16_t, uint16_t, int16x4_t, uint16x4_t, int16x4_t, 16)
VC_INT(u16, uint16_t, uint16_t, uint16x4_t, uint16x4_t, int16x4_t, 16)
VC_INT(s32, int32_t, uint32_t, int32x2_t, uint32x2_t, int32x2_t, 32)
VC_INT(u32, uint32_t, uint32_t, uint32x2_t, uint32x2_t, int32x2_t, 32)

#define vget_lane_s8(v, i) ((v)[i])
#define vget_lane_u8(v, i) ((v)[i])
#define vget_lane_s16(v, i) ((v)[i])
#define vget_lane_u16(v, i) ((v)[i])
#define vget_lane_s32(v, i) ((v)[i])
#define vget_lane_u32(v, i) ((v)[i])
#define vget_lane_f32(v, i) ((v)[i])

/* float32x2_t */
static inline float32x2_t vdup_n_f32(float x) { float32x2_t r = {x, x}; return r; }
static inline float32x2_t vadd_f32(float32x2_t a, float32x2_t b) { return a + b; }
static inline float32x2_t vsub_f32(float32x2_t a, float32x2_t b) { return a - b; }
static inline float32x2_t vmul_f32(float32x2_t a, float32x2_t b) { return a * b; }
static inline float32x2_t vdiv_f32(float32x2_t a, float32x2_t b) { return a / b; }
static inline float32x2_t vfma_f32(float32x2_t a, float32x2_t b, float32x2_t c) { float32x2_t r; for (int i = 0; i < 2; i++) r[i] = __builtin_fmaf(b[i], c[i], a[i]); return r; }     /* a + b*c, 한 번만 반올림 */
static inline float32x2_t vfms_f32(float32x2_t a, float32x2_t b, float32x2_t c) { float32x2_t r; for (int i = 0; i < 2; i++) r[i] = __builtin_fmaf(-b[i], c[i], a[i]); return r; }  /* a - b*c */
static inline float32x2_t vmax_f32(float32x2_t a, float32x2_t b) { float32x2_t r; for (int i = 0; i < 2; i++) r[i] = a[i] > b[i] ? a[i] : b[i]; return r; }
static inline float32x2_t vmin_f32(float32x2_t a, float32x2_t b) { float32x2_t r; for (int i = 0; i < 2; i++) r[i] = a[i] < b[i] ? a[i] : b[i]; return r; }
static inline float32x2_t vsqrt_f32(float32x2_t a) { float32x2_t r; for (int i = 0; i < 2; i++) r[i] = __builtin_sqrtf(a[i]); return r; }
static inline float32x2_t vabs_f32(float32x2_t a) { float32x2_t r; for (int i = 0; i < 2; i++) r[i] = __builtin_fabsf(a[i]); return r; }
static inline uint32x2_t vceq_f32(float32x2_t a, float32x2_t b) { uint32x2_t r; for (int i = 0; i < 2; i++) r[i] = a[i] == b[i] ? 0xffffffffu : 0; return r; }
static inline uint32x2_t vcgt_f32(float32x2_t a, float32x2_t b) { uint32x2_t r; for (int i = 0; i < 2; i++) r[i] = a[i] > b[i] ? 0xffffffffu : 0; return r; }
static inline uint32x2_t vcge_f32(float32x2_t a, float32x2_t b) { uint32x2_t r; for (int i = 0; i < 2; i++) r[i] = a[i] >= b[i] ? 0xffffffffu : 0; return r; }
static inline float32x2_t vpadd_f32(float32x2_t a, float32x2_t b) { float32x2_t r = {a[0] + a[1], b[0] + b[1]}; return r; }
static inline float32x2_t vcvt_f32_s32(int32x2_t a) { float32x2_t r = {(float)a[0], (float)a[1]}; return r; }
static inline float32x2_t vcvt_f32_u32(uint32x2_t a) { float32x2_t r = {(float)a[0], (float)a[1]}; return r; }
static inline int32x2_t vcvt_s32_f32(float32x2_t a) { int32x2_t r; for (int i = 0; i < 2; i++) r[i] = (a[i] >= 2147483648.0f || a[i] != a[i]) ? (a[i] != a[i] ? 0 : 0x7fffffff) : (a[i] <= -2147483648.0f ? (int32_t)0x80000000 : (int32_t)a[i]); return r; }
static inline float vc_rintf(float x) { volatile float big = 8388608.0f; float ax = x < 0 ? -x : x; if (!(ax < big)) return x; volatile float t = ax + big; float r = t - big; return x < 0 ? -r : r; }   /* 가장 가까운 짝수로(x87 은 중간값을 확장 정밀도로 들고 있으니 volatile 로 float 에 맞춘다) */
static inline float32x2_t vrndn_f32(float32x2_t a) { float32x2_t r; for (int i = 0; i < 2; i++) r[i] = vc_rintf(a[i]); return r; }
static inline float32x2_t vld1_f32(const float *p) { float32x2_t r; __builtin_memcpy(&r, p, sizeof r); return r; }
static inline void vst1_f32(float *p, float32x2_t v) { __builtin_memcpy(p, &v, sizeof v); }
static inline float32x2_t vset_lane_f32(float x, float32x2_t v, int i) { v[i] = x; return v; }

/* 64비트 원소(조각 하나 = 원소 하나)는 평범한 정수이므로 vld1·vst1·vdup 만 */
static inline int64x1_t vld1_s64(const int64_t *p) { int64x1_t r; __builtin_memcpy(&r, p, sizeof r); return r; }
static inline uint64x1_t vld1_u64(const uint64_t *p) { uint64x1_t r; __builtin_memcpy(&r, p, sizeof r); return r; }
static inline void vst1_s64(int64_t *p, int64x1_t v) { __builtin_memcpy(p, &v, sizeof v); }
static inline void vst1_u64(uint64_t *p, uint64x1_t v) { __builtin_memcpy(p, &v, sizeof v); }
#endif
