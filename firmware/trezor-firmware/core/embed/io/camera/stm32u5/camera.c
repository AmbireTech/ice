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
 * Every frame is CAMERA_FRAME_W x CAMERA_FRAME_H Y8. The zoom levels are
 * esp32-camera's proven output modes (sensors/ov5640.c, gc2145.c):
 *
 *   OV5640  0: 240x240 from the 1920x1920 centre square (binned, scaled)
 *           1: 640x480 (4:3, binned, scaled), DCMI crop of the centre 240x240
 *           2: 1024x768 likewise
 *   GC2145  0: 720x720 centre window subsampled 1/3 to 240x240
 *           1: 480x480 window subsampled 1/2
 *           2: 240x240 window, native pixels
 *   OV2640  0: 240x240 from the 1200x1200 square, CIF mode (FRAMESIZE_240X240)
 *           1: 400x400 centre window of SVGA mode (800x600), scaled
 *           2: 400x400 centre window of UXGA mode (1600x1200), scaled
 *
 * A capture is one DCMI snapshot into a buffer the app owns, by GPDMA1
 * (block mode, no interrupts): camera_get_status() polls it and closes it.
 */

#include <trezor_bsp.h>
#include <trezor_rtl.h>

#include <io/camera.h>
#include <sys/logging.h>
#include <sys/systick.h>

#ifdef KERNEL_MODE

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

// The slowest modes (OV5640 1024x768, OV2640 UXGA) run a few frames a second and a snapshot
// waits for the next frame start: two frame times, and some slack
#define CAPTURE_TIMEOUT_MS 1000
// Sync-pin probe after the sensor starts: longer than one frame at ~4 fps
#define PROBE_MS 300

// last_flags: beyond the DCMI's own raw flags
#define CAMERA_FLAG_DMA_FULL (1U << 8)   // buffer full, the frame went on
#define CAMERA_FLAG_DMA_ERROR (1U << 9)  // GPDMA transfer/setting error
#define CAMERA_FLAG_TIMEOUT (1U << 10)   // no frame end in time

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
  uint32_t capture_start_ms;
} camera_driver_t;

static camera_driver_t g_camera;

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

// esp32-camera set_framesize() for FRAMESIZE_240X240 (1:1 ratio), VGA and XGA
// (4:3), all binned and scaled; XCLK 20 MHz gives a 10 MHz PCLK in each
static const ov5640_mode_t g_ov5640_modes[CAMERA_ZOOM_LEVELS] = {
    {240, 240, 320, 0, 2543, 1951, 16, 8, 2060, 984, 20, 1, 1, 1, 8},
    {640, 480, 0, 0, 2623, 1951, 16, 8, 2060, 984, 10, 1, 2, 1, 2},
    {1024, 768, 0, 0, 2623, 1951, 16, 8, 2644, 984, 10, 1, 2, 1, 2},
};

