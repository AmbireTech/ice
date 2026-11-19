/*
 * Ambire boards, [ambire_camera]: the QR camera driver (kernel side).
 *
 * A 24-pin DVP module in the ESP32-CAM pinout on J_CAM: OmniVision OV2640
 * (the part proto 0 was drawn for: SCCB 0x30, 8-bit registers in two banks),
 * OmniVision OV5640 (0x3C, 16-bit registers, a Y8 grayscale output) or
 * GalaxyCore GC2145 (0x3C, 8-bit registers). The OV2640 and GC2145 give YUYV
 * only: the DCMI keeps every other byte, the Y. At 0x3C the GC2145 is probed
 * first, because an OV5640-style read (two address bytes) would write the
 * second byte into a GC2145 register.
 *
 * Every frame is side x side Y8 (the side chosen with the mode, io/camera.h).
 * The zoom levels, by sensor pixels per frame pixel:
 *
 *   OV2640  0: 600x600 centre of SVGA mode (800x600, the full height), scaled
 *              by the DSP to side
 *           1: 2side x 2side centre of UXGA mode (1600x1200), scaled 2:1
 *           2: side x side centre of UXGA mode: native pixels
 *   GC2145  0: 3side window subsampled 1/3 (the sensor skips pixels)
 *           1: 2side window subsampled 1/2
 *           2: side window, native pixels
 *           with tune 1, through its 1/2 scaler instead of skipping (as
 *           Linux's gc2145.c makes its 640x480 mode): 0 a 2.5side window
 *           scaled 1/2 and then 4 of 5, 1 a 2side window scaled 1/2
 *   OV5640  0: side x side from the 1920x1920 centre square (binned, scaled)
 *           1: 640x480 (4:3, binned, scaled), DCMI crop of the centre
 *           2: 1024x768 likewise
 *
 * The OV5640 modes are esp32-camera's (sensors/ov5640.c); the OV2640 and
 * GC2145 windows use esp32-camera's own window registers (set_window(),
 * set_framesize()) at other sizes.
 *
 * A capture is one DCMI snapshot into a buffer the app owns, by GPDMA1
 * (no interrupts): a frame over 64 KiB is cut into blocks of a linked list,
 * since one block counts at most 65535 bytes. camera_get_status() polls it
 * and closes it.
 *
 * Boards that define CAMERA_DVDD_PWM_PIN set the sensor's core supply (DVDD)
 * per sensor: an adjustable LDO trimmed by a filtered PWM (camera_dvdd_set()).
 * Detection runs at 1.5 V; the sensor found gets its own voltage, under reset.
 */

#include <trezor_bsp.h>
#include <trezor_rtl.h>

#include <io/camera.h>
#include <sys/logging.h>
#include <sys/systick.h>

#ifdef KERNEL_MODE

#include "camera_ae.h"
#include "camera_sensors.h"

LOG_DECLARE(camera);

// 7-bit SCCB addresses
#define SCCB_ADDR_0X3C 0x3C  // OV5640, GC2145
#define SCCB_ADDR_OV2640 0x30
#define SCCB_TIMEOUT_MS 10
// I2C2 on PCLK1 (160 MHz): PRESC 3 (25 ns), SCLL 5.0 us, SCLH 4.9 us,
// ~100 kHz (I2C_TIMING_200000_Hz in sys/i2c_bus, at half the speed)
#define SCCB_TIMING 0x30D2C3C7

#define XCLK_HZ 20000000U

// The slowest modes (OV2640 UXGA at its stock clock, ~2 fps) take ~0.5 s a
// frame, and a snapshot waits for the next frame start: two frame times, and
// some slack
#define CAPTURE_TIMEOUT_MS 1500
// One GPDMA block counts at most 65535 bytes: a frame goes in blocks of at
// most this (a multiple of the 4-byte transfers), chained by a linked list
#define DMA_BLOCK_MAX 65532U
#define DMA_BLOCKS_MAX 4
// Sync-pin probe after the sensor starts: longer than one frame at ~4 fps
#define PROBE_MS 300

// last_flags: beyond the DCMI's own raw flags
#define CAMERA_FLAG_DMA_FULL (1U << 8)   // buffer full, the frame went on
#define CAMERA_FLAG_DMA_ERROR (1U << 9)  // GPDMA transfer/setting error
#define CAMERA_FLAG_TIMEOUT (1U << 10)   // no frame end in time
#define CAMERA_FLAG_DEBUG_HOLD (1U << 11)  // held for the SWD debugger

typedef struct {
  GPIO_TypeDef *port;
  uint16_t pin;
  uint8_t af;
} camera_pin_t;

static const camera_pin_t g_dcmi_pins[] = CAMERA_DCMI_PINS;

typedef struct {
  bool powered;
  I2C_HandleTypeDef i2c;
  uint16_t sccb_addr;  // 7-bit, of the sensor being probed or driven
  camera_status_t st;
  // DCMI setup for the current sensor and zoom
  bool byte_select;  // keep every other byte (YUYV -> Y)
  bool crop;
  uint32_t crop_start;  // CWSTRTR
  uint32_t crop_size;   // CWSIZER
  uint32_t frame_bytes;
  uint32_t capture_start_ms;
  uint32_t capture_buf;
  // OV2640 exposure set from the frames (tune 0, camera_ae.h), and when the
  // last whole frame ended
  bool ae_soft;
  camera_ae_t ae;
  uint32_t last_frame_ms;
  // GC2145: its subsample mode register as the start-up table leaves it
  uint8_t gc2145_sub_mode;
  // Failed captures in a row (the fast clock falls back to the stock one)
  uint32_t fail_run;
} camera_driver_t;

static camera_driver_t g_camera;

// ---------------------------------------------------------------------------
// SWD debug mailbox (bring-up): firmware/bringup/camera/camdbg.py writes it
// over SWD while the camera runs; nothing else does. Register ops are applied
// at the next camera call (each one is about a frame apart). A hold keeps the
// next whole frame raw in the app's buffer: the capture reports FAILED with
// CAMERA_FLAG_DEBUG_HOLD, which the screens paint and do not decode, and no
// capture starts until the hold is released.

#define CAMERA_DEBUG_MAGIC 0xCA3DB600U
#define CAMERA_DEBUG_OPS 48
#define CAMERA_DEBUG_OP_WRITE 1
#define CAMERA_DEBUG_OP_READ 2
#define CAMERA_DEBUG_OP_DELAY 3  // val ms
#define CAMERA_DEBUG_NO_BANK 0xFFFF

typedef struct {
  uint16_t op;
  uint16_t bank;  // OV2640: written to 0xFF first; CAMERA_DEBUG_NO_BANK: none
  uint16_t reg;
  uint8_t val;
  uint8_t result;  // read value
  uint32_t ok;     // 1: acknowledged
} camera_debug_op_t;

typedef struct {
  uint32_t magic;
  uint32_t req_seq;  // the debugger bumps it after filling ops / n_ops
  uint32_t ack_seq;  // set to req_seq once the ops ran
  uint32_t n_ops;
  uint32_t hold_req;    // debugger: 1 hold the next whole frame, 0 release
  uint32_t held;        // 1: frame_addr holds a raw frame, no capture runs
  uint32_t frame_addr;  // the buffer of the last capture
  uint32_t frame_side;
  uint32_t frame_seq;   // bumped each time a frame is held
  camera_status_t st;   // copied at each camera call
  camera_debug_op_t ops[CAMERA_DEBUG_OPS];
} camera_debug_t;

volatile camera_debug_t g_camera_debug __attribute__((used)) = {
    .magic = CAMERA_DEBUG_MAGIC};

// The capture's linked list after its first block: each item reloads the
// block size (CBR1), the destination (CDAR) and the link (CLLR). The list
// must not cross a 64 KiB boundary (CLBAR holds its upper half): aligned to
// its own size, it cannot.
static uint32_t g_dma_lli[DMA_BLOCKS_MAX][4] __attribute__((aligned(64)));
_Static_assert(sizeof(g_dma_lli) <= 64, "the list's alignment must cover it");
_Static_assert((uint32_t)CAMERA_SIDE_MAX * CAMERA_SIDE_MAX <=
                   (uint32_t)DMA_BLOCK_MAX * DMA_BLOCKS_MAX,
               "the largest frame must fit the linked list");

