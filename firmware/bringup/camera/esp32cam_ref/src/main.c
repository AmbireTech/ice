/*
 * OV2640 reference: an Ai-Thinker ESP32-CAM running Espressif's own camera
 * driver (espressif/esp32-camera, pinned in idf_component.yml), with a Wi-Fi
 * access point and a page at http://192.168.4.1/ for live preview, sensor
 * controls and raw frame downloads.
 *
 * Every camera call happens in the HTTP server's one task, so nothing here
 * needs a lock.
 *
 * Endpoints:
 *   /         the page (page.html)
 *   /status   JSON: mode, window, sensor settings, live exposure and gain
 *   /set      ?var=NAME&val=INT: a mode change (framesize, field, format,
 *             xclk) restarts the camera; a sensor setting calls the driver's
 *             setter; var=defaults drops every setting made here
 *   /raw      ?fmt=y8 (PGM, grayscale mode) | jpeg (JPEG mode) &skip=N:
 *             the current frame, after discarding N frames
 *   /reg      ?bank=0|1&addr=N[&val=N]: read or write one sensor register
 *   /regs     both register banks, as text
 */

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/uart.h"
#include "esp_camera.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_rom_crc.h"
#include "esp_timer.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

static const char *TAG = "ov2640ref";

#define AP_SSID "ov2640-ref"
#define AP_PASS "ov2640ref"
#define AP_CHANNEL 6

extern const char page_html_start[] asm("_binary_page_html_start");

// Frame buffers live in PSRAM (4 MB on the ESP32-CAM); two buffers when both
// fit in this much, so the newest frame is always at hand.
#define FB_TWO_MAX_BYTES (2500u * 1024u)

// ---------------------------------------------------------------------------
// Mode: what needs a camera restart to change

// The field a frame covers. FULL is the driver's own framing for the frame
// size (set_framesize()); the others are windows set with set_res_raw(),
// matching our device's zoom levels (core/embed/io/camera/stm32u5/camera.c):
//   WIDE    zoom 0: the centre square of the array's full height (1200 rows),
//           widened to the output's aspect, scaled by the DSP
//   HALF    zoom 1: SVGA mode's own pixels (the array at half resolution),
//           centre window the output's size
//   NATIVE  zoom 2: UXGA mode's pixels (the array's native pixels), centre
//           window the output's size
typedef enum { FIELD_FULL, FIELD_WIDE, FIELD_HALF, FIELD_NATIVE, FIELD_COUNT } field_t;
static const char *const FIELD_NAMES[FIELD_COUNT] = {"full", "wide", "half", "native"};

// esp32-camera's ov2640_sensor_mode_t (sensors/private_include/
// ov2640_settings.h), which set_res_raw() takes as its first argument
#define OV_MODE_UXGA 0
#define OV_MODE_SVGA 1

typedef struct {
  framesize_t framesize;
  field_t field;
  bool gray;  // PIXFORMAT_GRAYSCALE, else PIXFORMAT_JPEG
  int xclk_mhz;
} cam_mode_t;

typedef struct {
  int sensor_mode;  // OV_MODE_*
  int ox, oy, mx, my;  // window in the sensor mode's pixels
} window_t;

// Default: our device's wide field at 320x320 grayscale, its decoder's input
static cam_mode_t g_mode = {FRAMESIZE_320X320, FIELD_WIDE, true, 20};
static window_t g_window;
static bool g_cam_running;
static char g_last_error[160];
static int64_t g_last_frame_us, g_frame_interval_us;

// Frame sizes offered (the OV2640 tops out at UXGA)
static const framesize_t FRAMESIZES[] = {
    FRAMESIZE_240X240, FRAMESIZE_QVGA, FRAMESIZE_320X320, FRAMESIZE_CIF,
    FRAMESIZE_HVGA,    FRAMESIZE_VGA,  FRAMESIZE_SVGA,    FRAMESIZE_XGA,
    FRAMESIZE_HD,      FRAMESIZE_SXGA, FRAMESIZE_UXGA,
};
static const char *const FRAMESIZE_NAMES[] = {
    "240x240", "QVGA", "320x320", "CIF", "HVGA", "VGA",
    "SVGA",    "XGA",  "HD",      "SXGA", "UXGA",
};
#define N_FRAMESIZES (sizeof(FRAMESIZES) / sizeof(FRAMESIZES[0]))

static bool framesize_offered(int fs) {
  for (size_t i = 0; i < N_FRAMESIZES; i++) {
    if ((int)FRAMESIZES[i] == fs) {
      return true;
    }
  }
  return false;
}

