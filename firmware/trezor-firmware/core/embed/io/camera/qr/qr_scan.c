/*
 * Ambire boards, [ambire_camera]: QR decoding with quirc, without a heap.
 *
 * quirc's own quirc_new()/quirc_resize() (lib/quirc.c, not vendored) allocate
 * with malloc; here the same state is laid out in the caller's buffers: the
 * image (which quirc's pixel map aliases: fewer than 255 regions) where the
 * caller wants it (the camera captures into it), and in a state buffer the
 * struct quirc, the flood-fill work area quirc_resize() would size, one code
 * and one data structure for extraction and decoding, and the work structures
 * quirc_decode() and quirc_flip() would otherwise put on the stack.
 */

#include "quirc_config.h"

#include <math.h>
#include <string.h>

#include <io/qr_scan.h>
#include <sys/systick.h>

#include "quirc/quirc_internal.h"
#include "quirc_ambire.h"

_Static_assert(QUIRC_PIXEL_ALIAS_IMAGE == 1,
               "the pixel map must alias the image (QUIRC_MAX_REGIONS < 255)");

#define ARENA_ALIGN 8U

static struct {
  struct quirc *q;
  struct quirc_code *code;
  struct quirc_data *data;
  uint8_t *local;  // the local threshold's work memory
  // The threshold for the next frame: kept while frames decode, the next
  // one tried after a frame that did not (a frame has one threshold pass:
  // the image is used up by it)
  int threshold;
} g_qr;

// quirc_resize(): about twice a ring's height, rings at most a third of the
// image's height
static size_t flood_fill_vars(int h) {
  size_t n = (size_t)h * 2 / 3;
  return n > 0 ? n : 1;
}

static size_t aligned(size_t n) {
  return (n + ARENA_ALIGN - 1) & ~(size_t)(ARENA_ALIGN - 1);
}

size_t qr_scan_state_size(int w, int h) {
  return aligned(sizeof(struct quirc)) +
         aligned(flood_fill_vars(h) * sizeof(struct quirc_flood_fill_vars)) +
         aligned(sizeof(struct quirc_code)) +
         aligned(sizeof(struct quirc_data)) +
         aligned(quirc_ambire_scratch_size()) +
         aligned(quirc_ambire_local_size(w));
}

static void *state_take(uint8_t **next, size_t n) {
  void *p = *next;
  *next += aligned(n);
  return p;
}

bool qr_scan_init(uint8_t *image, int w, int h, uint8_t *state, size_t size) {
  memset(&g_qr, 0, sizeof(g_qr));
  if (image == NULL || state == NULL ||
      ((uintptr_t)state & (ARENA_ALIGN - 1)) != 0 || w <= 0 || h <= 0 ||
      size < qr_scan_state_size(w, h)) {
    return false;
  }

  uint8_t *next = state;
  struct quirc *q = state_take(&next, sizeof(struct quirc));
  size_t num_vars = flood_fill_vars(h);
  struct quirc_flood_fill_vars *vars =
      state_take(&next, num_vars * sizeof(struct quirc_flood_fill_vars));
  g_qr.code = state_take(&next, sizeof(struct quirc_code));
  g_qr.data = state_take(&next, sizeof(struct quirc_data));
  quirc_ambire_set_scratch(state_take(&next, quirc_ambire_scratch_size()));
  g_qr.local = state_take(&next, quirc_ambire_local_size(w));

  memset(q, 0, sizeof(*q));
  q->image = image;
  q->pixels = (quirc_pixel_t *)image;
  q->w = w;
  q->h = h;
  q->flood_fill_vars = vars;
  q->num_flood_fill_vars = num_vars;
  g_qr.q = q;
  return true;
}

// The frame's exposure and focus, on its centre half (every other row): mean,
// clipped and black shares, and the edge width: the contrast range (2nd to
// 98th percentile) over the steepest horizontal gradients (99th percentile)
static void measure_frame(const struct quirc *q, qr_scan_result_t *result) {
  uint32_t level[256] = {0};
  uint32_t grad[256] = {0};
  uint32_t n = 0, sum = 0;
  int x0 = q->w / 4, x1 = q->w - q->w / 4;
  for (int y = q->h / 4; y < q->h - q->h / 4; y += 2) {
    const uint8_t *row = q->image + (size_t)y * (size_t)q->w;
    for (int x = x0; x < x1; x++) {
      uint8_t v = row[x];
      level[v]++;
      sum += v;
      int d = (int)row[x + 1] - (int)v;
      grad[d < 0 ? -d : d]++;
      n++;
    }
  }
  if (n == 0) {
    return;
  }
  result->mean_y = (int32_t)(sum / n);
  uint32_t clipped = 0, black = 0;
  for (int v = 250; v < 256; v++) clipped += level[v];
  for (int v = 0; v <= 5; v++) black += level[v];
  result->clipped_pm = (int32_t)(clipped * 1000 / n);
  result->black_pm = (int32_t)(black * 1000 / n);

  // Percentiles from the histograms
  uint32_t acc = 0;
  int lo = 0, hi = 255, g99 = 0;
  for (int v = 0; v < 256; v++) {
    acc += level[v];
    if (acc * 50 >= n) {
      lo = v;
      break;
    }
  }
  acc = 0;
  for (int v = 255; v >= 0; v--) {
    acc += level[v];
    if (acc * 50 >= n) {
      hi = v;
      break;
    }
  }
  acc = 0;
  for (int v = 255; v >= 0; v--) {
    acc += grad[v];
    if (acc * 100 >= n) {
      g99 = v;
      break;
    }
  }
  result->edge_x10 = g99 > 0 ? (int32_t)((hi - lo) * 10 / g99) : 0;
}