// ---------------------------------------------------------------------------
// SCCB

static bool sccb_write8(uint8_t reg, uint8_t val) {
  return HAL_I2C_Mem_Write(&g_camera.i2c, g_camera.sccb_addr << 1, reg,
                           I2C_MEMADD_SIZE_8BIT, &val, 1,
                           SCCB_TIMEOUT_MS) == HAL_OK;
}

static bool sccb_write16(uint16_t reg, uint8_t val) {
  return HAL_I2C_Mem_Write(&g_camera.i2c, g_camera.sccb_addr << 1, reg,
                           I2C_MEMADD_SIZE_16BIT, &val, 1,
                           SCCB_TIMEOUT_MS) == HAL_OK;
}

// A read is two transactions with a stop between, the address write and the
// data read: SCCB parts (the OV2640) need not take I2C's repeated start,
// and the others accept this too. A failure leaves its I2C error in the
// status (a sensor that does not answer yet is retried: the last one counts).
static bool sccb_read(const uint8_t *addr, uint16_t addr_len, uint8_t *val) {
  uint16_t dev = g_camera.sccb_addr << 1;
  HAL_StatusTypeDef status = HAL_I2C_Master_Transmit(
      &g_camera.i2c, dev, (uint8_t *)addr, addr_len, SCCB_TIMEOUT_MS);
  if (status == HAL_OK) {
    status = HAL_I2C_Master_Receive(&g_camera.i2c, dev, val, 1,
                                    SCCB_TIMEOUT_MS);
  }
  if (status != HAL_OK) {
    g_camera.st.i2c_error = HAL_I2C_GetError(&g_camera.i2c);
  }
  return status == HAL_OK;
}

static bool sccb_read8(uint8_t reg, uint8_t *val) {
  return sccb_read(&reg, 1, val);
}

static bool sccb_read16(uint16_t reg, uint8_t *val) {
  uint8_t addr[2] = {(uint8_t)(reg >> 8), (uint8_t)reg};
  return sccb_read(addr, 2, val);
}

// One register write in the sensor's addressing; a failure is recorded
static bool reg_write(uint16_t reg, uint8_t val) {
  bool ok = g_camera.st.sensor_id == CAMERA_SENSOR_OV5640
                ? sccb_write16(reg, val)
                : sccb_write8((uint8_t)reg, val);
  if (!ok && g_camera.st.open_error == CAMERA_ERR_NONE) {
    g_camera.st.open_error = CAMERA_ERR_SCCB_WRITE;
    g_camera.st.error_reg = reg;
    LOG_ERR("SCCB write 0x%04x failed", reg);
  }
  return ok;
}

static bool reg_write_table(const uint16_t (*regs)[2]) {
  for (size_t i = 0; regs[i][0] != CAM_REG_END; i++) {
    if (regs[i][0] == CAM_REG_DELAY) {
      systick_delay_ms(regs[i][1]);
    } else if (!reg_write(regs[i][0], (uint8_t)regs[i][1])) {
      return false;
    }
  }
  return true;
}

// A 16-bit value into two OV5640 registers, high byte first
static bool ov_write16(uint16_t reg, uint16_t val) {
  return reg_write(reg, (uint8_t)(val >> 8)) &&
         reg_write(reg + 1, (uint8_t)(val & 0xFF));
}

// ---------------------------------------------------------------------------
// OV5640

typedef struct {
  uint16_t w, h;            // output size
  uint16_t sx, sy, ex, ey;  // sensor array window
  uint16_t ox, oy;          // ISP offset (halved: binned)
  uint16_t tx, ty;          // total size (binned)
  // set_pll(bypass = 0, multiplier, sys_div, pre_div, root_2x = 0,
  //         pclk_root_div, pclk_manual = 1, pclk_div)
  uint8_t pll_mult, pll_sys_div, pll_pre_div, pll_root_div, pclk_div;
} ov5640_mode_t;

// esp32-camera set_framesize() for FRAMESIZE_240X240 (1:1 ratio, here at the
// frame's side), VGA and XGA (4:3), all binned and scaled; XCLK 20 MHz gives a
// 10 MHz PCLK in each
static const ov5640_mode_t g_ov5640_modes[CAMERA_ZOOM_LEVELS] = {
    {240, 240, 320, 0, 2543, 1951, 16, 8, 2060, 984, 20, 1, 1, 1, 8},
    {640, 480, 0, 0, 2623, 1951, 16, 8, 2060, 984, 10, 1, 2, 1, 2},
    {1024, 768, 0, 0, 2623, 1951, 16, 8, 2644, 984, 10, 1, 2, 1, 2},
};

static bool ov5640_set_mode(uint32_t zoom, uint32_t side) {
  ov5640_mode_t mode = g_ov5640_modes[zoom];
  const ov5640_mode_t *m = &mode;
  uint8_t isp01 = 0;
  if (zoom == CAMERA_ZOOM_WIDE) {
    mode.w = mode.h = (uint16_t)side;
  }
  // Sensor pixels per frame pixel, x10: the square's 1920, or the 2592-wide
  // field over the mode's width
  g_camera.st.scale_x10 =
      zoom == CAMERA_ZOOM_WIDE ? 19200 / side : 25920U / m->w;

  bool ok =
      // Y8 out of the YUV422 ISP path (sensor_fmt_grayscale)
      reg_write(0x501F, 0x00) && reg_write(0x4300, 0x10) &&
      // window, output size, totals, offsets
      ov_write16(0x3800, m->sx) && ov_write16(0x3802, m->sy) &&
      ov_write16(0x3804, m->ex) && ov_write16(0x3806, m->ey) &&
      ov_write16(0x3808, m->w) && ov_write16(0x380A, m->h) &&
      ov_write16(0x380C, m->tx) && ov_write16(0x380E, m->ty) &&
      ov_write16(0x3810, m->ox) && ov_write16(0x3812, m->oy) &&
      // scaler on (ISP_CONTROL_01 bit 5)
      sccb_read16(0x5001, &isp01) && reg_write(0x5001, isp01 | 0x20) &&
      // set_image_options(): binned, no flip, no mirror
      reg_write(0x3820, 0x01) && reg_write(0x3821, 0x01) &&
      reg_write(0x4514, 0xAA) && reg_write(0x4520, 0x0B) &&
      reg_write(0x3814, 0x31) && reg_write(0x3815, 0x31) &&
      // set_pll()
      reg_write(0x3039, 0x00) && reg_write(0x3034, 0x1A) &&
      reg_write(0x3035, 0x01 | (uint8_t)((m->pll_sys_div & 0x0F) << 4)) &&
      reg_write(0x3036, m->pll_mult) &&
      reg_write(0x3037, m->pll_pre_div & 0x0F) &&
      reg_write(0x3108, (uint8_t)((m->pll_root_div & 0x3) << 4) | 0x06) &&
      reg_write(0x3824, m->pclk_div & 0x1F) && reg_write(0x460C, 0x22) &&
      reg_write(0x3103, 0x13);

  g_camera.byte_select = false;
  g_camera.crop = m->w != side || m->h != side;
  if (g_camera.crop) {
    // Y8: one byte (one pixel clock) per pixel
    uint32_t x0 = (m->w - side) / 2;
    uint32_t y0 = (m->h - side) / 2;
    g_camera.crop_start =
        (y0 << DCMI_CWSTRT_VST_Pos) | (x0 << DCMI_CWSTRT_HOFFCNT_Pos);
    g_camera.crop_size = ((side - 1) << DCMI_CWSIZE_VLINE_Pos) |
                         ((side - 1) << DCMI_CWSIZE_CAPCNT_Pos);
  }
  return ok;
}

static bool ov5640_init(void) {
  // esp32-camera reset(): settle, the default table (software reset, power
  // down, setup, power up, 300 ms), settle
  systick_delay_ms(100);
  if (!reg_write_table(ov5640_init_regs)) {
    return false;
  }
  systick_delay_ms(100);
  return true;
}

// ---------------------------------------------------------------------------
// GC2145

