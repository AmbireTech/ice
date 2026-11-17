// Ambire boards, [ambire_camera]: our additions to quirc (quirc_decode.c,
// quirc_identify.c, quirc/README.ambire)

#pragma once

#include <stddef.h>
#include <stdint.h>

struct quirc;

// Bytes quirc_decode() and quirc_flip() need for their work structures
size_t quirc_ambire_scratch_size(void);

// `mem`: quirc_ambire_scratch_size() bytes, 4-byte aligned, kept for as long
// as quirc decodes
void quirc_ambire_set_scratch(void *mem);

// quirc_ambire_end()'s thresholds: quirc's own Otsu, Otsu x 1.1, the local
// box mean (radius w/64; it needs quirc_ambire_local_size() bytes of work
// memory, 2-byte aligned)
#define QUIRC_AMBIRE_THRESHOLD_OTSU 0
#define QUIRC_AMBIRE_THRESHOLD_OTSU_HIGH 1
#define QUIRC_AMBIRE_THRESHOLD_LOCAL 2
#define QUIRC_AMBIRE_THRESHOLDS 3

size_t quirc_ambire_local_size(int w);

// quirc_end() with the chosen threshold and the capstone grouping in an
// affine frame (quirc_identify.c says why). quirc_begin() first, as before
// quirc_end().
void quirc_ambire_end(struct quirc *q, int threshold, uint8_t *local_mem);

// Sets grid `index` to `grid_size` modules and redoes its perspective
void quirc_ambire_regrid(struct quirc *q, int index, int grid_size);

// Grid `index` read again (quirc_extract()'s output), the sampling points
// moved by (du, dv) modules. With `gray` (the frame before thresholding,
// q->w x q->h) each cell is read from it as BoofCV does: bilinear samples at
// the cell centre and 0.2 module either way on each axis, each against the
// mean grey of the cell's surroundings, by majority; without, from the
// thresholded image as quirc does, the offset alone (Krux's retry).
void quirc_ambire_extract(const struct quirc *q, int index, struct quirc_code *code,
                          const uint8_t *gray, float du, float dv);
