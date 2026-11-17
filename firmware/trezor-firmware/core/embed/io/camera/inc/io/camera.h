/*
 * Ambire boards, [ambire_camera]: the QR camera (a 24-pin DVP module in the
 * ESP32-CAM pinout on J_CAM: OmniVision OV2640 or OV5640, or GalaxyCore
 * GC2145).
 *
 * The kernel powers the module, clocks it, programs it over SCCB and captures
 * single grayscale frames with the DCMI straight into a buffer the app hands
 * it (the kernel's own RAM is far too small for a frame). One frame at a time:
 * camera_capture() starts it, camera_get_status() reports when it is in the
 * buffer. Bring-up firmware: the status carries every counter that helps tell
 * a wiring or timing fault from a sensor-setup one.
 */

#pragma once

#include <trezor_types.h>

// Every frame is a square side x side grayscale (Y8) image, one byte per
// pixel, rows top to bottom. The side is chosen at camera_open() /
// camera_set_mode(): a multiple of 8 from CAMERA_SIDE_MIN to CAMERA_SIDE_MAX
#define CAMERA_SIDE_MIN 160
#define CAMERA_SIDE_MAX 400
#define CAMERA_FRAME_BYTES(side) ((size_t)(side) * (size_t)(side))

// Zoom levels, by sensor pixels per frame pixel (the field of view grows with
// the side): 0 the widest (about the sensor's full height, scaled down),
// 1 a 2x window averaged 2:1 (subsampled on the GC2145), 2 native pixels
#define CAMERA_ZOOM_LEVELS 3
#define CAMERA_ZOOM_WIDE 0
#define CAMERA_ZOOM_HALF 1
#define CAMERA_ZOOM_NATIVE 2

// Sensor clock: 0 as esp32-camera runs it (slow for the ESP32's sake),
// 1 faster (OV2640: 4x the internal clock; GC2145: the PLL table's own
// PCLK). The OV5640 ignores it.
#define CAMERA_CLOCKS 2
// Image tuning: 0 esp32-camera's defaults, 1 for phone screens (lower
// exposure target, more contrast; GC2145: denoise off), 2 as 1 with a
// short fixed exposure and automatic gain (OV2640; the GC2145 as 1). The
// OV5640 ignores it.
#define CAMERA_TUNES 3

// What to capture, packed into one word for camera_open() and
// camera_set_mode()
#define CAMERA_MODE(zoom, side, clock, tune)                     \
  ((uint32_t)(zoom) | ((uint32_t)(side) / 8) << 8 |              \
   (uint32_t)(clock) << 16 | (uint32_t)(tune) << 24)
#define CAMERA_MODE_ZOOM(mode) ((mode) & 0xFF)
#define CAMERA_MODE_SIDE(mode) ((((mode) >> 8) & 0xFF) * 8)
#define CAMERA_MODE_CLOCK(mode) (((mode) >> 16) & 0xFF)
#define CAMERA_MODE_TUNE(mode) (((mode) >> 24) & 0xFF)

// SCCB chip ids
#define CAMERA_SENSOR_NONE 0
#define CAMERA_SENSOR_OV5640 0x5640
#define CAMERA_SENSOR_GC2145 0x2145
#define CAMERA_SENSOR_OV2640 0x2640  // PID 0x26 (raw_id adds its VER byte)

// camera_status_t.state
#define CAMERA_STATE_OFF 0     // unpowered
#define CAMERA_STATE_IDLE 1    // on, no capture running
#define CAMERA_STATE_BUSY 2    // a capture is running
#define CAMERA_STATE_DONE 3    // a whole frame is in the buffer
#define CAMERA_STATE_FAILED 4  // the last capture failed (see the counters)

// camera_status_t.open_error: the step camera_open() stopped at
#define CAMERA_ERR_NONE 0
#define CAMERA_ERR_NO_SENSOR 1   // nothing answered at SCCB 0x30 or 0x3C
#define CAMERA_ERR_UNKNOWN_ID 2  // something answered with another chip id
#define CAMERA_ERR_SCCB_WRITE 3  // a register write was not acknowledged
#define CAMERA_ERR_BAD_MODE 4    // the mode word is out of range

typedef struct {
  uint32_t state;
  uint32_t sensor_id;   // CAMERA_SENSOR_*
  uint32_t raw_id;      // the id bytes read, whatever answered
  uint32_t open_error;  // CAMERA_ERR_*
  uint32_t error_reg;   // the register whose write failed
  uint32_t zoom;
  uint32_t side;   // frames are side x side
  uint32_t clock;  // CAMERA_MODE_CLOCK of the mode running
  uint32_t tune;   // CAMERA_MODE_TUNE of the mode running
  uint32_t scale_x10;  // sensor pixels per frame pixel, x10 (10: native)
  uint32_t frames;           // whole frames captured
  uint32_t failures;         // captures that timed out or came short / long
  uint32_t last_bytes;       // bytes the DMA wrote in the last capture
  uint32_t last_flags;       // DCMI raw interrupt flags after the last capture
  uint32_t last_capture_ms;  // how long the last capture took
  // Sync pins sampled for ~60 ms after the sensor started streaming: share of
  // samples high (per mille) and level changes seen
  uint32_t vsync_high_pm;
  uint32_t vsync_edges;
  uint32_t href_high_pm;
  uint32_t href_edges;
  uint32_t pclk_edges;
  uint32_t vsync_blank_high;  // 1: VSYNC is high in blanking (DCMI VSPOL)
  uint8_t i2c_acks[16];       // camera bus addresses that acknowledged (bit n)
  // Bring-up, before the sensor answers: the SCCB lines' idle levels with the
  // module powered (bit 0 SCL, bit 1 SDA high: the pull-ups hold them high),
  // the I2C error of the last failed chip-id read (HAL_I2C_ERROR_*: 0x04 no
  // acknowledge, 0x20 timeout, 0x01 bus error) and XCLK level changes seen
  // on its pin (0: no clock leaves the MCU)
  uint32_t i2c_lines;
  uint32_t i2c_error;
  uint32_t xclk_edges;
} camera_status_t;

// Powers the module up, finds the sensor and starts it streaming in `mode`
// (CAMERA_MODE()). Blocks for up to ~1 s. Returns false if no supported
// sensor came up or the mode is invalid; the status says why. A module that
// does not come up is switched off.
bool camera_open(uint32_t mode);

// Stops any capture and powers the module down.
void camera_close(void);

// Changes the mode (stops a running capture). False if the camera is off,
// the mode is invalid or the sensor refused the change.
bool camera_set_mode(uint32_t mode);

// Starts capturing the next whole frame into `buf` (at least
// CAMERA_FRAME_BYTES(side) bytes of the mode running, 4-byte aligned). The buffer belongs to the camera until camera_get_status()
// reports DONE or FAILED, or the camera is closed.
bool camera_capture(uint8_t *buf, size_t size);

// Reports the state and the counters; also finishes a capture that is done.
void camera_get_status(camera_status_t *status);
