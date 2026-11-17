// Ambire boards, [ambire_camera]: quirc's identify.c, built with the firmware's
// warnings, less those its code trips (see quirc/README.ambire), and our
// additions beside it (they reach its static helpers; quirc stays unedited):
//
// - quirc_ambire_end(): quirc_end() with a choice of threshold, and the
//   capstone grouping test in an affine frame. quirc places the other
//   capstones in the perspective that one capstone's four corners give; at
//   3-5 px per module a corner one pixel off bends that perspective enough
//   over the code's width that the two legs of a turned code measure > 20 %
//   apart, and the three capstones are never grouped into a code (the host
//   simulation, firmware/bringup/camera/qrsim: 53 of 71 failures at >= 4 px
//   per module were three capstones and no code).
// - quirc_ambire_regrid(): sets a grid's size and redoes its perspective.
//   quirc takes the size (the version) from the capstones' size against
//   their distance; one pixel of corner error reads version 11 as 12. A
//   failed decode is retried at the neighbouring sizes.
#include "quirc_config.h"
#pragma GCC diagnostic ignored "-Wfloat-conversion"
#pragma GCC diagnostic ignored "-Wdouble-promotion"
#pragma GCC diagnostic ignored "-Wsign-compare"
#include "quirc/identify.c"

#include "quirc_ambire.h"

static void test_grouping_affine(struct quirc *q, int i) {
  struct quirc_capstone *c1 = &q->capstones[i];
  struct neighbour_list hlist, vlist;
  hlist.count = 0;
  vlist.count = 0;

  // Corner 0, axes to corners 1 and 3: 7 modules each, as perspective_setup()
  // maps them in test_grouping()
  quirc_float_t ax = c1->corners[1].x - c1->corners[0].x;
  quirc_float_t ay = c1->corners[1].y - c1->corners[0].y;
  quirc_float_t bx = c1->corners[3].x - c1->corners[0].x;
  quirc_float_t by = c1->corners[3].y - c1->corners[0].y;
  quirc_float_t det = ax * by - ay * bx;
  if (det == 0) {
    return;
  }

  for (int j = 0; j < q->num_capstones; j++) {
    if (i == j) {
      continue;
    }
    struct quirc_capstone *c2 = &q->capstones[j];
    quirc_float_t px = c2->center.x - c1->corners[0].x;
    quirc_float_t py = c2->center.y - c1->corners[0].y;
    quirc_float_t u = fabs(7 * (px * by - py * bx) / det - (quirc_float_t)3.5);
    quirc_float_t v = fabs(7 * (ax * py - ay * px) / det - (quirc_float_t)3.5);
    if (u < (quirc_float_t)0.2 * v && hlist.count < QUIRC_MAX_CAPSTONES) {
      hlist.n[hlist.count].index = j;
      hlist.n[hlist.count++].distance = v;
    }
    if (v < (quirc_float_t)0.2 * u && vlist.count < QUIRC_MAX_CAPSTONES) {
      vlist.n[vlist.count].index = j;
      vlist.n[vlist.count++].distance = u;
    }
  }
  if (hlist.count && vlist.count) {
    test_neighbours(q, i, &hlist, &vlist);
  }
}

// The local-mean threshold, in place: a pixel is black below 95 % of the
// mean of the (2r+1)^2 box around it. Column sums slide down the image; the
// last 2r+1 rows' original values wait in `ring` (they are overwritten in
// the image as the threshold passes them).
static void threshold_local(struct quirc *q, uint8_t *ring, uint16_t *colsum,
                            int r) {
  const int w = q->w, h = q->h, k = 2 * r + 1;
  uint8_t *img = q->image;
  int rows = 0;  // rows in the column sums

  memset(colsum, 0, (size_t)w * sizeof(*colsum));
  for (int y = 0; y <= r && y < h; y++) {
    memcpy(ring + (size_t)(y % k) * w, img + (size_t)y * w, (size_t)w);
    for (int x = 0; x < w; x++) colsum[x] += img[(size_t)y * w + x];
    rows++;
  }

  for (int y = 0; y < h; y++) {
    const uint8_t *orig = ring + (size_t)(y % k) * w;
    quirc_pixel_t *out = (quirc_pixel_t *)img + (size_t)y * w;
    uint32_t sum = 0;
    int cols = 0;
    for (int x = 0; x <= r && x < w; x++) {
      sum += colsum[x];
      cols++;
    }
    for (int x = 0; x < w; x++) {
      uint32_t area = (uint32_t)rows * (uint32_t)cols;
      out[x] = (uint32_t)orig[x] * area * 100 < sum * 95 ? QUIRC_PIXEL_BLACK
                                                       : QUIRC_PIXEL_WHITE;
      if (x + r + 1 < w) {
        sum += colsum[x + r + 1];
        cols++;
      }
      if (x - r >= 0) {
        sum -= colsum[x - r];
        cols--;
      }
    }
    // Slide down: the row leaving first (it shares the ring slot with the
    // row entering), then the row entering (still original in the image:
    // only rows up to y are written)
    if (y - r >= 0) {
      const uint8_t *gone = ring + (size_t)((y - r) % k) * w;
      for (int x = 0; x < w; x++) colsum[x] -= gone[x];
      rows--;
    }
    if (y + r + 1 < h) {
      const uint8_t *in = img + (size_t)(y + r + 1) * w;
      memcpy(ring + (size_t)((y + r + 1) % k) * w, in, (size_t)w);
      for (int x = 0; x < w; x++) colsum[x] += in[x];
      rows++;
    }
  }
  if (QUIRC_PIXEL_ALIAS_IMAGE) {
    q->pixels = (quirc_pixel_t *)q->image;
  }
}

