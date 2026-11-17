// ext_identify.c: quirc fixes that live beside quirc, not in it
#pragma once

struct quirc;

// quirc_end() with the capstone grouping test in an affine frame
void quirc_ext_end(struct quirc *q);

// Sets grid `index` to `grid_size` modules and redoes its perspective
void quirc_ext_regrid(struct quirc *q, int index, int grid_size);
