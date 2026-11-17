/*
 * QR scanner on an Ai-Thinker ESP32-CAM with an OV2640: the signer's video
 * QR decoder (io/camera/qr/fqr.c, built from the firmware's sources) on
 * Espressif's camera driver. Results are printed as text on the USB-serial
 * port (115200 baud):
 *
 *   QR OK v7 3.9 px/module, tracked (frame 381, decode 34 ms)
 *   UR:ETH-SIGN-REQUEST/...
 *
 * and every few seconds one status line saying what the decoder sees.
 *
 * Frames are 400x296 grayscale, decoded where the driver put them (the
 * decoder does not modify a frame). Which sensor pixels make a frame is the
 * mode (serial "R <mode>", MODES below). Exposure is set from the frames, as
 * on the signer: the sensor's own AEC averages the whole view, mostly a dim
 * room around a bright screen, and overexposes the screen; here the
 * brightest part of the code's area (the frame's centre until a code is
 * seen) is held below clipping, the exposure capped against hand blur, gain
 * making up the rest.
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

#include "fqr.h"

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

// Which sensor pixels make the 400x296 frame, chosen over serial
// ("R <name>"). The OV2640 reads its array out in one of three ways, and its
// DSP then crops and scales (the driver's set_res_raw: readout, window
// offset, window size, output size):
//   cif   the sensor's CIF readout: every 4th pixel pair, the whole view.
//         The fastest (the sensor's short frame), the coarsest sampling.
//   half  SVGA readout (every 2nd pixel pair), the whole view, scaled 2:1
//         by the DSP: the same view from 4x the pixels.
//   full  UXGA readout (every pixel), the whole view, scaled 4:1 by the DSP:
//         the same view from 16x the pixels; the sensor's slowest frame.
//   zoom  SVGA readout, the centre 400x296 as it is: half the view each
//         way at twice the pixels a module.
// `lines`: sensor lines a frame lasts (the exposure cap is in lines).
typedef struct {
  const char *name;
  int readout;  // ov2640 readout for set_res_raw (0 UXGA, 1 SVGA, 2 CIF); -1: the driver's CIF
  int ox, oy, tw, th;
  int lines;
} cam_mode_t;
static const cam_mode_t MODES[] = {
    {"cif", -1, 0, 0, 0, 0, 336},
    {"half", 1, 0, 4, 800, 592, 672},
    {"full", 0, 0, 8, 1600, 1184, 1248},
    {"zoom", 1, 200, 152, 400, 296, 672},
};
#define N_MODES ((int)(sizeof(MODES) / sizeof(MODES[0])))
#define DEFAULT_MODE 0
#define FRAME_W 400
#define FRAME_H 296

// Set by the serial "R" command: the mode to restart in (-1: none)
static volatile int g_mode_req = -1;

typedef struct {
  const cam_mode_t *mode;
  int w, h;
  float fps;
} scan_mode_t;

// Starts the camera at `fs`, retrying: this board misses the sensor at some
// starts
static bool camera_start(framesize_t fs) {
  size_t w = resolution[fs].width, h = resolution[fs].height;
  // Two buffers: the driver fills one while we decode the other
  size_t fb_count = 2;
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

// Starts the camera in `mode`; false if it does not start or stream
static bool start_mode(const cam_mode_t *mode, scan_mode_t *m) {
  m->mode = mode;
  m->w = FRAME_W;
  m->h = FRAME_H;
  if (!camera_start(FRAMESIZE_CIF)) {
    return false;
  }
  sensor_t *s = esp_camera_sensor_get();
  if (s == NULL) {
    printf("no sensor after the camera started\n");
  } else if (mode->readout >= 0 &&
             s->set_res_raw(s, mode->readout, 0, 0, 0, mode->ox, mode->oy, mode->tw, mode->th,
                            FRAME_W, FRAME_H, false, false) != 0) {
    printf("mode %s: the sensor refused the window\n", mode->name);
  } else {
    vTaskDelay(pdMS_TO_TICKS(300));
    if (camera_check(m)) {
      return true;
    }
    printf("mode %s does not stream whole frames here\n", mode->name);
  }
  esp_err_t d = esp_camera_deinit();
  if (d != ESP_OK) {
    printf("camera deinit: %s\n", esp_err_to_name(d));
  }
  return false;
}

// The driver runs the OV2640's internal clock slowly on the plain ESP32
// (CLKRC 0x03 in the CIF readout, 0x07 in the others: XCLK / 4 and / 8, for
// the I2S camera input's sake). Faster settings are tried, fastest first,
// and the fastest that still streams whole frames is kept. CLKRC is register
// 0x11 in the sensor bank (the driver's set_reg: bit 8 of the address
// selects the bank); its bit 7 doubles the clock before the divider.
#define REG_CLKRC 0x111

static void fastest_clock(sensor_t *s, scan_mode_t *m) {
  // XCLK / 2 is what the CIF readout was measured to stream whole at (25
  // fps). The other readouts send a frame scaled or cropped to a quarter or
  // less of their pixels, so their pixel clock leaves room for the sensor's
  // full rate, XCLK x 2 / 2: tried first there.
  static const uint8_t cif_clocks[] = {0x01, 0};
  static const uint8_t other_clocks[] = {0x81, 0x01, 0x03, 0};
  const uint8_t *clocks = m->mode->readout < 0 ? cif_clocks : other_clocks;
  float base_fps = m->fps;
  int stock = s->get_reg(s, REG_CLKRC, 0xff);
  if (stock < 0) {
    printf("CLKRC read failed: the driver's sensor clock stays\n");
    return;
  }
  for (size_t i = 0; clocks[i] != 0; i++) {
    if (clocks[i] == stock) {
      break;  // the driver's own is next: nothing faster streams
    }
    if (s->set_reg(s, REG_CLKRC, 0xff, clocks[i]) != 0) {
      printf("CLKRC write failed\n");
      continue;
    }
    vTaskDelay(pdMS_TO_TICKS(300));
    scan_mode_t trial = *m;
    if (camera_check(&trial) && trial.fps > base_fps * 1.3f) {
      printf("sensor clock CLKRC 0x%02x: %.1f fps (the driver's 0x%02x: %.1f)\n", clocks[i],
             trial.fps, stock, base_fps);
      *m = trial;
      return;
    }
  }
  if (s->set_reg(s, REG_CLKRC, 0xff, stock) != 0) {
    printf("CLKRC restore failed\n");
  }
  vTaskDelay(pdMS_TO_TICKS(300));
  printf("sensor clock: the driver's CLKRC 0x%02x, %.1f fps (faster ones did not stream)\n", stock,
         base_fps);
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
  // The 10 ms cap in sensor lines, from the measured frame rate
  int lines = m->mode->lines;
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

// After each frame: the 98th percentile of the code's area `box` (x0, y0,
// x1, y1; NULL: the frame's centre half) steers exposure and gain
static void ae_step(sensor_t *s, ae_t *ae, const uint8_t *px, int w, int h, const int32_t *box) {
  uint32_t hist[64] = {0}, n = 0;
  int x0 = box ? box[0] : w / 4, x1 = box ? box[2] : w * 3 / 4;
  int y0 = box ? box[1] : h / 4, y1 = box ? box[3] : h * 3 / 4;
  int step = (x1 - x0) * (y1 - y0) > 20000 ? 4 : 2;
  for (int y = y0; y < y1; y += step) {
    for (int x = x0; x < x1; x += step) {
      hist[px[y * w + x] >> 2]++;
      n++;
    }
  }
  if (n < 64) {
    return;
  }
  uint32_t above = 0;
  int bin = 63;
  while (bin > 0 && (above + hist[bin]) * 50 < n) {
    above += hist[bin--];
  }
  int p98 = bin * 4 + 2;

  ae_t next = *ae;
  if (p98 >= 250) {
    // Clipped: exposure and gain down together (two saved frames were
    // 42-46 % clipped at gain 4 when they came down one at a time)
    next.aec = next.aec > 2 ? next.aec / 2 : 1;
    next.agc = next.agc > 4 ? next.agc - 4 : 0;
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

static const char *stage_name(int stage) {
  static const char *names[] = {"no finder patterns", "finder patterns but no grid",
                                "a grid but no format", "the data did not correct", "decoded"};
  return stage >= 0 && stage < (int)(sizeof(names) / sizeof(names[0])) ? names[stage] : "?";
}

static void print_payload(const uint8_t *p, int n) {
  for (int i = 0; i < n; i++) {
    putchar(p[i] >= 0x20 && p[i] < 0x7f ? p[i] : '.');
  }
  putchar('\n');
}

// ---------------------------------------------------------------------------
// Near-miss frames, kept in flash (opening the serial port resets the board,
// which would lose them in RAM). Slot 0..SLOTS_MISS-1: frames of a code that
// has been in view for 1.5 s without decoding (finder patterns at least),
// one every 4 s at most; the rest: decoded frames, one every 30 s. Saving
// one stops the scan for about a second (the flash erase), which is why
// they are spaced out; a serial "S off" stops saving. "D" dumps them.

// Slots hold a 400x296 frame (118 400 bytes and the header)
#define SLOT_SIZE 0x20000
#define SLOTS 16
#define SLOTS_MISS 12
#define FRAME_MAGIC 0x31465251u  // "QRF1"

typedef struct {
  uint32_t magic;
  uint32_t seq;
  uint16_t w, h;
  int16_t px_x10;
  // stage: FQR_STAGE_*; flags: bit 0 tracked, bits 1-3 the camera mode,
  // bits 4-7 frames summed (15: more)
  uint8_t stage, version, decoded, flags, agc, finders;
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

static volatile bool g_save = true;

// `in_view_ms`: how long finder patterns have been in view without a decode
static void frames_save(const uint8_t *px, const scan_mode_t *m, const fqr_result_t *r,
                        const ae_t *ae, int decode_ms, int in_view_ms) {
  static int64_t last_miss_us = -10000000, last_ok_us = -100000000;
  static uint32_t n_miss, n_ok;
  if (g_frames == NULL || !g_save) {
    return;
  }
  int64_t now = esp_timer_get_time();
  int slot;
  if ((size_t)m->w * m->h + sizeof(frame_header_t) > SLOT_SIZE) {
    return;
  }
  if (r->decoded) {
    if (now - last_ok_us < 30000000) {
      return;
    }
    last_ok_us = now;
    slot = SLOTS_MISS + (int)(n_ok++ % (SLOTS - SLOTS_MISS));
  } else {
    if (r->stage < FQR_STAGE_FINDERS || in_view_ms < 1500 || now - last_miss_us < 4000000) {
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
      .stage = (uint8_t)r->stage,
      .version = (uint8_t)r->version,
      .decoded = (uint8_t)r->decoded,
      .flags = (uint8_t)((r->tracked ? 1 : 0) | (int)(m->mode - MODES) << 1 |
                         (r->fused > 15 ? 15 : r->fused) << 4),
      .agc = (uint8_t)ae->agc,
      .finders = (uint8_t)(r->finders > 255 ? 255 : r->finders),
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
  } else if (!g_quiet) {
    printf("saved frame %u (%s) for pull_frames.py\n", (unsigned)h.seq,
           r->decoded ? "decoded" : "not decoded");
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
//   S on|off  keep near-miss and decoded frames in flash, or not
//   T         time the decoder on the saved frames
//   R <mode>  camera mode (MODES)

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
    line[strcspn(line, "\r")] = '\0';
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
    } else if (line[0] == 'R') {
      char *name = line + 1;
      while (*name == ' ') {
        name++;
      }
      name[strcspn(name, " \r")] = '\0';
      int found = -1;
      for (int i = 0; i < N_MODES; i++) {
        if (strcmp(name, MODES[i].name) == 0) {
          found = i;
        }
      }
      if (found < 0) {
        printf("R cif | half | full | zoom\n");
      } else {
        g_mode_req = found;
      }
    } else if (line[0] == 'S') {
      g_save = strstr(line, "off") == NULL;
      printf("saving frames %s\n", g_save ? "on" : "off");
    } else if (line[0] == 'E') {
      g_pause = true;
      vTaskDelay(pdMS_TO_TICKS(600));
      esp_err_t e = g_frames ? esp_partition_erase_range(g_frames, 0, g_frames->size) : ESP_FAIL;
      g_pause = false;
      uart_out(e == ESP_OK ? "OK\n" : "ERR erase\n", e == ESP_OK ? 3 : 10);
    }
  }
}

// The decoder's time per stage (fqr.c, FQR_PROF)
extern uint32_t fqr_prof[8];
uint32_t fqr_prof_us(void) { return (uint32_t)esp_timer_get_time(); }

// Scans in camera mode `want` until the serial "R" asks for another (returns
// true then, the camera stopped and every buffer freed) or a fatal error
// (false)
static bool scan_main(int want) {
  bool restart = false;
  uint8_t *state = NULL, *saved = NULL;

  scan_mode_t m;
  if (!start_mode(&MODES[want], &m) &&
      (want == DEFAULT_MODE || !start_mode(&MODES[DEFAULT_MODE], &m))) {
    printf("FATAL: the camera does not start or stream; check the module\n");
    return false;
  }
  sensor_t *s = esp_camera_sensor_get();
  if (s == NULL) {
    printf("FATAL: no sensor after the camera started\n");
    goto out;
  }
  fastest_clock(s, &m);
  printf("camera: PID 0x%02x, %dx%d grayscale, mode %s, %.1f fps\n", s->id.PID, m.w, m.h,
         m.mode->name, m.fps);

  size_t img_len = (size_t)m.w * m.h;
  size_t state_len = fqr_state_size(m.w, m.h);
  // Internal RAM where it fits (the PSRAM is slower)
  state = heap_caps_aligned_alloc(16, state_len, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  bool internal = state != NULL;
  if (!internal) {
    state = heap_caps_aligned_alloc(16, state_len, MALLOC_CAP_SPIRAM);
  }
  fqr_t *qr = state != NULL ? fqr_init(state, state_len, m.w, m.h) : NULL;
  if (qr == NULL) {
    printf("FATAL: decoder setup failed (%u bytes)\n", (unsigned)state_len);
    goto out;
  }
  printf("decoder: %u bytes of state in %s RAM, frames read where the driver put them\n",
         (unsigned)state_len, internal ? "internal" : "PSRAM");

  ae_t ae;
  ae_start(s, &m, &ae);
  printf("scanning: show a QR code to the camera (serial: R cif|half|full|zoom changes "
         "the camera mode, T times the saved frames)\n");

  static uint8_t payload[1200], last_payload[1200];
  int last_len = -1;
  int64_t last_print_us = 0, status_us = esp_timer_get_time(), fps_t0 = status_us;
  int64_t first_seen_us = 0;  // when finder patterns came into view (0: none in view)
  uint32_t frames = 0, decodes = 0, changes = 0, fps_frames = 0, decode_ms_sum = 0;
  uint32_t win_decodes = 0, win_tracked = 0;
  fqr_result_t seen = {0};  // the furthest a frame got since the last status line
  bool have_box = false;
  int32_t box[4];

  for (;;) {
    while (g_pause) {
      vTaskDelay(pdMS_TO_TICKS(50));
    }
    if (g_mode_req >= 0) {
      restart = true;
      break;
    }
    if (g_replay) {
      // Each saved frame decoded on its own, then once more as the next frame
      // of a video (tracked): what a frame costs with and without the search
      int n = 0, ok = 0;
      if (saved == NULL) {
        saved = heap_caps_malloc(img_len, MALLOC_CAP_SPIRAM);
      }
      for (int slot = 0; g_frames != NULL && saved != NULL && slot < SLOTS; slot++) {
        frame_header_t fh;
        size_t at = (size_t)slot * SLOT_SIZE;
        if (esp_partition_read(g_frames, at, &fh, sizeof(fh)) != ESP_OK ||
            fh.magic != FRAME_MAGIC || fh.w != m.w || fh.h != m.h ||
            esp_partition_read(g_frames, at + sizeof(fh), saved, img_len) != ESP_OK) {
          continue;
        }
        n++;
        fqr_result_t r1, r2;
        fqr_reset(qr);
        memset(fqr_prof, 0, sizeof(fqr_prof));
        int64_t t1 = esp_timer_get_time();
        bool ok1 = fqr_scan(qr, saved, m.w, payload, sizeof(payload), &r1);
        int64_t t2 = esp_timer_get_time();
        uint32_t p[7];
        memcpy(p, fqr_prof, sizeof(p));
        bool ok2 = fqr_scan(qr, saved, m.w, payload, sizeof(payload), &r2);
        int64_t t3 = esp_timer_get_time();
        ok += ok1;
        printf("replay frame %u (%s on the board then): %s, %d ms (threshold %u, finders %u, grid %u, "
               "sample %u, levels %u, equalise %u, decode %u us); again as video: %s%s, %d ms\n",
               (unsigned)fh.seq, fh.decoded ? "decoded" : "missed",
               ok1 ? "DECODED" : stage_name((int)r1.stage), (int)((t2 - t1) / 1000), (unsigned)p[0],
               (unsigned)p[1], (unsigned)p[2], (unsigned)p[3], (unsigned)p[4], (unsigned)p[5],
               (unsigned)p[6], ok2 ? "DECODED" : stage_name((int)r2.stage),
               r2.tracked ? " tracked" : "", (int)((t3 - t2) / 1000));
        vTaskDelay(1);
      }
      if (saved == NULL) {
        printf("replay: no memory for a frame\n");
      }
      printf("replay: %d of %d saved frames decoded\n", ok, n);
      fqr_reset(qr);
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
    ae_step(s, &ae, fb->buf, m.w, m.h, have_box ? box : NULL);

    fqr_result_t r;
    int64_t t0 = esp_timer_get_time();
    bool ok = fqr_scan(qr, fb->buf, m.w, payload, sizeof(payload), &r);
    int decode_ms = (int)((esp_timer_get_time() - t0) / 1000);
    decode_ms_sum += (uint32_t)decode_ms;
    have_box = fqr_code_area(qr, box);
    // How long a code has been in view undecoded (a decode starts it again)
    if (r.stage < FQR_STAGE_FINDERS) {
      first_seen_us = 0;
    } else if (first_seen_us == 0) {
      first_seen_us = t0;
    }
    int in_view_ms = first_seen_us ? (int)((esp_timer_get_time() - first_seen_us) / 1000) : 0;
    frames_save(fb->buf, &m, &r, &ae, decode_ms, in_view_ms);
    esp_camera_fb_return(fb);

    // Lets the idle task run (the task watchdog watches it)
    vTaskDelay(1);
    int64_t now = esp_timer_get_time();
    if (r.stage >= seen.stage) {
      seen = r;
    }
    win_tracked += r.tracked != 0;
    if (ok) {
      decodes++;
      win_decodes++;
      int n = r.payload_len < (int)sizeof(payload) ? r.payload_len : (int)sizeof(payload);
      bool same = n == last_len && memcmp(payload, last_payload, (size_t)n) == 0;
      changes += !same;
      if (!g_quiet && (!same || now - last_print_us > 3000000)) {
        printf("QR OK v%d %d.%d px/module, %s", (int)r.version, (int)(r.px_per_module_x10 / 10),
               (int)(r.px_per_module_x10 % 10), r.tracked ? "tracked" : "found");
        if (r.fused > 1) {
          printf(", %d frames summed", (int)r.fused);
        }
        printf(" (frame %u, decode %d ms, %d ms after it came into view)\n", (unsigned)frames,
               decode_ms, in_view_ms);
        print_payload(payload, n);
        memcpy(last_payload, payload, (size_t)n);
        last_len = n;
        last_print_us = now;
      }
      first_seen_us = 0;  // the next code's time starts at its first frame
    }

    if (now - status_us > 4000000 && !g_quiet) {
      float secs = (float)(now - fps_t0) / 1e6f;
      float fps = (float)fps_frames / secs;
      int avg_ms = fps_frames ? (int)(decode_ms_sum / fps_frames) : 0;
      decode_ms_sum = 0;
      printf("scanning [%.1f fps, decode %d ms]: %u decoded (%.1f a second, %u tracked frames), ",
             fps, avg_ms, (unsigned)decodes, (float)win_decodes / secs, (unsigned)win_tracked);
      if (seen.stage >= FQR_STAGE_GRID) {
        printf("%s: v%d, %d.%d px/module, %d of its alignment patterns, timing %d %%", 
               seen.decoded ? "last code" : stage_name((int)seen.stage), (int)seen.version,
               (int)(seen.px_per_module_x10 / 10), (int)(seen.px_per_module_x10 % 10),
               (int)seen.aligns, (int)seen.timing_x100);
      } else {
        printf("%s", seen.stage == FQR_STAGE_FINDERS ? stage_name(1) : "no code in view");
      }
      printf(" (exposure %d gain %d, %u codes read)\n", ae.aec, ae.agc, (unsigned)changes);
      memset(&seen, 0, sizeof(seen));
      status_us = now;
      fps_t0 = now;
      fps_frames = 0;
      win_decodes = 0;
      win_tracked = 0;
    }
  }

out:
  heap_caps_free(state);
  heap_caps_free(saved);
  esp_err_t d = esp_camera_deinit();
  if (d != ESP_OK) {
    printf("camera stop: %s\n", esp_err_to_name(d));
  }
  return restart;
}

static void scan_task(void *arg) {
  (void)arg;
  int mode = DEFAULT_MODE;
  while (scan_main(mode)) {
    mode = g_mode_req;
    g_mode_req = -1;
    printf("restarting in mode %s\n", MODES[mode].name);
  }
  printf("scanning stopped\n");
  vTaskDelete(NULL);
}

void app_main(void) {
  printf("\nQR scanner (ESP32-CAM, OV2640, the signer's video decoder)\n");
  frames_init();
  xTaskCreatePinnedToCore(serial_task, "serial", 4096, NULL, 4, NULL, 0);
  // Core 1: the task watchdog watches core 0's idle task
  xTaskCreatePinnedToCore(scan_task, "scan", 16384, NULL, 5, NULL, 1);
}
