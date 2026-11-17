/*
 * Ambire boards, [ambire_camera]: QR code decoding of grayscale camera frames
 * (the video decoder, io/camera/qr/fqr.h), app side. No heap: the frame and
 * the decoder's state live in buffers the caller owns. The camera captures
 * straight into the frame, which the decoder reads and does not modify.
 * Frames are taken as a video of one scene: the decoder looks for the code
 * where the last frame had it, and adds up frames of one code that do not
 * read alone. Mirrored codes are not read.
 */

#pragma once

#include <trezor_types.h>

typedef struct {
  int32_t grids;         // 1: a code's grid was found (decoded or not)
  int32_t capstones;     // finder pattern candidates in the frame
  int32_t decoded;       // 1: `payload` holds the code
  int32_t error;         // why a grid did not decode: 3 format, 4 data
  int32_t version;       // QR version of the decoded code
  int32_t mirrored;      // always 0
  int32_t payload_len;   // its whole payload length (may exceed what fits)
  // The code reported: its grid side in modules (17 + 4 x version; 0: none),
  // its four corners (x, y, clockwise from top left) and its pixels per
  // module x10
  int32_t grid_size;
  int32_t corners[8];
  int32_t px_per_module_x10;
  // The frame before decoding, measured on its centre half: mean brightness
  // (0-255), the share of clipped (>= 250) and black (<= 5) pixels per mille,
  // and the edge width x10 in pixels (the contrast range over the steepest
  // gradients: 10 is a one-pixel step, more is blur)
  int32_t mean_y;
  int32_t clipped_pm;
  int32_t black_pm;
  int32_t edge_x10;
  // 1: the grid was picked up where the last frame had it (no search)
  int32_t tracked;
  // Frames whose soft bits gave this result (1: this frame alone)
  int32_t fused;
  uint32_t identify_ms;  // always 0 (the time is all in decode_ms)
  uint32_t decode_ms;    // time the decoder took
} qr_scan_result_t;

// Bytes of decoder state qr_scan_init() needs for w x h images (the image
// itself apart)
size_t qr_scan_state_size(int w, int h);

// Builds the decoder for the w x h image at `image` (the camera captures into
// it), its state in `state` (8-byte aligned, qr_scan_state_size() bytes).
// False if a buffer is misaligned or too small.
bool qr_scan_init(uint8_t *image, int w, int h, uint8_t *state, size_t size);

// Looks for a QR code in the frame and decodes it to `payload` (at most
// `payload_max` bytes). The frame is left as it is.
void qr_scan(uint8_t *payload, size_t payload_max, qr_scan_result_t *result);

// After qr_scan(): the area the code is in (x0, y0, x1, y1, frame pixels, a
// module of margin around). False if the last frame had no grid.
bool qr_scan_code_area(int32_t box[4]);
