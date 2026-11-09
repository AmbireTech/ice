/*
 * Ambire boards, [ambire_camera]: the emulator has no camera; it reports
 * none, like a board without a module.
 */

#include <trezor_rtl.h>

#include <io/camera.h>

static camera_status_t g_status;

bool camera_open(uint32_t zoom) {
  memset(&g_status, 0, sizeof(g_status));
  g_status.open_error = CAMERA_ERR_NO_SENSOR;
  return false;
}

void camera_close(void) { g_status.state = CAMERA_STATE_OFF; }

bool camera_set_zoom(uint32_t zoom) { return false; }

bool camera_capture(uint8_t *buf, size_t size) { return false; }

void camera_get_status(camera_status_t *status) { *status = g_status; }
