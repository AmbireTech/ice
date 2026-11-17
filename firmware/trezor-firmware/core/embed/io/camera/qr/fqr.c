/*
 * Ambire boards, [ambire_camera]: the video QR decoder (fqr.h says what it
 * does and why). Module coordinates: (0, 0) is the code's top left corner,
 * module (i, j) covers [i, i+1) x [j, j+1). Pixel coordinates likewise:
 * pixel (i, j)'s centre is at (i + 0.5, j + 0.5).
 */

#include "fqr.h"

#include <math.h>
#include <string.h>

#define MAXD FQR_MAX_DIM
#define MAX_ALIGN (FQR_MAX_VERSION / 7 + 2)
#define MAX_PTS (3 + MAX_ALIGN * MAX_ALIGN)
#define MAX_CW (((16 * FQR_MAX_VERSION + 128) * FQR_MAX_VERSION + 64) / 8)
#define MAX_CAND 64
#define MAX_TRIPLES 4
#define PATCH_SIDE 60
#define SCORE_SIDE 25
#define BLOCK 8        // threshold blocks, pixels
#define SMALL_R 4      // the fine threshold's window radius, pixels
#define WARP_STEP 4    // residual lattice spacing, modules
#define WARP_N (MAXD / WARP_STEP + 3)
#define ENV_N (MAXD / 4 + 3)
#ifndef EQ_ROUNDS
#define EQ_ROUNDS 6    // equaliser rounds
#endif

#ifdef FQR_HOST_TUNE
#include <stdio.h>
#include <time.h>
int fqr_opt[16];
double fqr_prof[8];  // ms: binarize, finders, grid, sample, soften, equalise, decode
#define OPT(i) fqr_opt[i]
static double prof_now(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (double)t.tv_sec * 1e3 + (double)t.tv_nsec / 1e6;
}
#define PROF(i, call)                 \
  do {                                \
    double prof_t = prof_now();       \
    call;                             \
    fqr_prof[i] += prof_now() - prof_t; \
  } while (0)
#else
#define OPT(i) 0
#define PROF(i, call) call
#endif

typedef struct {
  float x, y;
} pt_t;

typedef struct {
  float x, y;    // centre, pixels
  float hs, vs;  // run of the whole pattern across and down, pixels
  int n;         // scan lines that hit it
  float q;       // rank
} cand_t;

struct fqr {
  int w, h, bpr, bw, bh;
  const uint8_t *img;
  int stride;
  // Frame-sized work memory (after the struct)
  uint8_t *bits;   // 1: dark, bpr bytes a row
  uint16_t *col;   // column sums of the fine window
  uint16_t *bsum;  // per block: sum, then reused
  uint8_t *bmin, *bmax, *bmean, *btol;

  uint8_t gf_exp[512], gf_log[256];

  cand_t cand[MAX_CAND];
  int ncand;

  // The grid in use
  float H[9];
  int dim;
  pt_t fin[3];  // finder centres A (top left), B (top right), C (bottom left)
  float timing;
  int aligns;
  float warp[WARP_N * WARP_N][2];
  int warp_n;  // 0: no residual lattice

  // Tracking: the last good grid's finder centres and their motion
  int track;  // frames since a grid was seen + 1 (0: none)
  pt_t tfin[3], tvel[3];
  int tdim;

  // Fusion
  int acc_n, acc_dim;
  int16_t acc[MAXD * MAXD];

  int func_version;
  uint8_t func[(MAXD * MAXD + 7) / 8];
  int8_t known[MAXD * MAXD];  // per module: 64 known light, -64 known dark, 0 data
  uint8_t gray[MAXD * MAXD];
  int8_t soft[MAXD * MAXD];
  int8_t soft2[MAXD * MAXD];
  int8_t eq[MAXD * MAXD];
  int have_soft;

  uint8_t patch[PATCH_SIDE * PATCH_SIDE];
  float score[SCORE_SIDE * SCORE_SIDE];
  int16_t toff[9 * 9 * 4];
  uint8_t twhite[9 * 9 * 4];

  float lsq[2 * MAX_PTS][9];
  uint8_t env_lo[ENV_N * ENV_N], env_hi[ENV_N * ENV_N];

  uint8_t cw[MAX_CW], rel[MAX_CW], data[MAX_CW];
  uint8_t blk[256], brel[256], bwork[256];
};

// ---------------------------------------------------------------- GF(256)

static void gf_init(fqr_t *s) {
  int x = 1;
  for (int i = 0; i < 255; i++) {
    s->gf_exp[i] = (uint8_t)x;
    s->gf_exp[i + 255] = (uint8_t)x;
    s->gf_log[x] = (uint8_t)i;
    x <<= 1;
    if (x & 0x100) {
      x ^= 0x11D;
    }
  }
  s->gf_exp[510] = s->gf_exp[0];
  s->gf_exp[511] = s->gf_exp[1];
  s->gf_log[0] = 0;
}

static inline uint8_t gf_mul(const fqr_t *s, uint8_t a, uint8_t b) {
  return (a && b) ? s->gf_exp[s->gf_log[a] + s->gf_log[b]] : 0;
}

static inline uint8_t gf_div(const fqr_t *s, uint8_t a, uint8_t b) {
  return a ? s->gf_exp[s->gf_log[a] + 255 - s->gf_log[b]] : 0;
}

// Reed-Solomon over GF(256)/0x11D, generator roots alpha^0..alpha^(nsym-1):
// corrects `c` (n codewords, c[0] first) in place given `neras` positions
// known to be unreliable. Returns the codewords changed, or -1 when the
// word is beyond correction. Errors e and erasures f: 2e + f <= nsym; with
// erasures two check symbols are held back (2e + f <= nsym - 2), since a
// wrong word then passes too easily.
static int rs_decode(const fqr_t *s, uint8_t *c, int n, int nsym, const uint8_t *eras, int neras) {
  uint8_t syn[32], lam[33], b[33], t[33], omega[32];
  if (nsym > 30 || neras > nsym) {
    return -1;
  }
  int nonzero = 0;
  for (int j = 0; j < nsym; j++) {
    uint8_t v = 0;
    for (int i = 0; i < n; i++) {
      v = (uint8_t)(gf_mul(s, v, s->gf_exp[j]) ^ c[i]);
    }
    syn[j] = v;
    nonzero |= v;
  }
  if (!nonzero) {
    return 0;
  }
  memset(lam, 0, sizeof(lam));
  lam[0] = 1;
  for (int k = 0; k < neras; k++) {
    uint8_t x = s->gf_exp[(n - 1 - eras[k]) % 255];
    for (int i = k + 1; i > 0; i--) {
      lam[i] ^= gf_mul(s, lam[i - 1], x);
    }
  }
  memcpy(b, lam, sizeof(b));
  int el = neras;
  for (int r = neras + 1; r <= nsym; r++) {
    uint8_t d = 0;
    for (int i = 0; i < r; i++) {
      d ^= gf_mul(s, lam[i], syn[r - i - 1]);
    }
    if (d == 0) {
      memmove(b + 1, b, 32);
      b[0] = 0;
    } else {
      t[0] = lam[0];
      for (int i = 0; i < 32; i++) {
        t[i + 1] = (uint8_t)(lam[i + 1] ^ gf_mul(s, d, b[i]));
      }
      if (2 * el <= r + neras - 1) {
        el = r + neras - el;
        for (int i = 0; i <= 32; i++) {
          b[i] = gf_div(s, lam[i], d);
        }
      } else {
        memmove(b + 1, b, 32);
        b[0] = 0;
      }
      memcpy(lam, t, sizeof(lam));
    }
  }
  int deg = 0;
  for (int i = 0; i <= nsym; i++) {
    if (lam[i]) {
      deg = i;
    }
  }
  int limit = neras > 0 ? nsym - 2 : nsym;
  if (deg == 0 || 2 * deg - neras > limit) {
    return -1;
  }
  for (int i = 0; i < nsym; i++) {
    uint8_t v = 0;
    for (int j = 0; j <= i && j <= deg; j++) {
      v ^= gf_mul(s, syn[i - j], lam[j]);
    }
    omega[i] = v;
  }
  // Chien search over the n positions; the corrections are applied only if
  // every root is a position
  int found = 0;
  uint8_t pos[32], mag[32];
  for (int i = 0; i < n; i++) {
    int xl = (n - 1 - i) % 255;         // log of the locator X
    int il = (255 - xl) % 255;          // log of 1/X
    uint8_t v = 0;
    for (int j = 0; j <= deg; j++) {
      if (lam[j]) {
        v ^= s->gf_exp[(s->gf_log[lam[j]] + j * il) % 255];
      }
    }
    if (v != 0) {
      continue;
    }
    uint8_t num = 0, den = 0;
    for (int j = 0; j < nsym; j++) {
      if (omega[j]) {
        num ^= s->gf_exp[(s->gf_log[omega[j]] + j * il) % 255];
      }
    }
    for (int j = 1; j <= deg; j += 2) {
      if (lam[j]) {
        den ^= s->gf_exp[(s->gf_log[lam[j]] + (j - 1) * il) % 255];
      }
    }
    if (den == 0 || found >= 32) {
      return -1;
    }
    pos[found] = (uint8_t)i;
    mag[found] = gf_mul(s, gf_div(s, num, den), s->gf_exp[xl]);
    found++;
  }
  if (found != deg) {
    return -1;
  }
  int changed = 0;
  for (int k = 0; k < found; k++) {
    c[pos[k]] ^= mag[k];
    changed += mag[k] != 0;
  }
  // The corrected word must be a codeword
  for (int j = 0; j < nsym; j++) {
    uint8_t v = 0;
    for (int i = 0; i < n; i++) {
      v = (uint8_t)(gf_mul(s, v, s->gf_exp[j]) ^ c[i]);
    }
    if (v) {
      for (int k = 0; k < found; k++) {
        c[pos[k]] ^= mag[k];
      }
      return -1;
    }
  }
  return changed;
}

// ------------------------------------------------------- QR code structure

// Check codewords per block and blocks, by level (L, M, Q, H) and version
static const uint8_t ECC_PER_BLOCK[4][40] = {
    {7,  10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28,
     28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30},
    {10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
     26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28},
    {13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30,
     28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30},
    {17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28,
     30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30}};
