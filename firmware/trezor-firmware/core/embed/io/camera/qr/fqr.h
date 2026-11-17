/*
 * Ambire boards, [ambire_camera]: a QR decoder for small camera frames of
 * codes at 3-4 pixels a module, read as video.
 *
 * It reads the grey frame directly (the frame is never modified):
 *   - finder patterns from 1:1:3:1:1 runs of a locally thresholded bitmap;
 *   - the grid from grey correlation: the code's size from its timing
 *     patterns, the finder and every alignment pattern located sub-pixel
 *     against their templates, one least-squares homography over all of them
 *     plus the residual each pattern leaves (lens distortion);
 *   - each module as a grey value (5 bilinear samples) against the local
 *     black and white levels: a soft bit, not a thresholded pixel;
 *   - format bits by maximum likelihood, Reed-Solomon with erasures at the
 *     least certain codewords;
 *   - video: the next frame starts from this frame's finder positions (no
 *     search of the frame), and the soft bits of frames showing the same
 *     code add up until it decodes.
 *
 * No heap: all state lives in the caller's buffer. Single-precision float.
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Largest QR version decoded (grid side 17 + 4 x version). The state grows
// with its square: about 5 bytes a module.
#ifndef FQR_MAX_VERSION
#define FQR_MAX_VERSION 20
#endif
#define FQR_MAX_DIM (17 + 4 * FQR_MAX_VERSION)

typedef struct fqr fqr_t;

// fqr_result_t.stage: how far the frame got
#define FQR_STAGE_NONE 0     // fewer than three finder patterns
#define FQR_STAGE_FINDERS 1  // three finder patterns, no grid through them
#define FQR_STAGE_GRID 2     // a grid (timing patterns matched), no format
#define FQR_STAGE_FORMAT 3   // format read, the data did not correct
#define FQR_STAGE_DECODED 4

typedef struct {
  int32_t stage;         // FQR_STAGE_*
  int32_t decoded;       // 1: the payload is valid
  int32_t finders;       // finder pattern candidates in the frame
  int32_t tracked;       // 1: the grid came from the previous frame's
  int32_t fused;         // frames whose soft bits were combined (1: this one)
  int32_t version;       // of the grid (1-40), 0: none
  int32_t ecc_level;     // 0 L, 1 M, 2 Q, 3 H
  int32_t mask;
  int32_t payload_len;   // whole length (may exceed what fits)
  int32_t rs_errors;     // codewords corrected
  int32_t rs_erasures;   // of them, erased as uncertain before correcting
  int32_t aligns;        // alignment patterns located
  int32_t timing_x100;   // timing pattern match, 0-100
  int32_t px_per_module_x10;
  float corners[8];      // the grid's corners (x, y), clockwise from top left
} fqr_result_t;

// Bytes of state for w x h frames
size_t fqr_state_size(int w, int h);

// The decoder in `state` (8-byte aligned, fqr_state_size() bytes); NULL if
// the buffer is too small or misaligned
fqr_t *fqr_init(void *state, size_t size, int w, int h);

// Reads one frame (w x h grey, `stride` bytes a row). The payload of a code
// that decoded goes to `payload` (at most `payload_max` bytes). Frames are
// taken as video: call fqr_reset() when the next frame is unrelated.
bool fqr_scan(fqr_t *s, const uint8_t *gray, int stride, uint8_t *payload,
              size_t payload_max, fqr_result_t *result);

// Forgets the previous frames (tracking and combined soft bits)
void fqr_reset(fqr_t *s);

// After fqr_scan(): the last grid's soft bits, row by row (-127 black ..
// 127 white), and its side in modules (0: none). For evaluation.
const int8_t *fqr_modules(const fqr_t *s, int *dim);

// The area the last frame's code is in (x0, y0, x1, y1), a module of margin
// around; false when the last frame had no grid
bool fqr_code_area(const fqr_t *s, int32_t box[4]);