// The code's corners, grid side and pixels per module into the result
static void report_code(const struct quirc_code *code,
                        qr_scan_result_t *result) {
  float side = 0;
  for (int c = 0; c < 4; c++) {
    const struct quirc_point *a = &code->corners[c];
    const struct quirc_point *b = &code->corners[(c + 1) % 4];
    float dx = (float)(b->x - a->x), dy = (float)(b->y - a->y);
    side += sqrtf(dx * dx + dy * dy);
    result->corners[2 * c] = a->x;
    result->corners[2 * c + 1] = a->y;
  }
  result->grid_size = code->size;
  result->px_per_module_x10 =
      code->size > 0 ? (int32_t)(side * 10.0f / 4.0f / (float)code->size) : 0;
}

// Extracts and decodes grid `i` (as is, then mirrored); true and the code in
// the result if it decoded, else the failure
// The grey frame before thresholding, if the caller keeps one (NULL: none)
static const uint8_t *g_gray;

void qr_scan_set_gray(const uint8_t *gray) { g_gray = gray; }

static quirc_decode_error_t decode_code(struct quirc_code *code, struct quirc_data *data,
                                        bool *mirrored) {
  quirc_decode_error_t err = quirc_decode(code, data);
  *mirrored = false;
  if (err == QUIRC_ERROR_DATA_ECC) {
    // ISO 18004:2015 allows mirrored codes; quirc's documented retry
    quirc_flip(code);
    err = quirc_decode(code, data);
    *mirrored = true;
  }
  return err;
}

static bool decode_grid(struct quirc *q, int i, uint8_t *payload,
                        size_t payload_max, qr_scan_result_t *result) {
  struct quirc_code *code = g_qr.code;
  struct quirc_data *data = g_qr.data;
  quirc_extract(q, i, code);
  bool mirrored;
  quirc_decode_error_t err = decode_code(code, data, &mirrored);
  // quirc reads each module from one rounded pixel of the thresholded image;
  // at 3-4 px a module that misreads cells. Read again: from the grey frame
  // (a 5-point vote per cell, sub-pixel) when there is one, and at sampling
  // offsets of a fraction of a module (Krux's retry)
  static const float offsets[][2] = {{0, 0},     {0.2f, 0},  {-0.2f, 0}, {0, 0.2f},
                                     {0, -0.2f}, {0.35f, 0}, {-0.35f, 0}, {0, 0.35f},
                                     {0, -0.35f}};
  for (size_t k = g_gray != NULL ? 0 : 1;
       err != QUIRC_SUCCESS && k < sizeof(offsets) / sizeof(offsets[0]); k++) {
    quirc_decode_error_t first = err;
    quirc_ambire_extract(q, i, code, g_gray, offsets[k][0], offsets[k][1]);
    err = decode_code(code, data, &mirrored);
    if (err != QUIRC_SUCCESS) {
      err = first;  // the first reading's error is the one reported
    }
  }
  // The first failure is kept: quirc's own reading of the grid
  if (err != QUIRC_SUCCESS) {
    if (result->error == 0) {
      result->error = err;
      report_code(code, result);
    }
    return false;
  }

  report_code(code, result);
  result->error = 0;
  result->decoded = 1;
  result->mirrored = mirrored;
  result->version = data->version;
  result->payload_len = data->payload_len;
  size_t n = (size_t)data->payload_len;
  if (n > payload_max) {
    n = payload_max;
  }
  memcpy(payload, data->payload, n);
  return true;
}

// Edge sharpening before the threshold, in place: each pixel moves away from
// the mean of its four neighbours by as much again (a Laplacian unsharp mask).
// A soft module edge, smeared over a few pixels, gets a steep step back, so a
// module the blur had pulled toward its neighbours' gray lands on its own
// side of the threshold. Two rows of originals are kept (the row above and
// this one); the border pixels stay as they are.
#define SHARPEN_ROW_MAX 512