// How the GC2145 makes a zoom level: the sensor window in frame sides x2,
// the 1/2 scaler (0xfd), the subsample ratio (0x99), its mode (0x9a; 0: as
// the start-up table leaves it) and which of each `ratio` rows and columns
// are kept (0x9b..0xa2)
typedef struct {
  uint8_t win_x2;
  uint8_t scaler;
  uint8_t ratio;
  uint8_t sub_mode;
  uint8_t keep[8];
} gc2145_zoom_t;

// esp32-camera set_framesize() in subsample mode: 1/3, 1/2 and 1/1. The
// sensor skips the pixels between.
static const gc2145_zoom_t g_gc2145_skip[CAMERA_ZOOM_LEVELS] = {
    {6, 0x00, 0x33, 0, {0}},
    {4, 0x00, 0x22, 0, {0}},
    {2, 0x00, 0x11, 0, {0}},
};
// Through the 1/2 scaler, as Linux's drivers/media/i2c/gc2145.c makes its
// 640x480 mode (0xfd = 1, then 4 of every 5 rows and columns: 0x99 = 0x55,
// 0x9a = 0x06, rows and columns 0-3 kept): 2.5 and 2 sensor pixels a frame
// pixel. Not run on a module yet.
static const gc2145_zoom_t g_gc2145_scaled[CAMERA_ZOOM_LEVELS] = {
    {5, 0x01, 0x55, 0x06, {0x01, 0x23, 0x00, 0x00, 0x01, 0x23, 0x00, 0x00}},
    {4, 0x01, 0x11, 0, {0}},
    {2, 0x00, 0x11, 0, {0}},
};

static bool gc2145_set_mode(uint32_t zoom, uint32_t side, uint32_t tune) {
  const gc2145_zoom_t *z =
      tune == 1 ? &g_gc2145_scaled[zoom] : &g_gc2145_skip[zoom];
  uint16_t win = (uint16_t)(side * z->win_x2 / 2);
  // Even starts keep the Bayer phase (the colour order) of the full array
  uint16_t row = (uint16_t)(((1200 - win) / 2) & ~1U);
  uint16_t col = (uint16_t)(((1600 - win) / 2) & ~1U);
  uint16_t out = (uint16_t)side;
  g_camera.st.scale_x10 = z->win_x2 * 5U;

  bool ok = reg_write(0xfe, 0x00) && reg_write(0xfd, z->scaler) &&
            reg_write(0x90, 0x01) &&
            reg_write(0x09, (uint8_t)(row >> 8)) &&
            reg_write(0x0a, (uint8_t)row) &&
            reg_write(0x0b, (uint8_t)(col >> 8)) &&
            reg_write(0x0c, (uint8_t)col) &&
            reg_write(0x0d, (uint8_t)((win + 8) >> 8)) &&
            reg_write(0x0e, (uint8_t)(win + 8)) &&
            reg_write(0x0f, (uint8_t)((win + 16) >> 8)) &&
            reg_write(0x10, (uint8_t)(win + 16)) &&
            reg_write(0x99, z->ratio) &&
            reg_write(0x9a, z->sub_mode ? z->sub_mode
                                        : g_camera.gc2145_sub_mode);
  for (uint16_t i = 0; ok && i < 8; i++) {
    ok = reg_write(0x9b + i, z->keep[i]);
  }
  ok = ok && reg_write(0x95, (uint8_t)(out >> 8)) &&
       reg_write(0x96, (uint8_t)out) && reg_write(0x97, (uint8_t)(out >> 8)) &&
       reg_write(0x98, (uint8_t)out);

  // YUYV (0x84 = 2: Y Cb Y Cr): the Y is every even byte of a line
  g_camera.byte_select = true;
  g_camera.crop = false;
  return ok;
}

// Clock (CAMERA_MODE_CLOCK): 0 slows the PCLK to <= 15 MHz as esp32-camera
// does on the plain ESP32 (0xf8 low bits = 2, 0xfa high nibble = 2); 1 keeps
// the start-up table's PLL setting, which esp32-camera runs on the ESP32-S3.
// Tune (CAMERA_MODE_TUNE): 0 and 1 the table's image pipeline (1 zooms
// through the scaler, gc2145_set_mode()); 2 a lower AEC luma target (P1:0x13,
// 0x40 -> 0x30: a bright screen clips less) and the denoiser off (P0:0x80
// bit 2, DN_en: it smooths fine modules away).
static bool gc2145_set_tuning(uint32_t clock, uint32_t tune) {
  bool screen = tune == 2;
  return reg_write(0xfe, 0x00) && reg_write(0xf8, clock ? 0x83 : 0x82) &&
         reg_write(0xfa, clock ? 0x00 : 0x20) &&
         reg_write(0x80, screen ? 0xfb : 0xff) && reg_write(0xfe, 0x01) &&
         reg_write(0x13, screen ? 0x30 : 0x40) && reg_write(0xfe, 0x00);
}

static bool gc2145_init(void) {
  // esp32-camera reset(): software reset, the default table, and YUV422
  // output (the clock: gc2145_set_tuning())
  if (!reg_write(0xfe, 0xe0)) {
    return false;
  }
  systick_delay_ms(100);
  if (!reg_write_table(gc2145_init_regs)) {
    return false;
  }
  systick_delay_ms(100);
  return reg_write(0xfe, 0x00) && reg_write(0x84, 0x02) &&
         sccb_read8(0x9a, &g_camera.gc2145_sub_mode);
}

// ---------------------------------------------------------------------------
// OV2640

static bool reg_write_table8(const uint8_t (*regs)[2]) {
  for (size_t i = 0; regs[i][0] != 0; i++) {
    if (!reg_write(regs[i][0], regs[i][1])) {
      return false;
    }
  }
  return true;
}

// esp32-camera set_window(): a window (ox, oy, mx x my) of a sensor mode's
// field, scaled by the DSP to the frame's side. Clocks as esp32-camera sets
// them on the ESP32-S3 (CLKRC: doubler on, divider 8 or 4; R_DVP_SP: auto
// PCLK), or with `clock` the divider at 2: the internal clock 4x (UXGA, SVGA)
// or 2x (CIF) as fast, 20 MHz from the 20 MHz XCLK (the sensor is rated to
// 36 MHz), so ~4x the frame rate and a quarter of the longest exposure.
static bool ov2640_set_window(const uint8_t (*mode_regs)[2], uint16_t ox,
                              uint16_t oy, uint16_t mx_px, uint16_t my_px,
                              uint32_t side, uint8_t clkrc, uint8_t dvp_sp) {
  // set_window(): the DSP's window and zoom registers count in units of 4
  uint16_t mx = mx_px / 4, my = my_px / 4;
  uint16_t w = (uint16_t)(side / 4), h = (uint16_t)(side / 4);
  const uint8_t win[][2] = {
      {0xff, 0x00},  // DSP bank
      {0x51, (uint8_t)mx},                                   // HSIZE
      {0x52, (uint8_t)my},                                   // VSIZE
      {0x53, (uint8_t)ox},                                   // XOFFL
      {0x54, (uint8_t)oy},                                   // YOFFL
      {0x55, (uint8_t)(((my >> 1) & 0x80) | ((oy >> 4) & 0x70) |
                       ((mx >> 5) & 0x08) | ((ox >> 8) & 0x07))},  // VHYX
      {0x57, (uint8_t)((mx >> 2) & 0x80)},                   // TEST
      {0x5a, (uint8_t)w},                                    // ZMOW
      {0x5b, (uint8_t)h},                                    // ZMOH
      {0x5c, (uint8_t)(((h >> 6) & 0x04) | ((w >> 8) & 0x03))},  // ZMHH
      {0, 0},
  };

  bool ok = reg_write(0xff, 0x00) && reg_write(0x05, 0x01) &&  // DSP bypass
            reg_write_table8(mode_regs) && reg_write_table8(win) &&
            reg_write(0xff, 0x01) && reg_write(0x11, clkrc) &&  // CLKRC
            reg_write(0xff, 0x00) && reg_write(0xd3, dvp_sp) &&  // R_DVP_SP
            reg_write(0x05, 0x00);  // DSP on
  systick_delay_ms(10);
  // set_pixformat() again: required after a resolution change
  ok = ok && reg_write_table8(ov2640_yuv422_regs);
  systick_delay_ms(10);
  return ok;
}