static void set_error(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void set_error(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(g_last_error, sizeof(g_last_error), fmt, ap);
  va_end(ap);
  ESP_LOGE(TAG, "%s", g_last_error);
}

// The window for a mode; false (and g_last_error) if the output does not fit
static bool compute_window(const cam_mode_t *m, window_t *w) {
  int ow = resolution[m->framesize].width, oh = resolution[m->framesize].height;
  memset(w, 0, sizeof(*w));
  switch (m->field) {
    case FIELD_FULL:
      return true;
    case FIELD_WIDE: {
      // in array pixels: 1200 rows, widened to the output's aspect
      int mx = 1200 * ow / oh, my = 1200;
      if (mx > 1600) {
        mx = 1600;
        my = 1600 * oh / ow;
      }
      // SVGA mode (half resolution) when the output fits, else UXGA mode
      int div = (ow <= mx / 2 && oh <= my / 2) ? 2 : 1;
      w->sensor_mode = div == 2 ? OV_MODE_SVGA : OV_MODE_UXGA;
      w->mx = (mx / div) & ~3;
      w->my = (my / div) & ~3;
      w->ox = (1600 / div - w->mx) / 2;
      w->oy = (1200 / div - w->my) / 2;
      return true;
    }
    case FIELD_HALF:
      if (ow > 800 || oh > 600) {
        set_error("field half: %dx%d does not fit SVGA mode's 800x600", ow, oh);
        return false;
      }
      w->sensor_mode = OV_MODE_SVGA;
      w->mx = ow;
      w->my = oh;
      w->ox = (800 - ow) / 2;
      w->oy = (600 - oh) / 2;
      return true;
    case FIELD_NATIVE:
      w->sensor_mode = OV_MODE_UXGA;
      w->mx = ow;
      w->my = oh;
      w->ox = (1600 - ow) / 2;
      w->oy = (1200 - oh) / 2;
      return true;
    default:
      set_error("field %d unknown", (int)m->field);
      return false;
  }
}

// ---------------------------------------------------------------------------
// Sensor settings: what the driver's setters change without a restart. Only
// the ones set from the page are written (again after every restart); the
// rest stay at the driver's defaults.

enum {
  S_AEC, S_AEC_VALUE, S_AE_LEVEL, S_AEC2, S_AGC, S_AGC_GAIN, S_GAINCEILING,
  S_BRIGHTNESS, S_CONTRAST, S_SATURATION, S_SPECIAL_EFFECT, S_AWB, S_AWB_GAIN,
  S_WB_MODE, S_HMIRROR, S_VFLIP, S_LENC, S_BPC, S_WPC, S_RAW_GMA, S_DCW,
  S_QUALITY, S_COLORBAR, S_COUNT
};

static const struct {
  const char *name;
  int min, max;
} SETTINGS[S_COUNT] = {
    [S_AEC] = {"aec", 0, 1},
    [S_AEC_VALUE] = {"aec_value", 0, 1200},
    [S_AE_LEVEL] = {"ae_level", -2, 2},
    [S_AEC2] = {"aec2", 0, 1},
    [S_AGC] = {"agc", 0, 1},
    [S_AGC_GAIN] = {"agc_gain", 0, 30},
    [S_GAINCEILING] = {"gainceiling", 0, 6},
    [S_BRIGHTNESS] = {"brightness", -2, 2},
    [S_CONTRAST] = {"contrast", -2, 2},
    [S_SATURATION] = {"saturation", -2, 2},
    [S_SPECIAL_EFFECT] = {"special_effect", 0, 6},
    [S_AWB] = {"awb", 0, 1},
    [S_AWB_GAIN] = {"awb_gain", 0, 1},
    [S_WB_MODE] = {"wb_mode", 0, 4},
    [S_HMIRROR] = {"hmirror", 0, 1},
    [S_VFLIP] = {"vflip", 0, 1},
    [S_LENC] = {"lenc", 0, 1},
    [S_BPC] = {"bpc", 0, 1},
    [S_WPC] = {"wpc", 0, 1},
    [S_RAW_GMA] = {"raw_gma", 0, 1},
    [S_DCW] = {"dcw", 0, 1},
    [S_QUALITY] = {"quality", 0, 63},
    [S_COLORBAR] = {"colorbar", 0, 1},
};

static int g_setting_value[S_COUNT];
static bool g_setting_user[S_COUNT];

static int setting_call(sensor_t *s, int id, int v) {
  switch (id) {
    case S_AEC: return s->set_exposure_ctrl(s, v);
    case S_AEC_VALUE: return s->set_aec_value(s, v);
    case S_AE_LEVEL: return s->set_ae_level(s, v);
    case S_AEC2: return s->set_aec2(s, v);
    case S_AGC: return s->set_gain_ctrl(s, v);
    case S_AGC_GAIN: return s->set_agc_gain(s, v);
    case S_GAINCEILING: return s->set_gainceiling(s, (gainceiling_t)v);
    case S_BRIGHTNESS: return s->set_brightness(s, v);
    case S_CONTRAST: return s->set_contrast(s, v);
    case S_SATURATION: return s->set_saturation(s, v);
    case S_SPECIAL_EFFECT: return s->set_special_effect(s, v);
    case S_AWB: return s->set_whitebal(s, v);
    case S_AWB_GAIN: return s->set_awb_gain(s, v);
    case S_WB_MODE: return s->set_wb_mode(s, v);
    case S_HMIRROR: return s->set_hmirror(s, v);
    case S_VFLIP: return s->set_vflip(s, v);
    case S_LENC: return s->set_lenc(s, v);
    case S_BPC: return s->set_bpc(s, v);
    case S_WPC: return s->set_wpc(s, v);
    case S_RAW_GMA: return s->set_raw_gma(s, v);
    case S_DCW: return s->set_dcw(s, v);
    case S_QUALITY: return s->set_quality(s, v);
    case S_COLORBAR: return s->set_colorbar(s, v);
    default: return -1;
  }
}

// The driver's record of a setting (camera_status_t)
static int setting_status(const camera_status_t *st, int id) {
  switch (id) {
    case S_AEC: return st->aec;
    case S_AEC_VALUE: return st->aec_value;
    case S_AE_LEVEL: return st->ae_level;
    case S_AEC2: return st->aec2;
    case S_AGC: return st->agc;
    case S_AGC_GAIN: return st->agc_gain;
    case S_GAINCEILING: return st->gainceiling;
    case S_BRIGHTNESS: return st->brightness;
    case S_CONTRAST: return st->contrast;
    case S_SATURATION: return st->saturation;
    case S_SPECIAL_EFFECT: return st->special_effect;
    case S_AWB: return st->awb;
    case S_AWB_GAIN: return st->awb_gain;
    case S_WB_MODE: return st->wb_mode;
    case S_HMIRROR: return st->hmirror;
    case S_VFLIP: return st->vflip;
    case S_LENC: return st->lenc;
    case S_BPC: return st->bpc;
    case S_WPC: return st->wpc;
    case S_RAW_GMA: return st->raw_gma;
    case S_DCW: return st->dcw;
    case S_QUALITY: return st->quality;
    case S_COLORBAR: return st->colorbar;
    default: return 0;
  }
}

static int setting_find(const char *name) {
  for (int i = 0; i < S_COUNT; i++) {
    if (strcmp(SETTINGS[i].name, name) == 0) {
      return i;
    }
  }
  return -1;
}

// ---------------------------------------------------------------------------
// Camera

static esp_err_t camera_start(const cam_mode_t *m) {
  window_t win;
  if (!compute_window(m, &win)) {
    return ESP_ERR_INVALID_ARG;
  }

  if (g_cam_running) {
    esp_err_t err = esp_camera_deinit();
    g_cam_running = false;
    if (err != ESP_OK) {
      set_error("esp_camera_deinit failed: %s", esp_err_to_name(err));
      return err;
    }
  }

  size_t w = resolution[m->framesize].width, h = resolution[m->framesize].height;
  // JPEG: room for a lightly compressed frame of fine detail
  size_t fb_bytes = m->gray ? w * h : w * h / 3;
  size_t fb_count = 2 * fb_bytes <= FB_TWO_MAX_BYTES ? 2 : 1;

  camera_config_t cfg = {
      .pin_pwdn = 32,
      .pin_reset = -1,
      .pin_xclk = 0,
      .pin_sccb_sda = 26,
      .pin_sccb_scl = 27,
      .pin_d7 = 35,
      .pin_d6 = 34,
      .pin_d5 = 39,
      .pin_d4 = 36,
      .pin_d3 = 21,
      .pin_d2 = 19,
      .pin_d1 = 18,
      .pin_d0 = 5,
      .pin_vsync = 25,
      .pin_href = 23,
      .pin_pclk = 22,
      .xclk_freq_hz = m->xclk_mhz * 1000000,
      .ledc_timer = LEDC_TIMER_0,
      .ledc_channel = LEDC_CHANNEL_0,
      .pixel_format = m->gray ? PIXFORMAT_GRAYSCALE : PIXFORMAT_JPEG,
      .frame_size = m->framesize,
      .jpeg_quality = 10,
      .fb_count = fb_count,
      .fb_location = CAMERA_FB_IN_PSRAM,
      .grab_mode = fb_count > 1 ? CAMERA_GRAB_LATEST : CAMERA_GRAB_WHEN_EMPTY,
      .jpeg_buffer_size = m->gray ? 0 : fb_bytes,
  };
  ESP_LOGI(TAG, "camera start: %ux%u %s field %s, XCLK %d MHz, %u buffer(s)",
           (unsigned)w, (unsigned)h, m->gray ? "gray" : "jpeg",
           FIELD_NAMES[m->field], m->xclk_mhz, (unsigned)fb_count);
  esp_err_t err = esp_camera_init(&cfg);
  if (err != ESP_OK) {
    set_error("esp_camera_init failed: %s (0x%x)", esp_err_to_name(err), err);
    return err;
  }
  g_cam_running = true;

  sensor_t *s = esp_camera_sensor_get();
  if (s == NULL) {
    set_error("esp_camera_sensor_get returned NULL after init");
    return ESP_FAIL;
  }
  if (s->id.PID != OV2640_PID && m->field != FIELD_FULL) {
    set_error("sensor PID 0x%02x is not an OV2640: only field full is supported",
              s->id.PID);
    return ESP_ERR_NOT_SUPPORTED;
  }
  if (m->field != FIELD_FULL) {
    // OV2640 set_res_raw(): startX is the sensor mode, offsetX/Y and
    // totalX/Y the window, outputX/Y the frame (the rest unused)
    int r = s->set_res_raw(s, win.sensor_mode, 0, 0, 0, win.ox, win.oy, win.mx,
                           win.my, (int)w, (int)h, false, false);
    if (r != 0) {
      set_error("set_res_raw(mode %d, %d,%d %dx%d -> %ux%u) returned %d",
                win.sensor_mode, win.ox, win.oy, win.mx, win.my, (unsigned)w,
                (unsigned)h, r);
      return ESP_FAIL;
    }
  }
  for (int i = 0; i < S_COUNT; i++) {
    if (g_setting_user[i]) {
      int r = setting_call(s, i, g_setting_value[i]);
      if (r != 0) {
        set_error("re-applying %s=%d returned %d", SETTINGS[i].name,
                  g_setting_value[i], r);
        return ESP_FAIL;
      }
    }
  }
  g_window = win;
  g_last_frame_us = 0;
  g_frame_interval_us = 0;
  g_last_error[0] = '\0';
  return ESP_OK;
}

// Restart in a new mode; on failure back to the previous one
static esp_err_t camera_switch(const cam_mode_t *next) {
  cam_mode_t prev = g_mode;
  esp_err_t err = camera_start(next);
  if (err == ESP_OK) {
    g_mode = *next;
    return ESP_OK;
  }
  char why[sizeof(g_last_error)];
  strlcpy(why, g_last_error, sizeof(why));
  esp_err_t back = camera_start(&prev);
  if (back != ESP_OK) {
    ESP_LOGE(TAG, "restoring the previous mode failed too: %s", g_last_error);
  }
  // report the reason the switch failed, not the restore's outcome
  strlcpy(g_last_error, why, sizeof(g_last_error));
  return err;
}

// ---------------------------------------------------------------------------
// HTTP helpers

typedef struct {
  char *buf;
  size_t len, cap;
  bool overflow;
} sbuf_t;

static void sb_printf(sbuf_t *b, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void sb_printf(sbuf_t *b, const char *fmt, ...) {
  if (b->overflow) {
    return;
  }
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(b->buf + b->len, b->cap - b->len, fmt, ap);
  va_end(ap);
  if (n < 0 || (size_t)n >= b->cap - b->len) {
    b->overflow = true;
    return;
  }
  b->len += (size_t)n;
}

static esp_err_t send_error(httpd_req_t *req, const char *status, const char *msg) {
  ESP_LOGW(TAG, "%s %s: %s", req->uri, status, msg);
  httpd_resp_set_status(req, status);
  httpd_resp_set_type(req, "text/plain");
  return httpd_resp_sendstr(req, msg);
}

// Query parameter as an integer (decimal or 0x hex); false if absent or bad
static bool query_int(const char *query, const char *key, int *out, bool *present) {
  char val[24];
  *present = false;
  if (query == NULL || httpd_query_key_value(query, key, val, sizeof(val)) != ESP_OK) {
    return true;
  }
  *present = true;
  char *end = NULL;
  long v = strtol(val, &end, 0);
  if (end == val || *end != '\0') {
    return false;
  }
  *out = (int)v;
  return true;
}

static char *get_query(httpd_req_t *req) {
  size_t len = httpd_req_get_url_query_len(req);
  if (len == 0) {
    return NULL;
  }
  char *q = malloc(len + 1);
  if (q == NULL) {
    ESP_LOGE(TAG, "query buffer of %u bytes: out of memory", (unsigned)len + 1);
    return NULL;
  }
  if (httpd_req_get_url_query_str(req, q, len + 1) != ESP_OK) {
    ESP_LOGW(TAG, "%s: reading the query failed", req->uri);
    free(q);
    return NULL;
  }
  return q;
}

static int sensor_reg(sensor_t *s, int bank, int addr) {
  return s->get_reg(s, (bank << 8) | addr, 0xff);
}

// ---------------------------------------------------------------------------
// Handlers

static esp_err_t page_get(httpd_req_t *req) {
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, page_html_start, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t status_get(httpd_req_t *req) {
  char buf[3072];
  sbuf_t b = {buf, 0, sizeof(buf), false};
  sensor_t *s = g_cam_running ? esp_camera_sensor_get() : NULL;
  int w = resolution[g_mode.framesize].width, h = resolution[g_mode.framesize].height;

  sb_printf(&b, "{\"running\":%s,\"error\":\"", g_cam_running ? "true" : "false");
  for (const char *p = g_last_error; *p; p++) {  // JSON-escape
    if (*p == '"' || *p == '\\') {
      sb_printf(&b, "\\%c", *p);
    } else if ((unsigned char)*p >= 0x20) {
      sb_printf(&b, "%c", *p);
    }
  }
  sb_printf(&b, "\",\"pid\":%d,", s ? s->id.PID : -1);
  sb_printf(&b,
            "\"mode\":{\"framesize\":%d,\"width\":%d,\"height\":%d,"
            "\"field\":%d,\"field_name\":\"%s\",\"format\":%d,\"xclk\":%d},",
            (int)g_mode.framesize, w, h, (int)g_mode.field,
            FIELD_NAMES[g_mode.field], g_mode.gray ? 1 : 0, g_mode.xclk_mhz);
  sb_printf(&b,
            "\"window\":{\"sensor_mode\":\"%s\",\"ox\":%d,\"oy\":%d,\"mx\":%d,"
            "\"my\":%d},",
            g_mode.field == FIELD_FULL
                ? "driver"
                : (g_window.sensor_mode == OV_MODE_SVGA ? "SVGA" : "UXGA"),
            g_window.ox, g_window.oy, g_window.mx, g_window.my);
  sb_printf(&b, "\"framesizes\":[");
  for (size_t i = 0; i < N_FRAMESIZES; i++) {
    sb_printf(&b, "%s[%d,\"%s\",%d,%d]", i ? "," : "", (int)FRAMESIZES[i],
              FRAMESIZE_NAMES[i], resolution[FRAMESIZES[i]].width,
              resolution[FRAMESIZES[i]].height);
  }
  sb_printf(&b, "],\"fields\":[");
  for (int i = 0; i < FIELD_COUNT; i++) {
    sb_printf(&b, "%s\"%s\"", i ? "," : "", FIELD_NAMES[i]);
  }
  sb_printf(&b, "],\"settings\":{");
  for (int i = 0; i < S_COUNT; i++) {
    sb_printf(&b, "%s\"%s\":%d", i ? "," : "", SETTINGS[i].name,
              s ? setting_status(&s->status, i) : 0);
  }
  sb_printf(&b, "},\"user_set\":[");
  bool first = true;
  for (int i = 0; i < S_COUNT; i++) {
    if (g_setting_user[i]) {
      sb_printf(&b, "%s\"%s\"", first ? "" : ",", SETTINGS[i].name);
      first = false;
    }
  }
  sb_printf(&b, "],");
  if (s != NULL && s->id.PID == OV2640_PID) {
    // live exposure (rows) and gain from the sensor bank: AEC[15:10] in
    // REG45, [9:2] in AEC (0x10), [1:0] in REG04; GAIN (0x00) is
    // (b7+1)(b6+1)(b5+1)(b4+1)(1 + b[3:0]/16)
    int r45 = sensor_reg(s, 1, 0x45), aec = sensor_reg(s, 1, 0x10);
    int r04 = sensor_reg(s, 1, 0x04), gain = sensor_reg(s, 1, 0x00);
    if (r45 < 0 || aec < 0 || r04 < 0 || gain < 0) {
      ESP_LOGW(TAG, "live register read failed: 45=%d 10=%d 04=%d 00=%d", r45,
               aec, r04, gain);
      sb_printf(&b, "\"live\":null,");
    } else {
      int exposure = ((r45 & 0x3f) << 10) | (aec << 2) | (r04 & 3);
      int gain_x100 = ((gain >> 7 & 1) + 1) * ((gain >> 6 & 1) + 1) *
                      ((gain >> 5 & 1) + 1) * ((gain >> 4 & 1) + 1) *
                      (100 + (gain & 0x0f) * 100 / 16);
      sb_printf(&b, "\"live\":{\"exposure_rows\":%d,\"gain_reg\":%d,\"gain_x100\":%d},",
                exposure, gain, gain_x100);
    }
  } else {
    sb_printf(&b, "\"live\":null,");
  }
  sb_printf(&b, "\"frame_interval_ms\":%.1f}", (double)g_frame_interval_us / 1000.0);

  if (b.overflow) {
    return send_error(req, "500 Internal Server Error", "status JSON exceeded its buffer");
  }
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  return httpd_resp_send(req, buf, (ssize_t)b.len);
}

// One setting by name (the page's /set and the serial "S" command): NULL when
// applied, else why not
static const char *apply_setting(const char *var, int val) {
  static char msg[96];
  if (strcmp(var, "defaults") == 0) {
    memset(g_setting_user, 0, sizeof(g_setting_user));
    return camera_switch(&g_mode) == ESP_OK ? NULL : g_last_error;
  }

  cam_mode_t next = g_mode;
  bool mode_var = true;
  if (strcmp(var, "framesize") == 0) {
    if (!framesize_offered(val)) {
      return "framesize not offered";
    }
    next.framesize = (framesize_t)val;
  } else if (strcmp(var, "field") == 0) {
    if (val < 0 || val >= FIELD_COUNT) {
      return "field: 0 full, 1 wide, 2 half, 3 native";
    }
    next.field = (field_t)val;
  } else if (strcmp(var, "format") == 0) {
    if (val != 0 && val != 1) {
      return "format: 0 jpeg, 1 gray";
    }
    next.gray = val == 1;
  } else if (strcmp(var, "xclk") == 0) {
    if (val != 10 && val != 20) {
      return "xclk: 10 or 20 (MHz)";
    }
    next.xclk_mhz = val;
  } else {
    mode_var = false;
  }
  if (mode_var) {
    return camera_switch(&next) == ESP_OK ? NULL : g_last_error;
  }

  int id = setting_find(var);
  if (id < 0) {
    return "unknown var";
  }
  if (val < SETTINGS[id].min || val > SETTINGS[id].max) {
    return "val out of range";
  }
  if (!g_cam_running) {
    return "camera not running";
  }
  sensor_t *s = esp_camera_sensor_get();
  int r = setting_call(s, id, val);
  if (r != 0) {
    snprintf(msg, sizeof(msg), "driver setter for %s=%d returned %d", var, val, r);
    return msg;
  }
  g_setting_value[id] = val;
  g_setting_user[id] = true;
  return NULL;
}

static esp_err_t set_get(httpd_req_t *req) {
  char *q = get_query(req);
  char var[24] = "";
  if (q == NULL || httpd_query_key_value(q, "var", var, sizeof(var)) != ESP_OK) {
    free(q);
    return send_error(req, "400 Bad Request", "need ?var=NAME&val=INT");
  }
  int val = 0;
  bool have_val = false;
  bool val_ok = query_int(q, "val", &val, &have_val);
  free(q);
  if (strcmp(var, "defaults") != 0 && (!val_ok || !have_val)) {
    return send_error(req, "400 Bad Request", "val missing or not an integer");
  }
  const char *err = apply_setting(var, val);
  if (err != NULL) {
    return send_error(req, "400 Bad Request", err);
  }
  return status_get(req);
}

static esp_err_t raw_get(httpd_req_t *req) {
  char *q = get_query(req);
  char fmt[8] = "";
  int skip = 0;
  bool have_skip;
  bool skip_ok = true;
  if (q != NULL) {
    esp_err_t fe = httpd_query_key_value(q, "fmt", fmt, sizeof(fmt));
    if (fe != ESP_OK && fe != ESP_ERR_NOT_FOUND) {
      free(q);
      return send_error(req, "400 Bad Request", "fmt: y8 or jpeg");
    }
    if (fe == ESP_ERR_NOT_FOUND) {
      fmt[0] = '\0';  // absent: the current format's own
    }
    skip_ok = query_int(q, "skip", &skip, &have_skip);
    free(q);
  }
  if (!skip_ok || skip < 0 || skip > 30) {
    return send_error(req, "400 Bad Request", "skip: 0..30");
  }
  if (fmt[0] == '\0') {
    strlcpy(fmt, g_mode.gray ? "y8" : "jpeg", sizeof(fmt));
  }
  bool want_y8 = strcmp(fmt, "y8") == 0;
  if (!want_y8 && strcmp(fmt, "jpeg") != 0) {
    return send_error(req, "400 Bad Request", "fmt: y8 or jpeg");
  }
  if (want_y8 != g_mode.gray) {
    return send_error(req, "409 Conflict",
                      want_y8 ? "camera is in JPEG mode: set format=1 (gray) for y8"
                              : "camera is in grayscale mode: set format=0 for jpeg");
  }
  if (!g_cam_running) {
    return send_error(req, "503 Service Unavailable", "camera not running");
  }

  for (int i = 0; i <= skip; i++) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb == NULL) {
      return send_error(req, "503 Service Unavailable", "no frame: capture timed out");
    }
    int64_t t = (int64_t)fb->timestamp.tv_sec * 1000000 + fb->timestamp.tv_usec;
    if (g_last_frame_us != 0 && t > g_last_frame_us) {
      g_frame_interval_us = t - g_last_frame_us;
    }
    g_last_frame_us = t;
    if (i < skip) {
      esp_camera_fb_return(fb);
      continue;
    }

    if (want_y8 && fb->len != fb->width * fb->height) {
      char msg[80];
      snprintf(msg, sizeof(msg), "frame is %u bytes, %ux%u expected",
               (unsigned)fb->len, (unsigned)fb->width, (unsigned)fb->height);
      esp_camera_fb_return(fb);
      return send_error(req, "500 Internal Server Error", msg);
    }
    char wbuf[8], hbuf[8], tbuf[24], disp[64], head[32];
    snprintf(wbuf, sizeof(wbuf), "%u", (unsigned)fb->width);
    snprintf(hbuf, sizeof(hbuf), "%u", (unsigned)fb->height);
    snprintf(tbuf, sizeof(tbuf), "%lld", (long long)t);
    snprintf(disp, sizeof(disp), "inline; filename=\"ov2640_%ux%u.%s\"",
             (unsigned)fb->width, (unsigned)fb->height, want_y8 ? "pgm" : "jpg");
    httpd_resp_set_type(req, want_y8 ? "image/x-portable-graymap" : "image/jpeg");
    httpd_resp_set_hdr(req, "X-Width", wbuf);
    httpd_resp_set_hdr(req, "X-Height", hbuf);
    httpd_resp_set_hdr(req, "X-Timestamp-Us", tbuf);
    httpd_resp_set_hdr(req, "Content-Disposition", disp);
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");

    esp_err_t err = ESP_OK;
    if (want_y8) {
      int n = snprintf(head, sizeof(head), "P5\n%u %u\n255\n", (unsigned)fb->width,
                       (unsigned)fb->height);
      err = httpd_resp_send_chunk(req, head, n);
    }
    if (err == ESP_OK) {
      err = httpd_resp_send_chunk(req, (const char *)fb->buf, (ssize_t)fb->len);
    }
    if (err == ESP_OK) {
      err = httpd_resp_send_chunk(req, NULL, 0);
    }
    esp_camera_fb_return(fb);
    if (err != ESP_OK) {
      ESP_LOGW(TAG, "/raw: sending the frame failed: %s", esp_err_to_name(err));
    }
    return err;
  }
  return ESP_FAIL;  // unreachable: the loop returns on its last pass
}

static esp_err_t reg_get(httpd_req_t *req) {
  char *q = get_query(req);
  int bank = -1, addr = -1, val = -1;
  bool hb, ha, hv;
  bool ok = q != NULL && query_int(q, "bank", &bank, &hb) &&
            query_int(q, "addr", &addr, &ha) && query_int(q, "val", &val, &hv);
  free(q);
  if (!ok || !hb || !ha || bank < 0 || bank > 1 || addr < 0 || addr > 0xff ||
      (hv && (val < 0 || val > 0xff))) {
    return send_error(req, "400 Bad Request", "need ?bank=0|1&addr=0..255[&val=0..255]");
  }
  if (!g_cam_running) {
    return send_error(req, "503 Service Unavailable", "camera not running");
  }
  sensor_t *s = esp_camera_sensor_get();
  if (hv) {
    int r = s->set_reg(s, (bank << 8) | addr, 0xff, val);
    if (r != 0) {
      return send_error(req, "500 Internal Server Error", "register write failed");
    }
  }
  int v = sensor_reg(s, bank, addr);
  if (v < 0) {
    return send_error(req, "500 Internal Server Error", "register read failed");
  }
  char out[64];
  snprintf(out, sizeof(out), "{\"bank\":%d,\"addr\":%d,\"value\":%d}", bank, addr, v);
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_sendstr(req, out);
}

// Both banks in camdbg.py dump's layout (16 per row, "--" for a failed
// read), so a dump from our device diffs against this one line by line
static esp_err_t regs_get(httpd_req_t *req) {
  if (!g_cam_running) {
    return send_error(req, "503 Service Unavailable", "camera not running");
  }
  sensor_t *s = esp_camera_sensor_get();
  httpd_resp_set_type(req, "text/plain");
  char line[64];
  esp_err_t err = ESP_OK;
  for (int bank = 0; bank <= 1 && err == ESP_OK; bank++) {
    int n = snprintf(line, sizeof(line), "# bank %d (%s), PID 0x%02x\n", bank,
                     bank ? "sensor" : "DSP", s->id.PID);
    err = httpd_resp_send_chunk(req, line, n);
    for (int row = 0; row < 256 && err == ESP_OK; row += 16) {
      n = snprintf(line, sizeof(line), "%02x:", row);
      for (int i = 0; i < 16; i++) {
        int v = sensor_reg(s, bank, row + i);
        n += v < 0 ? snprintf(line + n, sizeof(line) - n, " --")
                   : snprintf(line + n, sizeof(line) - n, " %02x", v);
      }
      n += snprintf(line + n, sizeof(line) - n, "\n");
      err = httpd_resp_send_chunk(req, line, n);
    }
  }
  if (err == ESP_OK) {
    err = httpd_resp_send_chunk(req, NULL, 0);
  }
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "/regs: sending failed: %s", esp_err_to_name(err));
  }
  return err;
}

