// Ambire boards, [ambire_camera]: where quirc_decode() and quirc_flip() keep
// their work structures (quirc_decode.c, quirc/README.ambire)

#pragma once

#include <stddef.h>

// Bytes they need
size_t quirc_ambire_scratch_size(void);

// `mem`: quirc_ambire_scratch_size() bytes, 4-byte aligned, kept for as long
// as quirc decodes
void quirc_ambire_set_scratch(void *mem);
