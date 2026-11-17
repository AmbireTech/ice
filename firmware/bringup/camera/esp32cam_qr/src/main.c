/*
 * QR scanner on an Ai-Thinker ESP32-CAM with an OV2640, for comparison with
 * the signer: the signer's own QR decoder (io/camera/qr/qr_scan.c and its
 * quirc, built from the firmware's sources) on Espressif's camera driver.
 * Results are printed as text on the USB-serial port (115200 baud):
 *
 *   QR OK v11 4.2 px/module (frame 381, decode 212 ms)
 *   UR:ETH-SIGN-REQUEST/...
 *
 * and every few seconds one status line saying what the decoder sees.
 *
 * The sensor runs in grayscale at the largest frame size that streams
 * reliably here (checked at start: SVGA, VGA, CIF, QVGA), over the whole
 * field of view. Exposure is set from the frames, as on the signer: the
 * sensor's own AEC averages the whole view, mostly a dim room around a bright
 * screen, and overexposes the screen; here the brightest part of the frame's
 * centre is held below clipping, the exposure capped against hand blur, gain
 * making up the rest. A frame that shows a code but does not decode is tried
 * with each of the decoder's thresholds.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_camera.h"
#include "esp_partition.h"
#include "esp_rom_crc.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <io/qr_scan.h>

#include "qr_roi.h"

static const char *TAG = "qrscan";

// Ai-Thinker ESP32-CAM
static camera_config_t camera_config(framesize_t fs, size_t fb_count) {
  camera_config_t c = {
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
      .xclk_freq_hz = 20000000,
      .ledc_timer = LEDC_TIMER_0,
      .ledc_channel = LEDC_CHANNEL_0,
      .pixel_format = PIXFORMAT_GRAYSCALE,
      .frame_size = fs,
      .jpeg_quality = 0,
      .fb_count = fb_count,
      .fb_location = CAMERA_FB_IN_PSRAM,
      .grab_mode = CAMERA_GRAB_LATEST,
  };
  return c;
}

// Largest first: more pixels per module at the same distance
static const framesize_t CANDIDATES[] = {FRAMESIZE_CIF, FRAMESIZE_QVGA};

typedef struct {
  framesize_t fs;
  int w, h;
  float fps;
} scan_mode_t;

// Starts the camera at `fs`, retrying: this board misses the sensor at some
// starts
static bool camera_start(framesize_t fs) {
  size_t w = resolution[fs].width, h = resolution[fs].height;
  // Two buffers (the driver fills one while we decode the other) where the
  // PSRAM holds them beside our two frame-sized buffers
  size_t fb_count = w * h <= 320 * 240 ? 2 : (w * h <= 640 * 480 ? 2 : 1);
  for (int attempt = 1; attempt <= 5; attempt++) {
    // The sensor powered down for a while first (PWDN high): a reset of the
    // ESP32 alone leaves it powered, and some starts then read no valid chip id
    gpio_reset_pin(GPIO_NUM_32);
    gpio_set_direction(GPIO_NUM_32, GPIO_MODE_OUTPUT);
    gpio_set_level(GPIO_NUM_32, 1);
    vTaskDelay(pdMS_TO_TICKS(500 * attempt));
    camera_config_t cfg = camera_config(fs, fb_count);
    esp_err_t err = esp_camera_init(&cfg);
    if (err == ESP_OK) {
      return true;
    }
    printf("camera start %ux%u failed (%s), attempt %d of 5\n", (unsigned)w, (unsigned)h,
           esp_err_to_name(err), attempt);
    esp_err_t d = esp_camera_deinit();
    if (d != ESP_OK && d != ESP_ERR_INVALID_STATE) {
      printf("camera deinit after a failed start: %s\n", esp_err_to_name(d));
    }
    vTaskDelay(pdMS_TO_TICKS(300));
  }
  return false;
}

// Frames that arrive whole, and how fast
static bool camera_check(scan_mode_t *m) {
  int good = 0;
  int64_t t0 = 0, t1 = 0;
  for (int i = 0; i < 8; i++) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb == NULL) {
      continue;
    }
    bool whole = fb->len == (size_t)(m->w * m->h);
    esp_camera_fb_return(fb);
    if (!whole) {
      continue;
    }
    int64_t now = esp_timer_get_time();
    if (good == 2) {
      t0 = now;  // the first frames come out of a pipeline still settling
    }
    if (good >= 2) {
      t1 = now;
    }
    good++;
  }
  m->fps = good > 3 && t1 > t0 ? (float)(good - 3) * 1e6f / (float)(t1 - t0) : 0.0f;
  return good >= 6;
}

static bool choose_mode(scan_mode_t *m) {
  for (size_t i = 0; i < sizeof(CANDIDATES) / sizeof(CANDIDATES[0]); i++) {
    m->fs = CANDIDATES[i];
    m->w = resolution[m->fs].width;
    m->h = resolution[m->fs].height;
    if (!camera_start(m->fs)) {
      continue;
    }
    if (camera_check(m)) {
      return true;
    }
    printf("%dx%d does not stream reliably here, trying smaller\n", m->w, m->h);
    esp_err_t d = esp_camera_deinit();
    if (d != ESP_OK) {
      printf("camera deinit: %s\n", esp_err_to_name(d));
    }
  }
  return false;
}

// The driver runs the OV2640's internal clock at XCLK / 8 on the plain ESP32
// (CLKRC 0x07, for the I2S camera input's sake): about 1.6 fps at SVGA. Faster
// dividers are tried, fastest first, and the fastest that still streams whole
// frames is kept. CLKRC is register 0x11 in the sensor bank (the driver's
// set_reg: bit 8 of the address selects the bank).
#define REG_CLKRC 0x111

static void fastest_clock(sensor_t *s, scan_mode_t *m) {
  static const int dividers[] = {0x01, 0x03};
  float base_fps = m->fps;
  for (size_t i = 0; i < sizeof(dividers) / sizeof(dividers[0]); i++) {
    if (s->set_reg(s, REG_CLKRC, 0xff, dividers[i]) != 0) {
      printf("CLKRC write failed\n");
      continue;
    }
    vTaskDelay(pdMS_TO_TICKS(300));
    scan_mode_t trial = *m;
    if (camera_check(&trial) && trial.fps > base_fps * 1.3f) {
      printf("sensor clock XCLK/%d: %.1f fps (driver's XCLK/8: %.1f)\n", (dividers[i] + 1),
             trial.fps, base_fps);
      *m = trial;
      return;
    }
  }
  if (s->set_reg(s, REG_CLKRC, 0xff, 0x07) != 0) {
    printf("CLKRC restore failed\n");
  }
  vTaskDelay(pdMS_TO_TICKS(300));
  printf("sensor clock: the driver's XCLK/8 (faster ones did not stream)\n");
}

// ---------------------------------------------------------------------------
// Exposure from the frames (OV2640 through the driver's own setters)

#define AE_TARGET 200
#define AE_MAX_MS 10

typedef struct {
  int aec;       // exposure, sensor lines (the driver takes 0..1200)
  int agc;       // gain step (0..30)
  int aec_max;   // the longest exposure allowed
} ae_t;

static void ae_apply(sensor_t *s, const ae_t *ae) {
  if (s->set_aec_value(s, ae->aec) != 0 || s->set_agc_gain(s, ae->agc) != 0) {
    printf("exposure write failed (aec %d, gain %d)\n", ae->aec, ae->agc);
  }
}

static void ae_start(sensor_t *s, const scan_mode_t *m, ae_t *ae) {
  // Lines a frame lasts in the sensor's mode (the driver uses CIF timing up
  // to 400x296, SVGA above): the 10 ms cap in lines from the measured rate
  int lines = m->w * m->h <= 400 * 296 ? 336 : 672;
  float fps = m->fps > 0.5f ? m->fps : 5.0f;
  ae->aec_max = (int)((float)lines * fps * AE_MAX_MS / 1000.0f);
  if (ae->aec_max < 8) {
    ae->aec_max = 8;
  }
  ae->aec = ae->aec_max / 2;
  ae->agc = 4;
  if (s->set_exposure_ctrl(s, 0) != 0 || s->set_gain_ctrl(s, 0) != 0) {
    printf("could not turn the sensor's AEC/AGC off\n");
  }
  ae_apply(s, ae);
}

// After each frame: the centre's 98th percentile steers exposure and gain
static void ae_step(sensor_t *s, ae_t *ae, const uint8_t *px, int w, int h) {
  uint32_t hist[64] = {0}, n = 0;
  for (int y = h / 4; y < h * 3 / 4; y += 4) {
    for (int x = w / 4; x < w * 3 / 4; x += 4) {
      hist[px[y * w + x] >> 2]++;
      n++;
    }
  }
  uint32_t above = 0;
  int bin = 63;
  while (bin > 0 && (above + hist[bin]) * 50 < n) {
    above += hist[bin--];
  }
  int p98 = bin * 4 + 2;

  ae_t next = *ae;
  if (p98 >= 250) {
    // Clipped: halve the exposure, then the gain
    if (next.aec > 2) {
      next.aec /= 2;
    } else if (next.agc > 0) {
      next.agc = next.agc > 3 ? next.agc - 3 : 0;
    }
  } else {
    int want = ae->aec * AE_TARGET / (p98 > 8 ? p98 : 8);
    if (want * 100 > ae->aec * 85 && want * 100 < ae->aec * 118) {
      return;  // close enough
    }
    next.aec = (ae->aec + want) / 2;
    if (next.aec > next.aec_max) {
      next.aec = next.aec_max;
      if (next.agc < 30) {
        next.agc++;  // too dark at the longest exposure: more gain
      }
    } else if (next.aec < next.aec_max / 2 && next.agc > 0) {
      next.agc--;  // bright enough: less gain (less noise), longer exposure
      next.aec = next.aec * 5 / 4;
    }
    if (next.aec < 1) {
      next.aec = 1;
    }
  }
  if (next.aec != ae->aec || next.agc != ae->agc) {
    *ae = next;
    ae_apply(s, ae);
  }
}

// ---------------------------------------------------------------------------

static const char *qr_error_name(int e) {
  static const char *names[] = {"ok", "grid size", "version", "format ECC",
                                "data ECC", "data type", "data overflow", "data underflow"};
  return e >= 0 && e < (int)(sizeof(names) / sizeof(names[0])) ? names[e] : "?";
}

static void print_payload(const uint8_t *p, int n) {
  for (int i = 0; i < n; i++) {
    putchar(p[i] >= 0x20 && p[i] < 0x7f ? p[i] : '.');
  }
  putchar('\n');
}

// ---------------------------------------------------------------------------
// Near-miss frames, kept in flash (opening the serial port resets the board,
// which would lose them in RAM). Slot 0..SLOTS_MISS-1: frames that showed a
// code (finder patterns in view) that did not decode, one every 2 s at most;
// the rest: decoded frames, one every 10 s. A serial "D" dumps them.

#define SLOT_SIZE 0x20000
#define SLOTS 16
#define SLOTS_MISS 12
#define FRAME_MAGIC 0x31465251u  // "QRF1"

typedef struct {
  uint32_t magic;
  uint32_t seq;
  uint16_t w, h;
  int16_t px_x10;
  uint8_t error, version, decoded, threshold, agc, grids;
  uint16_t aec;
  uint16_t decode_ms;
  uint8_t pad[8];
} frame_header_t;
_Static_assert(sizeof(frame_header_t) == 32, "the header is 32 bytes");

static const esp_partition_t *g_frames;
// "T" replays the saved frames through the decoder the scan loop uses (set up
// by scan_main; the scan loop is paused meanwhile)
static volatile bool g_replay;
static uint32_t g_seq;
static volatile bool g_quiet, g_pause;

static void frames_init(void) {
  g_frames = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "frames");
  if (g_frames == NULL) {
    printf("no 'frames' partition: near-miss frames are not kept\n");
    return;
  }
  int kept = 0;
  for (int i = 0; i < SLOTS; i++) {
    frame_header_t h;
    if (esp_partition_read(g_frames, (size_t)i * SLOT_SIZE, &h, sizeof(h)) == ESP_OK &&
        h.magic == FRAME_MAGIC) {
      kept++;
      if (h.seq >= g_seq) {
        g_seq = h.seq + 1;
      }
    }
  }
  printf("%d saved frames in flash (D dumps them, E erases)\n", kept);
}

static void frames_save(const uint8_t *px, const scan_mode_t *m, const qr_scan_result_t *r,
                        const ae_t *ae, int decode_ms) {
  static int64_t last_miss_us = -10000000, last_ok_us = -100000000;
  static uint32_t n_miss, n_ok;
  if (g_frames == NULL) {
    return;
  }
  int64_t now = esp_timer_get_time();
  int slot;
  if (r->decoded) {
    if (now - last_ok_us < 10000000) {
      return;
    }
    last_ok_us = now;
    slot = SLOTS_MISS + (int)(n_ok++ % (SLOTS - SLOTS_MISS));
  } else {
    if (r->capstones < 2 || now - last_miss_us < 2000000) {
      return;
    }
    last_miss_us = now;
    slot = (int)(n_miss++ % SLOTS_MISS);
  }
  frame_header_t h = {
      .magic = FRAME_MAGIC,
      .seq = g_seq++,
      .w = (uint16_t)m->w,
      .h = (uint16_t)m->h,
      .px_x10 = (int16_t)r->px_per_module_x10,
      .error = (uint8_t)r->error,
      .version = (uint8_t)r->version,
      .decoded = (uint8_t)r->decoded,
      .threshold = (uint8_t)r->threshold,
      .agc = (uint8_t)ae->agc,
      .grids = (uint8_t)r->grids,
      .aec = (uint16_t)ae->aec,
      .decode_ms = (uint16_t)(decode_ms > 65535 ? 65535 : decode_ms),
  };
  size_t at = (size_t)slot * SLOT_SIZE;
  esp_err_t err = esp_partition_erase_range(g_frames, at, SLOT_SIZE);
  if (err == ESP_OK) {
    err = esp_partition_write(g_frames, at + sizeof(h), px, (size_t)m->w * m->h);
  }
  if (err == ESP_OK) {
    // The header last: a slot cut short by a reset has no magic
    err = esp_partition_write(g_frames, at, &h, sizeof(h));
  }
  if (err != ESP_OK) {
    printf("saving a frame failed: %s\n", esp_err_to_name(err));
  }
}

// ---------------------------------------------------------------------------
// Serial commands (UART0, the USB-serial cable), one per line:
//   B <baud>  "OK" at the old rate, then the new one (back to 115200 after 2 s
//             without a command at the new rate)
//   D [slot]  every saved frame (or one slot's): "FRM2", the 32-byte header, the
//             pixels, "END1", CRC-32 (zlib's) of header and pixels; then
//             "DONE <n>"
//   E         erase the saved frames

static void uart_out(const void *p, size_t n) { uart_write_bytes(UART_NUM_0, p, n); }

// Every slot (only < 0), or just `only`
static void frames_dump(int only) {
  static uint8_t chunk[4096];
  int sent = 0;
  for (int i = 0; g_frames != NULL && i < SLOTS; i++) {
    if (only >= 0 && i != only) {
      continue;
    }
    frame_header_t h;
    size_t at = (size_t)i * SLOT_SIZE;
    if (esp_partition_read(g_frames, at, &h, sizeof(h)) != ESP_OK || h.magic != FRAME_MAGIC) {
      continue;
    }
    uart_out("FRM2", 4);
    uart_out(&h, sizeof(h));
    uint32_t crc = esp_rom_crc32_le(0, (const uint8_t *)&h, sizeof(h));
    size_t left = (size_t)h.w * h.h, off = at + sizeof(h);
    while (left > 0) {
      size_t n = left < sizeof(chunk) ? left : sizeof(chunk);
      if (esp_partition_read(g_frames, off, chunk, n) != ESP_OK) {
        memset(chunk, 0, n);  // the CRC then tells the host this frame is bad
      }
      crc = esp_rom_crc32_le(crc, chunk, n);
      uart_out(chunk, n);
      off += n;
      left -= n;
    }
    uint8_t tail[8] = {'E', 'N', 'D', '1', (uint8_t)crc, (uint8_t)(crc >> 8),
                       (uint8_t)(crc >> 16), (uint8_t)(crc >> 24)};
    uart_out(tail, sizeof(tail));
    sent++;
  }
  char line[24];
  int n = snprintf(line, sizeof(line), "DONE %d\n", sent);
  uart_out(line, (size_t)n);
}

static void serial_task(void *arg) {
  (void)arg;
  esp_err_t err = uart_driver_install(UART_NUM_0, 1024, 0, 0, NULL, 0);
  if (err != ESP_OK) {
    printf("serial commands off: UART driver failed (%s)\n", esp_err_to_name(err));
    vTaskDelete(NULL);
    return;
  }
  uint32_t trial_baud = 0;
  int64_t trial_end = 0;
  char line[48];
  size_t n = 0;
  for (;;) {
    uint8_t c;
    if (uart_read_bytes(UART_NUM_0, &c, 1, pdMS_TO_TICKS(100)) != 1) {
      if (trial_baud != 0 && esp_timer_get_time() > trial_end) {
        trial_baud = 0;
        uart_set_baudrate(UART_NUM_0, 115200);
        g_quiet = false;
      }
      continue;
    }
    if (c != '\n') {
      if (n < sizeof(line) - 1) {
        line[n++] = (char)c;
      }
      continue;
    }
    line[n] = '\0';
    n = 0;
    if (line[0] == 'B') {
      int baud = atoi(line + 1);
      if (baud < 9600 || baud > 3000000) {
        uart_out("ERR B <baud>\n", 13);
        continue;
      }
      g_quiet = true;  // no status lines inside the transfer
      vTaskDelay(pdMS_TO_TICKS(50));
      uart_out("OK\n", 3);
      uart_wait_tx_done(UART_NUM_0, pdMS_TO_TICKS(100));
      uart_set_baudrate(UART_NUM_0, (uint32_t)baud);
      uart_flush_input(UART_NUM_0);
      trial_baud = (uint32_t)baud;
      trial_end = esp_timer_get_time() + 2000000;
    } else if (line[0] == 'D') {
      trial_baud = 0;  // a command arrived: the rate is kept
      g_quiet = true;
      g_pause = true;
      vTaskDelay(pdMS_TO_TICKS(600));  // the scan loop finishes its frame
      frames_dump(line[1] == ' ' ? atoi(line + 2) : -1);
      g_pause = false;
    } else if (line[0] == 'T') {
      g_replay = true;
    } else if (line[0] == 'E') {
      g_pause = true;
      vTaskDelay(pdMS_TO_TICKS(600));
      esp_err_t e = g_frames ? esp_partition_erase_range(g_frames, 0, g_frames->size) : ESP_FAIL;
      g_pause = false;
      uart_out(e == ESP_OK ? "OK\n" : "ERR erase\n", e == ESP_OK ? 3 : 10);
    }
  }
}

static void scan_main(void) {

  scan_mode_t m;
  if (!choose_mode(&m)) {
    printf("FATAL: the camera does not start or stream in any mode; check the module\n");
    return;
  }
  sensor_t *s = esp_camera_sensor_get();
  if (s == NULL) {
    printf("FATAL: no sensor after the camera started\n");
    return;
  }
  fastest_clock(s, &m);
  printf("camera: PID 0x%02x, %dx%d grayscale, whole field of view, %.1f fps\n", s->id.PID,
         m.w, m.h, m.fps);

  size_t img_len = (size_t)m.w * m.h;
  size_t state_len = qr_scan_state_size(m.w, m.h);
  // Internal RAM where it fits: quirc's flood fill reads the image in no
  // particular order, which the PSRAM's cache serves slowly
  uint8_t *image = heap_caps_aligned_alloc(16, img_len, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  uint8_t *state = heap_caps_aligned_alloc(16, state_len, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  bool internal = image != NULL && state != NULL;
  if (!internal) {
    heap_caps_free(image);
    heap_caps_free(state);
    image = heap_caps_aligned_alloc(16, img_len, MALLOC_CAP_SPIRAM);
    state = heap_caps_aligned_alloc(16, state_len, MALLOC_CAP_SPIRAM);
  }
  printf("decoder in %s RAM\n", internal ? "internal" : "PSRAM");
  if (image == NULL || state == NULL || !qr_scan_init(image, m.w, m.h, state, state_len)) {
    printf("FATAL: decoder setup failed (%u + %u bytes)\n", (unsigned)img_len,
           (unsigned)state_len);
    return;
  }

  // The second look (qr_roi.h): the frame upscaled, in PSRAM (up to 2.5x)
  size_t roi_len = qr_roi_image_size(m.w, m.h, 5, 2);
  size_t roi_state_len = qr_scan_state_size(m.w * 5 / 2 + 1, m.h * 5 / 2 + 1);
  uint8_t *roi = heap_caps_aligned_alloc(16, roi_len, MALLOC_CAP_SPIRAM);
  uint8_t *roi_state = heap_caps_aligned_alloc(16, roi_state_len, MALLOC_CAP_SPIRAM);
  if (roi == NULL || roi_state == NULL) {
    printf("no PSRAM for the upscaled second look (%u + %u bytes): 1x only\n",
           (unsigned)roi_len, (unsigned)roi_state_len);
  }
  // One look a frame, in turn, each one pass at the local-mean threshold
  // (it read nearly every upscaled near miss): 1.5x linear read the most and
  // is the cheapest, so two frames in three; 2x linear reads a few others.
  // (2.5x cubic read one more of 12 at ~5 s a look: left out.)
  static const qr_roi_scale_t scales[] = {
      {3, 2, QR_ROI_LINEAR, 2}, {2, 1, QR_ROI_LINEAR, 2}, {3, 2, QR_ROI_LINEAR, 2}};
  static const char *const scale_names[] = {"1.5x", "2x", "1.5x"};
  const int n_scales = (int)(sizeof(scales) / sizeof(scales[0]));
  uint32_t scale_turn = 0;
  const char *how = "1x";

  ae_t ae;
  ae_start(s, &m, &ae);
  printf("scanning: show a QR code to the camera, filling most of the view\n");

  static uint8_t payload[1200], last_payload[1200];
  int last_len = -1;
  int64_t last_print_us = 0, status_us = esp_timer_get_time(), fps_t0 = status_us;
  uint32_t frames = 0, decodes = 0, fps_frames = 0, decode_ms_sum = 0;
  qr_scan_result_t seen = {0};  // the last frame that found a code

  for (;;) {
    while (g_pause) {
      vTaskDelay(pdMS_TO_TICKS(50));
    }
    if (g_replay) {
      // Each saved frame: 1x with every threshold, then each upscaled look
      int n = 0, ok = 0;
      for (int slot = 0; g_frames != NULL && slot < SLOTS; slot++) {
        frame_header_t fh;
        size_t at = (size_t)slot * SLOT_SIZE;
        if (esp_partition_read(g_frames, at, &fh, sizeof(fh)) != ESP_OK ||
            fh.magic != FRAME_MAGIC || fh.w != m.w || fh.h != m.h ||
            esp_partition_read(g_frames, at + sizeof(fh), roi, img_len) != ESP_OK) {
          continue;
        }
        // roi holds the saved frame for the replay; the upscaled looks then
        // need their own buffer: the frame is copied to the camera-sized image
        static uint8_t *saved = NULL;
        if (saved == NULL) {
          saved = heap_caps_malloc(img_len, MALLOC_CAP_SPIRAM);
        }
        if (saved == NULL) {
          printf("replay: no memory\n");
          break;
        }
        memcpy(saved, roi, img_len);
        n++;
        int64_t t1 = esp_timer_get_time();
        int look_ms[4] = {0};  // n_scales <= 4
        qr_scan_result_t rr = {0};
        const char *at_scale = "-";
        for (int pass = 0; pass < 3 && !rr.decoded; pass++) {
          memcpy(image, saved, img_len);
          qr_scan(payload, sizeof(payload), &rr);
        }
        if (rr.decoded) {
          at_scale = "1x";
        }
        for (int k = 0; k < n_scales && !rr.decoded; k++) {
          qr_scan_result_t ur = {0};
          int64_t tl = esp_timer_get_time();
          if (qr_roi_decode(saved, m.w, m.h, NULL, scales[k], 1, roi, roi_state, roi_state_len,
                            payload, sizeof(payload), &ur)) {
            rr = ur;
            at_scale = scale_names[k];
          }
          look_ms[k] = (int)((esp_timer_get_time() - tl) / 1000);
        }
        if (!qr_scan_init(image, m.w, m.h, state, state_len)) {
          printf("FATAL: decoder setup failed after the replay\n");
          return;
        }
        ok += rr.decoded != 0;
        printf("replay frame %u (%s on the board then): %s %s, %d ms (looks %d/%d/%d/%d ms)\n",
               (unsigned)fh.seq, fh.decoded ? "decoded" : "missed",
               rr.decoded ? "DECODED at" : "not decoded", at_scale,
               (int)((esp_timer_get_time() - t1) / 1000), look_ms[0], look_ms[1], look_ms[2],
               look_ms[3]);  // the replay tries the looks once each (the 1.5x twice)
      }
      printf("replay: %d of %d saved frames decoded\n", ok, n);
      g_replay = false;
    }
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb == NULL) {
      if (!g_quiet) {
        printf("no frame (capture timed out)\n");
      }
      continue;
    }
    if (fb->len != img_len) {
      esp_camera_fb_return(fb);
      continue;
    }
    frames++;
    fps_frames++;
    ae_step(s, &ae, fb->buf, m.w, m.h);

    // Each pass thresholds the image in place: a code found but not decoded
    // gets the decoder's other thresholds on fresh copies
    qr_scan_result_t r = {0};
    int64_t t0 = esp_timer_get_time();
    for (int pass = 0; pass < 3; pass++) {
      memcpy(image, fb->buf, img_len);
      qr_scan_result_t pr;
      qr_scan(payload, sizeof(payload), &pr);
      // The pass that got furthest is reported: a decode, else one that found a code
      if (pass == 0 || pr.decoded || (pr.grids > 0 && r.grids == 0)) {
        r = pr;
      }
      // Further thresholds only while the frame is young: a frame that shows
      // a code is worth ~0.5 s, then the next frame is the better try
      if (r.decoded || pr.grids == 0 || esp_timer_get_time() - t0 > 300000) {
        break;
      }
    }
    how = "1x";
    // quirc at 1x often misses a code of 3-4 px a module, or misreads it:
    // with finder patterns in view, one upscaled look as well
    if (!r.decoded && r.capstones >= 2 && roi != NULL && roi_state != NULL) {
      int k = (int)(scale_turn++ % (uint32_t)n_scales);
      qr_scan_result_t rr = {0};
      if (qr_roi_decode(fb->buf, m.w, m.h, NULL, scales[k], 1, roi, roi_state, roi_state_len,
                        payload, sizeof(payload), &rr)) {
        r = rr;
        how = scale_names[k];
      }
      // The decoder back on the 1x frames
      if (!qr_scan_init(image, m.w, m.h, state, state_len)) {
        printf("FATAL: decoder setup failed after the second look\n");
        return;
      }
    }
    int decode_ms = (int)((esp_timer_get_time() - t0) / 1000);
    decode_ms_sum += (uint32_t)decode_ms;
    frames_save(fb->buf, &m, &r, &ae, decode_ms);
    esp_camera_fb_return(fb);

    // Lets the idle task run (the task watchdog watches it)
    vTaskDelay(1);
    int64_t now = esp_timer_get_time();
    if (r.grids > 0) {
      seen = r;
    }
    if (r.decoded) {
      decodes++;
      int n = r.payload_len < (int)sizeof(payload) ? r.payload_len : (int)sizeof(payload);
      bool same = n == last_len && memcmp(payload, last_payload, (size_t)n) == 0;
      if (!g_quiet && (!same || now - last_print_us > 3000000)) {
        printf("QR OK v%d %d.%d px/module, read at %s (frame %u, decode %d ms)\n",
               (int)r.version, (int)(r.px_per_module_x10 / 10),
               (int)(r.px_per_module_x10 % 10), how, (unsigned)frames, decode_ms);
        print_payload(payload, n);
        memcpy(last_payload, payload, (size_t)n);
        last_len = n;
        last_print_us = now;
      }
    }

    if (now - status_us > 4000000 && !g_quiet) {
      float fps = (float)fps_frames * 1e6f / (float)(now - fps_t0);
      int avg_ms = fps_frames ? (int)(decode_ms_sum / fps_frames) : 0;
      decode_ms_sum = 0;
      printf("scanning [%.1f fps, decode %d ms]: ", fps, avg_ms);
      if (seen.grids > 0 && !seen.decoded) {
        printf("%u decoded; last code seen not decoded (%s, v%d, %d.%d "
               "px/module, exposure %d gain %d)\n",
               (unsigned)decodes, qr_error_name((int)seen.error), (int)seen.version,
               (int)(seen.px_per_module_x10 / 10), (int)(seen.px_per_module_x10 % 10),
               ae.aec, ae.agc);
      } else if (seen.grids == 0) {
        printf("%u decoded; no code in view (exposure %d gain %d)\n",
               (unsigned)decodes, ae.aec, ae.agc);
      } else {
        printf("%u decoded\n", (unsigned)decodes);
      }
      memset(&seen, 0, sizeof(seen));
      status_us = now;
      fps_t0 = now;
      fps_frames = 0;
    }
  }
}

static void scan_task(void *arg) {
  (void)arg;
  scan_main();
  printf("scanning stopped\n");
  vTaskDelete(NULL);
}

void app_main(void) {
  printf("\nQR scanner (ESP32-CAM, OV2640, the signer's decoder)\n");
  frames_init();
  xTaskCreatePinnedToCore(serial_task, "serial", 4096, NULL, 4, NULL, 0);
  // Core 1: the decoder runs for up to ~0.5 s a frame, and the task watchdog
  // watches core 0's idle task
  xTaskCreatePinnedToCore(scan_task, "scan", 16384, NULL, 5, NULL, 1);
}
