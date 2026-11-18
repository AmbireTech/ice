/*
 * Ambire boards, [ambire_camera]: exposure set from the frames, for a camera
 * that reads QR codes off screens. Plain C with no hardware in it: the
 * driver gives it each frame and writes the exposure and gain it asks for
 * (and the host tests run it against a simulated sensor,
 * firmware/bringup/camera/qrdecode/scansim.c).
 *
 * A sensor's own automatic exposure averages the whole view, mostly a dim
 * room around a bright screen: it exposes long, the screen clips (the code's
 * dark modules bloom away) and every hand movement smears. Here:
 *   - the light is metered where the code is (the area the decoder last
 *     found it in), or on the frame's centre half while no code is in view:
 *     the 98th percentile there, the screen's white, is held at AE_TARGET;
 *   - light is bought with exposure first, up to a cap, and only then with
 *     gain (less noise for the decoder's deblurring to amplify);
 *   - the cap is what smears a moving code by AE_BLUR_PX: from the speed
 *     the code's area moves at between frames, AE_CAP_MIN_US to
 *     AE_CAP_MAX_US, and AE_CAP_US while no code is tracked;
 *   - a change takes a frame to show: the frame after one is not judged.
 */

#pragma once

#include <stdbool.h>
#include <stdint.h>

#define AE_TARGET 200
#define AE_CLIPPED 248         // from here a pixel counts as clipped
#define AE_CAP_US 6000         // the longest exposure with no code tracked
#define AE_CAP_MIN_US 3000     // ... with a fast-moving code
#define AE_CAP_MAX_US 12000    // ... with a code held still
#define AE_BLUR_PX_X10 10      // smear allowed during an exposure, pixels x10
#define AE_GAIN16_MAX 128      // 8x

typedef struct {
  // The sensor mode: one exposure line in nanoseconds, the most lines an
  // exposure can have
  uint32_t line_ns;
  uint32_t lines_max;
  // What the sensor should run at: exposure in lines, gain in 1/16 steps
  uint32_t lines;
  uint32_t gain16;
  // The exposure cap in force, microseconds and lines
  uint32_t cap_us;
  uint32_t cap_lines;
  // The last metered frame's 98th percentile, and whether it was metered on
  // a code's area
  uint32_t p98;
  bool on_code;
  // The code's area the app last reported (x0, y0, x1, y1), if any
  bool have_box;
  int32_t box[4];
  // The area's centre in the frame before (x2), and the last speed measured
  // (pixels per second)
  bool have_last;
  int32_t last_cx2, last_cy2;
  uint32_t speed_pps;
  // Frames still to skip after a change
  uint32_t hold;
} camera_ae_t;

// Starts at a middling exposure for a mode whose exposure line lasts
// `line_ns` and whose frame has `lines_max` lines
void camera_ae_start(camera_ae_t *ae, uint32_t line_ns, uint32_t lines_max);

// Where the code is in the frames (x0, y0, x1, y1); NULL: no code in view
void camera_ae_set_box(camera_ae_t *ae, const int32_t *box);

// Takes a whole w x h frame (one byte a pixel), `dt_ms` after the one
// before. True when `lines` or `gain16` changed: the caller writes them to
// the sensor.
bool camera_ae_frame(camera_ae_t *ae, const uint8_t *frame, uint32_t w,
                     uint32_t h, uint32_t dt_ms);
