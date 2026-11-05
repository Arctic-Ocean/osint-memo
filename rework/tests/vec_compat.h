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

/* 바이트 표 조회(tbl): 인덱스가 표 크기 이상이면 0 */
typedef struct { uint8x16_t val[2]; } uint8x16x2_t;
static inline uint8x16_t vcombine_u8(uint8x8_t lo, uint8x8_t hi) { uint8x16_t r; __builtin_memcpy(&r, &lo, 8); __builtin_memcpy((char *)&r + 8, &hi, 8); return r; }
static inline uint8x8_t vqtbl1_u8(uint8x16_t t, uint8x8_t idx) { uint8x8_t r; for (int i = 0; i < 8; i++) r[i] = idx[i] < 16 ? t[idx[i]] : 0; return r; }
static inline uint8x8_t vqtbl2_u8(uint8x16x2_t t, uint8x8_t idx) { uint8x8_t r; for (int i = 0; i < 8; i++) r[i] = idx[i] < 16 ? t.val[0][idx[i]] : idx[i] < 32 ? t.val[1][idx[i] - 16] : 0; return r; }

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
static inline float32x2_t vrndm_f32(float32x2_t a) { float32x2_t r; for (int i = 0; i < 2; i++) r[i] = __builtin_floorf(a[i]); return r; }
static inline float32x2_t vrndp_f32(float32x2_t a) { float32x2_t r; for (int i = 0; i < 2; i++) r[i] = __builtin_ceilf(a[i]); return r; }
static inline float32x2_t vrnd_f32(float32x2_t a) { float32x2_t r; for (int i = 0; i < 2; i++) r[i] = __builtin_truncf(a[i]); return r; }
static inline float32x2_t vrndn_f32(float32x2_t a) { float32x2_t r; for (int i = 0; i < 2; i++) r[i] = vc_rintf(a[i]); return r; }
static inline float32x2_t vld1_f32(const float *p) { float32x2_t r; __builtin_memcpy(&r, p, sizeof r); return r; }
static inline void vst1_f32(float *p, float32x2_t v) { __builtin_memcpy(p, &v, sizeof v); }
static inline float32x2_t vset_lane_f32(float x, float32x2_t v, int i) { v[i] = x; return v; }

/* 64비트 원소(조각 하나 = 원소 하나)는 평범한 정수이므로 vld1·vst1·vdup 만 */
static inline int64x1_t vld1_s64(const int64_t *p) { int64x1_t r; __builtin_memcpy(&r, p, sizeof r); return r; }
static inline uint64x1_t vld1_u64(const uint64_t *p) { uint64x1_t r; __builtin_memcpy(&r, p, sizeof r); return r; }
static inline void vst1_s64(int64_t *p, int64x1_t v) { __builtin_memcpy(p, &v, sizeof v); }
static inline void vst1_u64(uint64_t *p, uint64x1_t v) { __builtin_memcpy(p, &v, sizeof v); }

