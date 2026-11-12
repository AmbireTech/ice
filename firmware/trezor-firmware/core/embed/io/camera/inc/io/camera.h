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

// Every frame is a CAMERA_FRAME_W x CAMERA_FRAME_H grayscale (Y8) image, one
// byte per pixel, rows top to bottom
#define CAMERA_FRAME_W 240
#define CAMERA_FRAME_H 240
#define CAMERA_FRAME_SIZE (CAMERA_FRAME_W * CAMERA_FRAME_H)

// Zoom levels: 0 is the widest view, each next one a narrower centre window
#define CAMERA_ZOOM_LEVELS 3

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

typedef struct {
  uint32_t state;
  uint32_t sensor_id;   // CAMERA_SENSOR_*
  uint32_t raw_id;      // the id bytes read, whatever answered
  uint32_t open_error;  // CAMERA_ERR_*
  uint32_t error_reg;   // the register whose write failed
  uint32_t zoom;
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

// Powers the module up, finds the sensor and starts it streaming at `zoom`.
// Blocks for up to ~1 s. Returns false if no supported sensor came up; the
// status says why. A module that does not come up is switched off.
bool camera_open(uint32_t zoom);

// Stops any capture and powers the module down.
void camera_close(void);

// Changes the zoom level (stops a running capture). False if the camera is
// off or the sensor refused the change.
bool camera_set_zoom(uint32_t zoom);

// Starts capturing the next whole frame into `buf` (CAMERA_FRAME_SIZE bytes,
// 4-byte aligned). The buffer belongs to the camera until camera_get_status()
// reports DONE or FAILED, or the camera is closed.
bool camera_capture(uint8_t *buf, size_t size);

// Reports the state and the counters; also finishes a capture that is done.
void camera_get_status(camera_status_t *status);
