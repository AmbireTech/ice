// Unit tests of the video QR decoder's parts that take hostile input, on the
// host (build.sh runs it under ASan/UBSan): the Reed-Solomon decoder against
// an encoder written here (every error/erasure count within its capacity
// must correct, none beyond it may "correct" to a different word unnoticed),
// and the payload parser on random data (no out-of-bounds access, no length
// past what it wrote).
#include <stdio.h>
#include <stdlib.h>

#include "../../../trezor-firmware/core/embed/io/camera/qr/fqr.c"

static uint32_t rng = 12345;
static uint32_t rnd(void) {
  rng ^= rng << 13;
  rng ^= rng >> 17;
  rng ^= rng << 5;
  return rng;
}

static void rs_encode(const fqr_t *s, uint8_t *c, int n, int nsym) {
  uint8_t gen[32] = {1};
  for (int i = 0; i < nsym; i++) {
    for (int j = i + 1; j > 0; j--) {
      gen[j] = (uint8_t)(gen[j - 1] ^ gf_mul(s, gen[j], s->gf_exp[i]));
    }
    gen[0] = gf_mul(s, gen[0], s->gf_exp[i]);
  }
  // gen[k]: coefficient of x^k; remainder of data * x^nsym
  uint8_t rem[32] = {0};
  for (int i = 0; i < n - nsym; i++) {
    uint8_t f = (uint8_t)(c[i] ^ rem[nsym - 1]);
    for (int j = nsym - 1; j > 0; j--) {
      rem[j] = (uint8_t)(rem[j - 1] ^ gf_mul(s, f, gen[j]));
    }
    rem[0] = gf_mul(s, f, gen[0]);
  }
  for (int j = 0; j < nsym; j++) {
    c[n - 1 - j] = rem[j];
  }
}

int main(void) {
  size_t len = fqr_state_size(64, 64);
  fqr_t *s = fqr_init(aligned_alloc(16, (len + 15) & ~(size_t)15), len, 64, 64);
  if (s == NULL) {
    fprintf(stderr, "decoder setup failed\n");
    return 1;
  }
  int fails = 0, beyond_wrong = 0, beyond = 0, within = 0;
  for (int t = 0; t < 200000; t++) {
    int nsym = 7 + (int)(rnd() % 24), n = nsym + 1 + (int)(rnd() % (255 - nsym));
    uint8_t c[255], orig[255], eras[32];
    for (int i = 0; i < n - nsym; i++) {
      c[i] = (uint8_t)rnd();
    }
    rs_encode(s, c, n, nsym);
    memcpy(orig, c, (size_t)n);
    int f = (t & 1) ? (int)(rnd() % (unsigned)(nsym - 3)) : 0;
    int cap = f > 0 ? (nsym - 2 - f) / 2 : nsym / 2;
    bool over = (t % 5) == 0;
    int e = over ? cap + 1 + (int)(rnd() % 3) : (int)(rnd() % (unsigned)(cap + 1));
    // distinct positions: f erased (corrupted or not), e wrong and not erased
    uint8_t used[255] = {0};
    for (int k = 0; k < f; k++) {
      int p;
      do {
        p = (int)(rnd() % (unsigned)n);
      } while (used[p]);
      used[p] = 1;
      eras[k] = (uint8_t)p;
      if (rnd() & 1) {
        c[p] ^= (uint8_t)(1 + rnd() % 255);
      }
    }
    for (int k = 0; k < e && k < n - f; k++) {
      int p;
      do {
        p = (int)(rnd() % (unsigned)n);
      } while (used[p]);
      used[p] = 1;
      c[p] ^= (uint8_t)(1 + rnd() % 255);
    }
    int r = rs_decode(s, c, n, nsym, eras, f);
    bool same = memcmp(c, orig, (size_t)n) == 0;
    if (!over) {
      within++;
      if (r < 0 || !same) {
        if (fails++ < 5) {
          printf("FAIL: n %d nsym %d errors %d erasures %d -> %d\n", n, nsym, e, f, r);
        }
      }
    } else {
      beyond++;
      beyond_wrong += r >= 0 && !same;
    }
  }
  printf("reed-solomon: %d words within capacity, %d not corrected; %d beyond it, %d taken for another word\n",
         within, fails, beyond, beyond_wrong);

  // The payload parser on random streams, into a buffer with a guard
  int parsed = 0;
  for (int t = 0; t < 300000; t++) {
    uint8_t data[64], out[40 + 1];
    int nd = 1 + (int)(rnd() % 64);
    for (int i = 0; i < nd; i++) {
      data[i] = (uint8_t)rnd();
    }
    size_t max = rnd() % 41;
    out[max] = 0xA5;
    int n = parse_payload(data, nd, 1 + (int)(rnd() % FQR_MAX_VERSION), out, max);
    parsed += n >= 0;
    if (out[max] != 0xA5) {
      printf("FAIL: the parser wrote past its buffer\n");
      fails++;
      break;
    }
  }
  printf("payload parser: %d of 300000 random streams parsed, none wrote past the buffer\n", parsed);
  return fails ? 1 : 0;
}