// ---------------------------------------------------------------------------
// Start-up

static void wifi_start_ap(void) {
  ESP_ERROR_CHECK(esp_netif_init());
  ESP_ERROR_CHECK(esp_event_loop_create_default());
  esp_netif_create_default_wifi_ap();
  wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_wifi_init(&init));
  wifi_config_t wc = {
      .ap =
          {
              .ssid = AP_SSID,
              .ssid_len = sizeof(AP_SSID) - 1,
              .channel = AP_CHANNEL,
              .password = AP_PASS,
              .max_connection = 4,
              .authmode = WIFI_AUTH_WPA2_PSK,
          },
  };
  ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
  ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wc));
  ESP_ERROR_CHECK(esp_wifi_start());
  ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
  ESP_LOGI(TAG, "Wi-Fi AP \"%s\" (password \"%s\") up: http://192.168.4.1/", AP_SSID,
           AP_PASS);
}

static void http_start(void) {
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.stack_size = 8192;
  cfg.max_uri_handlers = 8;
  cfg.lru_purge_enable = true;
  cfg.send_wait_timeout = 10;
  httpd_handle_t srv = NULL;
  ESP_ERROR_CHECK(httpd_start(&srv, &cfg));
  const httpd_uri_t uris[] = {
      {.uri = "/", .method = HTTP_GET, .handler = page_get},
      {.uri = "/status", .method = HTTP_GET, .handler = status_get},
      {.uri = "/set", .method = HTTP_GET, .handler = set_get},
      {.uri = "/raw", .method = HTTP_GET, .handler = raw_get},
      {.uri = "/reg", .method = HTTP_GET, .handler = reg_get},
      {.uri = "/regs", .method = HTTP_GET, .handler = regs_get},
  };
  for (size_t i = 0; i < sizeof(uris) / sizeof(uris[0]); i++) {
    ESP_ERROR_CHECK(httpd_register_uri_handler(srv, &uris[i]));
  }
}