static bool ov2640_set_mode(uint32_t zoom, uint32_t side, uint32_t clock) {
  bool ok;
  if (zoom == CAMERA_ZOOM_WIDE) {
    // SVGA's field is the array at half resolution: 600x600 is 1200x1200
    g_camera.st.scale_x10 = 12000 / side;
    ok = ov2640_set_window(ov2640_to_svga_regs, 100, 0, 600, 600, side,
                           clock ? 0x81 : 0x87, 0x88);
  } else if (zoom == CAMERA_ZOOM_HALF) {
    // SVGA's own pixels (the array read at half resolution): the detail of a
    // 2:1 window of UXGA from a quarter of the pixels, so ~4x the frame rate
    g_camera.st.scale_x10 = 20;
    ok = ov2640_set_window(ov2640_to_svga_regs, (uint16_t)((800 - side) / 2),
                           (uint16_t)((600 - side) / 2), (uint16_t)side,
                           (uint16_t)side, side, clock ? 0x81 : 0x87, 0x88);
  } else {
    g_camera.st.scale_x10 = 10;
    ok = ov2640_set_window(ov2640_to_uxga_regs, (uint16_t)((1600 - side) / 2),
                           (uint16_t)((1200 - side) / 2), (uint16_t)side,
                           (uint16_t)side, side, clock ? 0x81 : 0x87, 0x8C);
  }

  // YUYV (IMAGE_MODE 0: high byte first): the Y is every even byte
  g_camera.byte_select = true;
  g_camera.crop = false;
  return ok;
}

// OV2640 GAIN (0x00): (bit7+1)(bit6+1)(bit5+1)(bit4+1)(1 + bits[3:0]/16)
static uint8_t ov2640_gain_reg(uint32_t gain16) {
  uint32_t doublings = 0;
  while (gain16 >= 32 && doublings < 4) {
    gain16 /= 2;
    doublings++;
  }
  uint32_t frac = gain16 > 16 ? gain16 - 16 : 0;
  return (uint8_t)((((1U << doublings) - 1U) << 4) | (frac & 0x0F));
}

static bool ov2640_write_exposure(void) {
  uint32_t lines = g_camera.ae.lines;
  uint8_t reg04 = 0, reg45 = 0;
  return reg_write(0xff, 0x01) && sccb_read8(0x04, &reg04) &&
         sccb_read8(0x45, &reg45) &&
         reg_write(0x04, (uint8_t)((reg04 & ~3U) | (lines & 3U))) &&
         reg_write(0x10, (uint8_t)(lines >> 2)) &&  // AEC[9:2]
         reg_write(0x45, (uint8_t)((reg45 & ~0x3FU) | ((lines >> 10) & 0x3FU))) &&
         reg_write(0x00, ov2640_gain_reg(g_camera.ae.gain16));
}

static void ae_report(void) {
  const camera_ae_t *ae = &g_camera.ae;
  camera_status_t *st = &g_camera.st;
  st->ae_p98 = ae->p98;
  st->ae_lines = ae->lines;
  st->ae_gain16 = ae->gain16;
  st->ae_exposure_us = (uint32_t)((uint64_t)ae->lines * ae->line_ns / 1000U);
  st->ae_cap_us = ae->cap_us;
  st->ae_on_code = ae->on_code;
}

// After a whole frame (tune 0): the exposure from it (camera_ae.h)
static void ov2640_soft_ae(const uint8_t *frame, uint32_t side) {
  uint32_t now = systick_ms();
  uint32_t dt = g_camera.last_frame_ms ? now - g_camera.last_frame_ms : 0;
  g_camera.last_frame_ms = now;
  bool changed = camera_ae_frame(&g_camera.ae, frame, side, side, dt);
  ae_report();
  if (changed && !ov2640_write_exposure()) {
    LOG_WARN("exposure write failed (lines %u gain16 %u)",
             (unsigned)g_camera.ae.lines, (unsigned)g_camera.ae.gain16);
  }
}

// Tune (CAMERA_MODE_TUNE):
//  0: exposure from the frames (camera_ae.h); SDE off
//  1: the sensor's AEC as esp32-camera starts it (AE level 0, gain ceiling 8x)
//  2: the sensor's AEC for a phone screen: AE level -1 (AEW/AEB/VV
//     0x34/0x1c/0x00), contrast +2 (the DSP's SDE), gain ceiling 16x
// The exposure's unit is a line: UXGA lines last 1922 internal clocks, SVGA's
// 1190 (assumed from the datasheet's timing), at 5 MHz (stock) or 20 MHz
// (fast); a frame has 1248 or 672 of them.
static bool ov2640_set_tuning(uint32_t zoom, uint32_t clock, uint32_t tune) {
  bool screen = tune == 2;
  const uint8_t sde_off[][2] = {{0xff, 0x00}, {0x7c, 0x00}, {0x7d, 0x00},
                                {0, 0}};
  // contrast_regs[+2]: SDE contrast on, then its gain and offset
  const uint8_t sde_contrast[][2] = {
      {0xff, 0x00}, {0x7c, 0x00}, {0x7d, 0x04}, {0x7c, 0x07}, {0x7d, 0x20},
      {0x7d, 0x28}, {0x7d, 0x0c}, {0x7d, 0x06}, {0, 0}};
  bool ok = reg_write_table8(screen ? sde_contrast : sde_off) &&
            reg_write(0xff, 0x01) &&
            reg_write(0x24, screen ? 0x34 : 0x40) &&  // AEW
            reg_write(0x25, screen ? 0x1c : 0x38) &&  // AEB
            reg_write(0x26, screen ? 0x00 : 0x82) &&  // VV
            reg_write(0x14, screen ? 0x68 : 0x48);    // COM9: gain ceiling
  if (!ok) {
    return false;
  }

  g_camera.ae_soft = tune == 0;
  if (!g_camera.ae_soft) {
    // (the status reports no exposure of ours)
    memset(&g_camera.ae, 0, sizeof(g_camera.ae));
    ae_report();
    // COM8: banding filter, AGC and AEC on (esp32-camera's start-up value)
    return reg_write(0x13, 0xe5);
  }
  bool uxga = zoom == CAMERA_ZOOM_NATIVE;
  uint32_t clock_khz = clock ? 20000 : 5000;
  camera_ae_start(&g_camera.ae, (uxga ? 1922U : 1190U) * 1000000U / clock_khz,
                  uxga ? 1248 : 672);
  g_camera.last_frame_ms = 0;
  ae_report();
  // COM8: AEC, AGC and the banding filter off
  return reg_write(0x13, 0xc0) && ov2640_write_exposure();
}

static bool ov2640_init(void) {
  // esp32-camera reset() and set_pixformat(YUV422): COM7 software reset in
  // the sensor bank, the CIF start-up table, YUV422
  bool ok = reg_write(0xff, 0x01) && reg_write(0x12, 0x80);
  systick_delay_ms(10);
  ok = ok && reg_write_table8(ov2640_init_regs) &&
       reg_write_table8(ov2640_yuv422_regs);
  systick_delay_ms(10);
  return ok;
}

// ---------------------------------------------------------------------------
// Hardware

static void pin_init(GPIO_TypeDef *port, uint16_t pin, uint32_t mode,
                     uint32_t pull, uint8_t af) {
  GPIO_InitTypeDef gpio = {0};
  gpio.Pin = pin;
  gpio.Mode = mode;
  gpio.Pull = pull;
  gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  gpio.Alternate = af;
  HAL_GPIO_Init(port, &gpio);
}

static void clocks_enable(void) {
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOE_CLK_ENABLE();
  __HAL_RCC_TIM3_CLK_ENABLE();
  __HAL_RCC_I2C2_CLK_ENABLE();
  __HAL_RCC_DCMI_PSSI_CLK_ENABLE();
  __HAL_RCC_GPDMA1_CLK_ENABLE();
}

