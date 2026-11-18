// The signer's scan loop on the host: an animated request code on a screen,
// a rolling-shutter sensor with the frame-based exposure of the firmware
// (io/camera/stm32u5/camera_ae.c) and the firmware's decoder behind its own
// wrapper (io/camera/qr/qr_scan.c), run in the order the scan screen runs
// them (one frame buffer: capture, decode, capture; two: the next frame is
// captured while this one is decoded).
//
// It answers what cannot be read off single saved frames: how fast a
// sequence of parts comes in for a frame size and a buffer count, how many
// frames the exposure takes to settle on a screen and whether it clips, and
// whether tracking and frame summing hold across parts that change.
//
// What is simulated, and so what a number here depends on:
//   - the code: parts "UR:ETH-SIGN-REQUEST/<i>-<n>/<letters>", ECC L, made
//     by the firmware's QR encoder (vendor/QR-Code-generator); the part on
//     the screen changes every `part_ms`;
//   - the picture: the code turned and drifting (a hand: a slow sway of
//     `sway_px` pixels), blurred by `blur_mod` modules (Gaussian sigma) and
//     by its movement during the exposure; the room around the screen dark;
//   - the sensor: rows exposed one after the other over the frame's time
//     (a part that changes mid-frame gives a frame of two parts), output
//     linear in light up to clipping then through a gamma, shot and read
//     noise growing with gain, a change of exposure in force `ae_lag`
//     frames after it is written;
//   - time: the sensor's frame period, a 16 ms screen tick at which the
//     scan screen looks for a finished frame, the host's own decode time
//     multiplied by `cpu` (the device is slower), `paint_ms` for the
//     preview.
// None of these is measured on the board; `cpu`, `paint_ms`, `ae_lag` and
// the blur are assumptions to be replaced by the camera test's readings.
//
//   scansim [key=value ...]   (defaults in opt[] below; scansim help)
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <io/qr_scan.h>

#include "camera_ae.h"
#include "qrcodegen.h"

// --------------------------------------------------------------- options

typedef struct {
  const char *name;
  double v;
  const char *what;
} opt_t;

static opt_t opt[] = {
    {"side", 384, "frame side, pixels"},
    {"buffers", 1, "frame buffers: 1 capture-decode in turn, 2 capture while decoding"},
    {"version", 11, "QR version of the parts (7-13)"},
    {"parts", 12, "distinct parts shown in turn"},
    {"part_ms", 150, "how long the screen shows a part (0: one still code)"},
    {"fill", 0.7, "the code's side as a share of the frame's"},
    {"blur_mod", 0.45, "optical blur, Gaussian sigma in modules"},
    {"sway_px", 6, "hand sway amplitude, frame pixels"},
    {"sway_hz", 1.5, "hand sway frequency"},
    {"angle", 7, "the code's turn, degrees"},
    {"light", 1.0, "screen brightness: 1 is white at the exposure target at 6 ms, 1x gain"},
    {"room", 0.03, "the room around the screen, share of the screen's white"},
    {"gamma", 0.6, "sensor output gamma (1: linear)"},
    {"noise", 1.0, "noise scale (1: about 38 dB at full signal, 1x gain)"},
    {"frame_ms", 40, "sensor frame period"},
    {"line_us", 59.5, "one exposure line"},
    {"ae_lag", 1, "sensor frames before a written exposure is in force"},
    {"ae", 1, "exposure: 1 from the frames, on the code's area; 2 the same, centre only; 0 fixed"},
    {"exp_us", 3000, "the fixed exposure (ae=0)"},
    {"gain16", 16, "the fixed gain in 1/16 (ae=0)"},
    {"cpu", 25, "device decode time over the host's"},
    {"paint_ms", 12, "preview paint"},
    {"seconds", 12, "how long to run"},
    {"seed", 1, "random seed"},
    {"verbose", 0, "1: a line per frame"},
    {"dump", 0, "n: write frame n as scansim_frame.pgm"},
};
#define N_OPT ((int)(sizeof(opt) / sizeof(opt[0])))