static bool ov5640_set_mode(uint32_t zoom) {
  const ov5640_mode_t *m = &g_ov5640_modes[zoom];
  uint8_t isp01 = 0;

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
  g_camera.crop = m->w != CAMERA_FRAME_W || m->h != CAMERA_FRAME_H;
  if (g_camera.crop) {
    // Y8: one byte (one pixel clock) per pixel
    uint32_t x0 = (m->w - CAMERA_FRAME_W) / 2;
    uint32_t y0 = (m->h - CAMERA_FRAME_H) / 2;
    g_camera.crop_start =
        (y0 << DCMI_CWSTRT_VST_Pos) | (x0 << DCMI_CWSTRT_HOFFCNT_Pos);
    g_camera.crop_size = ((uint32_t)(CAMERA_FRAME_H - 1) << DCMI_CWSIZE_VLINE_Pos) |
                         ((uint32_t)(CAMERA_FRAME_W - 1) << DCMI_CWSIZE_CAPCNT_Pos);
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

// esp32-camera set_framesize() in subsample mode: window side for a 240x240
// output, and the subsample register 0x99 (0x9b..0xa2 stay 0 for these)
static const struct {
  uint16_t win;
  uint8_t ratio;
} g_gc2145_modes[CAMERA_ZOOM_LEVELS] = {
    {720, 0x33},  // 1/3
    {480, 0x22},  // 1/2
    {240, 0x11},  // 1/1
};

static bool gc2145_set_mode(uint32_t zoom) {
  uint16_t win = g_gc2145_modes[zoom].win;
  uint16_t row = (1200 - win) / 2;
  uint16_t col = (1600 - win) / 2;
  uint16_t out = CAMERA_FRAME_W;

  bool ok = reg_write(0xfe, 0x00) && reg_write(0x90, 0x01) &&
            reg_write(0x09, (uint8_t)(row >> 8)) &&
            reg_write(0x0a, (uint8_t)row) &&
            reg_write(0x0b, (uint8_t)(col >> 8)) &&
            reg_write(0x0c, (uint8_t)col) &&
            reg_write(0x0d, (uint8_t)((win + 8) >> 8)) &&
            reg_write(0x0e, (uint8_t)(win + 8)) &&
            reg_write(0x0f, (uint8_t)((win + 16) >> 8)) &&
            reg_write(0x10, (uint8_t)(win + 16)) &&
            reg_write(0x99, g_gc2145_modes[zoom].ratio);
  for (uint16_t reg = 0x9b; ok && reg <= 0xa2; reg++) {
    ok = reg_write(reg, 0x00);
  }
  ok = ok && reg_write(0x95, (uint8_t)(out >> 8)) &&
       reg_write(0x96, (uint8_t)out) && reg_write(0x97, (uint8_t)(out >> 8)) &&
       reg_write(0x98, (uint8_t)out);

  // YUYV (0x84 = 2: Y Cb Y Cr): the Y is every even byte of a line
  g_camera.byte_select = true;
  g_camera.crop = false;
  return ok;
}

static bool gc2145_init(void) {
  // esp32-camera reset(): software reset, the default table, then (as on the
  // plain ESP32) PCLK slowed to <= 15 MHz (0xf8 low bits = 2, 0xfa high
  // nibble = 2), and YUV422 output
  if (!reg_write(0xfe, 0xe0)) {
    return false;
  }
  systick_delay_ms(100);
  if (!reg_write_table(gc2145_init_regs)) {
    return false;
  }
  systick_delay_ms(100);
  return reg_write(0xfe, 0x00) && reg_write(0xf8, 0x82) &&
         reg_write(0xfa, 0x20) && reg_write(0x84, 0x02);
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

typedef struct {
  const uint8_t (*mode_regs)[2];  // the sensor mode (resolution) table
  uint16_t ox, oy, mx, my;        // window into the mode's field
  uint8_t clkrc;                  // CLKRC: bit 7 clock doubler, divider - 1
  uint8_t dvp_sp;                 // R_DVP_SP: bit 7 auto, PCLK divider
} ov2640_mode_t;

// esp32-camera set_framesize()/set_window() for a 240x240 output. Zoom 0 is
// its FRAMESIZE_240X240 (1:1 ratio in CIF: the 1200x1200 square / 4, the
// height capped at 296); 1 and 2 are 400x400 centre windows of the SVGA and
// UXGA fields. Clocks as esp32-camera sets them on the ESP32-S3 (doubler on).
static const ov2640_mode_t g_ov2640_modes[CAMERA_ZOOM_LEVELS] = {
    {ov2640_to_cif_regs, 50, 0, 300, 296, 0x83, 0x88},
    {ov2640_to_svga_regs, 200, 100, 400, 400, 0x87, 0x88},
    {ov2640_to_uxga_regs, 600, 400, 400, 400, 0x87, 0x8C},
};

static bool ov2640_set_mode(uint32_t zoom) {
  const ov2640_mode_t *m = &g_ov2640_modes[zoom];
  // set_window(): the DSP's window and zoom registers count in units of 4
  uint16_t mx = m->mx / 4, my = m->my / 4;
  uint16_t w = CAMERA_FRAME_W / 4, h = CAMERA_FRAME_H / 4;
  const uint8_t win[][2] = {
      {0xff, 0x00},  // DSP bank
      {0x51, (uint8_t)mx},                                   // HSIZE
      {0x52, (uint8_t)my},                                   // VSIZE
      {0x53, (uint8_t)m->ox},                                // XOFFL
      {0x54, (uint8_t)m->oy},                                // YOFFL
      {0x55, (uint8_t)(((my >> 1) & 0x80) | ((m->oy >> 4) & 0x70) |
                       ((mx >> 5) & 0x08) | ((m->ox >> 8) & 0x07))},  // VHYX
      {0x57, (uint8_t)((mx >> 2) & 0x80)},                   // TEST
      {0x5a, (uint8_t)w},                                    // ZMOW
      {0x5b, (uint8_t)h},                                    // ZMOH
      {0x5c, (uint8_t)(((h >> 6) & 0x04) | ((w >> 8) & 0x03))},  // ZMHH
      {0, 0},
  };

  bool ok = reg_write(0xff, 0x00) && reg_write(0x05, 0x01) &&  // DSP bypass
            reg_write_table8(m->mode_regs) && reg_write_table8(win) &&
            reg_write(0xff, 0x01) && reg_write(0x11, m->clkrc) &&  // CLKRC
            reg_write(0xff, 0x00) && reg_write(0xd3, m->dvp_sp) &&  // R_DVP_SP
            reg_write(0x05, 0x00);  // DSP on
  systick_delay_ms(10);
  // set_pixformat() again: required after a resolution change
  ok = ok && reg_write_table8(ov2640_yuv422_regs);
  systick_delay_ms(10);

  // YUYV (IMAGE_MODE 0: high byte first): the Y is every even byte
  g_camera.byte_select = true;
  g_camera.crop = false;
  return ok;
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

static bool sensor_set_mode(uint32_t zoom) {
  bool ok;
  switch (g_camera.st.sensor_id) {
    case CAMERA_SENSOR_OV5640:
      ok = ov5640_set_mode(zoom);
      break;
    case CAMERA_SENSOR_GC2145:
      ok = gc2145_set_mode(zoom);
      break;
    default:
      ok = ov2640_set_mode(zoom);
      break;
  }
  if (ok) {
    g_camera.st.zoom = zoom;
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
  g_camera.powered = false;
  g_camera.st.state = CAMERA_STATE_OFF;
}

bool camera_open(uint32_t zoom) {
  camera_close();
  memset(&g_camera, 0, sizeof(g_camera));
  if (zoom >= CAMERA_ZOOM_LEVELS) {
    zoom = 0;
  }

  clocks_enable();

  // Reset held, then the module's AVDD and DVDD on (DOVDD is always on)
  HAL_GPIO_WritePin(CAMERA_RST_PORT, CAMERA_RST_PIN, GPIO_PIN_RESET);
  pin_init(CAMERA_RST_PORT, CAMERA_RST_PIN, GPIO_MODE_OUTPUT_PP, GPIO_NOPULL,
           0);
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
  sccb_scan();

  if (g_camera.st.sensor_id == CAMERA_SENSOR_NONE) {
    g_camera.st.open_error =
        sccb_acked(SCCB_ADDR_0X3C) || sccb_acked(SCCB_ADDR_OV2640)
            ? CAMERA_ERR_UNKNOWN_ID
            : CAMERA_ERR_NO_SENSOR;
    LOG_ERR("no sensor (id 0x%04x)", (unsigned)g_camera.st.raw_id);
    // Bring-up: left powered and clocked, so its rails and XCLK can be
    // measured on the failure screen; camera_close() switches it off
    return false;
  }
  LOG_INF("sensor 0x%04x", (unsigned)g_camera.st.sensor_id);

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
  if (!ok || !sensor_set_mode(zoom)) {
    camera_close();
    return false;
  }
  return true;
}

bool camera_set_zoom(uint32_t zoom) {
  if (!g_camera.powered || g_camera.st.sensor_id == CAMERA_SENSOR_NONE ||
      zoom >= CAMERA_ZOOM_LEVELS) {
    return false;
  }
  dma_stop();
  DCMI->CR = 0;
  g_camera.st.state = CAMERA_STATE_IDLE;
  return sensor_set_mode(zoom);
}

bool camera_capture(uint8_t *buf, size_t size) {
  if (!g_camera.powered || g_camera.st.sensor_id == CAMERA_SENSOR_NONE ||
      g_camera.st.state == CAMERA_STATE_BUSY || size < CAMERA_FRAME_SIZE || ((uintptr_t)buf & 3U) != 0) {
    return false;
  }

  dma_stop();
  DCMI->CR &= ~DCMI_CR_CAPTURE;
  DCMI->ICR = 0x1F;

  // DCMI data register (words) to the buffer (words, incrementing), one
  // block of the frame's size; the channel is privileged like the kernel
  DMA_Channel_TypeDef *ch = CAMERA_DMA_CHANNEL;
  GPDMA1->PRIVCFGR |= 1U << CAMERA_DMA_CHANNEL_NUM;
  ch->CCR = 0;
  ch->CLBAR = 0;
  ch->CLLR = 0;
  ch->CTR1 = (2U << DMA_CTR1_SDW_LOG2_Pos) | (2U << DMA_CTR1_DDW_LOG2_Pos) |
             DMA_CTR1_DINC | DMA_CTR1_DAP;
  ch->CTR2 = GPDMA1_REQUEST_DCMI_PSSI << DMA_CTR2_REQSEL_Pos;
  ch->CBR1 = CAMERA_FRAME_SIZE;
  ch->CSAR = (uint32_t)&DCMI->DR;
  ch->CDAR = (uint32_t)buf;
  ch->CCR = (3U << DMA_CCR_PRIO_Pos) | DMA_CCR_EN;  // high priority

  g_camera.capture_start_ms = systick_ms();
  g_camera.st.state = CAMERA_STATE_BUSY;
  DCMI->CR |= DCMI_CR_CAPTURE;
  return true;
}

static void capture_finish(bool ok, uint32_t flags) {
  DMA_Channel_TypeDef *ch = CAMERA_DMA_CHANNEL;
  camera_status_t *st = &g_camera.st;

  DCMI->CR &= ~DCMI_CR_CAPTURE;
  st->last_bytes = CAMERA_FRAME_SIZE - (ch->CBR1 & DMA_CBR1_BNDT);
  st->last_flags = flags | (DCMI->RISR & 0x1F);
  st->last_capture_ms = systick_ms() - g_camera.capture_start_ms;
  dma_stop();
  DCMI->ICR = 0x1F;

  if (ok) {
    st->frames++;
    st->state = CAMERA_STATE_DONE;
  } else {
    st->failures++;
    st->state = CAMERA_STATE_FAILED;
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
  // its first CAMERA_FRAME_SIZE bytes).
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
  *status = g_camera.st;
}

#endif  // KERNEL_MODE