// XCLK: TIM3_CH2 PWM at 20 MHz (TIM3 runs at SYSCLK: APB1 is not divided)
static void xclk_start(void) {
  uint32_t period = SystemCoreClock / XCLK_HZ;
  TIM3->CR1 = 0;
  TIM3->PSC = 0;
  TIM3->ARR = period - 1;
  TIM3->CCR2 = period / 2;
  TIM3->CCMR1 = (6U << TIM_CCMR1_OC2M_Pos) | TIM_CCMR1_OC2PE;  // PWM mode 1
  TIM3->CCER = TIM_CCER_CC2E;
  TIM3->EGR = TIM_EGR_UG;
  TIM3->CR1 = TIM_CR1_ARPE | TIM_CR1_CEN;
  pin_init(CAMERA_XCLK_PORT, CAMERA_XCLK_PIN, GPIO_MODE_AF_PP, GPIO_NOPULL,
           CAMERA_XCLK_AF);
}

static void xclk_stop(void) {
  pin_init(CAMERA_XCLK_PORT, CAMERA_XCLK_PIN, GPIO_MODE_ANALOG, GPIO_NOPULL,
           0);
  TIM3->CR1 = 0;
  TIM3->CCER = 0;
}

#ifdef CAMERA_DVDD_PWM_PIN
// DVDD: the LDO's feedback node takes a current from an RC-filtered PWM
// (board header: CAMERA_DVDD_*). With the node at Vn,
//   VOUT = VFB (1 + RT/RB) + (VFB - Vn) RT/RS
// so Vn = VFB - (VOUT - VFB (1 + RT/RB)) RS/RT, and the duty is Vn / VDD. The
// rail is taken as its nominal 3.3 V: its 3.21-3.45 V spread moves VOUT by
// under 30 mV at 1.25 V and under 5 mV at 1.8 V. Out of range clamps to 0 %
// (the circuit's ceiling, ~1.88 V) or 100 % (0 V).
#define DVDD_PWM_STEPS 4096U  // TIM4 at 160 MHz: ~39 kHz
// The RC node settles with tau ~8 ms: five of them
#define DVDD_SETTLE_MS 40

static uint32_t dvdd_duty(uint32_t mv) {
  int64_t base_uv = (int64_t)CAMERA_DVDD_VFB_UV *
                    (CAMERA_DVDD_R_BOTTOM + CAMERA_DVDD_R_TOP) /
                    CAMERA_DVDD_R_BOTTOM;
  int64_t node_uv = CAMERA_DVDD_VFB_UV - ((int64_t)mv * 1000 - base_uv) *
                                             CAMERA_DVDD_R_SET /
                                             CAMERA_DVDD_R_TOP;
  if (node_uv <= 0) {
    return 0;
  }
  if (node_uv >= CAMERA_DVDD_VDD_UV) {
    return DVDD_PWM_STEPS;
  }
  return (uint32_t)(node_uv * DVDD_PWM_STEPS / CAMERA_DVDD_VDD_UV);
}

// Sets DVDD to mv and waits for the RC node to settle
static void camera_dvdd_set(uint32_t mv) {
  uint32_t duty = dvdd_duty(mv);
  if (!(CAMERA_DVDD_PWM_TIM->CR1 & TIM_CR1_CEN)) {
    CAMERA_DVDD_PWM_CLK_EN();
    CAMERA_DVDD_PWM_TIM->CR1 = 0;
    CAMERA_DVDD_PWM_TIM->PSC = 0;
    CAMERA_DVDD_PWM_TIM->ARR = DVDD_PWM_STEPS - 1;
    CAMERA_DVDD_PWM_TIM->CCR1 = duty;
    CAMERA_DVDD_PWM_TIM->CCMR1 =
        (6U << TIM_CCMR1_OC1M_Pos) | TIM_CCMR1_OC1PE;  // PWM mode 1
    CAMERA_DVDD_PWM_TIM->CCER = TIM_CCER_CC1E;
    CAMERA_DVDD_PWM_TIM->EGR = TIM_EGR_UG;
    CAMERA_DVDD_PWM_TIM->CR1 = TIM_CR1_ARPE | TIM_CR1_CEN;
    // Slow edges: the pin sits between XCLK and VSYNC
    GPIO_InitTypeDef gpio = {0};
    gpio.Pin = CAMERA_DVDD_PWM_PIN;
    gpio.Mode = GPIO_MODE_AF_PP;
    gpio.Pull = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_LOW;
    gpio.Alternate = CAMERA_DVDD_PWM_AF;
    HAL_GPIO_Init(CAMERA_DVDD_PWM_PORT, &gpio);
  } else {
    CAMERA_DVDD_PWM_TIM->CCR1 = duty;
  }
  LOG_INF("DVDD %u mV (PWM %u/%u)", (unsigned)mv, (unsigned)duty,
          (unsigned)DVDD_PWM_STEPS);
  systick_delay_ms(DVDD_SETTLE_MS);
}

// PWM off, the pin floating: nothing feeds the feedback node while the LDO is
// off
static void camera_dvdd_off(void) {
  pin_init(CAMERA_DVDD_PWM_PORT, CAMERA_DVDD_PWM_PIN, GPIO_MODE_ANALOG,
           GPIO_NOPULL, 0);
  CAMERA_DVDD_PWM_TIM->CR1 = 0;
  CAMERA_DVDD_PWM_TIM->CCER = 0;
}

// Each sensor's core voltage: OV2640 1.14-1.26 V (data sheet v1.6) and
// 1.24-1.36 V (v2.2), so the overlap; OV5640 1.5 V +-5 %; GC2145 1.7-1.9 V
static uint32_t sensor_dvdd_mv(uint32_t sensor_id) {
  switch (sensor_id) {
    case CAMERA_SENSOR_OV2640:
      return 1250;
    case CAMERA_SENSOR_GC2145:
      return 1800;
    default:
      return 1500;
  }
}
#endif

// The SCCB lines as inputs, before the I2C takes them: both high means the
// pull-ups are there and nothing holds the bus down
static void sccb_lines_probe(void) {
  pin_init(CAMERA_I2C_SCL_PORT, CAMERA_I2C_SCL_PIN, GPIO_MODE_INPUT,
           GPIO_NOPULL, 0);
  pin_init(CAMERA_I2C_SDA_PORT, CAMERA_I2C_SDA_PIN, GPIO_MODE_INPUT,
           GPIO_NOPULL, 0);
  systick_delay_ms(1);
  bool scl = HAL_GPIO_ReadPin(CAMERA_I2C_SCL_PORT, CAMERA_I2C_SCL_PIN);
  bool sda = HAL_GPIO_ReadPin(CAMERA_I2C_SDA_PORT, CAMERA_I2C_SDA_PIN);
  g_camera.st.i2c_lines = (scl ? 1U : 0U) | (sda ? 2U : 0U);
}

// XCLK's pin read back while the timer drives it: sampled far below 20 MHz
// it still changes level often; never means no clock leaves the MCU. The
// samples are spaced unevenly: a fixed-length loop can lock onto the clock's
// phase (8 core cycles per XCLK period) and read the same level every time.
static void xclk_probe(void) {
  uint32_t edges = 0;
  bool prev = (CAMERA_XCLK_PORT->IDR & CAMERA_XCLK_PIN) != 0;
  for (int i = 0; i < 20000; i++) {
    for (volatile int d = i % 5; d > 0; d--) {
    }
    bool now = (CAMERA_XCLK_PORT->IDR & CAMERA_XCLK_PIN) != 0;
    edges += now != prev;
    prev = now;
  }
  g_camera.st.xclk_edges = edges;
}

static bool sccb_init(void) {
  pin_init(CAMERA_I2C_SCL_PORT, CAMERA_I2C_SCL_PIN, GPIO_MODE_AF_OD,
           GPIO_NOPULL, CAMERA_I2C_AF);
  pin_init(CAMERA_I2C_SDA_PORT, CAMERA_I2C_SDA_PIN, GPIO_MODE_AF_OD,
           GPIO_NOPULL, CAMERA_I2C_AF);

  memset(&g_camera.i2c, 0, sizeof(g_camera.i2c));
  g_camera.i2c.Instance = CAMERA_I2C_INSTANCE;
  g_camera.i2c.Init.Timing = SCCB_TIMING;
  g_camera.i2c.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
  g_camera.i2c.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
  g_camera.i2c.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
  g_camera.i2c.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
  if (HAL_I2C_Init(&g_camera.i2c) != HAL_OK) {
    LOG_ERR("I2C2 init failed");
    return false;
  }
  return true;
}