static double O(const char *name) {
  for (int i = 0; i < N_OPT; i++) {
    if (strcmp(opt[i].name, name) == 0) {
      return opt[i].v;
    }
  }
  fprintf(stderr, "scansim: no option %s\n", name);
  exit(2);
}

// ---------------------------------------------------------------- random

static uint64_t g_rng = 88172645463325252ULL;
static uint32_t rnd(void) {
  g_rng ^= g_rng << 13;
  g_rng ^= g_rng >> 7;
  g_rng ^= g_rng << 17;
  return (uint32_t)(g_rng >> 16);
}
static double rnd01(void) { return (rnd() & 0xFFFFFF) / 16777216.0; }
static double gauss(void) {
  double u = rnd01() + 1e-9, v = rnd01();
  return sqrt(-2 * log(u)) * cos(6.283185307 * v);
}

// ----------------------------------------------------------------- codes

#define MAX_PARTS 64
#define MAX_DIM 73
static uint8_t g_mod[MAX_PARTS][MAX_DIM * MAX_DIM];  // 1: dark
static char g_text[MAX_PARTS][800];
static int g_dim, g_parts;

// Alphanumeric capacity at ECC L, by version
static const int ALNUM_L[14] = {0, 25, 47, 77, 114, 154, 195, 224, 279, 335, 395, 468, 535, 619};

static void make_parts(int version, int parts) {
  static uint8_t tmp[qrcodegen_BUFFER_LEN_MAX], out[qrcodegen_BUFFER_LEN_MAX];
  g_parts = parts;
  for (int p = 0; p < parts; p++) {
    int n = snprintf(g_text[p], sizeof(g_text[p]), "UR:ETH-SIGN-REQUEST/%d-%d/", p + 1, parts);
    while (n < ALNUM_L[version] - 2) {
      g_text[p][n++] = (char)('A' + rnd() % 26);
    }
    g_text[p][n] = '\0';
    if (!qrcodegen_encodeText(g_text[p], tmp, out, qrcodegen_Ecc_LOW, version, version,
                              qrcodegen_Mask_AUTO, false)) {
      fprintf(stderr, "scansim: part %d does not fit version %d\n", p, version);
      exit(2);
    }
    g_dim = qrcodegen_getSize(out);
    for (int y = 0; y < g_dim; y++) {
      for (int x = 0; x < g_dim; x++) {
        g_mod[p][y * g_dim + x] = qrcodegen_getModule(out, x, y);
      }
    }
  }
}

// ---------------------------------------------------------------- render

#define QUIET 2  // modules of white around the code, as the app draws it

// The scene's light (screen white = 1) at frame position (x, y) for part
// `p`, the code's centre at (cx, cy), `ppm` pixels a module, turned by
// (ca, sa)
static inline float scene(int p, float x, float y, float cx, float cy, float ppm, float ca,
                          float sa, float room) {
  float dx = (x - cx) / ppm, dy = (y - cy) / ppm;
  float mx = dx * ca + dy * sa + g_dim * 0.5f, my = -dx * sa + dy * ca + g_dim * 0.5f;
  if (mx < -QUIET || my < -QUIET || mx >= g_dim + QUIET || my >= g_dim + QUIET) {
    return room;
  }
  if (mx < 0 || my < 0 || mx >= g_dim || my >= g_dim) {
    return 1.0f;
  }
  return g_mod[p][(int)my * g_dim + (int)mx] ? 0.05f : 1.0f;
}

static void blur_rows(float *img, float *tmp, int w, int h, float sigma) {
  int r = (int)ceilf(sigma * 2.5f);
  if (r < 1) {
    return;
  }
  float k[64], sum = 0;
  if (r > 31) {
    r = 31;
  }
  for (int i = -r; i <= r; i++) {
    k[i + r] = expf(-(float)(i * i) / (2 * sigma * sigma));
    sum += k[i + r];
  }
  for (int i = 0; i <= 2 * r; i++) {
    k[i] /= sum;
  }
  for (int pass = 0; pass < 2; pass++) {
    for (int y = 0; y < h; y++) {
      for (int x = 0; x < w; x++) {
        float a = 0;
        for (int i = -r; i <= r; i++) {
          int xx = pass == 0 ? x + i : x, yy = pass == 0 ? y : y + i;
          xx = xx < 0 ? 0 : xx >= w ? w - 1 : xx;
          yy = yy < 0 ? 0 : yy >= h ? h - 1 : yy;
          a += k[i + r] * img[yy * w + xx];
        }
        tmp[y * w + x] = a;
      }
    }
    memcpy(img, tmp, sizeof(float) * (size_t)w * (size_t)h);
  }
}

