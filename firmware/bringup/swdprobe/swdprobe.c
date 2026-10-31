// swdprobe: read an SWD target's DP IDCODE by bit-banging two GPIOs of a
// Linux board (libgpiod v2), at several clock speeds, to tell a timing
// problem (clean when slower) from an electrical one (garbled at any speed).
//
//   cc -O2 -o swdprobe swdprobe.c -lgpiod
//   ./swdprobe [SWCLK_LINE_NAME] [SWDIO_LINE_NAME] [reads_per_speed]
//   default: GPIO25 GPIO24 20 (the Raspberry Pi header names, also on the
//   BeagleY-AI)
//
// Prints, per half-period delay, how many reads returned each (ACK, IDCODE).
// The STM32U5's correct reply is ACK 1 (OK), IDCODE 0x0be12477. Both lines
// are left as inputs at exit (no back-feeding the target).

#include <errno.h>
#include <gpiod.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct {
  struct gpiod_chip *chip;
  struct gpiod_line_request *req;
  unsigned int offset;
} line_t;

static long g_half_ns;

static void die(const char *what) {
  fprintf(stderr, "swdprobe: %s: %s\n", what, strerror(errno));
  exit(1);
}

static void delay_half(void) {
  if (g_half_ns <= 0) {
    return;
  }
  struct timespec t0, t;
  clock_gettime(CLOCK_MONOTONIC, &t0);
  do {  // busy wait: nanosleep's granularity is far coarser than 2 us
    clock_gettime(CLOCK_MONOTONIC, &t);
  } while ((t.tv_sec - t0.tv_sec) * 1000000000L + (t.tv_nsec - t0.tv_nsec) <
           g_half_ns);
}

static struct gpiod_line_config *config(unsigned int offset, int output,
                                        int value) {
  struct gpiod_line_settings *s = gpiod_line_settings_new();
  if (!s) die("line settings");
  gpiod_line_settings_set_direction(
      s, output ? GPIOD_LINE_DIRECTION_OUTPUT : GPIOD_LINE_DIRECTION_INPUT);
  if (output) {
    gpiod_line_settings_set_output_value(
        s, value ? GPIOD_LINE_VALUE_ACTIVE : GPIOD_LINE_VALUE_INACTIVE);
  }
  struct gpiod_line_config *lc = gpiod_line_config_new();
  if (!lc) die("line config");
  if (gpiod_line_config_add_line_settings(lc, &offset, 1, s) < 0)
    die("add line settings");
  gpiod_line_settings_free(s);
  return lc;
}

static line_t open_line(const char *name, int value) {
  for (int n = 0; n < 16; n++) {
    char path[32];
    snprintf(path, sizeof(path), "/dev/gpiochip%d", n);
    struct gpiod_chip *chip = gpiod_chip_open(path);
    if (!chip) continue;  // no such chip, or not ours to open: try the next
    int off = gpiod_chip_get_line_offset_from_name(chip, name);
    if (off < 0) {
      gpiod_chip_close(chip);
      continue;
    }
    struct gpiod_request_config *rc = gpiod_request_config_new();
    if (!rc) die("request config");
    gpiod_request_config_set_consumer(rc, "swdprobe");
    struct gpiod_line_config *lc = config((unsigned)off, 1, value);
    struct gpiod_line_request *req = gpiod_chip_request_lines(chip, rc, lc);
    if (!req) die(name);
    gpiod_line_config_free(lc);
    gpiod_request_config_free(rc);
    printf("%s: %s line %d\n", name, path, off);
    return (line_t){chip, req, (unsigned)off};
  }
  fprintf(stderr, "swdprobe: no GPIO line named %s\n", name);
  exit(1);
}

static void set_dir(line_t *l, int output, int value) {
  struct gpiod_line_config *lc = config(l->offset, output, value);
  if (gpiod_line_request_reconfigure_lines(l->req, lc) < 0) die("reconfigure");
  gpiod_line_config_free(lc);
}