// ---------------------------------------------------------------------------
// Serial (UART0, the USB-serial cable): frames and settings without Wi-Fi.
// After boot the log goes quiet (it would land inside frames) and the port
// stays at 115200 until the host asks for more. Commands, one per line:
//   B <baud>      "OK\n" at the old rate, then the new one; back to 115200 if
//                 no command arrives at the new rate within 2 s (an adapter
//                 that cannot run it)
//   G [skip]      a grayscale frame: "FRM1", u16 width, u16 height, u32 length
//                 (little endian), the Y8 bytes, "END1", u32 CRC-32 of the
//                 bytes (zlib's); or "ERR <why>\n"
//   S <var> <val> a setting, as the page's /set: "OK\n" or "ERR <why>\n"
//   Q             "ST <framesize> <field> <gray> <xclk> <aec_value> <gain>\n"

#define SERIAL_BAUD_BOOT 115200
#define SERIAL_TRIAL_MS 2000

// Set by "B": the rate on trial, and when the trial ends
static uint32_t g_serial_trial_baud;
static int64_t g_serial_trial_end_us;

static void serial_reply(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void serial_reply(const char *fmt, ...) {
  char line[160];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(line, sizeof(line), fmt, ap);
  va_end(ap);
  if (n > (int)sizeof(line) - 1) {
    n = (int)sizeof(line) - 1;
  }
  uart_write_bytes(UART_NUM_0, line, (size_t)n);
}

static void serial_frame(int skip) {
  if (!g_cam_running) {
    serial_reply("ERR camera not running: %s\n", g_last_error);
    return;
  }
  if (!g_mode.gray) {
    serial_reply("ERR camera is in JPEG mode: S format 1\n");
    return;
  }
  for (int i = 0; i <= skip; i++) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb == NULL) {
      serial_reply("ERR no frame: capture timed out\n");
      return;
    }
    if (i < skip) {
      esp_camera_fb_return(fb);
      continue;
    }
    if (fb->len != fb->width * fb->height) {
      serial_reply("ERR frame is %u bytes, %ux%u expected\n", (unsigned)fb->len,
                   (unsigned)fb->width, (unsigned)fb->height);
      esp_camera_fb_return(fb);
      return;
    }
    uint8_t head[12] = {'F', 'R', 'M', '1'};
    head[4] = (uint8_t)fb->width;
    head[5] = (uint8_t)(fb->width >> 8);
    head[6] = (uint8_t)fb->height;
    head[7] = (uint8_t)(fb->height >> 8);
    for (int b = 0; b < 4; b++) {
      head[8 + b] = (uint8_t)(fb->len >> (8 * b));
    }
    uart_write_bytes(UART_NUM_0, head, sizeof(head));
    uart_write_bytes(UART_NUM_0, fb->buf, fb->len);
    uint32_t crc = esp_rom_crc32_le(0, fb->buf, fb->len);
    uint8_t tail[8] = {'E', 'N', 'D', '1', (uint8_t)crc, (uint8_t)(crc >> 8),
                       (uint8_t)(crc >> 16), (uint8_t)(crc >> 24)};
    uart_write_bytes(UART_NUM_0, tail, sizeof(tail));
    esp_camera_fb_return(fb);
  }
}