static void sharpen(uint8_t *img, int w, int h) {
  static uint8_t above[SHARPEN_ROW_MAX], here[SHARPEN_ROW_MAX];
  if (w > SHARPEN_ROW_MAX || w < 3 || h < 3) {
    return;
  }
  memcpy(above, img, (size_t)w);
  for (int y = 1; y < h - 1; y++) {
    uint8_t *row = img + y * w;
    const uint8_t *below = row + w;
    memcpy(here, row, (size_t)w);
    for (int x = 1; x < w - 1; x++) {
      int c = here[x];
      int lap = 4 * c - above[x] - below[x] - here[x - 1] - here[x + 1];
      int v = c + lap / 4;
      row[x] = (uint8_t)(v < 0 ? 0 : v > 255 ? 255 : v);
    }
    memcpy(above, here, (size_t)w);
  }
}

// Time a frame may spend on its grids. Each size retry redoes quirc's
// perspective fit (81 passes over the whole grid): a frame with a code that
// would not decode took 3-6 s on an ESP32 at 240 MHz. Past the budget the
// frame gives up; the next one tries again (with the next threshold).
#ifndef QR_SCAN_BUDGET_MS
#define QR_SCAN_BUDGET_MS 300
#endif

void qr_scan(uint8_t *payload, size_t payload_max, qr_scan_result_t *result) {
  memset(result, 0, sizeof(*result));
  struct quirc *q = g_qr.q;
  if (q == NULL) {
    return;
  }

  measure_frame(q, result);

  uint32_t start = systick_ms();
  sharpen(q->image, q->w, q->h);
  // quirc_begin() only hands out q->image, which already holds the frame
  quirc_begin(q, NULL, NULL);
  result->threshold = g_qr.threshold;
  quirc_ambire_end(q, g_qr.threshold, g_qr.local);
  uint32_t identified = systick_ms();
  result->identify_ms = identified - start;
  result->grids = q->num_grids;
  result->capstones = q->num_capstones;

  for (int i = 0; i < q->num_grids && !result->decoded; i++) {
    // quirc's grid size first, then its neighbours: a size misread by one
    // version is the common miss at a few pixels per module
    int measured = q->grids[i].grid_size;
    static const int8_t sizes[] = {0, 4, -4, 8, -8};
    for (size_t k = 0; k < sizeof(sizes); k++) {
      // The first grid's own reading always gets its decode
      if ((i > 0 || k > 0) && systick_ms() - start > QR_SCAN_BUDGET_MS) {
        break;
      }
      int size = measured + sizes[k];
      if (size < 21 || size > QUIRC_MAX_GRID_SIZE) {
        continue;
      }
      if (k > 0) {
        quirc_ambire_regrid(q, i, size);
      }
      if (decode_grid(q, i, payload, payload_max, result)) {
        break;
      }
    }
  }

  if (!result->decoded) {
    g_qr.threshold = (g_qr.threshold + 1) % QUIRC_AMBIRE_THRESHOLDS;
  }

  result->decode_ms = systick_ms() - identified;
}

bool qr_scan_code_area(int32_t box[4]) {
  const struct quirc *q = g_qr.q;
  if (q == NULL || q->num_capstones < 2) {
    return false;
  }
  int x0 = q->w, y0 = q->h, x1 = 0, y1 = 0;
  for (int i = 0; i < q->num_capstones; i++) {
    for (int c = 0; c < 4; c++) {
      const struct quirc_point *p = &q->capstones[i].corners[c];
      x0 = p->x < x0 ? p->x : x0;
      y0 = p->y < y0 ? p->y : y0;
      x1 = p->x > x1 ? p->x : x1;
      y1 = p->y > y1 ? p->y : y1;
    }
  }
  // Three or more span the code (a finder pattern's width of quiet zone
  // around); two may be one side of it: the third is up to their spacing away
  int grow;
  if (q->num_capstones >= 3) {
    int cw = 0;
    for (int c = 1; c < 4; c++) {
      int dx = q->capstones[0].corners[c].x - q->capstones[0].corners[0].x;
      int dy = q->capstones[0].corners[c].y - q->capstones[0].corners[0].y;
      int d = (dx < 0 ? -dx : dx) + (dy < 0 ? -dy : dy);
      cw = d > cw ? d : cw;
    }
    grow = cw / 2 + 2;
  } else {
    int dx = q->capstones[1].center.x - q->capstones[0].center.x;
    int dy = q->capstones[1].center.y - q->capstones[0].center.y;
    grow = (dx < 0 ? -dx : dx) + (dy < 0 ? -dy : dy);
  }
  box[0] = x0 - grow < 0 ? 0 : x0 - grow;
  box[1] = y0 - grow < 0 ? 0 : y0 - grow;
  box[2] = x1 + grow >= q->w ? q->w - 1 : x1 + grow;
  box[3] = y1 + grow >= q->h ? q->h - 1 : y1 + grow;
  return true;
}

void qr_scan_set_threshold(int threshold) {
  if (threshold >= 0 && threshold < QUIRC_AMBIRE_THRESHOLDS) {
    g_qr.threshold = threshold;
  }
}