static void put(line_t *l, int v) {
  if (gpiod_line_request_set_value(
          l->req, l->offset,
          v ? GPIOD_LINE_VALUE_ACTIVE : GPIOD_LINE_VALUE_INACTIVE) < 0)
    die("set value");
}

static int get(line_t *l) {
  int v = gpiod_line_request_get_value(l->req, l->offset);
  if (v < 0) die("get value");
  return v == GPIOD_LINE_VALUE_ACTIVE;
}

static line_t clk, dio;

// Host writes: data valid before the rising edge (the target samples there)
static void wbit(int b) {
  put(&dio, b);
  put(&clk, 0);
  delay_half();
  put(&clk, 1);
  delay_half();
}

// Host reads: with the clock low, after the target changed it on the last
// rising edge (OpenOCD's bitbang reads the same way)
static int rbit(void) {
  put(&clk, 0);
  delay_half();
  int b = get(&dio);
  put(&clk, 1);
  delay_half();
  return b;
}

static void wbits(uint32_t v, int n) {
  for (int i = 0; i < n; i++) wbit((v >> i) & 1);
}

// Line reset, JTAG-to-SWD, line reset, idle, then read DPIDR (request 0xA5).
static int read_idcode(uint32_t *idcode, int *parity_ok) {
  set_dir(&dio, 1, 1);
  for (int i = 0; i < 60; i++) wbit(1);
  wbits(0xE79E, 16);
  for (int i = 0; i < 60; i++) wbit(1);
  wbits(0, 4);
  wbits(0xA5, 8);
  set_dir(&dio, 0, 0);
  rbit();  // turnaround
  int ack = rbit() | (rbit() << 1) | (rbit() << 2);
  uint32_t v = 0;
  int ones = 0;
  for (int i = 0; i < 32; i++) {
    int b = rbit();
    v |= (uint32_t)b << i;
    ones += b;
  }
  int par = rbit();
  rbit();  // turnaround
  set_dir(&dio, 1, 0);
  wbits(0, 8);
  *idcode = v;
  *parity_ok = ((ones + par) & 1) == 0;
  return ack;
}

int main(int argc, char **argv) {
  const char *clk_name = argc > 1 ? argv[1] : "GPIO25";
  const char *dio_name = argc > 2 ? argv[2] : "GPIO24";
  int reads = argc > 3 ? atoi(argv[3]) : 20;
  static const long delays_ns[] = {0, 2000, 20000, 200000};

  clk = open_line(clk_name, 1);
  dio = open_line(dio_name, 1);

  for (size_t d = 0; d < sizeof(delays_ns) / sizeof(delays_ns[0]); d++) {
    g_half_ns = delays_ns[d];
    struct {
      int ack;
      uint32_t id;
      int par;
      int n;
    } seen[64];
    int nseen = 0, good = 0;
    for (int r = 0; r < reads; r++) {
      uint32_t id;
      int par;
      int ack = read_idcode(&id, &par);
      good += (ack == 1 && id == 0x0be12477 && par);
      int k;
      for (k = 0; k < nseen; k++)
        if (seen[k].ack == ack && seen[k].id == id && seen[k].par == par) break;
      if (k == nseen && nseen < 64) seen[nseen++] = (typeof(seen[0])){ack, id, par, 0};
      if (k < 64) seen[k].n++;
    }
    printf("half period %6ld ns: %d/%d correct\n", delays_ns[d], good, reads);
    for (int k = 0; k < nseen; k++)
      printf("    %3dx  ack %d  idcode 0x%08x  parity %s\n", seen[k].n,
             seen[k].ack, seen[k].id, seen[k].par ? "ok" : "BAD");
  }

  // leave both lines as inputs: never drive the target between sessions
  set_dir(&clk, 0, 0);
  set_dir(&dio, 0, 0);
  gpiod_line_request_release(clk.req);
  gpiod_line_request_release(dio.req);
  gpiod_chip_close(clk.chip);
  gpiod_chip_close(dio.chip);
  return 0;
}