static void serial_command(char *line) {
  char *cmd = strtok(line, " \r");
  if (cmd == NULL) {
    return;
  }
  if (strcmp(cmd, "G") == 0) {
    char *a = strtok(NULL, " \r");
    int skip = a != NULL ? atoi(a) : 0;
    serial_frame(skip < 0 ? 0 : skip > 30 ? 30 : skip);
  } else if (strcmp(cmd, "S") == 0) {
    char *var = strtok(NULL, " \r");
    char *val = strtok(NULL, " \r");
    if (var == NULL || (val == NULL && strcmp(var, "defaults") != 0)) {
      serial_reply("ERR S <var> <val>\n");
      return;
    }
    const char *err = apply_setting(var, val != NULL ? atoi(val) : 0);
    if (err != NULL) {
      serial_reply("ERR %s\n", err);
    } else {
      serial_reply("OK\n");
    }
  } else if (strcmp(cmd, "B") == 0) {
    char *a = strtok(NULL, " \r");
    int baud = a != NULL ? atoi(a) : 0;
    if (baud < 9600 || baud > 3000000) {
      serial_reply("ERR B <baud>\n");
      return;
    }
    serial_reply("OK\n");
    uart_wait_tx_done(UART_NUM_0, pdMS_TO_TICKS(100));
    uart_set_baudrate(UART_NUM_0, (uint32_t)baud);
    uart_flush_input(UART_NUM_0);
    g_serial_trial_baud = (uint32_t)baud;
    g_serial_trial_end_us = esp_timer_get_time() + SERIAL_TRIAL_MS * 1000;
  } else if (strcmp(cmd, "Q") == 0) {
    sensor_t *s = g_cam_running ? esp_camera_sensor_get() : NULL;
    serial_reply("ST %d %d %d %d %d %d\n", (int)g_mode.framesize, (int)g_mode.field,
                 g_mode.gray ? 1 : 0, g_mode.xclk_mhz, s ? s->status.aec_value : -1,
                 s ? s->status.agc_gain : -1);
  } else {
    serial_reply("ERR unknown command %s (G, S, Q)\n", cmd);
  }
}