// The code's centre at time t (ms): a slow sway about the frame's centre
static void centre_at(double t_ms, int side, float *cx, float *cy) {
  double a = O("sway_px"), f = O("sway_hz") * 6.283185307 / 1000.0;
  *cx = (float)(side / 2.0 + a * sin(f * t_ms) + 0.3);
  *cy = (float)(side / 2.0 + a * 0.6 * sin(f * 1.3 * t_ms + 1.0) + 0.6);
}

// One frame starting at `t0_ms`, exposed `exp_us` at gain `gain16`
static void render(uint8_t *out, int side, double t0_ms, double exp_us, double gain16) {
  static float *lum, *tmp;
  if (lum == NULL) {
    lum = malloc(sizeof(float) * 400 * 400);
    tmp = malloc(sizeof(float) * 400 * 400);
  }
  float ppm = (float)(O("fill") * side / (g_dim + 2 * QUIET));
  float ang = (float)(O("angle") * 3.14159265 / 180.0), ca = cosf(ang), sa = sinf(ang);
  float room = (float)O("room");
  double part_ms = O("part_ms"), row_ms = O("frame_ms") / (side * 1.75);  // the window's rows of the frame
  for (int y = 0; y < side; y++) {
    double t = t0_ms + y * row_ms;
    int part = part_ms > 0 ? (int)(t / part_ms) % g_parts : 0;
    // Movement during the exposure: 4 moments of it averaged
    float cxs[4], cys[4];
    for (int k = 0; k < 4; k++) {
      centre_at(t + exp_us / 1000.0 * (k + 0.5) / 4.0, side, &cxs[k], &cys[k]);
    }
    for (int x = 0; x < side; x++) {
      float a = 0;
      for (int k = 0; k < 4; k++) {
        for (int s = 0; s < 4; s++) {
          float fx = (float)x + 0.25f + 0.5f * (float)(s & 1), fy = (float)y + 0.25f + 0.5f * (float)(s >> 1);
          a += scene(part, fx, fy, cxs[k], cys[k], ppm, ca, sa, room);
        }
      }
      lum[y * side + x] = a / 16.0f;
    }
  }
  blur_rows(lum, tmp, side, side, (float)(O("blur_mod") * ppm));
  // light 1: white reaches AE_TARGET's signal at 6 ms and 1x gain
  double gamma = O("gamma"), g = gain16 / 16.0, nz = O("noise");
  double white_lin = pow(AE_TARGET / 255.0, 1.0 / gamma);
  double k = O("light") * white_lin * (exp_us / 6000.0) * g;
  for (int i = 0; i < side * side; i++) {
    double lin = lum[i] * k;  // 1.0: full well x gain
    double sd = nz * (0.0125 * sqrt(fabs(lin) * g) + 0.004 * g);
    lin += gauss() * sd;
    lin = lin < 0 ? 0 : lin > 1 ? 1 : lin;
    double v = pow(lin, gamma) * 255.0 + 0.5;
    out[i] = (uint8_t)(v > 255 ? 255 : v);
  }
}

// ------------------------------------------------------------------ main

static double host_ms(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (double)t.tv_sec * 1e3 + (double)t.tv_nsec / 1e6;
}

// The decoder's clock (its search budget): the host's, running `cpu` times
// as fast, so a search is cut where the device would cut it
static double g_cpu = 1, g_t0;
uint32_t scansim_ms(void) { return (uint32_t)(uint64_t)((host_ms() - g_t0) * g_cpu); }