static const uint8_t ECC_BLOCKS[4][40] = {
    {1, 1, 1, 1, 1, 2, 2, 2, 2, 4,  4,  4,  4,  4,  6,  6,  6,  6,  7,  8,
     8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25},
    {1,  1,  1,  2,  2,  4,  4,  4,  5,  5,  5,  8,  9,  9,  10, 10, 11, 13, 14, 16,
     17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49},
    {1,  1,  2,  2,  4,  4,  6,  6,  8,  8,  8,  10, 12, 16, 12, 17, 16, 18, 21, 20,
     23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68},
    {1,  1,  2,  4,  4,  4,  5,  6,  8,  8,  11, 11, 16, 16, 18, 16, 19, 21, 25, 25,
     25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81}};

// Alignment pattern centres along one axis (module index); returns how many
static int align_positions(int version, int pos[MAX_ALIGN]) {
  if (version < 2) {
    return 0;
  }
  int n = version / 7 + 2;
  int step = version == 32 ? 26 : (version * 4 + n * 2 + 1) / (n * 2 - 2) * 2;
  int p = version * 4 + 10;
  for (int i = n - 1; i >= 1; i--, p -= step) {
    pos[i] = p;
  }
  pos[0] = 6;
  return n;
}

static int total_codewords(int version) {
  int r = (16 * version + 128) * version + 64;
  if (version >= 2) {
    int n = version / 7 + 2;
    r -= (25 * n - 10) * n - 55;
    if (version >= 7) {
      r -= 36;
    }
  }
  return r / 8;
}

// A module whose value the standard fixes: 1 dark, 0 light, -1 data (or
// format and version bits)
static int known_module(int version, int x, int y) {
  int D = 17 + 4 * version;
  for (int f = 0; f < 3; f++) {
    int ox = f == 1 ? D - 7 : 0, oy = f == 2 ? D - 7 : 0;
    int i = x - ox, j = y - oy;
    if (i >= -1 && i <= 7 && j >= -1 && j <= 7) {
      if (i < 0 || j < 0 || i > 6 || j > 6) {
        return 0;
      }
      int r = i < j ? i : j, m = 6 - (i > j ? i : j);
      int ring = r < m ? r : m;
      return ring != 1;
    }
  }
  if (x == 6 || y == 6) {
    return ((x + y) & 1) == 0;
  }
  int pos[MAX_ALIGN];
  int n = align_positions(version, pos);
  for (int a = 0; a < n; a++) {
    for (int b = 0; b < n; b++) {
      if ((a == 0 && b == 0) || (a == 0 && b == n - 1) || (a == n - 1 && b == 0)) {
        continue;
      }
      int i = x - pos[a], j = y - pos[b];
      if (i >= -2 && i <= 2 && j >= -2 && j <= 2) {
        int ring = (i < 0 ? -i : i) > (j < 0 ? -j : j) ? (i < 0 ? -i : i) : (j < 0 ? -j : j);
        return ring != 1;
      }
    }
  }
  return -1;
}

static inline int is_func(const fqr_t *s, int D, int x, int y) {
  int i = y * D + x;
  return (s->func[i >> 3] >> (i & 7)) & 1;
}

static void build_func(fqr_t *s, int version) {
  if (s->func_version == version) {
    return;
  }
  int D = 17 + 4 * version;
  memset(s->func, 0, sizeof(s->func));
  int pos[MAX_ALIGN];
  int n = align_positions(version, pos);
  for (int y = 0; y < D; y++) {
    for (int x = 0; x < D; x++) {
      int f = (x < 9 && y < 9) || (x >= D - 8 && y < 9) || (x < 9 && y >= D - 8) || x == 6 || y == 6;
      if (version >= 7) {
        f |= (x < 6 && y >= D - 11 && y < D - 8) || (y < 6 && x >= D - 11 && x < D - 8);
      }
      for (int a = 0; a < n && !f; a++) {
        for (int b = 0; b < n && !f; b++) {
          if ((a == 0 && b == 0) || (a == 0 && b == n - 1) || (a == n - 1 && b == 0)) {
            continue;
          }
          f = x >= pos[a] - 2 && x <= pos[a] + 2 && y >= pos[b] - 2 && y <= pos[b] + 2;
        }
      }
      if (f) {
        int i = y * D + x;
        s->func[i >> 3] |= (uint8_t)(1 << (i & 7));
      }
    }
  }
  for (int y = 0; y < D; y++) {
    for (int x = 0; x < D; x++) {
      int k = known_module(version, x, y);
      s->known[y * D + x] = (int8_t)(k < 0 ? 0 : k ? -64 : 64);
    }
  }
  s->func_version = version;
}

static inline int mask_bit(int mask, int i, int j) {
  switch (mask) {
    case 0:
      return (i + j) % 2 == 0;
    case 1:
      return i % 2 == 0;
    case 2:
      return j % 3 == 0;
    case 3:
      return (i + j) % 3 == 0;
    case 4:
      return (i / 2 + j / 3) % 2 == 0;
    case 5:
      return (i * j) % 2 + (i * j) % 3 == 0;
    case 6:
      return ((i * j) % 2 + (i * j) % 3) % 2 == 0;
    default:
      return ((i * j) % 3 + (i + j) % 2) % 2 == 0;
  }
}

// ------------------------------------------------------------ the payload

typedef struct {
  const uint8_t *d;
  int nbits, pos;
} bits_t;

static int take(bits_t *b, int n) {
  int v = 0;
  if (b->pos + n > b->nbits) {
    b->pos = b->nbits + 1;
    return -1;
  }
  for (int i = 0; i < n; i++, b->pos++) {
    v = (v << 1) | ((b->d[b->pos >> 3] >> (7 - (b->pos & 7))) & 1);
  }
  return v;
}

