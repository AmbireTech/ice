// Second look at a frame the QR decoder could not read: the frame (or the
// area of a code it found) upscaled from the untouched grayscale and decoded
// again. quirc reads each module from one rounded pixel of the thresholded
// image, and finds finder patterns by their 1-module rings; at 3-4 px a module
// both fail. Upscaled, it samples between the original pixels (ESP32-CAM
// frames, 2026-11-17: 0 of 12 near misses decoded at 1x; 7 at 1.5x linear,
// 7 at 2x linear, 7 at 2.5x cubic, 9 by any of them).
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <io/qr_scan.h>

#define QR_ROI_LINEAR 0
#define QR_ROI_CUBIC 1  // Catmull-Rom

typedef struct {
  int num, den;   // scale num/den (e.g. 3/2)
  int kernel;     // QR_ROI_*
  int threshold;  // the first pass's threshold (qr_scan_set_threshold); the
                  // local mean (2) read nearly every upscaled near miss
} qr_roi_scale_t;

// Bytes of image buffer qr_roi_decode() needs for frames up to w x h at a
// scale of at most max_num/max_den
size_t qr_roi_image_size(int w, int h, int max_num, int max_den);

// Decodes again from `gray` (the w x h frame before thresholding), upscaled
// by `sc`: the whole frame (corners NULL), or the area around a code's
// corners (x, y clockwise, frame pixels). `roi` and `roi_state` are its work
// buffers (16-byte aligned; qr_roi_image_size() and qr_scan_state_size() of
// the largest upscaled frame). Every threshold is tried unless `passes` < 3.
// The decoder is left set up for the upscaled image: the caller calls
// qr_scan_init() for its own frames again before its next qr_scan(). Corners
// and px/module in `result` are in frame terms. True if it decoded.
bool qr_roi_decode(const uint8_t *gray, int w, int h, const int32_t corners[8],
                   qr_roi_scale_t sc, int passes, uint8_t *roi, uint8_t *roi_state,
                   size_t roi_state_len, uint8_t *payload, size_t payload_max,
                   qr_scan_result_t *result);

// As qr_roi_decode(), on the area `box` (x0, y0, x1, y1, frame pixels, from
// qr_scan_code_area()) taken as it is, at the largest scale up to `sc` whose
// upscaled image fits `roi_len` bytes (none below 1.2x: false then).
bool qr_roi_decode_box(const uint8_t *gray, int w, int h, const int32_t box[4],
                       qr_roi_scale_t sc, int passes, uint8_t *roi, size_t roi_len,
                       uint8_t *roi_state, size_t roi_state_len, uint8_t *payload,
                       size_t payload_max, qr_scan_result_t *result);
