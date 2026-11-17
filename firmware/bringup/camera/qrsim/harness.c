/*
 * Host harness for the firmware's QR decoder: the vendored quirc
 * (core/embed/io/camera/qr), laid out in one arena as qr_scan.c does, with
 * its retry of a mirrored code on a data ECC failure.
 *
 * stdin: records of four little-endian int32 (w, h, fixed threshold or -1,
 * id), then w*h Y8 pixels. argv: the threshold modes to run on every record.
 * stdout: one line per record and mode:
 *   id mode grids decoded error version otsu_threshold regions capstones
 *
 * Modes (all but "otsu" binarise the frame to 0/255 first, which makes
 * quirc's own Otsu a no-op, so the firmware could do the same without
 * editing quirc):
 *   otsu      quirc as vendored: one global Otsu threshold
 *   otsu90    0.9 x the Otsu threshold;  otsu110: 1.1 x
 *   fixed     the record's threshold (e.g. the previous frame's Otsu)
 *   rowavg    quirc's old row-wise moving average (before the Otsu change):
 *             running averages along each row in both directions, window
 *             w/8, black below 95 % of it
 *   box8, box16, box32   mean of a (w/8, w/16, w/32)-sided box, black below 95 %
 *   box16t10  as box16, black below 90 %
 */
#include "quirc_config.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "quirc/quirc_internal.h"
#include "quirc_ambire.h"

#if EXT_AFFINE || EXT_REGRID
#include "ext_identify.h"
#endif

#define ALIGN8(n) (((n) + 7) & ~(size_t)7)

static struct quirc *q;
static struct quirc_code *g_code;
static struct quirc_data *g_data;
static uint8_t *arena;
static int last_regions, last_capstones;
static int arena_w, arena_h;

static size_t ff_vars(int h) {
  size_t n = (size_t)h * 2 / 3;
  return n > 0 ? n : 1;
}

static void setup(int w, int h) {
  if (arena != NULL && w == arena_w && h == arena_h) return;
  free(arena);
  size_t size = ALIGN8(sizeof(struct quirc)) + ALIGN8((size_t)w * h) +
                ALIGN8(ff_vars(h) * sizeof(struct quirc_flood_fill_vars)) +
                ALIGN8(sizeof(struct quirc_code)) +
                ALIGN8(sizeof(struct quirc_data)) +
                ALIGN8(quirc_ambire_scratch_size());
  arena = aligned_alloc(8, ALIGN8(size));
  if (arena == NULL) {
    fprintf(stderr, "harness: arena of %zu bytes not allocated\n", size);
    exit(2);
  }
  uint8_t *next = arena;
  q = (struct quirc *)next;
  next += ALIGN8(sizeof(struct quirc));
  uint8_t *image = next;
  next += ALIGN8((size_t)w * h);
  struct quirc_flood_fill_vars *vars = (struct quirc_flood_fill_vars *)next;
  next += ALIGN8(ff_vars(h) * sizeof(struct quirc_flood_fill_vars));
  g_code = (struct quirc_code *)next;
  next += ALIGN8(sizeof(struct quirc_code));
  g_data = (struct quirc_data *)next;
  next += ALIGN8(sizeof(struct quirc_data));
  quirc_ambire_set_scratch(next);
  memset(q, 0, sizeof(*q));
  q->image = image;
  q->pixels = (quirc_pixel_t *)image;
  q->w = w;
  q->h = h;
  q->flood_fill_vars = vars;
  q->num_flood_fill_vars = ff_vars(h);
  arena_w = w;
  arena_h = h;
}

// quirc's otsu(), same arithmetic
static int otsu(const uint8_t *img, size_t n) {
  size_t hist[256] = {0};
  for (size_t i = 0; i < n; i++) hist[img[i]]++;
  float sum = 0;
  for (int i = 0; i < 256; i++) sum += (float)i * hist[i];
  float sumB = 0, max = 0;
  size_t q1 = 0;
  int t = 0;
  for (int i = 0; i < 256; i++) {
    q1 += hist[i];
    if (q1 == 0) continue;
    size_t q2 = n - q1;
    if (q2 == 0) break;
    sumB += (float)i * hist[i];
    float m1 = sumB / q1, m2 = (sum - sumB) / q2, d = m1 - m2;
    float var = d * d * q1 * q2;
    if (var >= max) {
      t = i;
      max = var;
    }
  }
  return t;
}

