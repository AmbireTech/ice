/*
 * Ambire boards, [ambire_camera]: exposure set from the frames
 * (camera_ae.h).
 */

#if defined(KERNEL_MODE) || defined(CAMERA_AE_HOST)

#include "camera_ae.h"

#include <string.h>

static uint32_t us_to_lines(const camera_ae_t *ae, uint32_t us) {
  uint32_t lines = (uint32_t)((uint64_t)us * 1000U / ae->line_ns);
  if (lines > ae->lines_max) {
    lines = ae->lines_max;
  }
  return lines > 0 ? lines : 1;
}

static void set_cap(camera_ae_t *ae, uint32_t cap_us) {
  ae->cap_us = cap_us;
  ae->cap_lines = us_to_lines(ae, cap_us);
}

void camera_ae_start(camera_ae_t *ae, uint32_t line_ns, uint32_t lines_max) {
  memset(ae, 0, sizeof(*ae));
  ae->line_ns = line_ns > 0 ? line_ns : 1;
  ae->lines_max = lines_max > 0 ? lines_max : 1;
  set_cap(ae, AE_CAP_US);
  // Half the cap at 2x gain: about right for a phone screen at full
  // brightness; the frames take it from there
  ae->lines = ae->cap_lines / 2 > 0 ? ae->cap_lines / 2 : 1;
  ae->gain16 = 32;
}

void camera_ae_set_box(camera_ae_t *ae, const int32_t *box) {
  ae->have_box = box != NULL && box[2] > box[0] && box[3] > box[1];
  if (ae->have_box) {
    memcpy(ae->box, box, sizeof(ae->box));
  }
}

// The exposure cap from how fast the code's area moves: the exposure that
// smears it by AE_BLUR_PX. True if the cap (in lines) changed.
static bool update_cap(camera_ae_t *ae, uint32_t dt_ms) {
  uint32_t cap_us = AE_CAP_US;
  if (!ae->have_box) {
    ae->have_last = false;
    ae->speed_pps = 0;
  } else {
    int32_t cx2 = ae->box[0] + ae->box[2], cy2 = ae->box[1] + ae->box[3];
    if (ae->have_last && dt_ms > 0) {
      int32_t dx = cx2 - ae->last_cx2, dy = cy2 - ae->last_cy2;
      // The larger axis plus half the smaller: within 12 % of the length
      uint32_t ax = (uint32_t)(dx < 0 ? -dx : dx), ay = (uint32_t)(dy < 0 ? -dy : dy);
      uint32_t d2 = ax > ay ? ax + ay / 2 : ay + ax / 2;  // pixels x2
      uint32_t speed = d2 * 500U / dt_ms;                 // pixels a second
      // The faster of this step and the last: one still step between two
      // moves is not rest
      uint32_t worst = speed > ae->speed_pps ? speed : ae->speed_pps;
      ae->speed_pps = speed;
      // AE_BLUR_PX / speed, in microseconds
      cap_us = worst > 0 ? AE_BLUR_PX_X10 * 100000U / worst : AE_CAP_MAX_US;
      if (cap_us > AE_CAP_MAX_US) {
        cap_us = AE_CAP_MAX_US;
      }
      if (cap_us < AE_CAP_MIN_US) {
        cap_us = AE_CAP_MIN_US;
      }
      // Longer only a quarter of the way at a time, shorter at once: a code
      // that starts moving is the urgent case
      if (cap_us > ae->cap_us) {
        cap_us = ae->cap_us + (cap_us - ae->cap_us + 3) / 4;
      }
    } else {
      cap_us = ae->cap_us;  // the first frame of a code: no speed yet
    }
    ae->have_last = true;
    ae->last_cx2 = cx2;
    ae->last_cy2 = cy2;
  }
  uint32_t before = ae->cap_lines;
  set_cap(ae, cap_us);
  return ae->cap_lines != before;
}