// The data codewords' segments to bytes. Returns the payload length (the
// whole of it, even past `max`), or -1 if the stream is not a valid one.
static int parse_payload(const uint8_t *data, int ndata, int version, uint8_t *out, size_t max) {
  static const char ALNUM[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:";
  bits_t b = {data, ndata * 8, 0};
  int len = 0;
  int vr = version < 10 ? 0 : version < 27 ? 1 : 2;
#define PUT(c)                      \
  do {                              \
    if ((size_t)len < max) {        \
      out[len] = (uint8_t)(c);      \
    }                               \
    len++;                          \
  } while (0)
  while (b.nbits - b.pos >= 4) {
    int mode = take(&b, 4);
    if (mode == 0) {
      break;
    }
    if (mode == 7) {  // ECI: the designator is skipped
      int e = take(&b, 8);
      if (e < 0) {
        return -1;
      }
      if ((e & 0xC0) == 0x80) {
        take(&b, 8);
      } else if ((e & 0xE0) == 0xC0) {
        take(&b, 16);
      }
      continue;
    }
    static const uint8_t count_bits[4][3] = {{10, 12, 14}, {9, 11, 13}, {8, 16, 16}, {8, 10, 12}};
    int kind = mode == 1 ? 0 : mode == 2 ? 1 : mode == 4 ? 2 : mode == 8 ? 3 : -1;
    if (kind < 0) {
      return -1;
    }
    int count = take(&b, count_bits[kind][vr]);
    if (count < 0) {
      return -1;
    }
    if (kind == 0) {
      while (count >= 3) {
        int v = take(&b, 10);
        if (v < 0 || v > 999) {
          return -1;
        }
        PUT('0' + v / 100);
        PUT('0' + v / 10 % 10);
        PUT('0' + v % 10);
        count -= 3;
      }
      if (count == 2) {
        int v = take(&b, 7);
        if (v < 0 || v > 99) {
          return -1;
        }
        PUT('0' + v / 10);
        PUT('0' + v % 10);
      } else if (count == 1) {
        int v = take(&b, 4);
        if (v < 0 || v > 9) {
          return -1;
        }
        PUT('0' + v);
      }
    } else if (kind == 1) {
      while (count >= 2) {
        int v = take(&b, 11);
        if (v < 0 || v >= 45 * 45) {
          return -1;
        }
        PUT(ALNUM[v / 45]);
        PUT(ALNUM[v % 45]);
        count -= 2;
      }
      if (count == 1) {
        int v = take(&b, 6);
        if (v < 0 || v >= 45) {
          return -1;
        }
        PUT(ALNUM[v]);
      }
    } else if (kind == 2) {
      while (count-- > 0) {
        int v = take(&b, 8);
        if (v < 0) {
          return -1;
        }
        PUT(v);
      }
    } else {
      while (count-- > 0) {  // Kanji: Shift-JIS, two bytes
        int v = take(&b, 13);
        if (v < 0) {
          return -1;
        }
        int sj = ((v / 0xC0) << 8) | (v % 0xC0);
        sj += sj < 0x1F00 ? 0x8140 : 0xC140;
        PUT(sj >> 8);
        PUT(sj & 0xFF);
      }
    }
  }
#undef PUT
  return len > 0 ? len : -1;
}

// ------------------------------------------------- soft bits to a payload

// Format codeword for the 5 data bits `d` (level, mask), masked as printed
static int format_word(int d) {
  int r = d << 10;
  for (int i = 14; i >= 10; i--) {
    if (r & (1 << i)) {
      r ^= 0x537 << (i - 10);
    }
  }
  return ((d << 10) | r) ^ 0x5412;
}

// Decodes the soft bits `z` (D x D, > 0 white) of a version's grid
static bool decode_soft(fqr_t *s, const int8_t *z, int version, uint8_t *payload, size_t max,
                        fqr_result_t *res) {
  int D = 17 + 4 * version;
  // Format: both copies against all 32 words, the best soft match
  static const uint8_t fx[15] = {8, 8, 8, 8, 8, 8, 8, 8, 7, 5, 4, 3, 2, 1, 0};
  static const uint8_t fy[15] = {0, 1, 2, 3, 4, 5, 7, 8, 8, 8, 8, 8, 8, 8, 8};
  int fz[2][15];  // soft value of bit i (bit 14 first in the word's top)
  int total = 0;
  for (int i = 0; i < 15; i++) {
    fz[0][i] = z[fy[i] * D + fx[i]];
    // second copy: bits 14..8 down the left of the bottom finder, 7..0 right
    // of the top right one
    int k = 14 - i;  // 0..14 in reading order
    fz[1][i] = k < 7 ? z[(D - 1 - k) * D + 8] : z[8 * D + (D - 8 + (k - 7))];
    total += (fz[0][i] < 0 ? -fz[0][i] : fz[0][i]) + (fz[1][i] < 0 ? -fz[1][i] : fz[1][i]);
  }
  int best = -1, best_m = -1000000;
  for (int d = 0; d < 32; d++) {
    int wd = format_word(d), m = 0;
    for (int i = 0; i < 15; i++) {
      int dark = (wd >> i) & 1;
      m += dark ? -(fz[0][i] + fz[1][i]) : fz[0][i] + fz[1][i];
    }
    if (m > best_m) {
      best_m = m;
      best = d;
    }
  }
  if (best < 0 || best_m * 2 < total) {
    return false;
  }
  static const uint8_t level_of[4] = {1, 0, 3, 2};  // format bits to L M Q H order
  int level = level_of[best >> 3], mask = best & 7;
  res->stage = FQR_STAGE_FORMAT;
  res->ecc_level = level;
  res->mask = mask;

  // The codewords, in placement order, each with its least certain bit
  build_func(s, version);
  int ncw = total_codewords(version);
  int n = 0, nbit = 0, v = 0, rmin = 255;
  int up = 1;
  for (int x = D - 1; x > 0; x -= 2, up = !up) {
    if (x == 6) {
      x = 5;
    }
    for (int k = 0; k < D; k++) {
      int y = up ? D - 1 - k : k;
      for (int j = 0; j < 2; j++) {
        int xx = x - j;
        if (is_func(s, D, xx, y) || n >= ncw) {
          continue;
        }
        int zz = z[y * D + xx];
        int dark = (zz < 0) ^ mask_bit(mask, y, xx);
        int a = zz < 0 ? -zz : zz;
        v = (v << 1) | dark;
        rmin = a < rmin ? a : rmin;
        if (++nbit == 8) {
          s->cw[n] = (uint8_t)v;
          s->rel[n] = (uint8_t)rmin;
          n++;
          nbit = 0;
          v = 0;
          rmin = 255;
        }
      }
    }
  }
  if (n != ncw) {
    return false;
  }

  // Blocks: de-interleave, correct, collect the data
  int nb = ECC_BLOCKS[level][version - 1], ecl = ECC_PER_BLOCK[level][version - 1];
  int ndata = ncw - nb * ecl, shortlen = ndata / nb, nlong = ndata % nb;
  int out = 0, errors = 0, erased = 0;
  for (int b = 0; b < nb; b++) {
    int dlen = shortlen + (b >= nb - nlong);
    int len = dlen + ecl;
    for (int i = 0; i < dlen; i++) {
      // data codeword i of block b: i full rounds, then the long blocks' extra
      int idx = i < shortlen ? i * nb + b : shortlen * nb + (b - (nb - nlong));
      s->blk[i] = s->cw[idx];
      s->brel[i] = s->rel[idx];
    }
    for (int i = 0; i < ecl; i++) {
      int idx = ndata + i * nb + b;
      s->blk[dlen + i] = s->cw[idx];
      s->brel[dlen + i] = s->rel[idx];
    }
    // Errors only first; then the least certain codewords erased, 4 more
    // at a time
    int fixed = -1, used = 0;
    uint8_t eras[32];
    for (int ne = 0; ne <= ecl - 4 && fixed < 0; ne += 4) {
      memcpy(s->bwork, s->blk, (size_t)len);
      for (int k = used; k < ne; k++) {
        int bi = -1, bv = 256;
        for (int i = 0; i < len; i++) {
          if (s->brel[i] < bv) {
            bv = s->brel[i];
            bi = i;
          }
        }
        eras[k] = (uint8_t)bi;
        s->brel[bi] = 255;
      }
      used = ne;
      fixed = rs_decode(s, s->bwork, len, ecl, eras, ne);
      if (fixed >= 0) {
        erased += ne;
      }
      if (OPT(0)) {
        break;  // tuning: no erasure passes
      }
    }
    if (fixed < 0) {
      return false;
    }
    errors += fixed;
    memcpy(s->data + out, s->bwork, (size_t)dlen);
    out += dlen;
  }
  int plen = parse_payload(s->data, ndata, version, payload, max);
  if (plen < 0) {
    return false;
  }
  res->stage = FQR_STAGE_DECODED;
  res->decoded = 1;
  res->payload_len = plen;
  res->rs_errors = errors;
  res->rs_erasures = erased;
  return true;
}

// ----------------------------------------------------------- finder scan

#define BIT(s, x, y) (((s)->bits[(y) * (s)->bpr + ((x) >> 3)] >> ((x) & 7)) & 1)

// The frame to a bitmap. A pixel is dark when it is below the mean of its
// 9 x 9 surroundings by more than a tolerance (fine detail the blur has
// flattened still shows against its own neighbourhood); where it is within
// the tolerance (a flat area), when it is below the mean of the 5 x 5 blocks
// around it.
static void binarize(fqr_t *s) {
  int w = s->w, h = s->h, bw = s->bw, bh = s->bh;
  const uint8_t *img = s->img;
  for (int by = 0; by < bh; by++) {
    for (int bx = 0; bx < bw; bx++) {
      int x1 = bx * BLOCK + BLOCK > w ? w : bx * BLOCK + BLOCK;
      int y1 = by * BLOCK + BLOCK > h ? h : by * BLOCK + BLOCK;
      // every other pixel of every other row: a quarter of the block
      unsigned sum = 0, mn = 255, mx = 0, cnt = 0;
      for (int y = by * BLOCK; y < y1; y += 2) {
        const uint8_t *row = img + (size_t)y * (size_t)s->stride;
        for (int x = bx * BLOCK; x < x1; x += 2) {
          unsigned v = row[x];
          sum += v;
          mn = v < mn ? v : mn;
          mx = v > mx ? v : mx;
          cnt++;
        }
      }
      int i = by * bw + bx;
      s->bsum[i] = (uint16_t)(sum / cnt);
      s->bmin[i] = (uint8_t)mn;
      s->bmax[i] = (uint8_t)mx;
    }
  }
  for (int by = 0; by < bh; by++) {
    for (int bx = 0; bx < bw; bx++) {
      unsigned sum = 0, cnt = 0, mn = 255, mx = 0;
      for (int dy = -2; dy <= 2; dy++) {
        for (int dx = -2; dx <= 2; dx++) {
          int xx = bx + dx, yy = by + dy;
          if (xx < 0 || yy < 0 || xx >= bw || yy >= bh) {
            continue;
          }
          int i = yy * bw + xx;
          sum += s->bsum[i];
          cnt++;
          if (dx >= -1 && dx <= 1 && dy >= -1 && dy <= 1) {
            mn = s->bmin[i] < mn ? s->bmin[i] : mn;
            mx = s->bmax[i] > mx ? s->bmax[i] : mx;
          }
        }
      }
      int i = by * bw + bx;
      s->bmean[i] = (uint8_t)(sum / cnt);
      unsigned tol = (mx - mn) / 12;
      s->btol[i] = (uint8_t)(tol < 3 ? 3 : tol);
    }
  }

  memset(s->col, 0, (size_t)w * sizeof(uint16_t));
  int rows = 0;
  for (int y = 0; y <= SMALL_R && y < h; y++) {
    const uint8_t *row = img + (size_t)y * (size_t)s->stride;
    for (int x = 0; x < w; x++) {
      s->col[x] = (uint16_t)(s->col[x] + row[x]);
    }
    rows++;
  }
  for (int y = 0; y < h; y++) {
    const uint8_t *row = img + (size_t)y * (size_t)s->stride;
    uint8_t *out = s->bits + (size_t)y * (size_t)s->bpr;
    const uint8_t *bm = s->bmean + (y / BLOCK) * bw, *bt = s->btol + (y / BLOCK) * bw;
    int sum = 0, cols = 0;
    for (int x = 0; x <= SMALL_R && x < w; x++) {
      sum += s->col[x];
      cols++;
    }
    for (int x = 0; x < w;) {
      // a block of 8 pixels: one threshold pair, one byte of the bitmap
      int mean = bm[x / BLOCK], tolv = bt[x / BLOCK];
      int end = x + BLOCK > w ? w : x + BLOCK;
      unsigned byte = 0;
      if (x - SMALL_R >= 0 && end + SMALL_R < w) {
        int cnt = rows * (2 * SMALL_R + 1), tol = tolv * cnt;
        const uint16_t *cp = s->col + x;
        for (int i = 0; i < BLOCK; i++) {
          int v = row[x + i], d = v * cnt - sum;
          byte |= (unsigned)(d < -tol || (d <= tol && v < mean)) << i;
          sum += cp[i + SMALL_R + 1] - cp[i - SMALL_R];
        }
        x = end;
      } else {
        for (int i = 0; x < end; x++, i++) {
          int cnt = rows * cols, tol = tolv * cnt;
          int v = row[x], d = v * cnt - sum;
          byte |= (unsigned)(d < -tol || (d <= tol && v < mean)) << i;
          if (x + SMALL_R + 1 < w) {
            sum += s->col[x + SMALL_R + 1];
            cols++;
          }
          if (x - SMALL_R >= 0) {
            sum -= s->col[x - SMALL_R];
            cols--;
          }
        }
      }
      *out++ = (uint8_t)byte;
    }
    const uint8_t *add = y + SMALL_R + 1 < h ? img + (size_t)(y + SMALL_R + 1) * (size_t)s->stride : NULL;
    const uint8_t *sub = y - SMALL_R >= 0 ? img + (size_t)(y - SMALL_R) * (size_t)s->stride : NULL;
    if (add != NULL && sub != NULL) {
      for (int x = 0; x < w; x++) {
        s->col[x] = (uint16_t)(s->col[x] + add[x] - sub[x]);
      }
    } else if (add != NULL) {
      for (int x = 0; x < w; x++) {
        s->col[x] = (uint16_t)(s->col[x] + add[x]);
      }
      rows++;
    } else if (sub != NULL) {
      for (int x = 0; x < w; x++) {
        s->col[x] = (uint16_t)(s->col[x] - sub[x]);
      }
      rows--;
    }
  }
}

// Five runs dark-light-dark-light-dark as 1:1:3:1:1, loosely: at 3 px a
// module the blur moves every edge
static bool ratio_ok(const int c[5]) {
  int total = c[0] + c[1] + c[2] + c[3] + c[4];
  if (total < 7) {
    return false;
  }
  float m = (float)total / 7.0f;
  float tol = m * 0.8f + 0.5f;
  for (int i = 0; i < 5; i++) {
    float want = i == 2 ? 3.0f * m : m;
    float t = i == 2 ? 2.0f * tol : tol;
    if (fabsf((float)c[i] - want) > t) {
      return false;
    }
  }
  return true;
}

// The five runs through (x, y) along (dx, dy), which is dark; false when
// they run off the frame or past `maxrun`. `centre` gets the middle of the
// centre run along the axis.
static bool cross_runs(const fqr_t *s, int x, int y, int dx, int maxrun, int c[5],
                       float *centre) {
  int lim = dx ? s->w : s->h;
  int p0 = dx ? x : y;
  int p;
  memset(c, 0, 5 * sizeof(int));
#define AT(p) (dx ? BIT(s, (p), y) : BIT(s, x, (p)))
  p = p0;
  while (p >= 0 && AT(p)) {
    c[2]++;
    p--;
  }
  int lo = p + 1;
  while (p >= 0 && !AT(p) && c[1] <= maxrun) {
    c[1]++;
    p--;
  }
  while (p >= 0 && AT(p) && c[0] <= maxrun) {
    c[0]++;
    p--;
  }
  if (c[1] == 0 || c[0] == 0 || c[1] > maxrun || c[0] > maxrun) {
    return false;
  }
  p = p0 + 1;
  while (p < lim && AT(p)) {
    c[2]++;
    p++;
  }
  int hi = p;
  while (p < lim && !AT(p) && c[3] <= maxrun) {
    c[3]++;
    p++;
  }
  while (p < lim && AT(p) && c[4] <= maxrun) {
    c[4]++;
    p++;
  }
#undef AT
  if (c[3] == 0 || c[4] == 0 || c[3] > maxrun || c[4] > maxrun || c[2] > 3 * maxrun) {
    return false;
  }
  *centre = (float)(lo + hi) * 0.5f;
  return true;
}

static void add_candidate(fqr_t *s, float x, float y, float hs, float vs) {
  for (int i = 0; i < s->ncand; i++) {
    cand_t *c = &s->cand[i];
    float dx = c->x - x, dy = c->y - y, r = (c->hs + c->vs) * 0.2f;
    if (dx * dx + dy * dy < r * r) {
      float n = (float)c->n;
      c->x = (c->x * n + x) / (n + 1);
      c->y = (c->y * n + y) / (n + 1);
      c->hs = (c->hs * n + hs) / (n + 1);
      c->vs = (c->vs * n + vs) / (n + 1);
      c->n++;
      return;
    }
  }
  if (s->ncand == MAX_CAND) {
    // Full: drop the ones the scan has passed that few rows hit
    int k = 0;
    for (int i = 0; i < s->ncand; i++) {
      const cand_t *c = &s->cand[i];
      if (c->n >= 3 || c->y + c->vs * 0.5f >= y) {
        s->cand[k++] = *c;
      }
    }
    s->ncand = k;
  }
  if (s->ncand < MAX_CAND) {
    cand_t *c = &s->cand[s->ncand++];
    c->x = x;
    c->y = y;
    c->hs = hs;
    c->vs = vs;
    c->n = 1;
  }
}

static void row_hit(fqr_t *s, int y, int xend, const int c[5]) {
  int total = c[0] + c[1] + c[2] + c[3] + c[4];
  if (total < 12) {
    return;  // under 2 px a module: nothing to read, and noise looks like it
  }
  int cx = xend - c[4] - c[3] - c[2] / 2;
  if (cx < 0 || cx >= s->w || !BIT(s, cx, y)) {
    return;
  }
  int v[5], h2[5];
  float cy, cx2;
  if (!cross_runs(s, cx, y, 0, total, v, &cy) || !ratio_ok(v)) {
    return;
  }
  int vt = v[0] + v[1] + v[2] + v[3] + v[4];
  if (vt * 5 < total * 2 || total * 5 < vt * 2) {
    return;
  }
  int iy = (int)cy;
  if (iy < 0 || iy >= s->h || !BIT(s, cx, iy)) {
    return;
  }
  if (!cross_runs(s, cx, iy, 1, total, h2, &cx2) || !ratio_ok(h2)) {
    return;
  }
  add_candidate(s, cx2, cy, (float)(h2[0] + h2[1] + h2[2] + h2[3] + h2[4]), (float)vt);
}

static void find_finders(fqr_t *s) {
  s->ncand = 0;
  uint16_t *run = s->col;  // the row's runs (the column sums are done with)
  for (int y = 0; y < s->h; y++) {
    // The row as runs, whole bytes of one colour at a time
    const uint8_t *row = s->bits + (size_t)y * (size_t)s->bpr;
    int n = 0, len = 0, colour = row[0] & 1, first = colour;
    for (int x = 0; x < s->w;) {
      unsigned byte = row[x >> 3];
      if (s->w - x >= 8 && byte == (colour ? 0xFFu : 0u)) {
        len += 8;
        x += 8;
        continue;
      }
      int end = x + 8 > s->w ? s->w : x + 8;
      for (; x < end; x++, byte >>= 1) {
        if ((int)(byte & 1) == colour) {
          len++;
        } else {
          run[n++] = (uint16_t)len;
          len = 1;
          colour = !colour;
        }
      }
    }
    run[n++] = (uint16_t)len;
    // Five runs starting at a dark one
    int x = first ? 0 : run[0];
    for (int i = first ? 0 : 1; i + 4 < n; x += run[i] + run[i + 1], i += 2) {
      int c[5] = {run[i], run[i + 1], run[i + 2], run[i + 3], run[i + 4]};
      if (c[2] >= c[0] && c[2] >= c[4] && ratio_ok(c)) {
        row_hit(s, y, x + c[0] + c[1] + c[2] + c[3] + c[4], c);
      }
    }
  }
  // Best first: a finder is hit by the rows through its centre, 3 modules
  // of them; rank by the share of those rows that hit
  for (int i = 0; i < s->ncand; i++) {
    cand_t *c = &s->cand[i];
    float want = c->vs * (3.0f / 7.0f);
    c->q = (float)c->n / (want < 2 ? 2 : want);
  }
  for (int i = 1; i < s->ncand; i++) {
    cand_t t = s->cand[i];
    int j = i;
    for (; j > 0 && s->cand[j - 1].q < t.q; j--) {
      s->cand[j] = s->cand[j - 1];
    }
    s->cand[j] = t;
  }
#ifdef FQR_HOST_TUNE
  if (OPT(15)) {
    for (int i = 0; i < s->ncand; i++) {
      printf("cand %d: %.1f %.1f hs %.1f vs %.1f n %d q %.2f\n", i, (double)s->cand[i].x, (double)s->cand[i].y,
             (double)s->cand[i].hs, (double)s->cand[i].vs, s->cand[i].n, (double)s->cand[i].q);
    }
  }
#endif
}

// ---------------------------------------------------------- grey geometry

static inline float sample(const fqr_t *s, float x, float y) {
  x -= 0.5f;
  y -= 0.5f;
  float xm = (float)(s->w - 1), ym = (float)(s->h - 1);
  x = x < 0 ? 0 : x > xm ? xm : x;
  y = y < 0 ? 0 : y > ym ? ym : y;
  int ix = (int)x, iy = (int)y;
  ix = ix > s->w - 2 ? s->w - 2 : ix;
  iy = iy > s->h - 2 ? s->h - 2 : iy;
  float fx = x - (float)ix, fy = y - (float)iy;
  const uint8_t *p = s->img + (size_t)iy * (size_t)s->stride + ix;
  float a = (float)p[0] + ((float)p[1] - (float)p[0]) * fx;
  float b = (float)p[s->stride] + ((float)p[s->stride + 1] - (float)p[s->stride]) * fx;
  return a + (b - a) * fy;
}

static inline pt_t hmap(const float *H, float x, float y) {
  float w = 1.0f / (H[6] * x + H[7] * y + H[8]);
  pt_t p = {(H[0] * x + H[1] * y + H[2]) * w, (H[3] * x + H[4] * y + H[5]) * w};
  return p;
}

// Module coordinates to pixels: the homography plus the residual lattice
static pt_t grid_point(const fqr_t *s, float x, float y) {
  pt_t p = hmap(s->H, x, y);
  if (s->warp_n > 0) {
    float fx = x / WARP_STEP, fy = y / WARP_STEP;
    float lim = (float)(s->warp_n - 1) - 0.001f;
    fx = fx < 0 ? 0 : fx > lim ? lim : fx;
    fy = fy < 0 ? 0 : fy > lim ? lim : fy;
    int ix = (int)fx, iy = (int)fy;
    fx -= (float)ix;
    fy -= (float)iy;
    const float(*wp)[2] = s->warp + iy * s->warp_n + ix;
    for (int k = 0; k < 2; k++) {
      float a = wp[0][k] + (wp[1][k] - wp[0][k]) * fx;
      float b = wp[s->warp_n][k] + (wp[s->warp_n + 1][k] - wp[s->warp_n][k]) * fx;
      float v = a + (b - a) * fy;
      if (k == 0) {
        p.x += v;
      } else {
        p.y += v;
      }
    }
  }
  return p;
}

// The homography through the finder centres A, B, C for a grid of D modules
// whose scale changes along the top by pl and down the left by ql (each the
// far end's perspective term times the span L = D - 7)
static void h_from_finders(float *H, const pt_t f[3], int D, float pl, float ql) {
  float L = (float)(D - 7), p = pl / L, q = ql / L;
  float ux = (f[1].x * (1 + pl) - f[0].x) / L, uy = (f[1].y * (1 + pl) - f[0].y) / L;
  float vx = (f[2].x * (1 + ql) - f[0].x) / L, vy = (f[2].y * (1 + ql) - f[0].y) / L;
  H[0] = ux;
  H[1] = vx;
  H[2] = f[0].x - 3.5f * (ux + vx);
  H[3] = uy;
  H[4] = vy;
  H[5] = f[0].y - 3.5f * (uy + vy);
  H[6] = p;
  H[7] = q;
  H[8] = 1 - 3.5f * (p + q);
}

// How well the timing pattern between finder `a` and finder `b` (the third
// one's direction is `o`: pixels per module) fits D modules with perspective
// term pl: -1..1
static float timing_line(const fqr_t *s, pt_t a, pt_t b, pt_t o, int D, float pl) {
  float L = (float)(D - 7), p = pl / L;
  float g[MAXD];
  float mean = 0;
  int n = 0;
  for (int i = 6; i <= D - 7; i++) {
    float X = (float)i - 3.0f;
    float t = (X / L) * (1 + pl) / (1 + p * X);
    float x = a.x + t * (b.x - a.x) + 3.0f * o.x, y = a.y + t * (b.y - a.y) + 3.0f * o.y;
    g[n] = sample(s, x, y);
    mean += g[n++];
  }
  mean /= (float)n;
  float corr = 0, mag = 0;
  for (int i = 0; i < n; i++) {
    float d = g[i] - mean;
    corr += (i & 1) ? d : -d;  // module 6 is dark, then they alternate
    mag += fabsf(d);
  }
  return mag > (float)n ? corr / mag : 0;
}

// Best perspective term for one timing line; returns its score
static float timing_search(const fqr_t *s, pt_t a, pt_t b, pt_t o, int D, float centre, float span,
                           float step, float *pl) {
  float best = -2, bp = 0, prev = 0, sc[64];
  int n = 0, bi = 0;
  for (float v = centre - span; v <= centre + span + 1e-4f && n < 64; v += step, n++) {
    sc[n] = timing_line(s, a, b, o, D, v) - 0.15f * fabsf(v);
    if (sc[n] > best) {
      best = sc[n];
      bp = v;
      bi = n;
    }
  }
  (void)prev;
  if (bi > 0 && bi < n - 1) {
    float den = sc[bi - 1] - 2 * sc[bi] + sc[bi + 1];
    if (den < -1e-6f) {
      bp += step * 0.5f * (sc[bi - 1] - sc[bi + 1]) / den;
    }
  }
  *pl = bp;
  return best + 0.15f * fabsf(bp);
}

// Template cells: 1 light. Finder with its light surround (9 x 9), alignment
// pattern (5 x 5)
static int tmpl_light(int T, int i, int j) {
  int r = i < j ? i : j, m = T - 1 - (i > j ? i : j);
  int ring = r < m ? r : m;
  if (T == 9) {
    return ring == 0 || ring == 2;
  }
  return ring == 1;
}

// Finds the pattern (T modules a side) whose centre the grid puts at pixel
// `c`, with module vectors `ux`, `uy`, within R modules of it, on a patch
// resampled at K samples a module. Returns the correlation (-1..1) and moves
// `c` to the best fit, sub-sample.
static float locate(fqr_t *s, pt_t *c, pt_t ux, pt_t uy, int T, float R, int K) {
  int shifts = (int)(2 * R * (float)K + 0.5f) + 1;
  int side = T * K + shifts - 1;
  if (side > PATCH_SIDE || shifts > SCORE_SIDE) {
    return -1;
  }
  float half = (float)side / (2.0f * (float)K);
  for (int j = 0; j < side; j++) {
    float b = ((float)j + 0.5f) / (float)K - half;
    float bx = c->x + b * uy.x, by = c->y + b * uy.y;
    for (int i = 0; i < side; i++) {
      float a = ((float)i + 0.5f) / (float)K - half;
      s->patch[j * side + i] = (uint8_t)(sample(s, bx + a * ux.x, by + a * ux.y) + 0.5f);
    }
  }
  // Template samples: the inner ones of each cell (its edges are blurred)
  int n = 0, nw = 0;
  int in0 = K >= 4 ? K / 4 : 0, in1 = K >= 4 ? K - K / 4 : K;
  for (int j = 0; j < T; j++) {
    for (int i = 0; i < T; i++) {
      int light = tmpl_light(T, i, j);
      for (int b = in0; b < in1; b++) {
        for (int a = in0; a < in1; a++) {
          s->toff[n] = (int16_t)((j * K + b) * side + i * K + a);
          s->twhite[n] = (uint8_t)light;
          nw += light;
          n++;
        }
      }
    }
  }
  // Every other shift first, then the shifts around the best of those
  float best = -2;
  int bx = 0, by = 0;
  float tn = (float)nw * (float)(n - nw);
  for (int i = 0; i < shifts * shifts; i++) {
    s->score[i] = -2;
  }
  for (int pass = 0; pass < 2; pass++) {
    int x0 = pass ? (bx > 0 ? bx - 1 : 0) : 0, x1 = pass ? (bx < shifts - 1 ? bx + 1 : bx) : shifts - 1;
    int y0 = pass ? (by > 0 ? by - 1 : 0) : 0, y1 = pass ? (by < shifts - 1 ? by + 1 : by) : shifts - 1;
    int step = pass ? 1 : 2;
    for (int sy = y0; sy <= y1; sy += step) {
      for (int sx = x0; sx <= x1; sx += step) {
        if (s->score[sy * shifts + sx] > -2) {
          continue;
        }
        const uint8_t *p = s->patch + sy * side + sx;
        uint32_t sum = 0, sumw = 0, sq = 0;
        for (int k = 0; k < n; k++) {
          uint32_t v = p[s->toff[k]];
          sum += v;
          sq += v * v;
          if (s->twhite[k]) {
            sumw += v;
          }
        }
        float var = (float)n * (float)sq - (float)sum * (float)sum;
        float num = (float)n * (float)sumw - (float)nw * (float)sum;
        float sc = var > 1.0f ? num / sqrtf(var * tn) : 0;
        s->score[sy * shifts + sx] = sc;
        if (sc > best) {
          best = sc;
          bx = sx;
          by = sy;
        }
      }
    }
  }
  // (the best's four neighbours were all scored in the second pass, or lie
  // off the search area)
  float ox = (float)bx, oy = (float)by;
  if (bx > 0 && bx < shifts - 1) {
    float l = s->score[by * shifts + bx - 1], r = s->score[by * shifts + bx + 1];
    float den = l - 2 * best + r;
    if (den < -1e-6f) {
      ox += 0.5f * (l - r) / den;
    }
  }
  if (by > 0 && by < shifts - 1) {
    float u = s->score[(by - 1) * shifts + bx], d = s->score[(by + 1) * shifts + bx];
    float den = u - 2 * best + d;
    if (den < -1e-6f) {
      oy += 0.5f * (u - d) / den;
    }
  }
  float mx = (ox - (float)(shifts - 1) * 0.5f) / (float)K;
  float my = (oy - (float)(shifts - 1) * 0.5f) / (float)K;
  c->x += mx * ux.x + my * uy.x;
  c->y += mx * ux.y + my * uy.y;
  return best;
}

// Least-squares homography module -> pixel over n points (Householder QR on
// normalised coordinates); false if they do not determine one
static bool fit_homography(fqr_t *s, const pt_t *m, const pt_t *p, int n, int D, float *H) {
  if (n < 4) {
    return false;
  }
  float u0 = 0, v0 = 0, su = 1;
  for (int i = 0; i < n; i++) {
    u0 += p[i].x;
    v0 += p[i].y;
  }
  u0 /= (float)n;
  v0 /= (float)n;
  for (int i = 0; i < n; i++) {
    float a = fabsf(p[i].x - u0), b = fabsf(p[i].y - v0);
    su = a > su ? a : su;
    su = b > su ? b : su;
  }
  float k = 2.0f / (float)D;
  int rows = 2 * n;
  for (int i = 0; i < n; i++) {
    float x = m[i].x * k - 1, y = m[i].y * k - 1;
    float u = (p[i].x - u0) / su, v = (p[i].y - v0) / su;
    float *r0 = s->lsq[2 * i], *r1 = s->lsq[2 * i + 1];
    r0[0] = x; r0[1] = y; r0[2] = 1; r0[3] = 0; r0[4] = 0; r0[5] = 0; r0[6] = -u * x; r0[7] = -u * y; r0[8] = u;
    r1[0] = 0; r1[1] = 0; r1[2] = 0; r1[3] = x; r1[4] = y; r1[5] = 1; r1[6] = -v * x; r1[7] = -v * y; r1[8] = v;
  }
  for (int c = 0; c < 8; c++) {
    float norm = 0;
    for (int r = c; r < rows; r++) {
      norm += s->lsq[r][c] * s->lsq[r][c];
    }
    norm = sqrtf(norm);
    if (norm < 1e-6f) {
      return false;
    }
    float alpha = s->lsq[c][c] > 0 ? -norm : norm;
    float v0c = s->lsq[c][c] - alpha;
    float vnorm = norm * norm - s->lsq[c][c] * s->lsq[c][c] + v0c * v0c;
    if (vnorm < 1e-12f) {
      continue;
    }
    for (int j = c + 1; j < 9; j++) {
      float dot = v0c * s->lsq[c][j];
      for (int r = c + 1; r < rows; r++) {
        dot += s->lsq[r][c] * s->lsq[r][j];
      }
      float f = 2 * dot / vnorm;
      s->lsq[c][j] -= f * v0c;
      for (int r = c + 1; r < rows; r++) {
        s->lsq[r][j] -= f * s->lsq[r][c];
      }
    }
    s->lsq[c][c] = alpha;
  }
  float h[8];
  for (int c = 7; c >= 0; c--) {
    float v = s->lsq[c][8];
    for (int j = c + 1; j < 8; j++) {
      v -= s->lsq[c][j] * h[j];
    }
    if (fabsf(s->lsq[c][c]) < 1e-6f) {
      return false;
    }
    h[c] = v / s->lsq[c][c];
  }
  float g = h[6], hh = h[7];
  H[0] = su * h[0] * k + u0 * g * k;
  H[1] = su * h[1] * k + u0 * hh * k;
  H[2] = su * (h[2] - h[0] - h[1]) + u0 * (1 - g - hh);
  H[3] = su * h[3] * k + v0 * g * k;
  H[4] = su * h[4] * k + v0 * hh * k;
  H[5] = su * (h[5] - h[3] - h[4]) + v0 * (1 - g - hh);
  H[6] = g * k;
  H[7] = hh * k;
  H[8] = 1 - g - hh;
  return fabsf(H[8]) > 1e-3f;
}

static void module_vectors(const fqr_t *s, float x, float y, pt_t *c, pt_t *ux, pt_t *uy) {
  pt_t a = grid_point(s, x - 0.5f, y), b = grid_point(s, x + 0.5f, y);
  pt_t d = grid_point(s, x, y - 0.5f), e = grid_point(s, x, y + 0.5f);
  *c = grid_point(s, x, y);
  ux->x = b.x - a.x;
  ux->y = b.y - a.y;
  uy->x = e.x - d.x;
  uy->y = e.y - d.y;
}

// The timing fit of both lines for a grid of D modules through s->fin;
// leaves the homography in s->H
static float fit_timing(fqr_t *s, int D, bool fine, float *pl, float *ql) {
  const pt_t *f = s->fin;
  float L = (float)(D - 7);
  pt_t ou = {(f[2].x - f[0].x) / L, (f[2].y - f[0].y) / L};
  pt_t ov = {(f[1].x - f[0].x) / L, (f[1].y - f[0].y) / L};
  float a, b;
  if (fine) {
    a = timing_search(s, f[0], f[1], ou, D, *pl, 0.075f, 0.025f, pl);
    b = timing_search(s, f[0], f[2], ov, D, *ql, 0.075f, 0.025f, ql);
  } else {
    a = timing_search(s, f[0], f[1], ou, D, 0, 0.35f, 0.05f, pl);
    b = timing_search(s, f[0], f[2], ov, D, 0, 0.35f, 0.05f, ql);
  }
  h_from_finders(s->H, f, D, *pl, *ql);
  return (a < b ? a : b) * 0.5f + (a + b) * 0.25f;
}

// Builds the grid through the finder centres in s->fin: its size from the
// timing patterns (D > 0: that size), the finders and alignment patterns
// located in grey, the homography and residual lattice fitted. `R` is how far
// (modules) the finders may be from s->fin. Returns the timing score.
static float build_grid(fqr_t *s, int D, float est_dim, float R, int K) {
  float pl = 0, ql = 0, best = -2;
  s->warp_n = 0;
  if (D == 0) {
    int v0 = (int)((est_dim - 17.0f) / 4.0f + 0.5f);
    for (int v = v0 - 2; v <= v0 + 2; v++) {
      if (v < 1 || v > FQR_MAX_VERSION) {
        continue;
      }
      float a, b;
      float sc = fit_timing(s, 17 + 4 * v, false, &a, &b);
      if (sc > best) {
        best = sc;
        D = 17 + 4 * v;
        pl = a;
        ql = b;
      }
    }
    if (D == 0) {
      return -1;
    }
  }
  s->dim = D;
  best = fit_timing(s, D, false, &pl, &ql);

  // Finder centres against the template, then the timing lines again
  float fs = 1;
  for (int round = 0; round < 2; round++) {
    for (int i = 0; i < 3; i++) {
      pt_t c, ux, uy;
      float mx = i == 1 ? (float)D - 3.5f : 3.5f, my = i == 2 ? (float)D - 3.5f : 3.5f;
      module_vectors(s, mx, my, &c, &ux, &uy);
      c = s->fin[i];
      float sc = locate(s, &c, ux, uy, 9, round == 0 ? R : 0.75f, round == 0 ? K : 4);
      s->fin[i] = c;
      fs = sc < fs ? sc : fs;
    }
    best = fit_timing(s, D, round > 0 || K == 4, &pl, &ql);
    if (K == 4 && R <= 1.5f) {
      break;
    }
  }
  if (fs < 0.3f) {
    return -1;
  }
  s->timing = best;

  // Alignment patterns, nearest the top left finder first; the homography
  // is refitted once a pattern off the timing lines is in
  int version = (D - 17) / 4;
  int pos[MAX_ALIGN];
  int na = align_positions(version, pos);
  pt_t m[MAX_PTS], p[MAX_PTS];
  int n = 3;
  m[0].x = 3.5f; m[0].y = 3.5f;
  m[1].x = (float)D - 3.5f; m[1].y = 3.5f;
  m[2].x = 3.5f; m[2].y = (float)D - 3.5f;
  p[0] = s->fin[0];
  p[1] = s->fin[1];
  p[2] = s->fin[2];
  s->aligns = 0;
  bool off_axis = false;
  for (int ring = 0; ring < 2 * na - 1; ring++) {
    for (int a = 0; a < na; a++) {
      int b = ring - a;
      if (b < 0 || b >= na || (a == 0 && b == 0) || (a == 0 && b == na - 1) || (a == na - 1 && b == 0)) {
        continue;
      }
      pt_t c, ux, uy;
      float mx = (float)pos[a] + 0.5f, my = (float)pos[b] + 0.5f;
      module_vectors(s, mx, my, &c, &ux, &uy);
      float sc = locate(s, &c, ux, uy, 5, off_axis || (a > 0 && b > 0) ? 2.5f : 1.5f, 4);
      if (sc < 0.45f || n >= MAX_PTS) {
        continue;
      }
      m[n].x = mx;
      m[n].y = my;
      p[n++] = c;
      s->aligns++;
      if (a > 0 && b > 0) {
        off_axis = true;
      }
      if (off_axis) {
        float H[9];
        if (fit_homography(s, m, p, n, D, H)) {
          memcpy(s->H, H, sizeof(H));
        }
      }
    }
  }

  // What the homography leaves at each located pattern, spread over a
  // lattice: lens distortion bends the grid
  if (n >= 6 && !OPT(1)) {
    pt_t r[MAX_PTS];
    for (int i = 0; i < n; i++) {
      pt_t q = hmap(s->H, m[i].x, m[i].y);
      r[i].x = p[i].x - q.x;
      r[i].y = p[i].y - q.y;
    }
    int wn = D / WARP_STEP + 2;
    for (int j = 0; j < wn; j++) {
      for (int i = 0; i < wn; i++) {
        float x = (float)(i * WARP_STEP), y = (float)(j * WARP_STEP), sw = 0, sx = 0, sy = 0;
        for (int k = 0; k < n; k++) {
          float dx = m[k].x - x, dy = m[k].y - y;
          float d = dx * dx + dy * dy + 8.0f;
          float wgt = 1.0f / (d * d);
          sw += wgt;
          sx += wgt * r[k].x;
          sy += wgt * r[k].y;
        }
        s->warp[j * wn + i][0] = sx / sw;
        s->warp[j * wn + i][1] = sy / sw;
      }
    }
    s->warp_n = wn;
  }
  return best;
}

// -------------------------------------------------------------- sampling

// Every module's grey value: the mean of 5 bilinear samples, at the centre
// and a quarter module either way on each axis
static void sample_grid(fqr_t *s) {
  int D = s->dim;
  for (int y = 0; y < D; y++) {
    float my = (float)y + 0.5f;
    pt_t c = grid_point(s, 0.5f, my), below = grid_point(s, 0.5f, my + 1.0f);
    for (int x = 0; x < D; x++) {
      // a quarter module along each axis, from the next module's centre
      pt_t next = grid_point(s, (float)x + 1.5f, my), nb = grid_point(s, (float)x + 1.5f, my + 1.0f);
      float ax = (next.x - c.x) * 0.25f, ay = (next.y - c.y) * 0.25f;
      float bx = (below.x - c.x) * 0.25f, by = (below.y - c.y) * 0.25f;
      float v = 2 * sample(s, c.x, c.y) + sample(s, c.x + ax, c.y + ay) + sample(s, c.x - ax, c.y - ay) +
                sample(s, c.x + bx, c.y + by) + sample(s, c.x - bx, c.y - by);
      s->gray[y * D + x] = (uint8_t)(v / 6.0f + 0.5f);
      c = next;
      below = nb;
    }
  }
}

// Grey modules to soft bits: each against the local dark and light levels
// (the 15th and 85th percentile of the 11 x 11 modules around, on a lattice
// every 4 modules)
static void soften(fqr_t *s) {
  int D = s->dim;
  int nt = (D - 1) / 4 + 2;
  for (int ty = 0; ty < nt; ty++) {
    for (int tx = 0; tx < nt; tx++) {
      uint16_t hist[256];
      memset(hist, 0, sizeof(hist));
      int n = 0;
      for (int y = ty * 4 - 5; y <= ty * 4 + 5; y++) {
        for (int x = tx * 4 - 5; x <= tx * 4 + 5; x++) {
          if (x >= 0 && y >= 0 && x < D && y < D) {
            hist[s->gray[y * D + x]]++;
            n++;
          }
        }
      }
      int lo = 0, hi = 255, acc = 0;
      for (int v = 0; v < 256; v++) {
        acc += hist[v];
        if (acc * 100 >= n * 15) {
          lo = v;
          break;
        }
      }
      acc = 0;
      for (int v = 255; v >= 0; v--) {
        acc += hist[v];
        if (acc * 100 >= n * 15) {
          hi = v;
          break;
        }
      }
      s->env_lo[ty * nt + tx] = (uint8_t)lo;
      s->env_hi[ty * nt + tx] = (uint8_t)hi;
    }
  }
  for (int y = 0; y < D; y++) {
    int iy = y / 4, fy = y % 4;
    for (int x = 0; x < D; x++) {
      int ix = x / 4, fx = x % 4;
      const uint8_t *l = s->env_lo + iy * nt + ix, *h = s->env_hi + iy * nt + ix;
      int lo = (l[0] * (4 - fx) + l[1] * fx) * (4 - fy) + (l[nt] * (4 - fx) + l[nt + 1] * fx) * fy;
      int hi = (h[0] * (4 - fx) + h[1] * fx) * (4 - fy) + (h[nt] * (4 - fx) + h[nt + 1] * fx) * fy;
      int amp = hi - lo;  // x16, the full swing
      amp = amp < 16 * 12 ? 16 * 12 : amp;
      int v = (s->gray[y * D + x] * 32 - (lo + hi)) * 64 / amp;
      s->soft[y * D + x] = (int8_t)(v < -127 ? -127 : v > 127 ? 127 : v);
    }
  }
}

// Undoes the blur between modules. At 3 px a module each module's grey value
// carries a share of its neighbours'; the shares (edge and diagonal
// neighbours) are fitted to the frame itself by least squares over all
// modules, the current decisions standing in for the true values (the
// function patterns' are known), and each module is then decided with its
// neighbours' share taken out; a few rounds. Soft bits in, soft bits out.
static void equalise(fqr_t *s, int version) {
  int D = 17 + 4 * version, cells = D * D;
  int8_t *z = s->soft, *b = s->soft2, *nz = s->eq;
  const int8_t *known = s->known;
  build_func(s, version);
  for (int i = 0; i < cells; i++) {
    b[i] = known[i] ? known[i] : (int8_t)(z[i] >= 0 ? 64 : -64);
  }
#define B(xx, yy) ((xx) < 0 || (yy) < 0 || (xx) >= D || (yy) >= D ? 64 : b[(yy) * D + (xx)])
  for (int it = 0; it < EQ_ROUNDS; it++) {
    // Least squares of z on (b, edge neighbours, diagonal neighbours, 1)
    float A[4][5];
    memset(A, 0, sizeof(A));
    for (int y = 0; y < D; y++) {
      int32_t r[4][5];
      memset(r, 0, sizeof(r));
      for (int x = 0; x < D; x++) {
        int32_t f[5];
        f[0] = b[y * D + x];
        f[1] = B(x - 1, y) + B(x + 1, y) + B(x, y - 1) + B(x, y + 1);
        f[2] = B(x - 1, y - 1) + B(x + 1, y - 1) + B(x - 1, y + 1) + B(x + 1, y + 1);
        f[3] = 64;
        f[4] = z[y * D + x];
        for (int i = 0; i < 4; i++) {
          for (int j = i; j < 5; j++) {
            r[i][j] += f[i] * f[j];
          }
        }
      }
      for (int i = 0; i < 4; i++) {
        for (int j = i; j < 5; j++) {
          A[i][j] += (float)r[i][j];
        }
      }
    }
    for (int i = 1; i < 4; i++) {
      for (int j = 0; j < i; j++) {
        A[i][j] = A[j][i];
      }
    }
    float k[4] = {1, 0, 0, 0};
    for (int c = 0; c < 4; c++) {
      int piv = c;
      for (int r = c + 1; r < 4; r++) {
        if (fabsf(A[r][c]) > fabsf(A[piv][c])) {
          piv = r;
        }
      }
      if (fabsf(A[piv][c]) < 1e-3f) {
        return;  // a degenerate grid (all one value): left as it is
      }
      for (int j = 0; j < 5; j++) {
        float t = A[c][j];
        A[c][j] = A[piv][j];
        A[piv][j] = t;
      }
      for (int r = c + 1; r < 4; r++) {
        float f = A[r][c] / A[c][c];
        for (int j = c; j < 5; j++) {
          A[r][j] -= f * A[c][j];
        }
      }
    }
    for (int c = 3; c >= 0; c--) {
      float v = A[c][4];
      for (int j = c + 1; j < 4; j++) {
        v -= A[c][j] * k[j];
      }
      k[c] = v / A[c][c];
    }
    // Each module with its neighbours' share taken out, in 1/256
    bool last = it == EQ_ROUNDS - 1;
    float k0 = k[0] < 0.2f ? 0.2f : k[0];
    int32_t g = (int32_t)(256.0f / k0 * (last ? 1.0f : 2.0f));
    int32_t k1 = (int32_t)(k[1] * 256.0f), k2 = (int32_t)(k[2] * 256.0f), k3 = (int32_t)(k[3] * 64.0f * 256.0f);
    int32_t lim = last ? 127 : 64;
    for (int y = 0; y < D; y++) {
      for (int x = 0; x < D; x++) {
        int32_t n4 = B(x - 1, y) + B(x + 1, y) + B(x, y - 1) + B(x, y + 1);
        int32_t nd = B(x - 1, y - 1) + B(x + 1, y - 1) + B(x - 1, y + 1) + B(x + 1, y + 1);
        int32_t v = ((z[y * D + x] * 256 - k1 * n4 - k2 * nd - k3) / 256) * g / 256;
        nz[y * D + x] = (int8_t)(v < -lim ? -lim : v > lim ? lim : v);
      }
    }
    if (last) {
      memcpy(z, nz, (size_t)cells);
      return;
    }
    for (int i = 0; i < cells; i++) {
      b[i] = known[i] ? known[i] : nz[i];
    }
  }
#undef B
}

// ---------------------------------------------------------------- frames

static void report_grid(const fqr_t *s, fqr_result_t *res) {
  float D = (float)s->dim;
  static const float cx[4] = {0, 1, 1, 0}, cy[4] = {0, 0, 1, 1};
  for (int i = 0; i < 4; i++) {
    pt_t p = grid_point(s, cx[i] * D, cy[i] * D);
    res->corners[2 * i] = p.x;
    res->corners[2 * i + 1] = p.y;
  }
  float dx = s->fin[1].x - s->fin[0].x, dy = s->fin[1].y - s->fin[0].y;
  float ex = s->fin[2].x - s->fin[0].x, ey = s->fin[2].y - s->fin[0].y;
  res->px_per_module_x10 =
      (int32_t)((sqrtf(dx * dx + dy * dy) + sqrtf(ex * ex + ey * ey)) * 5.0f / (D - 7.0f));
  res->version = (s->dim - 17) / 4;
  res->aligns = s->aligns;
  res->timing_x100 = (int32_t)(s->timing * 100.0f);
}

// A grid is in s->H: sample it and decode; on failure add its soft bits to
// the frames before it (if they show the same code) and decode the sum
static bool read_grid(fqr_t *s, uint8_t *payload, size_t max, fqr_result_t *res) {
  int D = s->dim, version = (D - 17) / 4, cells = D * D;
  PROF(3, sample_grid(s));
  PROF(4, soften(s));
  if (!OPT(5)) {
    PROF(5, equalise(s, version));
  }
  s->have_soft = 1;
  report_grid(s, res);
  if (res->stage < FQR_STAGE_GRID) {
    res->stage = FQR_STAGE_GRID;
  }
  res->fused = 1;
  bool ok;
  PROF(6, ok = decode_soft(s, s->soft, version, payload, max, res));

  // Same code as the frames summed so far? Their signs agree on most
  // modules; another code's agree on half
  if (s->acc_n > 0 && s->acc_dim == D) {
    int agree = 0, n = 0;
    for (int i = 0; i < cells; i++) {
      int a = s->acc[i], b = s->soft[i];
      if ((a > 8 * s->acc_n || a < -8 * s->acc_n) && (b > 8 || b < -8)) {
        n++;
        agree += (a > 0) == (b > 0);
      }
    }
    if (n < cells / 4 || agree * 100 < n * 80) {
      s->acc_n = 0;
    }
  } else {
    s->acc_n = 0;
  }
  if (s->acc_n == 0) {
    for (int i = 0; i < cells; i++) {
      s->acc[i] = s->soft[i];
    }
    s->acc_dim = D;
    s->acc_n = 1;
  } else if (s->acc_n < 64) {
    for (int i = 0; i < cells; i++) {
      s->acc[i] = (int16_t)(s->acc[i] + s->soft[i]);
    }
    s->acc_n++;
  }
  if (!ok && s->acc_n > 1 && !OPT(2)) {
    for (int i = 0; i < cells; i++) {
      int v = s->acc[i] / s->acc_n;
      // a module the frames disagree on is no more certain for being summed
      s->soft2[i] = (int8_t)(v < -127 ? -127 : v > 127 ? 127 : v);
    }
    fqr_result_t r2 = *res;
    if (decode_soft(s, s->soft2, version, payload, max, &r2)) {
      *res = r2;
      res->fused = s->acc_n;
      ok = true;
    }
  }
  return ok;
}

static void track_update(fqr_t *s, bool good) {
  if (good) {
    for (int i = 0; i < 3; i++) {
      if (s->track == 1 && s->tdim == s->dim) {
        s->tvel[i].x = s->fin[i].x - s->tfin[i].x;
        s->tvel[i].y = s->fin[i].y - s->tfin[i].y;
      } else {
        s->tvel[i].x = 0;
        s->tvel[i].y = 0;
      }
      s->tfin[i] = s->fin[i];
    }
    s->tdim = s->dim;
    s->track = 1;
  } else if (s->track > 0 && ++s->track > 3) {
    s->track = 0;
  }
}

bool fqr_scan(fqr_t *s, const uint8_t *gray, int stride, uint8_t *payload, size_t payload_max,
              fqr_result_t *res) {
  memset(res, 0, sizeof(*res));
  s->img = gray;
  s->stride = stride;
  s->have_soft = 0;

  // The previous frame's grid, moved on: the finders are looked for around
  // where they were
  if (s->track > 0 && !OPT(3)) {
    for (int i = 0; i < 3; i++) {
      s->fin[i].x = s->tfin[i].x + s->tvel[i].x * (float)s->track;
      s->fin[i].y = s->tfin[i].y + s->tvel[i].y * (float)s->track;
    }
    float sc;
    PROF(2, sc = build_grid(s, s->tdim, 0, 3.0f, 2));
    if (sc > 0.35f) {
      res->tracked = 1;
      res->finders = 3;
      bool ok = read_grid(s, payload, payload_max, res);
      track_update(s, true);
      return ok;
    }
  }

  PROF(0, binarize(s));
  PROF(1, find_finders(s));
  res->finders = s->ncand;
  int nc = s->ncand > 8 ? 8 : s->ncand;
  // Triples: the corner finder is where the two legs meet square and equal
  struct {
    float cost;
    uint8_t a, b, c;
  } tri[MAX_TRIPLES];
  int nt = 0;
  for (int a = 0; a < nc; a++) {
    for (int b = 0; b < nc; b++) {
      for (int c = b + 1; c < nc; c++) {
        if (a == b || a == c || s->cand[a].n < 2 || s->cand[b].n < 2 || s->cand[c].n < 2) {
          continue;
        }
        const cand_t *A = &s->cand[a], *B = &s->cand[b], *C = &s->cand[c];
        float ux = B->x - A->x, uy = B->y - A->y, vx = C->x - A->x, vy = C->y - A->y;
        float lu = sqrtf(ux * ux + uy * uy), lv = sqrtf(vx * vx + vy * vy);
        float sz = (A->hs + A->vs + B->hs + B->vs + C->hs + C->vs) / 6.0f;
        if (lu < sz * 1.5f || lv < sz * 1.5f) {
          continue;
        }
        float cosang = fabsf(ux * vx + uy * vy) / (lu * lv);
        float ratio = lu < lv ? lu / lv : lv / lu;
        if (cosang > 0.5f || ratio < 0.55f) {
          continue;
        }
        float cost = cosang + (1 - ratio);
        int k;
        if (nt < MAX_TRIPLES) {
          k = nt++;
        } else if (cost < tri[MAX_TRIPLES - 1].cost) {
          k = MAX_TRIPLES - 1;
        } else {
          continue;
        }
        for (; k > 0 && tri[k - 1].cost > cost; k--) {
          tri[k] = tri[k - 1];
        }
        tri[k].cost = cost;
        tri[k].a = (uint8_t)a;
        // B is clockwise of C about A on screen (y down): cross > 0
        bool swap = ux * vy - uy * vx < 0;
        tri[k].b = (uint8_t)(swap ? c : b);
        tri[k].c = (uint8_t)(swap ? b : c);
      }
    }
  }
  bool ok = false, any = false;
  for (int t = 0; t < nt && !ok; t++) {
    const cand_t *A = &s->cand[tri[t].a], *B = &s->cand[tri[t].b], *C = &s->cand[tri[t].c];
    s->fin[0].x = A->x; s->fin[0].y = A->y;
    s->fin[1].x = B->x; s->fin[1].y = B->y;
    s->fin[2].x = C->x; s->fin[2].y = C->y;
    if (res->stage < FQR_STAGE_FINDERS) {
      res->stage = FQR_STAGE_FINDERS;
    }
    // The grid's size from the finders' own: a pattern's run across is 7
    // modules over the larger of the leg's |cos| and |sin|
    float ux = B->x - A->x, uy = B->y - A->y, vx = C->x - A->x, vy = C->y - A->y;
    float lu = sqrtf(ux * ux + uy * uy), lv = sqrtf(vx * vx + vy * vy);
    float cu = fabsf(ux) > fabsf(uy) ? fabsf(ux) / lu : fabsf(uy) / lu;
    float mod = (A->hs + A->vs + B->hs + B->vs + C->hs + C->vs) / 42.0f * cu;
    float est = (lu + lv) * 0.5f / mod + 7.0f;
    float sc;
    PROF(2, sc = build_grid(s, 0, est, 1.5f, 4));
    if (sc < 0.35f) {
      continue;
    }
    any = true;
    ok = read_grid(s, payload, payload_max, res);
    if (res->stage >= FQR_STAGE_FORMAT) {
      break;  // this is the code, readable or not
    }
  }
  // Two finders and no third: the run scan missed one (glare, a dim
  // corner). It can only be in six places about the pair (the pair is a side
  // of the code, or its diagonal); look for it there against the template.
  for (int a = 0; a < nc && !any && res->stage < FQR_STAGE_FORMAT; a++) {
    for (int b = a + 1; b < nc && !any; b++) {
      const cand_t *P = &s->cand[a], *Q = &s->cand[b];
      if (P->q < 0.6f || Q->q < 0.6f) {
        continue;
      }
      float dx = Q->x - P->x, dy = Q->y - P->y, d = sqrtf(dx * dx + dy * dy);
      float cu = fabsf(dx) > fabsf(dy) ? fabsf(dx) / d : fabsf(dy) / d;
      float mod = (P->hs + P->vs + Q->hs + Q->vs) / 28.0f * cu;
      if (d < 12 * mod) {
        continue;
      }
      float best = 0.3f;
      pt_t bf[3] = {{0, 0}, {0, 0}, {0, 0}};
      for (int hyp = 0; hyp < 6; hyp++) {
        // perpendicular of the pair, either side
        float sgn = (hyp & 1) ? 1.0f : -1.0f, px = -dy * sgn, py = dx * sgn;
        pt_t c, f[3];
        if (hyp < 2) {  // third at P's end: P is the corner
          c.x = P->x + px; c.y = P->y + py;
          f[0].x = P->x; f[0].y = P->y; f[1].x = Q->x; f[1].y = Q->y;
        } else if (hyp < 4) {  // third at Q's end: Q is the corner
          c.x = Q->x + px; c.y = Q->y + py;
          f[0].x = Q->x; f[0].y = Q->y; f[1].x = P->x; f[1].y = P->y;
        } else {  // the pair is the diagonal: the corner is the third
          c.x = (P->x + Q->x + px) * 0.5f; c.y = (P->y + Q->y + py) * 0.5f;
          f[1].x = P->x; f[1].y = P->y; f[2].x = Q->x; f[2].y = Q->y;
        }
        float ex = hyp < 4 ? dx : (dx + px), ey = hyp < 4 ? dy : (dy + py);
        float el = sqrtf(ex * ex + ey * ey);
        pt_t ux = {ex / el * mod, ey / el * mod}, uy = {-ey / el * mod, ex / el * mod};
        float sc = locate(s, &c, ux, uy, 9, 6.0f, 2);
#ifdef FQR_HOST_TUNE
        if (OPT(15)) {
          printf("pair %d %d hyp %d: %.1f %.1f score %.2f mod %.2f\n", a, b, hyp, (double)c.x, (double)c.y, (double)sc, (double)mod);
        }
#endif
        if (sc > best) {
          best = sc;
          if (hyp < 4) {
            f[2] = c;
          } else {
            f[0] = c;
          }
          // B clockwise of C about A
          float ax = f[1].x - f[0].x, ay = f[1].y - f[0].y, bx = f[2].x - f[0].x, by = f[2].y - f[0].y;
          if (ax * by - ay * bx < 0) {
            pt_t t = f[1];
            f[1] = f[2];
            f[2] = t;
          }
          memcpy(bf, f, sizeof(bf));
        }
      }
      if (best <= 0.3f) {
        continue;
      }
      memcpy(s->fin, bf, sizeof(bf));
      if (res->stage < FQR_STAGE_FINDERS) {
        res->stage = FQR_STAGE_FINDERS;
      }
      float lu = sqrtf((bf[1].x - bf[0].x) * (bf[1].x - bf[0].x) + (bf[1].y - bf[0].y) * (bf[1].y - bf[0].y));
      float lv = sqrtf((bf[2].x - bf[0].x) * (bf[2].x - bf[0].x) + (bf[2].y - bf[0].y) * (bf[2].y - bf[0].y));
      float gs = build_grid(s, 0, (lu + lv) * 0.5f / mod + 7.0f, 1.5f, 4);
#ifdef FQR_HOST_TUNE
      if (OPT(15)) {
        printf("pair grid %.2f dim %d est %.1f\n", (double)gs, s->dim, (double)((lu + lv) * 0.5f / mod + 7.0f));
      }
#endif
      if (gs < 0.35f) {
        continue;
      }
      any = true;
      ok = read_grid(s, payload, payload_max, res);
    }
  }
  track_update(s, any);
  return ok;
}

void fqr_reset(fqr_t *s) {
  s->track = 0;
  s->acc_n = 0;
}

const int8_t *fqr_modules(const fqr_t *s, int *dim) {
  *dim = s->have_soft ? s->dim : 0;
  return s->soft;
}

bool fqr_code_area(const fqr_t *s, int32_t box[4]) {
  if (!s->have_soft) {
    return false;
  }
  float D = (float)s->dim, x0 = 1e9f, y0 = 1e9f, x1 = -1e9f, y1 = -1e9f;
  for (int i = 0; i < 4; i++) {
    pt_t p = grid_point(s, (i == 1 || i == 2) ? D + 1 : -1.0f, i >= 2 ? D + 1 : -1.0f);
    x0 = p.x < x0 ? p.x : x0;
    y0 = p.y < y0 ? p.y : y0;
    x1 = p.x > x1 ? p.x : x1;
    y1 = p.y > y1 ? p.y : y1;
  }
  box[0] = x0 < 0 ? 0 : (int32_t)x0;
  box[1] = y0 < 0 ? 0 : (int32_t)y0;
  box[2] = x1 > (float)(s->w - 1) ? s->w - 1 : (int32_t)x1;
  box[3] = y1 > (float)(s->h - 1) ? s->h - 1 : (int32_t)y1;
  return true;
}

#ifdef FQR_HOST_TUNE
const uint8_t *fqr_debug_gray(const fqr_t *s) { return s->gray; }
void fqr_debug_point(const fqr_t *s, float x, float y, float *px, float *py) {
  pt_t p = grid_point(s, x, y);
  *px = p.x;
  *py = p.y;
}
#endif

static size_t align8(size_t n) { return (n + 7) & ~(size_t)7; }

size_t fqr_state_size(int w, int h) {
  size_t bw = (size_t)(w + BLOCK - 1) / BLOCK, bh = (size_t)(h + BLOCK - 1) / BLOCK;
  return align8(sizeof(fqr_t)) + align8((size_t)((w + 7) / 8) * (size_t)h) +
         align8((size_t)w * sizeof(uint16_t)) + align8(bw * bh * sizeof(uint16_t)) +
         4 * align8(bw * bh);
}

fqr_t *fqr_init(void *state, size_t size, int w, int h) {
  if (state == NULL || ((uintptr_t)state & 7) != 0 || w < 32 || h < 32 || size < fqr_state_size(w, h)) {
    return NULL;
  }
  fqr_t *s = state;
  memset(s, 0, sizeof(*s));
  uint8_t *next = (uint8_t *)state + align8(sizeof(fqr_t));
  s->w = w;
  s->h = h;
  s->bpr = (w + 7) / 8;
  s->bw = (w + BLOCK - 1) / BLOCK;
  s->bh = (h + BLOCK - 1) / BLOCK;
  size_t nb = (size_t)s->bw * (size_t)s->bh;
  s->bits = next;
  next += align8((size_t)s->bpr * (size_t)h);
  s->col = (uint16_t *)next;
  next += align8((size_t)w * sizeof(uint16_t));
  s->bsum = (uint16_t *)next;
  next += align8(nb * sizeof(uint16_t));
  s->bmin = next;
  next += align8(nb);
  s->bmax = next;
  next += align8(nb);
  s->bmean = next;
  next += align8(nb);
  s->btol = next;
  gf_init(s);
  return s;
}