/* 암호 명령 도우미(AES‑NI·pclmulqdq): 의사코드의 __aesenc(state, key) 같은 호출을 C 로 구현한다. 속도가 아니라 '의미가 맞는가'를 확인하는 용도 */
static const uint8_t vc_sbox[256] = {
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16
};
static const uint8_t vc_isbox[256] = {
    0x52, 0x09, 0x6a, 0xd5, 0x30, 0x36, 0xa5, 0x38, 0xbf, 0x40, 0xa3, 0x9e, 0x81, 0xf3, 0xd7, 0xfb,
    0x7c, 0xe3, 0x39, 0x82, 0x9b, 0x2f, 0xff, 0x87, 0x34, 0x8e, 0x43, 0x44, 0xc4, 0xde, 0xe9, 0xcb,
    0x54, 0x7b, 0x94, 0x32, 0xa6, 0xc2, 0x23, 0x3d, 0xee, 0x4c, 0x95, 0x0b, 0x42, 0xfa, 0xc3, 0x4e,
    0x08, 0x2e, 0xa1, 0x66, 0x28, 0xd9, 0x24, 0xb2, 0x76, 0x5b, 0xa2, 0x49, 0x6d, 0x8b, 0xd1, 0x25,
    0x72, 0xf8, 0xf6, 0x64, 0x86, 0x68, 0x98, 0x16, 0xd4, 0xa4, 0x5c, 0xcc, 0x5d, 0x65, 0xb6, 0x92,
    0x6c, 0x70, 0x48, 0x50, 0xfd, 0xed, 0xb9, 0xda, 0x5e, 0x15, 0x46, 0x57, 0xa7, 0x8d, 0x9d, 0x84,
    0x90, 0xd8, 0xab, 0x00, 0x8c, 0xbc, 0xd3, 0x0a, 0xf7, 0xe4, 0x58, 0x05, 0xb8, 0xb3, 0x45, 0x06,
    0xd0, 0x2c, 0x1e, 0x8f, 0xca, 0x3f, 0x0f, 0x02, 0xc1, 0xaf, 0xbd, 0x03, 0x01, 0x13, 0x8a, 0x6b,
    0x3a, 0x91, 0x11, 0x41, 0x4f, 0x67, 0xdc, 0xea, 0x97, 0xf2, 0xcf, 0xce, 0xf0, 0xb4, 0xe6, 0x73,
    0x96, 0xac, 0x74, 0x22, 0xe7, 0xad, 0x35, 0x85, 0xe2, 0xf9, 0x37, 0xe8, 0x1c, 0x75, 0xdf, 0x6e,
    0x47, 0xf1, 0x1a, 0x71, 0x1d, 0x29, 0xc5, 0x89, 0x6f, 0xb7, 0x62, 0x0e, 0xaa, 0x18, 0xbe, 0x1b,
    0xfc, 0x56, 0x3e, 0x4b, 0xc6, 0xd2, 0x79, 0x20, 0x9a, 0xdb, 0xc0, 0xfe, 0x78, 0xcd, 0x5a, 0xf4,
    0x1f, 0xdd, 0xa8, 0x33, 0x88, 0x07, 0xc7, 0x31, 0xb1, 0x12, 0x10, 0x59, 0x27, 0x80, 0xec, 0x5f,
    0x60, 0x51, 0x7f, 0xa9, 0x19, 0xb5, 0x4a, 0x0d, 0x2d, 0xe5, 0x7a, 0x9f, 0x93, 0xc9, 0x9c, 0xef,
    0xa0, 0xe0, 0x3b, 0x4d, 0xae, 0x2a, 0xf5, 0xb0, 0xc8, 0xeb, 0xbb, 0x3c, 0x83, 0x53, 0x99, 0x61,
    0x17, 0x2b, 0x04, 0x7e, 0xba, 0x77, 0xd6, 0x26, 0xe1, 0x69, 0x14, 0x63, 0x55, 0x21, 0x0c, 0x7d
};
static inline uint8_t vc_xt(uint8_t a) { return (uint8_t)((a << 1) ^ ((a & 0x80) ? 0x1b : 0)); }
static inline uint8_t vc_gm(uint8_t a, uint8_t b) { uint8_t r = 0; while (b) { if (b & 1) r ^= a; a = vc_xt(a); b >>= 1; } return r; }
static inline void vc_aes_shift(uint8_t *s, int inv) { uint8_t t[16]; for (int r = 0; r < 4; r++) for (int c = 0; c < 4; c++) t[r + 4 * c] = s[r + 4 * (inv ? (c - r + 4) % 4 : (c + r) % 4)]; __builtin_memcpy(s, t, 16); }
static inline void vc_aes_sub(uint8_t *s, int inv) { for (int i = 0; i < 16; i++) s[i] = inv ? vc_isbox[s[i]] : vc_sbox[s[i]]; }
static inline void vc_aes_mix(uint8_t *s, int inv) {
    for (int c = 0; c < 4; c++) {
        uint8_t a0 = s[4 * c], a1 = s[4 * c + 1], a2 = s[4 * c + 2], a3 = s[4 * c + 3];
        if (!inv) { s[4 * c] = vc_gm(a0, 2) ^ vc_gm(a1, 3) ^ a2 ^ a3; s[4 * c + 1] = a0 ^ vc_gm(a1, 2) ^ vc_gm(a2, 3) ^ a3; s[4 * c + 2] = a0 ^ a1 ^ vc_gm(a2, 2) ^ vc_gm(a3, 3); s[4 * c + 3] = vc_gm(a0, 3) ^ a1 ^ a2 ^ vc_gm(a3, 2); }
        else { s[4 * c] = vc_gm(a0, 14) ^ vc_gm(a1, 11) ^ vc_gm(a2, 13) ^ vc_gm(a3, 9); s[4 * c + 1] = vc_gm(a0, 9) ^ vc_gm(a1, 14) ^ vc_gm(a2, 11) ^ vc_gm(a3, 13); s[4 * c + 2] = vc_gm(a0, 13) ^ vc_gm(a1, 9) ^ vc_gm(a2, 14) ^ vc_gm(a3, 11); s[4 * c + 3] = vc_gm(a0, 11) ^ vc_gm(a1, 13) ^ vc_gm(a2, 9) ^ vc_gm(a3, 14); }
    }
}
static inline uint64x2_t vc_aes(uint64x2_t st, uint64x2_t key, int inv, int last) {
    uint8_t s[16], k[16]; __builtin_memcpy(s, &st, 16); __builtin_memcpy(k, &key, 16);
    vc_aes_shift(s, inv); vc_aes_sub(s, inv); if (!last && !inv) vc_aes_mix(s, 0); if (!last && inv) vc_aes_mix(s, 1);
    for (int i = 0; i < 16; i++) s[i] ^= k[i];
    uint64x2_t r; __builtin_memcpy(&r, s, 16); return r;
}
static inline uint64x2_t __aesenc(uint64x2_t a, uint64x2_t k) { return vc_aes(a, k, 0, 0); }
static inline uint64x2_t __aesenclast(uint64x2_t a, uint64x2_t k) { return vc_aes(a, k, 0, 1); }
static inline uint64x2_t __aesdec(uint64x2_t a, uint64x2_t k) { return vc_aes(a, k, 1, 0); }
static inline uint64x2_t __aesdeclast(uint64x2_t a, uint64x2_t k) { return vc_aes(a, k, 1, 1); }
static inline uint64x2_t __aesimc(uint64x2_t a) { uint8_t s[16]; __builtin_memcpy(s, &a, 16); vc_aes_mix(s, 1); uint64x2_t r; __builtin_memcpy(&r, s, 16); return r; }
static inline uint64x2_t __aeskeygenassist(uint64x2_t a, int imm) {
    uint32_t w[4]; __builtin_memcpy(w, &a, 16);
    uint32_t x1 = w[1], x3 = w[3], s1 = 0, s3 = 0;
    for (int i = 0; i < 4; i++) { s1 |= (uint32_t)vc_sbox[(x1 >> (8 * i)) & 0xff] << (8 * i); s3 |= (uint32_t)vc_sbox[(x3 >> (8 * i)) & 0xff] << (8 * i); }
    uint32_t o[4] = {s1, ((s1 >> 8) | (s1 << 24)) ^ (uint32_t)(imm & 0xff), s3, ((s3 >> 8) | (s3 << 24)) ^ (uint32_t)(imm & 0xff)};
    uint64x2_t r; __builtin_memcpy(&r, o, 16); return r;
}
static inline uint64x2_t __pclmulqdq(uint64x2_t a, uint64x2_t b, int imm) {
    uint64_t x = a[(imm & 1) ? 1 : 0], y = b[(imm & 0x10) ? 1 : 0], lo = 0, hi = 0;
    for (int i = 0; i < 64; i++) if ((y >> i) & 1) { lo ^= x << i; if (i) hi ^= x >> (64 - i); }
    uint64x2_t r = {lo, hi}; return r;
}
#endif