// `total` (exposure lines x gain in 1/16 steps) as exposure up to the cap,
// then gain; what the gain cannot give either, as exposure again up to the
// longest cap (a dim screen smeared a little reads better than one lost in
// the dark). True if either changed.
static bool split(camera_ae_t *ae, uint32_t total) {
  uint32_t lines = total / 16;
  if (lines > ae->cap_lines) {
    lines = ae->cap_lines;
    uint32_t most = us_to_lines(ae, AE_CAP_MAX_US);
    if (total / lines > AE_GAIN16_MAX && most > lines) {
      lines = total / AE_GAIN16_MAX < most ? total / AE_GAIN16_MAX : most;
    }
  }
  if (lines < 1) {
    lines = 1;
  }
  uint32_t gain16 = (total + lines / 2) / lines;
  if (gain16 < 16) {
    gain16 = 16;
  }
  if (gain16 > AE_GAIN16_MAX) {
    gain16 = AE_GAIN16_MAX;
  }
  if (lines == ae->lines && gain16 == ae->gain16) {
    return false;
  }
  ae->lines = lines;
  ae->gain16 = gain16;
  return true;
}

bool camera_ae_frame(camera_ae_t *ae, const uint8_t *frame, uint32_t w,
                     uint32_t h, uint32_t dt_ms) {
  bool cap_changed = update_cap(ae, dt_ms);
  uint32_t total = ae->lines * ae->gain16;
  if (ae->hold > 0) {
    // The last change is not in this frame yet
    ae->hold--;
    return false;
  }

  // The code's area, or the centre half of each side; about 4000 samples
  uint32_t x0 = w / 4, x1 = w - w / 4, y0 = h / 4, y1 = h - h / 4;
  ae->on_code = false;
  if (ae->have_box) {
    int32_t bx0 = ae->box[0] < 0 ? 0 : ae->box[0];
    int32_t by0 = ae->box[1] < 0 ? 0 : ae->box[1];
    int32_t bx1 = ae->box[2] > (int32_t)w ? (int32_t)w : ae->box[2];
    int32_t by1 = ae->box[3] > (int32_t)h ? (int32_t)h : ae->box[3];
    if (bx1 - bx0 >= 16 && by1 - by0 >= 16) {
      x0 = (uint32_t)bx0;
      x1 = (uint32_t)bx1;
      y0 = (uint32_t)by0;
      y1 = (uint32_t)by1;
      ae->on_code = true;
    }
  }
  uint32_t step = 1;
  while ((x1 - x0) / (step + 1) * ((y1 - y0) / (step + 1)) >= 4000) {
    step++;
  }
  uint32_t hist[64] = {0};
  uint32_t n = 0;
  for (uint32_t y = y0; y < y1; y += step) {
    const uint8_t *row = frame + (size_t)y * w;
    for (uint32_t x = x0; x < x1; x += step) {
      hist[row[x] >> 2]++;
      n++;
    }
  }
  if (n == 0) {
    return false;
  }
  uint32_t above = 0, bin = 63;
  while (bin > 0 && (above + hist[bin]) * 50 < n) {  // the top 2 %
    above += hist[bin];
    bin--;
  }
  uint32_t p98 = bin * 4 + 2;
  ae->p98 = p98;

  uint32_t next = total;
  if (p98 >= AE_CLIPPED) {
    // Clipped: how far over is unknown. Halved; to a quarter when most of
    // the area is (a screen's white is about half a code's area).
    uint32_t clipped = hist[63] + hist[62];
    next = clipped * 3 > n ? total / 4 : total / 2;
  } else {
    uint32_t want = total * AE_TARGET / (p98 > 8 ? p98 : 8);
    if (want * 100 > total * 85 && want * 100 < total * 118) {
      want = total;  // close enough
    } else if (want > total) {
      // The sensor's output is not linear in light (its gamma lifts the
      // dark end): going up, half as far again, at most 4x a step
      want += (want - total) / 2;
      if (want > total * 4) {
        want = total * 4;
      }
    }
    next = want;
  }
  if (next < 16) {
    next = 16;
  }
  if (next == total && !cap_changed) {
    return false;
  }
  if (!split(ae, next)) {
    return false;
  }
  // A change of brightness shows a frame later; the same light shared anew
  // between exposure and gain needs no wait
  ae->hold = next != total ? 1 : 0;
  return true;
}

#endif