static void bin_global(uint8_t *img, size_t n, int t) {
  for (size_t i = 0; i < n; i++) img[i] = img[i] < t ? 0 : 255;
}

// quirc's threshold() before it moved to Otsu (2020): THRESHOLD_S_DEN 8,
// THRESHOLD_T 5; the averages run on across rows
static void bin_rowavg(uint8_t *img, int w, int h) {
  int s = w / 8;
  if (s < 1) s = 1;
  int *avg = malloc(sizeof(int) * (size_t)w);
  if (avg == NULL) {
    fprintf(stderr, "harness: row average buffer not allocated\n");
    exit(2);
  }
  int avg_w = 0, avg_u = 0;
  for (int y = 0; y < h; y++) {
    uint8_t *row = img + (size_t)y * w;
    memset(avg, 0, sizeof(int) * (size_t)w);
    for (int x = 0; x < w; x++) {
      int a, b;
      if (y & 1) {
        a = x;
        b = w - 1 - x;
      } else {
        a = w - 1 - x;
        b = x;
      }
      avg_w = (avg_w * (s - 1)) / s + row[a];
      avg_u = (avg_u * (s - 1)) / s + row[b];
      avg[a] += avg_w;
      avg[b] += avg_u;
    }
    for (int x = 0; x < w; x++)
      row[x] = row[x] < avg[x] * (100 - 5) / (200 * s) ? 0 : 255;
  }
  free(avg);
}

// Mean of a side x side box (integral image), black below pct % of it
static void bin_box(uint8_t *img, int w, int h, int side, int pct) {
  if (side < 3) side = 3;
  uint32_t *ii = malloc(sizeof(uint32_t) * (size_t)(w + 1) * (h + 1));
  if (ii == NULL) {
    fprintf(stderr, "harness: integral image not allocated\n");
    exit(2);
  }
  for (int x = 0; x <= w; x++) ii[x] = 0;
  for (int y = 1; y <= h; y++) {
    uint32_t rs = 0;
    ii[(size_t)y * (w + 1)] = 0;
    for (int x = 1; x <= w; x++) {
      rs += img[(size_t)(y - 1) * w + x - 1];
      ii[(size_t)y * (w + 1) + x] = ii[(size_t)(y - 1) * (w + 1) + x] + rs;
    }
  }
  int r = side / 2;
  for (int y = 0; y < h; y++) {
    int y0 = y - r < 0 ? 0 : y - r, y1 = y + r + 1 > h ? h : y + r + 1;
    for (int x = 0; x < w; x++) {
      int x0 = x - r < 0 ? 0 : x - r, x1 = x + r + 1 > w ? w : x + r + 1;
      uint32_t s = ii[(size_t)y1 * (w + 1) + x1] - ii[(size_t)y0 * (w + 1) + x1] -
                   ii[(size_t)y1 * (w + 1) + x0] + ii[(size_t)y0 * (w + 1) + x0];
      uint32_t cnt = (uint32_t)(x1 - x0) * (uint32_t)(y1 - y0);
      uint8_t *p = &img[(size_t)y * w + x];
      *p = (uint32_t)*p * 100 * cnt < s * (uint32_t)pct ? 0 : 255;
    }
  }
  free(ii);
}

// One grid: extract and decode (mirrored on a data ECC failure)
static quirc_decode_error_t decode_grid(int i) {
  quirc_extract(q, i, g_code);
  quirc_decode_error_t err = quirc_decode(g_code, g_data);
  if (err == QUIRC_ERROR_DATA_ECC) {
    quirc_flip(g_code);
    err = quirc_decode(g_code, g_data);
  }
  return err;
}

