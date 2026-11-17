#include "qr_roi.h"

#include <math.h>
#include <string.h>

size_t qr_roi_image_size(int w, int h, int max_num, int max_den) {
  return (size_t)(w * max_num / max_den + 1) * (size_t)(h * max_num / max_den + 1);
}

static int clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

// Catmull-Rom weights (x16384) for a sample at fraction t past the second tap
static void cubic_weights(float t, int32_t wt[4]) {
  float t2 = t * t, t3 = t2 * t;
  wt[0] = (int32_t)((-0.5f * t3 + t2 - 0.5f * t) * 16384.0f);
  wt[1] = (int32_t)((1.5f * t3 - 2.5f * t2 + 1.0f) * 16384.0f);
  wt[2] = (int32_t)((-1.5f * t3 + 2.0f * t2 + 0.5f * t) * 16384.0f);
  wt[3] = (int32_t)((0.5f * t3 - 0.5f * t2) * 16384.0f);
}

// The source position of output pixel o (pixel centres aligned, as OpenCV's
// resize): integer part and fraction
static void source_pos(int x0, int o, float inv, int *i, float *f) {
  float s = (float)x0 + ((float)o + 0.5f) * inv - 0.5f;
  int is = (int)(s < 0 ? s - 1.0f : s);
  *i = is;
  *f = s - (float)is;
}

static void upscale(const uint8_t *src, int w, int h, int x0, int y0, int rw, int rh, int ow,
                    int oh, int kernel, uint8_t *out) {
  float inv_x = (float)rw / (float)ow, inv_y = (float)rh / (float)oh;
  for (int oy = 0; oy < oh; oy++) {
    int iy;
    float fy;
    source_pos(y0, oy, inv_y, &iy, &fy);
    uint8_t *dst = out + (size_t)oy * ow;
    if (kernel == QR_ROI_CUBIC) {
      int32_t wy[4];
      cubic_weights(fy, wy);
      const uint8_t *rows[4];
      for (int j = 0; j < 4; j++) {
        rows[j] = src + (size_t)clampi(iy - 1 + j, 0, h - 1) * w;
      }
      for (int ox = 0; ox < ow; ox++) {
        int ix;
        float fx;
        source_pos(x0, ox, inv_x, &ix, &fx);
        int32_t wx[4];
        cubic_weights(fx, wx);
        int xs[4];
        for (int i = 0; i < 4; i++) {
          xs[i] = clampi(ix - 1 + i, 0, w - 1);
        }
        int64_t acc = 0;
        for (int j = 0; j < 4; j++) {
          int32_t r = wx[0] * rows[j][xs[0]] + wx[1] * rows[j][xs[1]] +
                      wx[2] * rows[j][xs[2]] + wx[3] * rows[j][xs[3]];
          acc += (int64_t)wy[j] * r;
        }
        dst[ox] = (uint8_t)clampi((int)(acc >> 28), 0, 255);
      }
    } else {
      int wy = (int)(fy * 256.0f);
      const uint8_t *r1 = src + (size_t)clampi(iy, 0, h - 1) * w;
      const uint8_t *r2 = src + (size_t)clampi(iy + 1, 0, h - 1) * w;
      for (int ox = 0; ox < ow; ox++) {
        int ix;
        float fx;
        source_pos(x0, ox, inv_x, &ix, &fx);
        int wx = (int)(fx * 256.0f);
        int xa = clampi(ix, 0, w - 1), xb = clampi(ix + 1, 0, w - 1);
        int top = r1[xa] * (256 - wx) + r1[xb] * wx;
        int bot = r2[xa] * (256 - wx) + r2[xb] * wx;
        dst[ox] = (uint8_t)((top * (256 - wy) + bot * wy + 32768) >> 16);
      }
    }
  }
}

static bool decode_area(const uint8_t *gray, int w, int h, int x0, int y0, int rw, int rh,
                        int ow, int oh, int kernel, int threshold, int passes, uint8_t *roi,
                        uint8_t *roi_state, size_t roi_state_len, uint8_t *payload,
                        size_t payload_max, qr_scan_result_t *result);

