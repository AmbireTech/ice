// The quirc-based decoder the device used before its own (quirc/: qr_scan.c
// with sharpening, a threshold choice, quirc with our identify fixes, and the
// upscaled second looks of ../qrcommon), kept as the reference to compare
// fqrdecode against. Reads binary PGM (P5) files, runs the decoder with each
// of its thresholds and prints one line per file:
//   FILE  OK|NO  v<version> <px/module> t<threshold> grids <n> err <e> mean <y> clip <pm> edge <x10> <payload head>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <io/qr_scan.h>

#include "qr_roi.h"

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

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: qrdecode FILE.pgm...\n");
    return 2;
  }
  int failures = 0;
  for (int a = 1; a < argc; a++) {
    int w = 0, h = 0;
    unsigned char *px = read_pgm(argv[a], &w, &h);
    if (px == NULL) {
      failures++;
      continue;
    }
    size_t state_len = qr_scan_state_size(w, h);
    unsigned char *image = aligned_alloc(16, ((size_t)w * h + 15) & ~(size_t)15);
    unsigned char *state = aligned_alloc(16, (state_len + 15) & ~(size_t)15);
    if (image == NULL || state == NULL || !qr_scan_init(image, w, h, state, state_len)) {
      fprintf(stderr, "%s: decoder setup failed (%dx%d)\n", argv[a], w, h);
      free(px);
      free(image);
      free(state);
      failures++;
      continue;
    }
    qr_scan_result_t r = {0};
    unsigned char payload[600] = {0};
    // The grey frame for the decoder's sub-pixel re-reads (QR_NO_GRAY: none)
    qr_scan_set_gray(getenv("QR_NO_GRAY") ? NULL : px);
    // One pass per threshold, each on a fresh copy (a pass thresholds in place)
    for (int t = 0; t < 3; t++) {
      memcpy(image, px, (size_t)w * h);
      qr_scan(payload, sizeof(payload) - 1, &r);
      if (r.decoded) {
        break;
      }
    }
    const char *how = "";
    if (!r.decoded && getenv("QR_NO_ROI") == NULL) {
      // The scanners' second looks: the whole frame upscaled, each scale
      static const qr_roi_scale_t scales[] = {
          {3, 2, QR_ROI_LINEAR, 2}, {2, 1, QR_ROI_LINEAR, 2}, {5, 2, QR_ROI_CUBIC, 2}};
      // QR_ROI_PASSES: passes per scale (the ESP32 does 1, at threshold 2)
      int roi_passes = getenv("QR_ROI_PASSES") ? atoi(getenv("QR_ROI_PASSES")) : 3;
      static const char *names[] = {" (1.5x linear)", " (2x linear)", " (2.5x cubic)"};
      size_t roi_len = qr_roi_image_size(w, h, 5, 2);
      size_t roi_state_len = qr_scan_state_size(w * 5 / 2 + 1, h * 5 / 2 + 1);
      unsigned char *roi = aligned_alloc(16, (roi_len + 15) & ~(size_t)15);
      unsigned char *roi_state = aligned_alloc(16, (roi_state_len + 15) & ~(size_t)15);
      // QR_ROI_BOX=<bytes>: as the ESP32 does it, the code's area (from the
      // finder patterns the last 1x pass found) in a buffer of that size
      const char *box_env = getenv("QR_ROI_BOX");
      int32_t box[4];
      bool have_box = box_env != NULL && qr_scan_code_area(box);
      qr_scan_set_gray(NULL);  // the upscaled image has no grey copy
      for (int k = 0; roi != NULL && roi_state != NULL && k < 3 && (box_env == NULL || have_box); k++) {
        qr_scan_result_t rr = {0};
        bool ok = have_box
                      ? qr_roi_decode_box(px, w, h, box, scales[k], roi_passes, roi, (size_t)atol(box_env),
                                          roi_state, roi_state_len, payload, sizeof(payload) - 1, &rr)
                      : qr_roi_decode(px, w, h, NULL, scales[k], roi_passes, roi, roi_state, roi_state_len,
                                      payload, sizeof(payload) - 1, &rr);
        if (ok) {
          how = names[k];
          r = rr;
          break;
        }
      }
      free(roi);
      free(roi_state);
    }
    printf("%s  %s%s  v%d %d.%dpx/m t%d grids %d err %d mean %d clip %d edge %d.%d", argv[a],
           r.decoded ? "OK" : "NO", how, (int)r.version, (int)(r.px_per_module_x10 / 10),
           (int)(r.px_per_module_x10 % 10), (int)r.threshold, (int)r.grids, (int)r.error,
           (int)r.mean_y, (int)r.clipped_pm, (int)(r.edge_x10 / 10), (int)(r.edge_x10 % 10));
    if (r.decoded) {
      int n = r.payload_len < 40 ? (int)r.payload_len : 40;
      printf("  %.*s", n, (const char *)payload);
    }
    printf("\n");
    free(px);
    free(image);
    free(state);
  }
  return failures ? 1 : 0;
}