int quirc_ambire_local_radius(int w) {
  int r = w / 64;
  return r < 2 ? 2 : r;
}

size_t quirc_ambire_local_size(int w) {
  int r = quirc_ambire_local_radius(w);
  return (size_t)(2 * r + 1) * (size_t)w + (size_t)w * sizeof(uint16_t);
}

void quirc_ambire_end(struct quirc *q, int threshold, uint8_t *local_mem) {
  if (threshold == QUIRC_AMBIRE_THRESHOLD_LOCAL && local_mem != NULL) {
    int r = quirc_ambire_local_radius(q->w);
    uint8_t *ring = local_mem;
    uint16_t *colsum =
        (uint16_t *)(local_mem + (size_t)(2 * r + 1) * (size_t)q->w);
    threshold_local(q, ring, colsum, r);
  } else {
    unsigned t = otsu(q);
    if (threshold == QUIRC_AMBIRE_THRESHOLD_OTSU_HIGH) {
      // Blur in gamma-encoded data thins the dark modules: a higher cut
      // gives them back
      t = t * 11 / 10;
      t = t > 255 ? 255 : t;
    }
    pixels_setup(q, (uint8_t)t);
  }

  for (int y = 0; y < q->h; y++) {
    finder_scan(q, (unsigned)y);
  }
  for (int i = 0; i < q->num_capstones; i++) {
    test_grouping_affine(q, i);
  }
}

void quirc_ambire_regrid(struct quirc *q, int index, int grid_size) {
  q->grids[index].grid_size = grid_size;
  setup_qr_perspective(q, index);
}

// Sub-pixel perspective_map()
static void map_f(const quirc_float_t *c, quirc_float_t u, quirc_float_t v, float *x, float *y) {
  quirc_float_t den = (quirc_float_t)1 / (c[6] * u + c[7] * v + (quirc_float_t)1.0);
  *x = (float)((c[0] * u + c[1] * v + c[2]) * den);
  *y = (float)((c[3] * u + c[4] * v + c[5]) * den);
}

// Bilinear grey at (x, y), pixel centres at integer coordinates
static int gray_at(const uint8_t *g, int w, int h, float x, float y) {
  if (x < 0) x = 0;
  if (y < 0) y = 0;
  if (x > (float)(w - 1)) x = (float)(w - 1);
  if (y > (float)(h - 1)) y = (float)(h - 1);
  int x0 = (int)x, y0 = (int)y;
  int x1 = x0 + 1 < w ? x0 + 1 : x0, y1 = y0 + 1 < h ? y0 + 1 : y0;
  int fx = (int)((x - (float)x0) * 256.0f), fy = (int)((y - (float)y0) * 256.0f);
  const uint8_t *r0 = g + y0 * w, *r1 = g + y1 * w;
  int top = r0[x0] * (256 - fx) + r0[x1] * fx;
  int bot = r1[x0] * (256 - fx) + r1[x1] * fx;
  return (top * (256 - fy) + bot * fy) >> 16;
}

void quirc_ambire_extract(const struct quirc *q, int index, struct quirc_code *code,
                          const uint8_t *gray, float du, float dv) {
  const struct quirc_grid *qr = &q->grids[index];
  memset(code, 0, sizeof(*code));
  if (index < 0 || index >= q->num_grids) {
    return;
  }
  int n = qr->grid_size;
  perspective_map(qr->c, 0.0, 0.0, &code->corners[0]);
  perspective_map(qr->c, n, 0.0, &code->corners[1]);
  perspective_map(qr->c, n, n, &code->corners[2]);
  perspective_map(qr->c, 0.0, n, &code->corners[3]);
  code->size = n;
  if (n > QUIRC_MAX_GRID_SIZE) {
    return;
  }

  int i = 0;
  for (int y = 0; y < n; y++) {
    for (int x = 0; x < n; x++, i++) {
      quirc_float_t u = (quirc_float_t)x + 0.5f + du, v = (quirc_float_t)y + 0.5f + dv;
      bool dark;
      if (gray == NULL) {
        struct quirc_point p;
        perspective_map(qr->c, u, v, &p);
        dark = p.y >= 0 && p.y < q->h && p.x >= 0 && p.x < q->w && q->pixels[p.y * q->w + p.x];
      } else {
        // The surroundings' mean: a 5 x 5 lattice over +-2 modules (a code
        // is about half dark, so this sits between its dark and light)
        int sum = 0;
        for (int b = -2; b <= 2; b++) {
          for (int a = -2; a <= 2; a++) {
            float sx, sy;
            map_f(qr->c, u + (quirc_float_t)a, v + (quirc_float_t)b, &sx, &sy);
            sum += gray_at(gray, q->w, q->h, sx, sy);
          }
        }
        int mean = sum / 25;
        static const float pts[5][2] = {{0, 0}, {-0.2f, 0}, {0.2f, 0}, {0, -0.2f}, {0, 0.2f}};
        int votes = 0;
        for (int k = 0; k < 5; k++) {
          float sx, sy;
          map_f(qr->c, u + pts[k][0], v + pts[k][1], &sx, &sy);
          votes += gray_at(gray, q->w, q->h, sx, sy) < mean;
        }
        dark = votes >= 3;
      }
      if (dark) {
        code->cell_bitmap[i >> 3] |= (uint8_t)(1 << (i & 7));
      }
    }
  }
}