// Which camera-bus addresses acknowledge: tells "no module" from "another
// sensor" (an OV2640 answers at 0x30) without guessing
static void sccb_scan(void) {
  for (uint32_t addr = 0x08; addr < 0x78; addr++) {
    if (HAL_I2C_IsDeviceReady(&g_camera.i2c, (uint16_t)(addr << 1), 1, 2) ==
        HAL_OK) {
      g_camera.st.i2c_acks[addr / 8] |= (uint8_t)(1U << (addr % 8));
    }
  }
}

static bool sccb_acked(uint32_t addr) {
  return (g_camera.st.i2c_acks[addr / 8] >> (addr % 8)) & 1U;
}

static void dcmi_pins_init(void) {
  for (size_t i = 0; i < ARRAY_LENGTH(g_dcmi_pins); i++) {
    pin_init(g_dcmi_pins[i].port, g_dcmi_pins[i].pin, GPIO_MODE_AF_PP,
             GPIO_NOPULL, g_dcmi_pins[i].af);
  }
}

static void dcmi_pins_deinit(void) {
  for (size_t i = 0; i < ARRAY_LENGTH(g_dcmi_pins); i++) {
    pin_init(g_dcmi_pins[i].port, g_dcmi_pins[i].pin, GPIO_MODE_ANALOG,
             GPIO_NOPULL, 0);
  }
}

static void dma_stop(void) {
  DMA_Channel_TypeDef *ch = CAMERA_DMA_CHANNEL;
  if (ch->CCR & DMA_CCR_EN) {
    ch->CCR |= DMA_CCR_SUSP;
    uint32_t start = systick_ms();
    while (!(ch->CSR & (DMA_CSR_SUSPF | DMA_CSR_IDLEF)) &&
           systick_ms() - start < 2) {
    }
  }
  ch->CCR = DMA_CCR_RESET;
  ch->CFCR = DMA_CFCR_TCF | DMA_CFCR_HTF | DMA_CFCR_DTEF | DMA_CFCR_ULEF |
             DMA_CFCR_USEF | DMA_CFCR_SUSPF | DMA_CFCR_TOF;
}

// DCMI in snapshot mode: PIXCLK rising, HREF low = blanking (HSPOL 0),
// VSYNC's blanking level as measured (VSPOL)
static void dcmi_configure(void) {
  DCMI->CR = 0;
  DCMI->IER = 0;
  DCMI->ICR = 0x1F;
  uint32_t cr = DCMI_CR_CM | DCMI_CR_PCKPOL;
  if (g_camera.st.vsync_blank_high) {
    cr |= DCMI_CR_VSPOL;
  }
  if (g_camera.byte_select) {
    cr |= DCMI_CR_BSM_0;  // every other byte, starting with the first
  }
  if (g_camera.crop) {
    DCMI->CWSTRTR = g_camera.crop_start;
    DCMI->CWSIZER = g_camera.crop_size;
    cr |= DCMI_CR_CROP;
  }
  DCMI->CR = cr;
  DCMI->CR = cr | DCMI_CR_ENABLE;
}

// Samples the sync pins while the sensor streams: proves VSYNC, HREF and PCLK
// move at all, and gives VSYNC's blanking level (the rarer one: its pulse or
// the vertical blanking is short next to a frame)
static void probe_sync_pins(void) {
  uint32_t samples = 0, vs_high = 0, hr_high = 0;
  uint32_t vs_edges = 0, hr_edges = 0, pc_edges = 0;
  bool vs_prev = false, hr_prev = false, pc_prev = false;
  uint32_t start = systick_ms();

  while (systick_ms() - start < PROBE_MS) {
    for (int i = 0; i < 64; i++) {
      bool vs = (CAMERA_VSYNC_PORT->IDR & CAMERA_VSYNC_PIN) != 0;
      bool hr = (CAMERA_HREF_PORT->IDR & CAMERA_HREF_PIN) != 0;
      bool pc = (CAMERA_PCLK_PORT->IDR & CAMERA_PCLK_PIN) != 0;
      if (samples > 0) {
        vs_edges += vs != vs_prev;
        hr_edges += hr != hr_prev;
        pc_edges += pc != pc_prev;
      }
      vs_prev = vs;
      hr_prev = hr;
      pc_prev = pc;
      vs_high += vs;
      hr_high += hr;
      samples++;
    }
  }

  camera_status_t *st = &g_camera.st;
  st->vsync_high_pm = (uint32_t)((uint64_t)vs_high * 1000 / samples);
  st->href_high_pm = (uint32_t)((uint64_t)hr_high * 1000 / samples);
  st->vsync_edges = vs_edges;
  st->href_edges = hr_edges;
  st->pclk_edges = pc_edges;
  // Both sensors' setups make VSYNC high in blanking; only a measured frame
  // (two edges or more) overrides that
  st->vsync_blank_high = vs_edges >= 2 ? st->vsync_high_pm < 500 : 1;
  LOG_INF("sync probe: VSYNC %u/1000 high, %u edges; HREF %u/1000, %u; PCLK %u",
          (unsigned)st->vsync_high_pm, (unsigned)vs_edges,
          (unsigned)st->href_high_pm, (unsigned)hr_edges, (unsigned)pc_edges);
}

static bool mode_valid(uint32_t mode) {
  uint32_t side = CAMERA_MODE_SIDE(mode);
  return CAMERA_MODE_ZOOM(mode) < CAMERA_ZOOM_LEVELS && side >= CAMERA_SIDE_MIN &&
         side <= CAMERA_SIDE_MAX && CAMERA_MODE_CLOCK(mode) < CAMERA_CLOCKS &&
         CAMERA_MODE_TUNE(mode) < CAMERA_TUNES;
}

static bool sensor_set_mode(uint32_t mode) {
  uint32_t zoom = CAMERA_MODE_ZOOM(mode);
  uint32_t side = CAMERA_MODE_SIDE(mode);
  uint32_t clock = CAMERA_MODE_CLOCK(mode);
  uint32_t tune = CAMERA_MODE_TUNE(mode);
  bool ok;
  switch (g_camera.st.sensor_id) {
    case CAMERA_SENSOR_OV5640:
      ok = ov5640_set_mode(zoom, side);
      break;
    case CAMERA_SENSOR_GC2145:
      ok = gc2145_set_tuning(clock, tune) && gc2145_set_mode(zoom, side, tune);
      break;
    default:
      ok = ov2640_set_mode(zoom, side, clock) &&
           ov2640_set_tuning(zoom, clock, tune);
      break;
  }
  if (ok) {
    g_camera.st.zoom = zoom;
    g_camera.st.side = side;
    g_camera.st.clock = clock;
    g_camera.st.tune = tune;
    g_camera.frame_bytes = (uint32_t)CAMERA_FRAME_BYTES(side);
    // The new mode's first frames come out of a reconfigured pipeline
    systick_delay_ms(100);
    probe_sync_pins();
    dcmi_configure();
  }
  return ok;
}

// Finds the sensor: at 0x3C the GC2145 first (8-bit register addresses),
// then the OV5640; then the OV2640 at 0x30. Leaves sccb_addr at the one found.
static uint32_t sensor_detect(void) {
  uint8_t hi = 0, lo = 0;

  g_camera.sccb_addr = SCCB_ADDR_OV2640;
  // Sensor bank, then PID (0x26) and VER
  if (sccb_write8(0xff, 0x01) && sccb_read8(0x0A, &hi) &&
      sccb_read8(0x0B, &lo)) {
    g_camera.st.raw_id = ((uint32_t)hi << 8) | lo;
    if (hi == 0x26) {
      return CAMERA_SENSOR_OV2640;
    }
  }

  g_camera.sccb_addr = SCCB_ADDR_0X3C;
  if (sccb_read8(0xF0, &hi) && sccb_read8(0xF1, &lo)) {
    g_camera.st.raw_id = ((uint32_t)hi << 8) | lo;
    if (g_camera.st.raw_id == CAMERA_SENSOR_GC2145) {
      return CAMERA_SENSOR_GC2145;
    }
  }
  if (sccb_read16(0x300A, &hi) && sccb_read16(0x300B, &lo)) {
    g_camera.st.raw_id = ((uint32_t)hi << 8) | lo;
    if (g_camera.st.raw_id == CAMERA_SENSOR_OV5640) {
      return CAMERA_SENSOR_OV5640;
    }
  }
  return CAMERA_SENSOR_NONE;
}

