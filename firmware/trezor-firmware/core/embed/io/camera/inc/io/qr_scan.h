/*
 * Ambire boards, [ambire_camera]: QR code decoding of grayscale camera frames
 * (quirc), app side. No heap: the decoder, its image included, lives in a
 * buffer the caller owns. The camera captures straight into that image.
 */

#pragma once

#include <trezor_types.h>

typedef struct {
  int32_t grids;         // QR codes found in the image (decoded or not)
  int32_t decoded;       // 1: `payload` holds the first one that decoded
  int32_t error;         // quirc_decode_error_t of the last failed decode
  int32_t version;       // QR version of the decoded code
  int32_t mirrored;      // 1: it decoded only mirrored
  int32_t payload_len;   // its whole payload length (may exceed what fits)
  int32_t corners[8];    // x, y of its four corners, clockwise from top left
  uint32_t identify_ms;  // time spent finding codes
  uint32_t decode_ms;    // time spent decoding them
} qr_scan_result_t;

// Bytes of arena qr_scan_init() needs for w x h images
size_t qr_scan_arena_size(int w, int h);

// Builds the decoder in `arena` (8-byte aligned) for w x h images. False if
// the arena is too small.
bool qr_scan_init(uint8_t *arena, size_t size, int w, int h);

// The w x h grayscale image the decoder reads, inside the arena (8-byte
// aligned); NULL before qr_scan_init()
uint8_t *qr_scan_image(void);

// Looks for QR codes in the image and decodes them; the first that decodes
// (as is, or mirrored) is copied to `payload` (at most `payload_max` bytes).
// The image is used up: it is thresholded in place.
void qr_scan(uint8_t *payload, size_t payload_max, qr_scan_result_t *result);
