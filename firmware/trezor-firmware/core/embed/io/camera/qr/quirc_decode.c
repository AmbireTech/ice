// Ambire boards, [ambire_camera]: quirc's decode.c, built with the firmware's
// warnings, less those its code trips (see quirc/README.ambire). Its two big
// work structures live in the caller's arena (quirc_ambire_set_scratch()).
#include "quirc_config.h"
#pragma GCC diagnostic ignored "-Wfloat-conversion"
#pragma GCC diagnostic ignored "-Wdouble-promotion"
#pragma GCC diagnostic ignored "-Wsign-compare"

#include <stddef.h>

#include "quirc/quirc.h"

struct datastream;
static struct datastream *ambire_ds;
static struct quirc_code *ambire_flipped;

#include "quirc/decode.c"

#include "quirc_ambire.h"

// quirc_decode()'s datastream, then quirc_flip()'s code
size_t quirc_ambire_scratch_size(void) {
  return sizeof(struct datastream) + sizeof(struct quirc_code);
}

// `mem`: quirc_ambire_scratch_size() bytes, 4-byte aligned
void quirc_ambire_set_scratch(void *mem) {
  ambire_ds = mem;
  ambire_flipped = (struct quirc_code *)((uint8_t *)mem + sizeof(struct datastream));
}