bool qr_roi_decode(const uint8_t *gray, int w, int h, const int32_t corners[8],
                   qr_roi_scale_t sc, int passes, uint8_t *roi, uint8_t *roi_state,
                   size_t roi_state_len, uint8_t *payload, size_t payload_max,
                   qr_scan_result_t *result) {
  int x0 = 0, y0 = 0, x1 = w - 1, y1 = h - 1;
  if (corners != NULL) {
    int minx = w, miny = h, maxx = 0, maxy = 0;
    for (int c = 0; c < 4; c++) {
      int x = corners[2 * c], y = corners[2 * c + 1];
      minx = x < minx ? x : minx;
      maxx = x > maxx ? x : maxx;
      miny = y < miny ? y : miny;
      maxy = y > maxy ? y : maxy;
    }
    if (maxx <= minx || maxy <= miny) {
      return false;
    }
    // The quiet zone, and corners a few modules off: 20 % around
    int mx = (maxx - minx) / 5 + 4, my = (maxy - miny) / 5 + 4;
    x0 = clampi(minx - mx, 0, w - 1);
    y0 = clampi(miny - my, 0, h - 1);
    x1 = clampi(maxx + mx, 0, w - 1);
    y1 = clampi(maxy + my, 0, h - 1);
  }
  int rw = x1 - x0 + 1, rh = y1 - y0 + 1;
  return decode_area(gray, w, h, x0, y0, rw, rh, rw * sc.num / sc.den, rh * sc.num / sc.den,
                     sc.kernel, sc.threshold, passes, roi, roi_state, roi_state_len, payload, payload_max,
                     result);
}

static bool decode_area(const uint8_t *gray, int w, int h, int x0, int y0, int rw, int rh,
                        int ow, int oh, int kernel, int threshold, int passes, uint8_t *roi,
                        uint8_t *roi_state, size_t roi_state_len, uint8_t *payload,
                        size_t payload_max, qr_scan_result_t *result) {
  if (rw < 21 || rh < 21 || qr_scan_state_size(ow, oh) > roi_state_len) {
    return false;
  }
  upscale(gray, w, h, x0, y0, rw, rh, ow, oh, kernel, roi);
  if (!qr_scan_init(roi, ow, oh, roi_state, roi_state_len)) {
    return false;
  }
  qr_scan_set_threshold(threshold);
  for (int pass = 0; pass < passes; pass++) {
    if (pass > 0) {
      upscale(gray, w, h, x0, y0, rw, rh, ow, oh, kernel, roi);  // a pass uses it up
    }
    qr_scan(payload, payload_max, result);
    // Not stopped when a threshold finds no code: another one may
    if (result->decoded) {
      break;
    }
  }
  if (result->decoded) {
    result->px_per_module_x10 = result->px_per_module_x10 * rw / ow;
    for (int c = 0; c < 4; c++) {
      result->corners[2 * c] = x0 + result->corners[2 * c] * rw / ow;
      result->corners[2 * c + 1] = y0 + result->corners[2 * c + 1] * rh / oh;
    }
  }
  return result->decoded != 0;
}

bool qr_roi_decode_box(const uint8_t *gray, int w, int h, const int32_t box[4],
                       qr_roi_scale_t sc, int passes, uint8_t *roi, size_t roi_len,
                       uint8_t *roi_state, size_t roi_state_len, uint8_t *payload,
                       size_t payload_max, qr_scan_result_t *result) {
  int x0 = clampi(box[0], 0, w - 1), y0 = clampi(box[1], 0, h - 1);
  int rw = clampi(box[2], x0, w - 1) - x0 + 1, rh = clampi(box[3], y0, h - 1) - y0 + 1;
  // The scale asked for, or the largest that fits the buffer
  float scale = (float)sc.num / (float)sc.den;
  float fit = (float)roi_len / ((float)rw * (float)rh);
  if (scale * scale > fit) {
    scale = sqrtf(fit);
  }
  if (scale < 1.2f) {
    return false;
  }
  int ow = (int)((float)rw * scale), oh = (int)((float)rh * scale);
  while ((size_t)ow * (size_t)oh > roi_len) {
    ow--;
    oh = ow * rh / rw;
  }
  return decode_area(gray, w, h, x0, y0, rw, rh, ow, oh, sc.kernel, sc.threshold, passes, roi,
                     roi_state,
                     roi_state_len, payload, payload_max, result);
}
