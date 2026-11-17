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
