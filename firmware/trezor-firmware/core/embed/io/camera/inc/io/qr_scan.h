/*
 * Ambire boards, [ambire_camera]: QR code decoding of grayscale camera frames
 * (quirc), app side. No heap: the decoder's image and its state live in
 * buffers the caller owns. The camera captures straight into the image.
 */

#pragma once

#include <trezor_types.h>

typedef struct {
  int32_t grids;         // QR codes found in the image (decoded or not)
  int32_t capstones;     // finder patterns found (a code needs three)
  int32_t decoded;       // 1: `payload` holds the first one that decoded
  int32_t error;         // quirc_decode_error_t of the last failed decode
  int32_t version;       // QR version of the decoded code
  int32_t mirrored;      // 1: it decoded only mirrored
  int32_t payload_len;   // its whole payload length (may exceed what fits)
  // The code reported (the one decoded, else the last one tried): its grid
  // side in modules (17 + 4 x version, as quirc measured it; 0: none), its
  // four corners (x, y, clockwise from top left) and its pixels per module x10
  // (the mean side over the grid side)
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
  // The threshold used: 0 quirc's Otsu, 1 Otsu x 1.1, 2 the local mean. The
  // next frame tries the next one when this one did not decode.
  int32_t threshold;
  uint32_t identify_ms;  // time spent finding codes
  uint32_t decode_ms;    // time spent decoding them
} qr_scan_result_t;

// Bytes of decoder state qr_scan_init() needs for w x h images (the image
// itself apart)
size_t qr_scan_state_size(int w, int h);

// Builds the decoder for the w x h image at `image` (the camera captures into
// it), its state in `state` (8-byte aligned, qr_scan_state_size() bytes).
// False if a buffer is misaligned or too small.
bool qr_scan_init(uint8_t *image, int w, int h, uint8_t *state, size_t size);

// Looks for QR codes in the image and decodes them; the first that decodes
// (as is, or mirrored) is copied to `payload` (at most `payload_max` bytes).
// The image is used up: it is thresholded in place.
void qr_scan(uint8_t *payload, size_t payload_max, qr_scan_result_t *result);

// After qr_scan(): the area a code is in, from what the last scan found (x0,
// y0, x1, y1, frame pixels): the code reported, else the finder patterns
// grown by their spacing (with two of three, the third lies within that).
// False if it found fewer than two finder patterns.
bool qr_scan_code_area(int32_t box[4]);

// The threshold the next qr_scan() uses (0 Otsu, 1 Otsu x 1.1, 2 the local
// mean); it then cycles as usual
void qr_scan_set_threshold(int threshold);
