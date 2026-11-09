/*
 * Ambire boards, [ambire_camera]: QR decoding with quirc, without a heap.
 *
 * quirc's own quirc_new()/quirc_resize() (lib/quirc.c, not vendored) allocate
 * with malloc; here the same state is laid out in the caller's arena: the
 * struct quirc, its image (which its pixel map aliases: fewer than 255
 * regions), the flood-fill work area quirc_resize() would size, one code and
 * one data structure for extraction and decoding, and the work structures
 * quirc_decode() and quirc_flip() would otherwise put on the stack.
 */

#include "quirc_config.h"

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

size_t qr_scan_arena_size(int w, int h) {
  return aligned(sizeof(struct quirc)) + aligned((size_t)w * (size_t)h) +
         aligned(flood_fill_vars(h) * sizeof(struct quirc_flood_fill_vars)) +
         aligned(sizeof(struct quirc_code)) +
         aligned(sizeof(struct quirc_data)) +
         aligned(quirc_ambire_scratch_size());
}

static void *arena_take(uint8_t **next, size_t n) {
  void *p = *next;
  *next += aligned(n);
  return p;
}

bool qr_scan_init(uint8_t *arena, size_t size, int w, int h) {
  memset(&g_qr, 0, sizeof(g_qr));
  if (arena == NULL || ((uintptr_t)arena & (ARENA_ALIGN - 1)) != 0 || w <= 0 ||
      h <= 0 || size < qr_scan_arena_size(w, h)) {
    return false;
  }

  uint8_t *next = arena;
  struct quirc *q = arena_take(&next, sizeof(struct quirc));
  uint8_t *image = arena_take(&next, (size_t)w * (size_t)h);
  size_t num_vars = flood_fill_vars(h);
  struct quirc_flood_fill_vars *vars =
      arena_take(&next, num_vars * sizeof(struct quirc_flood_fill_vars));
  g_qr.code = arena_take(&next, sizeof(struct quirc_code));
  g_qr.data = arena_take(&next, sizeof(struct quirc_data));
  quirc_ambire_set_scratch(arena_take(&next, quirc_ambire_scratch_size()));

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

uint8_t *qr_scan_image(void) {
  return g_qr.q != NULL ? g_qr.q->image : NULL;
}

void qr_scan(uint8_t *payload, size_t payload_max, qr_scan_result_t *result) {
  memset(result, 0, sizeof(*result));
  struct quirc *q = g_qr.q;
  if (q == NULL) {
    return;
  }

  uint32_t start = systick_ms();
  // quirc_begin() only hands out q->image, which already holds the frame
  quirc_begin(q, NULL, NULL);
  quirc_end(q);
  uint32_t identified = systick_ms();
  result->identify_ms = identified - start;
  result->grids = q->num_grids;

  for (int i = 0; i < q->num_grids; i++) {
    struct quirc_code *code = g_qr.code;
    struct quirc_data *data = g_qr.data;
    quirc_extract(q, i, code);
    quirc_decode_error_t err = quirc_decode(code, data);
    bool mirrored = false;
    if (err == QUIRC_ERROR_DATA_ECC) {
      // ISO 18004:2015 allows mirrored codes; quirc's documented retry
      quirc_flip(code);
      err = quirc_decode(code, data);
      mirrored = true;
    }
    if (err != QUIRC_SUCCESS) {
      result->error = err;
      continue;
    }

    result->decoded = 1;
    result->mirrored = mirrored;
    result->version = data->version;
    result->payload_len = data->payload_len;
    size_t n = (size_t)data->payload_len;
    if (n > payload_max) {
      n = payload_max;
    }
    memcpy(payload, data->payload, n);
    for (int c = 0; c < 4; c++) {
      result->corners[2 * c] = code->corners[c].x;
      result->corners[2 * c + 1] = code->corners[c].y;
    }
    break;
  }

  result->decode_ms = systick_ms() - identified;
}
