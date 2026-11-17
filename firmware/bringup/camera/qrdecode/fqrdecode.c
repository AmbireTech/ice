// The video QR decoder (io/camera/qr/fqr.c) on the host. Reads binary PGM
// (P5) files and prints one line per file:
//   FILE  OK|NO  stage v<version> <px/module> finders aligns timing fused errors/erasures ms  <payload head>
// By default every file is a frame of its own; FQR_VIDEO=1 takes the files as
// consecutive frames (tracking, soft bits summed). FQR_DUMP=<dir> writes each
// frame's soft bits as <dir>/<name>.soft (text, one row of modules a line).
// FQR_OPT="i=v,..." sets tuning switches (FQR_HOST_TUNE builds).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "fqr.h"

extern int fqr_opt[16];
extern uint32_t fqr_prof[8], fqr_hits;
uint32_t fqr_prof_us(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (uint32_t)(t.tv_sec * 1000000 + t.tv_nsec / 1000);
}
void fqr_debug_point(const fqr_t *s, float x, float y, float *px, float *py);

static unsigned char *read_pgm(const char *path, int *w, int *h) {
  FILE *f = fopen(path, "rb");
  if (f == NULL) {
    perror(path);
    return NULL;
  }
  int maxv = 0;
  if (fscanf(f, "P5 %d %d %d", w, h, &maxv) != 3 || maxv != 255 || fgetc(f) == EOF) {
    fprintf(stderr, "%s: not an 8-bit binary PGM\n", path);
    fclose(f);
    return NULL;
  }
  size_t n = (size_t)*w * (size_t)*h;
  unsigned char *px = malloc(n);
  if (px == NULL || fread(px, 1, n, f) != n) {
    fprintf(stderr, "%s: short pixel data\n", path);
    free(px);
    fclose(f);
    return NULL;
  }
  fclose(f);
  return px;
}

static double now_ms(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (double)t.tv_sec * 1e3 + (double)t.tv_nsec / 1e6;
}

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: fqrdecode FILE.pgm...\n");
    return 2;
  }
  const char *opt = getenv("FQR_OPT");
  while (opt != NULL && *opt) {
    int i = 0, v = 0;
    if (sscanf(opt, "%d=%d", &i, &v) == 2 && i >= 0 && i < 16) {
      fqr_opt[i] = v;
    }
    opt = strchr(opt, ',');
    opt = opt ? opt + 1 : NULL;
  }
  bool video = getenv("FQR_VIDEO") != NULL;
  const char *dump = getenv("FQR_DUMP");
  int failures = 0, decoded = 0, sw = 0, sh = 0;
  double total_ms = 0;
  fqr_t *s = NULL;
  void *state = NULL;
  for (int a = 1; a < argc; a++) {
    int w = 0, h = 0;
    unsigned char *px = read_pgm(argv[a], &w, &h);
    if (px == NULL) {
      failures++;
      continue;
    }
    if (s == NULL || w != sw || h != sh) {
      free(state);
      size_t len = fqr_state_size(w, h);
      state = aligned_alloc(16, (len + 15) & ~(size_t)15);
      s = state ? fqr_init(state, len, w, h) : NULL;
      sw = w;
      sh = h;
      if (s == NULL) {
        fprintf(stderr, "%s: decoder setup failed (%dx%d)\n", argv[a], w, h);
        free(px);
        failures++;
        continue;
      }
    }
    if (!video) {
      fqr_reset(s);
    }
    fqr_result_t r;
    unsigned char payload[4096] = {0};
    double t0 = now_ms();
    int reps = getenv("FQR_REPS") ? atoi(getenv("FQR_REPS")) : 1;
    bool ok = false;
    for (int k = 0; k < reps; k++) {
      if (k > 0 && !video) {
        fqr_reset(s);
      }
      ok = fqr_scan(s, px, w, payload, sizeof(payload) - 1, &r);
    }
    double ms = (now_ms() - t0) / reps;
    total_ms += ms;
    decoded += ok;
    printf("%s  %s  stage %d v%d %d.%dpx/m finders %d aligns %d timing %d%s fused %d rs %d/%d  %.2f ms", argv[a],
           ok ? "OK" : "NO", (int)r.stage, (int)r.version, (int)(r.px_per_module_x10 / 10),
           (int)(r.px_per_module_x10 % 10), (int)r.finders, (int)r.aligns, (int)r.timing_x100,
           r.tracked ? " tracked" : "", (int)r.fused, (int)r.rs_errors, (int)r.rs_erasures, ms);
    if (ok) {
      // FQR_FULL: the whole payload, else its head
      printf(getenv("FQR_FULL") ? "\t%s" : "  %.40s", (const char *)payload);
    }
    printf("\n");
    int dim = 0;
    const int8_t *z = fqr_modules(s, &dim);
    if (dump != NULL && dim > 0) {
      char path[1024];
      const char *base = strrchr(argv[a], '/');
      snprintf(path, sizeof(path), "%s/%s.soft", dump, base ? base + 1 : argv[a]);
      FILE *f = fopen(path, "w");
      if (f == NULL) {
        perror(path);
        failures++;
      } else {
        for (int y = 0; y < dim; y++) {
          for (int x = 0; x < dim; x++) {
            fprintf(f, "%d ", z[y * dim + x]);
          }
          fprintf(f, "\n");
        }
        fclose(f);
      }
      // where the grid puts each module centre
      snprintf(path, sizeof(path), "%s/%s.pts", dump, base ? base + 1 : argv[a]);
      f = fopen(path, "w");
      if (f == NULL) {
        perror(path);
        failures++;
      } else {
        for (int y = 0; y < dim; y++) {
          for (int x = 0; x < dim; x++) {
            float qx, qy;
            fqr_debug_point(s, (float)x + 0.5f, (float)y + 0.5f, &qx, &qy);
            fprintf(f, "%.2f %.2f\n", (double)qx, (double)qy);
          }
        }
        fclose(f);
      }
    }
    free(px);
  }
  printf("decoded %d of %d, %.2f ms a frame\n", decoded, argc - 1, total_ms / (argc - 1));
  if (getenv("FQR_PROF")) {
    static const char *names[] = {"binarize", "finders", "grid", "sample", "soften", "equalise", "decode"};
    int runs = (argc - 1) * (getenv("FQR_REPS") ? atoi(getenv("FQR_REPS")) : 1);
    printf("  column checks %.0f a frame\n", (double)fqr_hits / runs);
    for (int i = 0; i < 7; i++) {
      printf("  %s %.3f ms\n", names[i], fqr_prof[i] / 1000.0 / runs);
    }
  }
  free(state);
  return failures ? 1 : 0;
}