int main(int argc, char **argv) {
  for (int a = 1; a < argc; a++) {
    char *eq = strchr(argv[a], '=');
    int found = 0;
    for (int i = 0; eq != NULL && i < N_OPT; i++) {
      if (strlen(opt[i].name) == (size_t)(eq - argv[a]) && strncmp(opt[i].name, argv[a], (size_t)(eq - argv[a])) == 0) {
        opt[i].v = atof(eq + 1);
        found = 1;
      }
    }
    if (!found) {
      fprintf(stderr, "usage: scansim [key=value ...]\n");
      for (int i = 0; i < N_OPT; i++) {
        fprintf(stderr, "  %-9s %-6g %s\n", opt[i].name, opt[i].v, opt[i].what);
      }
      return 2;
    }
  }
  g_rng ^= (uint64_t)O("seed") * 0x9E3779B97F4A7C15ULL;
  int side = (int)O("side"), buffers = (int)O("buffers"), verbose = (int)O("verbose");
  if (side < 160 || side > 400 || (buffers != 1 && buffers != 2)) {
    fprintf(stderr, "scansim: side 160-400, buffers 1 or 2\n");
    return 2;
  }
  make_parts((int)O("version"), (int)O("parts") > MAX_PARTS ? MAX_PARTS : (int)O("parts"));

  size_t state_len = qr_scan_state_size(side, side);
  uint8_t *state = aligned_alloc(16, (state_len + 15) & ~(size_t)15);
  uint8_t *buf[2] = {malloc((size_t)side * side), malloc((size_t)side * side)};
  if (state == NULL || buf[0] == NULL || buf[1] == NULL || !qr_scan_init(side, side, state, state_len)) {
    fprintf(stderr, "scansim: decoder setup failed\n");
    return 1;
  }

  // The sensor's exposure: what the driver last wrote, and what is in force
  // (a write takes ae_lag sensor frames)
  int ae_mode = (int)O("ae");
  camera_ae_t ae;
  double line_ns = O("line_us") * 1000.0, frame_ms = O("frame_ms");
  camera_ae_start(&ae, (uint32_t)line_ns, (uint32_t)(frame_ms * 1e6 / line_ns));
  if (ae_mode == 0) {
    ae.lines = (uint32_t)(O("exp_us") * 1000.0 / line_ns);
    ae.gain16 = (uint32_t)O("gain16");
  }
  struct {
    double at_ms;
    uint32_t lines, gain16;
  } pend[8];
  int npend = 0;
  uint32_t cur_lines = ae.lines, cur_gain = ae.gain16;

  double tick = 16.0, now = 0, end = O("seconds") * 1000.0, cpu = O("cpu"), paint = O("paint_ms");
  g_cpu = cpu;
  g_t0 = host_ms();
  int have[MAX_PARTS] = {0}, got = 0, frames = 0, decoded = 0, tracked = 0, fusedn = 0, wrong = 0;
  int clipped_frames = 0, settle_frame = -1, cuts = 0, reads = 0, last_part = -1;
  double first_all = -1, last_done = 0, decode_sum = 0, last_frame_end = 0, busy_until = 0;
  int cur = 0;  // the buffer being captured into
  static uint8_t payload[2048];

  while (now < end) {
    // The capture asked for at `now` takes the sensor's next whole frame
    double start = ceil(now / frame_ms) * frame_ms;
    while (npend > 0 && pend[0].at_ms <= start) {
      cur_lines = pend[0].lines;
      cur_gain = pend[0].gain16;
      memmove(pend, pend + 1, sizeof(pend[0]) * (size_t)--npend);
    }
    double exp_us = cur_lines * line_ns / 1000.0;
    render(buf[cur], side, start, exp_us, cur_gain);
    if ((int)O("dump") == frames + 1) {
      FILE *f = fopen("scansim_frame.pgm", "wb");
      if (f == NULL) {
        perror("scansim_frame.pgm");
      } else {
        fprintf(f, "P5 %d %d 255\n", side, side);
        fwrite(buf[cur], 1, (size_t)side * side, f);
        fclose(f);
      }
    }
    // The frame is whole at the sensor's next frame start; the scan screen
    // sees it at its next tick, once it is done with the frame before
    double done = ceil((start + frame_ms) / tick) * tick;
    if (done < busy_until) {
      done = ceil(busy_until / tick) * tick;
    }
    frames++;

    // Kernel, as the capture is finished: exposure from this frame
    if (ae_mode != 0) {
      if (ae_mode == 2) {
        camera_ae_set_box(&ae, NULL);
      }
      if (camera_ae_frame(&ae, buf[cur], (uint32_t)side, (uint32_t)side, (uint32_t)(done - last_frame_end)) && npend < 8) {
        pend[npend].at_ms = done + O("ae_lag") * frame_ms;
        pend[npend].lines = ae.lines;
        pend[npend].gain16 = ae.gain16;
        npend++;
      }
    }
    last_frame_end = done;

    // App: with two buffers the next capture starts now, into the other
    double next_capture = done;
    uint8_t *frame = buf[cur];
    if (buffers == 2) {
      cur ^= 1;
    }
    qr_scan_result_t r;
    double h0 = host_ms();
    qr_scan(frame, payload, sizeof(payload) - 1, &r);
    double dec_ms = (host_ms() - h0) * cpu;
    decode_sum += dec_ms;
    int32_t box[4];
    camera_ae_set_box(&ae, qr_scan_meter_area(box) ? box : NULL);
    cuts += r.cut;
    double after = done + dec_ms + paint;
    int part = -1;
    if (r.decoded) {
      decoded++;
      tracked += r.tracked;
      fusedn += r.fused > 1;
      payload[r.payload_len < (int)sizeof(payload) - 1 ? r.payload_len : (int)sizeof(payload) - 1] = 0;
      for (int p = 0; p < g_parts; p++) {
        if (strcmp((const char *)payload, g_text[p]) == 0) {
          part = p;
        }
      }
      // A part read: a decode of another part than the last one read (the
      // UR assembler takes any parts; repeats of one bring nothing)
      if (part >= 0 && part != last_part) {
        reads++;
        last_part = part;
      }
      if (part < 0) {
        wrong++;
      } else if (!have[part]) {
        have[part] = 1;
        got++;
        if (got == g_parts && first_all < 0) {
          first_all = after;
        }
      }
    }
    clipped_frames += r.clipped_pm > 100;
    if (settle_frame < 0 && ae.p98 >= 170 && ae.p98 < AE_CLIPPED) {
      settle_frame = frames;
    }
    if (verbose) {
      printf("%7.0f ms frame %3d exp %5.0f us gain %4.1f cap %5u us p98 %3u%s mean %3d clip %3d edge %2d.%d  %s%s f%d %4.1f px/m part %d  decode %.0f ms\n",
             start, frames, exp_us, cur_gain / 16.0, (unsigned)ae.cap_us, (unsigned)ae.p98, ae.on_code ? "c" : " ",
             (int)r.mean_y, (int)r.clipped_pm, (int)(r.edge_x10 / 10), (int)(r.edge_x10 % 10),
             r.decoded ? "OK" : r.grids ? "grid" : "--", r.tracked ? " tracked" : "", (int)r.fused,
             r.px_per_module_x10 / 10.0, part, dec_ms);
    }
    // One buffer: the next capture starts at the tick after the paint. Two:
    // it started when this frame was seen, and is looked at once this one
    // is dealt with.
    now = buffers == 1 ? ceil(after / tick) * tick : next_capture;
    busy_until = after;
    last_done = after;
  }

  double secs = last_done / 1000.0;
  printf("side %d buffers %d v%d %.1f px/module, parts %d every %g ms, blur %.2f mod, light %g: "
         "%.1f fps, %d/%d frames decoded (%d tracked, %d from summed frames, %d wrong), "
         "%.1f parts read a second, %d/%d parts, all after %.1f s, decode %.0f ms avg, exposure settled at frame %d, "
         "%d frames clipped, final %u us x%.1f, %d cut\n",
         side, buffers, (int)O("version"), O("fill") * side / (g_dim + 2 * QUIET), g_parts, O("part_ms"),
         O("blur_mod"), O("light"), frames / secs, decoded, frames, tracked, fusedn, wrong, reads / secs, got, g_parts,
         first_all / 1000.0, decode_sum / frames, settle_frame, clipped_frames,
         (unsigned)(ae.lines * line_ns / 1000.0), ae.gain16 / 16.0, cuts);
  free(state);
  free(buf[0]);
  free(buf[1]);
  return wrong ? 1 : 0;
}