// Runs the debugger's register ops, if it posted new ones
static void camera_debug_poll(void) {
  volatile camera_debug_t *d = &g_camera_debug;
  memcpy((void *)&d->st, &g_camera.st, sizeof(d->st));
  if (d->req_seq == d->ack_seq) {
    return;
  }
  uint32_t n = d->n_ops < CAMERA_DEBUG_OPS ? d->n_ops : CAMERA_DEBUG_OPS;
  bool ov5640 = g_camera.st.sensor_id == CAMERA_SENSOR_OV5640;
  for (uint32_t i = 0; i < n; i++) {
    volatile camera_debug_op_t *op = &d->ops[i];
    bool ok = g_camera.powered;
    if (op->op == CAMERA_DEBUG_OP_DELAY) {
      systick_delay_ms(op->val);
    } else if (ok) {
      if (op->bank != CAMERA_DEBUG_NO_BANK && !ov5640) {
        ok = sccb_write8(0xff, (uint8_t)op->bank);
      }
      if (ok && op->op == CAMERA_DEBUG_OP_WRITE) {
        ok = ov5640 ? sccb_write16(op->reg, op->val)
                    : sccb_write8((uint8_t)op->reg, op->val);
      } else if (ok && op->op == CAMERA_DEBUG_OP_READ) {
        uint8_t v = 0;
        ok = ov5640 ? sccb_read16(op->reg, &v) : sccb_read8((uint8_t)op->reg, &v);
        op->result = v;
      }
    }
    op->ok = ok ? 1 : 0;
    if (!ok) {
      LOG_WARN("debug op %u reg 0x%04x failed", (unsigned)op->op,
               (unsigned)op->reg);
    }
  }
  __DSB();
  d->ack_seq = d->req_seq;
}

// ---------------------------------------------------------------------------
// API