// qr_scan(): identify, then decode each grid. EXT_AFFINE groups the
// capstones in an affine frame; EXT_REGRID retries a grid that fails at the
// neighbouring versions (ext_identify.c)
static void scan(int *grids, int *decoded, int *error, int *version) {
  quirc_begin(q, NULL, NULL);
#if EXT_AFFINE
  quirc_ext_end(q);
#else
  quirc_end(q);
#endif
  *grids = q->num_grids;
  last_regions = q->num_regions;
  last_capstones = q->num_capstones;
  *decoded = 0;
  *error = 0;
  *version = 0;
  for (int i = 0; i < q->num_grids; i++) {
    quirc_decode_error_t err = decode_grid(i);
#if EXT_REGRID
    if (err != QUIRC_SUCCESS && q->grids[i].grid_size > 21) {
      static const int steps[] = {4, -4, 8, -8};
      int size = q->grids[i].grid_size;
      for (unsigned s = 0; s < sizeof(steps) / sizeof(steps[0]); s++) {
        int g = size + steps[s];
        if (g < 21 || g > 177) continue;
        quirc_ext_regrid(q, i, g);
        err = decode_grid(i);
        if (err == QUIRC_SUCCESS) break;
      }
    }
#endif
    if (err != QUIRC_SUCCESS) {
      *error = err;
      continue;
    }
    *decoded = 1;
    *version = g_data->version;
    return;
  }
}

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: harness MODE... < records\n");
    return 1;
  }
  uint8_t *orig = NULL;
  size_t orig_cap = 0;
  int32_t hdr[4];
  while (fread(hdr, sizeof(hdr), 1, stdin) == 1) {
    int w = hdr[0], h = hdr[1], fixed = hdr[2], id = hdr[3];
    size_t n = (size_t)w * h;
    if (w <= 0 || h <= 0 || n > 4096u * 4096u) {
      fprintf(stderr, "harness: bad record header %d x %d\n", w, h);
      return 2;
    }
    if (n > orig_cap) {
      free(orig);
      orig = malloc(n);
      orig_cap = n;
      if (orig == NULL) {
        fprintf(stderr, "harness: frame buffer not allocated\n");
        return 2;
      }
    }
    if (fread(orig, 1, n, stdin) != n) {
      fprintf(stderr, "harness: short frame %d\n", id);
      return 2;
    }
    setup(w, h);
    int t = otsu(orig, n);
    for (int m = 1; m < argc; m++) {
      const char *mode = argv[m];
      memcpy(q->image, orig, n);
      if (!strcmp(mode, "otsu")) {
      } else if (!strcmp(mode, "otsu90")) {
        bin_global(q->image, n, t * 9 / 10);
      } else if (!strcmp(mode, "otsu110")) {
        bin_global(q->image, n, t * 11 / 10 > 255 ? 255 : t * 11 / 10);
      } else if (!strcmp(mode, "fixed")) {
        bin_global(q->image, n, fixed < 0 ? t : fixed);
      } else if (!strcmp(mode, "rowavg")) {
        bin_rowavg(q->image, w, h);
      } else if (!strcmp(mode, "box8")) {
        bin_box(q->image, w, h, w / 8, 95);
      } else if (!strcmp(mode, "box16")) {
        bin_box(q->image, w, h, w / 16, 95);
      } else if (!strcmp(mode, "box32")) {
        bin_box(q->image, w, h, w / 32, 95);
      } else if (!strcmp(mode, "box16t10")) {
        bin_box(q->image, w, h, w / 16, 90);
      } else {
        fprintf(stderr, "harness: unknown mode %s\n", mode);
        return 1;
      }
      int grids, decoded, error, version;
      scan(&grids, &decoded, &error, &version);
      printf("%d %s %d %d %d %d %d %d %d\n", id, mode, grids, decoded, error, version, t,
             last_regions, last_capstones);
    }
    fflush(stdout);
  }
  return 0;
}
