/*
 * Ambire boards, [ambire_camera]: QR code decoding of grayscale camera frames
 * (the video decoder, io/camera/qr/fqr.h), app side. No heap: the frames and
 * the decoder's state live in buffers the caller owns. The decoder reads a
 * frame where the camera captured it and does not modify it, so the camera
 * can capture the next frame into another buffer meanwhile.
 * Frames are taken as a video of one scene: the decoder looks for the code
 * where the last frame had it, and adds up frames of one code that do not
 * read alone (a new code, as the next part of an animated one, starts the
 * sum again by itself). Mirrored codes are not read.
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
  // 1: the search for a code stopped at its time budget (a frame of clutter)
  int32_t cut;
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

// The longest a frame with no code where the last one was is searched for
// one: the screen does not answer a touch meanwhile, and the next frame
// waits. On the host a search of a frame with a code takes 1.3 ms and one of
// clutter up to 30 ms; the device is taken to be 30 to 60 times slower (not
// measured: the camera test shows the decode time, and "cut" when a search
// ended here).
#define QR_SCAN_BUDGET_MS 150

// Bytes of decoder state qr_scan_init() needs for w x h frames
size_t qr_scan_state_size(int w, int h);

// Builds the decoder for w x h frames, its state in `state` (8-byte aligned,
// qr_scan_state_size() bytes). False if the buffer is misaligned or too
// small.
bool qr_scan_init(int w, int h, uint8_t *state, size_t size);

// Looks for a QR code in the frame at `image` (w x h bytes, rows top to
// bottom; left as it is) and decodes it to `payload` (at most `payload_max`
// bytes). Takes the frame as the next one of the video.
void qr_scan(const uint8_t *image, uint8_t *payload, size_t payload_max,
             qr_scan_result_t *result);

// The next frame is unrelated to the last (the camera's mode changed): the
// code is searched for afresh and no earlier frame is added to it.
void qr_scan_reset(void);

// A code's area steers the exposure for this many frames after the decoder
// last made out a code: a blurred frame in between does not send the
// exposure back to the frame's centre
#define QR_SCAN_METER_KEEP 4

// After qr_scan(): the area to meter the light on (x0, y0, x1, y1, frame
// pixels), for camera_set_meter(): where the decoder last made out a code
// (decoded it, or read its format), a module of margin around. False if it
// has made out none for QR_SCAN_METER_KEEP frames: the frame's centre is
// metered.
bool qr_scan_meter_area(int32_t box[4]);