static void serial_task(void *arg) {
  (void)arg;
  esp_err_t err = uart_driver_install(UART_NUM_0, 1024, 0, 0, NULL, 0);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "serial: UART driver failed: %s", esp_err_to_name(err));
    vTaskDelete(NULL);
    return;
  }
  // printf, not the log (errors only): the host waits for this line
  printf("ov2640ref: serial ready at %d baud (B to change), log off\n", SERIAL_BAUD_BOOT);
  fflush(stdout);
  vTaskDelay(pdMS_TO_TICKS(50));
  esp_log_level_set("*", ESP_LOG_NONE);
  char line[80];
  size_t n = 0;
  for (;;) {
    uint8_t c;
    if (uart_read_bytes(UART_NUM_0, &c, 1, pdMS_TO_TICKS(100)) != 1) {
      if (g_serial_trial_baud != 0 && esp_timer_get_time() > g_serial_trial_end_us) {
        // Nothing understood at the new rate: back to the boot rate
        g_serial_trial_baud = 0;
        uart_set_baudrate(UART_NUM_0, SERIAL_BAUD_BOOT);
        uart_flush_input(UART_NUM_0);
        n = 0;
      }
      continue;
    }
    if (c == '\n') {
      line[n] = '\0';
      bool trial = g_serial_trial_baud != 0;
      serial_command(line);
      if (trial && strncmp(line, "B", 1) != 0) {
        g_serial_trial_baud = 0;  // a command arrived at the new rate: kept
      }
      n = 0;
    } else if (n < sizeof(line) - 1) {
      line[n++] = (char)c;
    }
  }
}

void app_main(void) {
  esp_err_t err = nvs_flash_init();
  if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_LOGW(TAG, "NVS needs erasing (%s): erasing", esp_err_to_name(err));
    ESP_ERROR_CHECK(nvs_flash_erase());
    err = nvs_flash_init();
  }
  ESP_ERROR_CHECK(err);

  // The camera first: a failure is shown on the page, the server runs anyway
  if (camera_start(&g_mode) != ESP_OK) {
    ESP_LOGE(TAG, "camera did not start: %s", g_last_error);
  }
  wifi_start_ap();
  http_start();
  xTaskCreate(serial_task, "serial", 4096, NULL, 5, NULL);
}
