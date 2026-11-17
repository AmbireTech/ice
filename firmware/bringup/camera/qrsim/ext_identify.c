/*
 * quirc's identify.c plus two fixes that need no edit of quirc itself: this
 * file includes identify.c, as the firmware's quirc_identify.c does, and
 * adds functions that reach its static helpers.
 *
 * quirc_ext_end(): quirc_end() with the capstone grouping test in an affine
 *   frame. quirc places the other capstones in the perspective that one
 *   capstone's four corners give; a corner off by a pixel (a 7-module
 *   capstone is ~20-30 px across at 3-4 px per module) bends that perspective
 *   enough over the code's width that the two legs of a turned code measure
 *   >20 % apart, and the triple is never grouped.
 *
 * quirc_ext_regrid(): sets a grid's size and redoes its perspective. quirc
 *   takes the size (the version) from the capstones' size against their
 *   distance; at 3-5 px per module one pixel of corner error makes a
 *   version 11 code read as 12 or 10. A failed decode is retried at the
 *   neighbouring sizes.
 */
#include "quirc_config.h"
#pragma GCC diagnostic ignored "-Wfloat-conversion"
#pragma GCC diagnostic ignored "-Wdouble-promotion"
#pragma GCC diagnostic ignored "-Wsign-compare"
#include "quirc/identify.c"

#include "ext_identify.h"

static void test_grouping_affine(struct quirc *q, int i) {
  struct quirc_capstone *c1 = &q->capstones[i];
  struct neighbour_list hlist, vlist;
  hlist.count = 0;
  vlist.count = 0;

  /* Corner 0, axes to corners 1 and 3: 7 modules each, as perspective_setup()
   * maps them */
  quirc_float_t ax = c1->corners[1].x - c1->corners[0].x;
  quirc_float_t ay = c1->corners[1].y - c1->corners[0].y;
  quirc_float_t bx = c1->corners[3].x - c1->corners[0].x;
  quirc_float_t by = c1->corners[3].y - c1->corners[0].y;
  quirc_float_t det = ax * by - ay * bx;
  if (det == 0) return;

  for (int j = 0; j < q->num_capstones; j++) {
    if (i == j) continue;
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
  if (hlist.count && vlist.count) test_neighbours(q, i, &hlist, &vlist);
}

void quirc_ext_end(struct quirc *q) {
  pixels_setup(q, otsu(q));
  for (int y = 0; y < q->h; y++) finder_scan(q, (unsigned)y);
  for (int i = 0; i < q->num_capstones; i++) test_grouping_affine(q, i);
}

void quirc_ext_regrid(struct quirc *q, int index, int grid_size) {
  q->grids[index].grid_size = grid_size;
  setup_qr_perspective(q, index);
}
