/*
 * Ambire boards, [ambire_camera]: QR decoding of the camera's frames with
 * the video decoder (fqr.h), behind io/qr_scan.h. No heap: the decoder's
 * state lives in the caller's buffer; the frame is read where the camera
 * captured it and is not modified.
 */

#include <string.h>

#include <io/qr_scan.h>
#include <sys/systick.h>

#include "fqr.h"

static struct {
  fqr_t *qr;
  const uint8_t *image;
  int w, h;
} g_qr;

size_t qr_scan_state_size(int w, int h) { return fqr_state_size(w, h); }

bool qr_scan_init(uint8_t *image, int w, int h, uint8_t *state, size_t size) {
  memset(&g_qr, 0, sizeof(g_qr));
  if (image == NULL) {
    return false;
  }
  g_qr.qr = fqr_init(state, size, w, h);
  g_qr.image = image;
  g_qr.w = w;
  g_qr.h = h;
  return g_qr.qr != NULL;
}

// The frame's exposure and focus, on its centre half (every other row): mean,
// clipped and black shares, and the edge width: the contrast range (2nd to
// 98th percentile) over the steepest horizontal gradients (99th percentile)
static void measure_frame(qr_scan_result_t *result) {
  uint32_t level[256] = {0};
  uint32_t grad[256] = {0};
  uint32_t n = 0, sum = 0;
  int w = g_qr.w, h = g_qr.h;
  int x0 = w / 4, x1 = w - w / 4;
  for (int y = h / 4; y < h - h / 4; y += 2) {
    const uint8_t *row = g_qr.image + (size_t)y * (size_t)w;
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

void qr_scan(uint8_t *payload, size_t payload_max, qr_scan_result_t *result) {
  memset(result, 0, sizeof(*result));
  if (g_qr.qr == NULL) {
    return;
  }
  measure_frame(result);

  uint32_t start = systick_ms();
  fqr_result_t r;
  bool ok = fqr_scan(g_qr.qr, g_qr.image, g_qr.w, payload, payload_max, &r);
  result->decode_ms = systick_ms() - start;

  result->capstones = r.finders;
  result->tracked = r.tracked;
  result->fused = r.fused;
  if (r.stage >= FQR_STAGE_GRID) {
    result->grids = 1;
    result->grid_size = 17 + 4 * r.version;
    result->px_per_module_x10 = r.px_per_module_x10;
    for (int i = 0; i < 8; i++) {
      result->corners[i] = (int32_t)r.corners[i];
    }
    // As quirc numbered them: 3 the format did not read, 4 the data did not
    // correct
    result->error = ok ? 0 : r.stage == FQR_STAGE_GRID ? 3 : 4;
  }
  if (ok) {
    result->decoded = 1;
    result->version = r.version;
    result->payload_len = r.payload_len;
  }
}

bool qr_scan_code_area(int32_t box[4]) {
  return g_qr.qr != NULL && fqr_code_area(g_qr.qr, box);
}