void camera_close(void) {
  if (!g_camera.powered) {
    return;
  }
  dma_stop();
  DCMI->CR = 0;
  HAL_I2C_DeInit(&g_camera.i2c);
  dcmi_pins_deinit();
  xclk_stop();
  HAL_GPIO_WritePin(CAMERA_RST_PORT, CAMERA_RST_PIN, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(CAMERA_EN_PORT, CAMERA_EN_PIN, GPIO_PIN_RESET);
#ifdef CAMERA_DVDD_PWM_PIN
  camera_dvdd_off();
#endif
  g_camera.powered = false;
  g_camera.st.state = CAMERA_STATE_OFF;
  g_camera_debug.held = 0;
}

bool camera_open(uint32_t mode) {
  camera_close();
  memset(&g_camera, 0, sizeof(g_camera));
  if (!mode_valid(mode)) {
    g_camera.st.open_error = CAMERA_ERR_BAD_MODE;
    return false;
  }

  clocks_enable();

  // Reset held and XCLK low, then the module's rails on (AVDD and DVDD; on v4
  // DOVDD too, with the XCLK/RESET buffer on it: its inputs must not float)
  HAL_GPIO_WritePin(CAMERA_RST_PORT, CAMERA_RST_PIN, GPIO_PIN_RESET);
  pin_init(CAMERA_RST_PORT, CAMERA_RST_PIN, GPIO_MODE_OUTPUT_PP, GPIO_NOPULL,
           0);
  HAL_GPIO_WritePin(CAMERA_XCLK_PORT, CAMERA_XCLK_PIN, GPIO_PIN_RESET);
  pin_init(CAMERA_XCLK_PORT, CAMERA_XCLK_PIN, GPIO_MODE_OUTPUT_PP, GPIO_NOPULL,
           0);
#ifdef CAMERA_DVDD_PWM_PIN
  // Detection at 1.5 V: in or near every sensor's range (OV2640 runs there on
  // Espressif's ESP32-S3-EYE), so whichever is fitted answers its ID
  camera_dvdd_set(1500);
#endif
  HAL_GPIO_WritePin(CAMERA_EN_PORT, CAMERA_EN_PIN, GPIO_PIN_SET);
  pin_init(CAMERA_EN_PORT, CAMERA_EN_PIN, GPIO_MODE_OUTPUT_PP, GPIO_NOPULL, 0);
  g_camera.powered = true;
  g_camera.st.state = CAMERA_STATE_IDLE;
  systick_delay_ms(10);

  xclk_start();
  xclk_probe();
  systick_delay_ms(5);
  HAL_GPIO_WritePin(CAMERA_RST_PORT, CAMERA_RST_PIN, GPIO_PIN_SET);
  // OV5640: 20 ms from reset release to the first SCCB transaction
  systick_delay_ms(30);

  dcmi_pins_init();
  sccb_lines_probe();
  if (!sccb_init()) {
    g_camera.st.open_error = CAMERA_ERR_NO_SENSOR;
    camera_close();
    return false;
  }
  // A slow start-up gets a few chances (~250 ms) before the verdict
  for (int attempt = 0; attempt < 5; attempt++) {
    if (attempt > 0) {
      systick_delay_ms(50);
    }
    g_camera.st.sensor_id = sensor_detect();
    if (g_camera.st.sensor_id != CAMERA_SENSOR_NONE) {
      break;
    }
  }
#ifdef CAMERA_DVDD_PWM_PIN
  // A GC2145 module that feeds DVDD straight to the sensor needs 1.7-1.9 V,
  // and may not answer at 1.5 V
  if (g_camera.st.sensor_id == CAMERA_SENSOR_NONE) {
    HAL_GPIO_WritePin(CAMERA_RST_PORT, CAMERA_RST_PIN, GPIO_PIN_RESET);
    camera_dvdd_set(1800);
    HAL_GPIO_WritePin(CAMERA_RST_PORT, CAMERA_RST_PIN, GPIO_PIN_SET);
    systick_delay_ms(30);
    for (int attempt = 0; attempt < 3; attempt++) {
      if (attempt > 0) {
        systick_delay_ms(50);
      }
      g_camera.st.sensor_id = sensor_detect();
      if (g_camera.st.sensor_id != CAMERA_SENSOR_NONE) {
        break;
      }
    }
  }
#endif
  sccb_scan();

  if (g_camera.st.sensor_id == CAMERA_SENSOR_NONE) {
    g_camera.st.open_error =
        sccb_acked(SCCB_ADDR_0X3C) || sccb_acked(SCCB_ADDR_OV2640)
            ? CAMERA_ERR_UNKNOWN_ID
            : CAMERA_ERR_NO_SENSOR;
    LOG_ERR("no sensor (id 0x%04x)", (unsigned)g_camera.st.raw_id);
    // Switched off: a module that does not answer may be loading a rail
    // (a camera LDO ran hot on proto 0 with modules that never answered)
    camera_close();
    return false;
  }
  LOG_INF("sensor 0x%04x", (unsigned)g_camera.st.sensor_id);
#ifdef CAMERA_DVDD_PWM_PIN
  // The sensor's own core voltage, changed under reset; the start-up tables
  // then begin from a fresh reset
  uint32_t dvdd_mv = sensor_dvdd_mv(g_camera.st.sensor_id);
  if (dvdd_duty(dvdd_mv) != CAMERA_DVDD_PWM_TIM->CCR1) {
    HAL_GPIO_WritePin(CAMERA_RST_PORT, CAMERA_RST_PIN, GPIO_PIN_RESET);
    camera_dvdd_set(dvdd_mv);
    HAL_GPIO_WritePin(CAMERA_RST_PORT, CAMERA_RST_PIN, GPIO_PIN_SET);
    systick_delay_ms(30);
  }
#endif

  bool ok;
  switch (g_camera.st.sensor_id) {
    case CAMERA_SENSOR_OV5640:
      ok = ov5640_init();
      break;
    case CAMERA_SENSOR_GC2145:
      ok = gc2145_init();
      break;
    default:
      ok = ov2640_init();
      break;
  }
  if (!ok || !sensor_set_mode(mode)) {
    camera_close();
    return false;
  }
  return true;
}

bool camera_set_mode(uint32_t mode) {
  if (!g_camera.powered || g_camera.st.sensor_id == CAMERA_SENSOR_NONE ||
      !mode_valid(mode)) {
    return false;
  }
  dma_stop();
  DCMI->CR = 0;
  g_camera.st.state = CAMERA_STATE_IDLE;
  return sensor_set_mode(mode);
}

bool camera_capture(uint8_t *buf, size_t size) {
  camera_debug_poll();
  if (g_camera_debug.held) {
    if (g_camera_debug.hold_req) {
      return false;
    }
    g_camera_debug.held = 0;
  }
  uint32_t bytes = g_camera.frame_bytes;
  if (!g_camera.powered || g_camera.st.sensor_id == CAMERA_SENSOR_NONE ||
      g_camera.st.state == CAMERA_STATE_BUSY || bytes == 0 || size < bytes ||
      ((uintptr_t)buf & 3U) != 0) {
    return false;
  }

  dma_stop();
  DCMI->CR &= ~DCMI_CR_CAPTURE;
  DCMI->ICR = 0x1F;

  // The frame in equal blocks of whole words, each at most DMA_BLOCK_MAX
  uint32_t blocks = (bytes + DMA_BLOCK_MAX - 1) / DMA_BLOCK_MAX;
  uint32_t block = ((bytes + blocks - 1) / blocks + 3U) & ~3U;
  uint32_t dst = (uint32_t)buf;

  // After the first block, each list item loads the next block's size and
  // destination, and the link to the item after it (none after the last)
  const uint32_t update = DMA_CLLR_UB1 | DMA_CLLR_UDA | DMA_CLLR_ULL;
  for (uint32_t i = 1; i < blocks; i++) {
    uint32_t at = block * i;
    uint32_t *item = g_dma_lli[i - 1];
    item[0] = (i + 1 < blocks ? block : bytes - at) & DMA_CBR1_BNDT;  // CBR1
    item[1] = dst + at;                                               // CDAR
    item[2] = i + 1 < blocks
                  ? (((uint32_t)g_dma_lli[i] & DMA_CLLR_LA) | update)
                  : 0;  // CLLR
  }
  __DSB();

  // DCMI data register (words) to the buffer (words, incrementing); the
  // transfer-complete flag only after the list's last block; the channel is
  // privileged like the kernel
  DMA_Channel_TypeDef *ch = CAMERA_DMA_CHANNEL;
  GPDMA1->PRIVCFGR |= 1U << CAMERA_DMA_CHANNEL_NUM;
  ch->CCR = 0;
  ch->CLBAR = (uint32_t)g_dma_lli & DMA_CLBAR_LBA;
  ch->CTR1 = (2U << DMA_CTR1_SDW_LOG2_Pos) | (2U << DMA_CTR1_DDW_LOG2_Pos) |
             DMA_CTR1_DINC | DMA_CTR1_DAP;
  ch->CTR2 = (GPDMA1_REQUEST_DCMI_PSSI << DMA_CTR2_REQSEL_Pos) |
             (3U << DMA_CTR2_TCEM_Pos);
  ch->CBR1 = (blocks > 1 ? block : bytes) & DMA_CBR1_BNDT;
  ch->CSAR = (uint32_t)&DCMI->DR;
  ch->CDAR = dst;
  ch->CLLR = blocks > 1 ? (((uint32_t)g_dma_lli[0] & DMA_CLLR_LA) | update) : 0;
  ch->CCR = (3U << DMA_CCR_PRIO_Pos) | DMA_CCR_EN;  // high priority

  g_camera.capture_buf = dst;
  g_camera.capture_start_ms = systick_ms();
  g_camera.st.state = CAMERA_STATE_BUSY;
  DCMI->CR |= DCMI_CR_CAPTURE;
  return true;
}

static void capture_finish(bool ok, uint32_t flags) {
  DMA_Channel_TypeDef *ch = CAMERA_DMA_CHANNEL;
  camera_status_t *st = &g_camera.st;

  DCMI->CR &= ~DCMI_CR_CAPTURE;
  // Where the destination has got to: the bytes written, over all blocks
  st->last_bytes = ch->CDAR - g_camera.capture_buf;
  st->last_flags = flags | (DCMI->RISR & 0x1F);
  st->last_capture_ms = systick_ms() - g_camera.capture_start_ms;
  dma_stop();
  DCMI->ICR = 0x1F;

  if (ok && g_camera_debug.hold_req) {
    // Kept raw for the debugger: FAILED with the frame in, so the screen
    // paints it and does not decode (threshold) it
    st->frames++;
    st->last_flags |= CAMERA_FLAG_DEBUG_HOLD;
    st->state = CAMERA_STATE_FAILED;
    g_camera_debug.frame_addr = g_camera.capture_buf;
    g_camera_debug.frame_side = st->side;
    g_camera_debug.frame_seq++;
    g_camera_debug.held = 1;
  } else if (ok) {
    st->frames++;
    st->state = CAMERA_STATE_DONE;
  } else {
    st->failures++;
    st->state = CAMERA_STATE_FAILED;
  }

  g_camera.fail_run = ok ? 0 : g_camera.fail_run + 1;
  if (ok && g_camera.ae_soft && st->sensor_id == CAMERA_SENSOR_OV2640) {
    // The kernel reads the app's buffer before the app gets the frame
    ov2640_soft_ae((const uint8_t *)g_camera.capture_buf, st->side);
  }
  if (!ok && st->clock != 0 && g_camera.fail_run >= 3) {
    // The fast clock does not capture here: back to the stock one (the
    // status shows clock 0)
    LOG_WARN("captures fail at the fast clock, falling back");
    g_camera.fail_run = 0;
    if (!sensor_set_mode(CAMERA_MODE(st->zoom, st->side, 0, st->tune))) {
      LOG_ERR("fallback to the stock clock failed");
    }
  }
}

static void capture_poll(void) {
  DMA_Channel_TypeDef *ch = CAMERA_DMA_CHANNEL;
  uint32_t csr = ch->CSR;
  uint32_t ris = DCMI->RISR;

  if (csr & (DMA_CSR_DTEF | DMA_CSR_ULEF | DMA_CSR_USEF)) {
    capture_finish(false, CAMERA_FLAG_DMA_ERROR);
    return;
  }

  if (ris & DCMI_RIS_FRAME_RIS) {
    // The frame ended: the DMA drains the DCMI's FIFO in microseconds
    uint32_t start = systick_ms();
    while (!(ch->CSR & DMA_CSR_TCF) && systick_ms() - start < 2) {
    }
    bool whole = (ch->CSR & DMA_CSR_TCF) != 0;
    bool overrun = (DCMI->RISR & DCMI_RIS_OVR_RIS) != 0;
    capture_finish(whole && !overrun, 0);
    return;
  }

  // A whole frame fills the buffer a little before the frame-end flag (that
  // waits for VSYNC). Data still coming after it has nowhere to go and
  // overruns the DCMI's FIFO: a frame longer than expected (the buffer holds
  // its first frame_bytes).
  if ((csr & DMA_CSR_TCF) && (ris & DCMI_RIS_OVR_RIS)) {
    capture_finish(false, CAMERA_FLAG_DMA_FULL);
    return;
  }

  if (systick_ms() - g_camera.capture_start_ms > CAPTURE_TIMEOUT_MS) {
    capture_finish(false, CAMERA_FLAG_TIMEOUT |
                              ((csr & DMA_CSR_TCF) ? CAMERA_FLAG_DMA_FULL : 0));
  }
}

void camera_get_status(camera_status_t *status) {
  if (g_camera.st.state == CAMERA_STATE_BUSY) {
    capture_poll();
  }
  camera_debug_poll();
  *status = g_camera.st;
}

void camera_set_meter(uint32_t top_left, uint32_t bottom_right) {
  const int32_t box[4] = {(int32_t)(top_left & 0xFFFF),
                          (int32_t)(top_left >> 16),
                          (int32_t)(bottom_right & 0xFFFF),
                          (int32_t)(bottom_right >> 16)};
  // An empty area is none (camera_ae_set_box())
  camera_ae_set_box(&g_camera.ae, box);
}

#endif  // KERNEL_MODE
