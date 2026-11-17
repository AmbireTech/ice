/*
 * Ambire boards, [ambire_power]: power off on a long press of the power key.
 *
 * Proto 0's power key lifts the 3.3 V regulator's enable while pressed; the
 * boardloader latches it on with PWR_HOLD (projects/boardloader/main.c). The
 * key also reaches the MCU, inverted by a 2N7002, as BTN_N (active low, PA0's
 * internal pull-up). Nothing else ever lets go of PWR_HOLD, so without this
 * the device cannot be switched off.
 *
 * Holding the key for AMBIRE_POWER_OFF_HOLD_MS turns the backlight off and
 * drops PWR_HOLD. The key itself still holds the enable up while pressed, so
 * the rail goes ~1 s after the key is let go (the enable's RC), USB or not.
 *
 * The press that switched the device on does not count: the key must first be
 * seen released. If the device is still running well after the key was let
 * go (it was pressed again while the rail was falling), it restarts, and the
 * boardloader latches the power on again.
 *
 * Cell monitor (boards whose header defines CELL_PLUS_ADC_CHANNEL: v4's pack
 * of two AAA cells in series). Nothing in hardware stops the boost draining a
 * weak pack: it runs down to 0.5 V across the pack, drawing more current as the
 * pack sags, until the 3.3 V rail falls through the MCU's brown-out reset
 * (~2.8 V), which drops PWR_HOLD. A weak cell beside a good one is the usual
 * case: the good one hides it in the pack's total and can drive it below 0 V.
 * So every AMBIRE_CELLS_READ_MS, off USB, each cell is read on its own (plus -
 * mid-point, mid-point - minus); if either stays below AMBIRE_CELL_EMPTY_MV
 * under load for AMBIRE_CELL_EMPTY_READS reads in a row, the device switches
 * off as on a long press. Their RC filters (50 ms) and the run of reads keep a
 * load step from tripping it. On USB the pack carries no load and is never
 * judged (it may also be off). The last readings sit in g_ambire_cells, for
 * the bench over SWD (`print g_ambire_cells`).
 */

#include <trezor_bsp.h>
#include <trezor_rtl.h>

#include <io/backlight.h>
#include <sys/logging.h>
#include <sys/mpu.h>
#include <sys/systimer.h>

#ifdef KERNEL_MODE

LOG_DECLARE(ambire_power);

#define AMBIRE_POWER_POLL_MS 20U
// Released this long before a press may count (debounce, and the power-on
// press must end first)
#define AMBIRE_POWER_ARM_MS 100U
#define AMBIRE_POWER_OFF_HOLD_MS 3000U
// The enable's RC holds the rail ~1 s after the key is let go: alive well
// past that means the key was pressed again, so start over
#define AMBIRE_POWER_RESTART_MS 3000U

typedef enum {
  POWER_WAIT_RELEASE,  // the power-on press (or a bounce) still on the key
  POWER_ARMED,         // key up, counting a press
  POWER_OFF,           // PWR_HOLD dropped, waiting for the rail to go
} ambire_power_state_t;

typedef struct {
  ambire_power_state_t state;
  uint32_t released_ms;  // time the key has been up
  uint32_t held_ms;      // time the key has been down while armed
  uint32_t cells_ms;     // time since the cells were last read
} ambire_power_t;

static ambire_power_t g_ambire_power;

#ifdef CELL_PLUS_ADC_CHANNEL

#define AMBIRE_CELLS_READ_MS 100U
// Under load: the usual end point for NiMH and alkaline cells; a lithium
// primary is near empty there too
#define AMBIRE_CELL_EMPTY_MV 1000
#define AMBIRE_CELL_EMPTY_READS 5U  // 500 ms in a row
#define AMBIRE_ADC_FULL_SCALE 16383U
// A conversion takes ~20 us; this bounds a stuck ADC to about a millisecond
#define AMBIRE_ADC_EOC_SPINS 100000U

// Regular sequence, in rank order
enum { CELLS_RAW_VREFINT, CELLS_RAW_PLUS, CELLS_RAW_MID, CELLS_RAW_MINUS, CELLS_RAW_COUNT };

typedef struct {
  bool adc_ok;       // ADC set up; false: no monitor (logged at init)
  bool usb;          // VBUS present at the last read
  uint32_t reads;    // completed reads since boot
  uint32_t adc_errors;
  uint16_t raw[CELLS_RAW_COUNT];
  uint16_t vrefint_cal;
  int32_t vdda_mv;
  int32_t plus_mv;   // the pack's plus, its mid-point and its minus, to GND
  int32_t mid_mv;
  int32_t minus_mv;
  int32_t cell_plus_mv;   // the cell on the plus side: plus - mid
  int32_t cell_minus_mv;  // the cell on the minus side: mid - minus
  int32_t lowest_cell_mv; // lowest either cell read off USB since boot
  uint32_t empty_reads;   // reads in a row with a cell below empty, off USB
  bool switched_off;      // the monitor dropped PWR_HOLD
} ambire_cells_t;

// Not static: read over SWD on the bench
ambire_cells_t g_ambire_cells;

static ADC_HandleTypeDef g_cells_adc;

static bool cells_adc_init(void) {
  ambire_cells_t *c = &g_ambire_cells;
  GPIO_InitTypeDef gpio = {0};

  CELLS_CLK_EN();
  gpio.Mode = GPIO_MODE_ANALOG;
  gpio.Pull = GPIO_NOPULL;
  gpio.Pin = CELL_PLUS_PIN | CELL_MINUS_PIN | CELL_MID_PIN;
  HAL_GPIO_Init(CELLS_PORT, &gpio);

  VBUS_SENSE_CLK_EN();
  gpio.Mode = GPIO_MODE_INPUT;
  gpio.Pin = VBUS_SENSE_PIN;
  HAL_GPIO_Init(VBUS_SENSE_PORT, &gpio);

  // VREFINT read at 3.0 V in ST's test: the rail's actual voltage follows from
  // it, so a sagging rail does not skew the cells' readings. It is an
  // engineering byte, behind the MPU's OTP window (odd address: read bytes).
  mpu_mode_t mpu_mode = mpu_reconfig(MPU_MODE_OTP);
  const volatile uint8_t *cal = (const volatile uint8_t *)VREFINT_CAL_ADDR;
  c->vrefint_cal = (uint16_t)(cal[0] | (cal[1] << 8));
  mpu_restore(mpu_mode);
  if (c->vrefint_cal < 1000 || c->vrefint_cal > AMBIRE_ADC_FULL_SCALE) {
    LOG_ERR("cell monitor off: VREFINT_CAL %u implausible", c->vrefint_cal);
    return false;
  }

  __HAL_RCC_ADC12_CLK_ENABLE();

  ADC_HandleTypeDef *h = &g_cells_adc;
  h->Instance = ADC1;
  // Kernel clock HCLK (160 MHz) / 8 = 20 MHz, under ADC1's 55 MHz
  h->Init.ClockPrescaler = ADC_CLOCK_ASYNC_DIV8;
  h->Init.Resolution = ADC_RESOLUTION_14B;
  h->Init.DataAlign = ADC_DATAALIGN_RIGHT;
  h->Init.ScanConvMode = ADC_SCAN_ENABLE;
  h->Init.EOCSelection = ADC_EOC_SINGLE_CONV;
  h->Init.LowPowerAutoWait = DISABLE;
  h->Init.LowPowerAutoPowerOff = ADC_LOW_POWER_NONE;
  h->Init.ContinuousConvMode = DISABLE;
  h->Init.NbrOfConversion = CELLS_RAW_COUNT;
  h->Init.DiscontinuousConvMode = DISABLE;
  h->Init.ExternalTrigConv = ADC_SOFTWARE_START;
  h->Init.ExternalTrigConvEdge = ADC_EXTERNALTRIGCONVEDGE_NONE;
  h->Init.ConversionDataManagement = ADC_CONVERSIONDATA_DR;
  h->Init.Overrun = ADC_OVR_DATA_OVERWRITTEN;
  h->Init.LeftBitShift = ADC_LEFTBITSHIFT_NONE;
  h->Init.OversamplingMode = DISABLE;
  if (HAL_ADC_Init(h) != HAL_OK) {
    LOG_ERR("cell monitor off: ADC init failed (state 0x%lx, error 0x%lx)",
            (unsigned long)h->State, (unsigned long)h->ErrorCode);
    return false;
  }

  if (HAL_ADCEx_Calibration_Start(h, ADC_CALIB_OFFSET_LINEARITY,
                                  ADC_SINGLE_ENDED) != HAL_OK) {
    LOG_ERR("cell monitor off: ADC calibration failed (state 0x%lx)",
            (unsigned long)h->State);
    return false;
  }

  // Sources are 100 nF caps (and VREFINT, which needs >= 4 us): 391.5 cycles
  // at 20 MHz is ~20 us
  static const uint32_t channels[CELLS_RAW_COUNT] = {
      ADC_CHANNEL_VREFINT, CELL_PLUS_ADC_CHANNEL, CELL_MID_ADC_CHANNEL,
      CELL_MINUS_ADC_CHANNEL};
  static const uint32_t ranks[CELLS_RAW_COUNT] = {
      ADC_REGULAR_RANK_1, ADC_REGULAR_RANK_2, ADC_REGULAR_RANK_3,
      ADC_REGULAR_RANK_4};
  for (int i = 0; i < CELLS_RAW_COUNT; i++) {
    ADC_ChannelConfTypeDef ch = {0};
    ch.Channel = channels[i];
    ch.Rank = ranks[i];
    ch.SamplingTime = ADC_SAMPLETIME_391CYCLES;
    ch.SingleDiff = ADC_SINGLE_ENDED;
    ch.OffsetNumber = ADC_OFFSET_NONE;
    if (HAL_ADC_ConfigChannel(h, &ch) != HAL_OK) {
      LOG_ERR("cell monitor off: ADC channel %d config failed", i);
      return false;
    }
  }

  if (ADC_Enable(h) != HAL_OK) {
    LOG_ERR("cell monitor off: ADC enable failed (state 0x%lx)",
            (unsigned long)h->State);
    return false;
  }
  return true;
}

// One pass over the sequence, polled (runs in the systimer's interrupt)
static bool cells_adc_read(uint16_t raw[CELLS_RAW_COUNT]) {
  ADC_TypeDef *adc = g_cells_adc.Instance;

  LL_ADC_ClearFlag_EOS(adc);
  LL_ADC_ClearFlag_OVR(adc);
  LL_ADC_REG_StartConversion(adc);
  for (int i = 0; i < CELLS_RAW_COUNT; i++) {
    uint32_t spins = 0;
    while (LL_ADC_IsActiveFlag_EOC(adc) == 0) {
      if (++spins >= AMBIRE_ADC_EOC_SPINS) {
        return false;
      }
    }
    // Reading the data clears EOC
    raw[i] = (uint16_t)LL_ADC_REG_ReadConversionData32(adc);
  }
  return true;
}

static int32_t cells_pin_mv(const ambire_cells_t *c, uint16_t raw) {
  return (int32_t)(((int64_t)raw * c->vdda_mv) / AMBIRE_ADC_FULL_SCALE);
}

// Reads the pack; true when a cell has stayed empty long enough to switch off
static bool cells_check(ambire_cells_t *c) {
  c->usb = HAL_GPIO_ReadPin(VBUS_SENSE_PORT, VBUS_SENSE_PIN) == GPIO_PIN_SET;

  uint16_t raw[CELLS_RAW_COUNT] = {0};
  if (!cells_adc_read(raw) || raw[CELLS_RAW_VREFINT] == 0) {
    // A missed read decides nothing either way: the run of empty reads
    // continues from where it was at the next good one
    if (c->adc_errors++ == 0) {
      LOG_ERR("cell monitor: ADC read failed (vrefint raw %u)",
              raw[CELLS_RAW_VREFINT]);
    }
    return false;
  }
  memcpy(c->raw, raw, sizeof(c->raw));
  c->reads++;

  c->vdda_mv = (int32_t)((VREFINT_CAL_VREF * (uint32_t)c->vrefint_cal) /
                         raw[CELLS_RAW_VREFINT]);
  // plus and mid are halved by 1M/1M, minus is unscaled
  c->plus_mv = 2 * cells_pin_mv(c, raw[CELLS_RAW_PLUS]);
  c->mid_mv = 2 * cells_pin_mv(c, raw[CELLS_RAW_MID]);
  c->minus_mv = cells_pin_mv(c, raw[CELLS_RAW_MINUS]);
  c->cell_plus_mv = c->plus_mv - c->mid_mv;
  c->cell_minus_mv = c->mid_mv - c->minus_mv;

  if (c->usb) {
    // On USB the pack carries no load (and may be off): nothing to judge
    c->empty_reads = 0;
    return false;
  }

  int32_t lowest = c->cell_plus_mv < c->cell_minus_mv ? c->cell_plus_mv
                                                      : c->cell_minus_mv;
  if (lowest < c->lowest_cell_mv) {
    c->lowest_cell_mv = lowest;
  }
  c->empty_reads = lowest < AMBIRE_CELL_EMPTY_MV ? c->empty_reads + 1 : 0;
  return c->empty_reads >= AMBIRE_CELL_EMPTY_READS;
}

#endif  // CELL_PLUS_ADC_CHANNEL

static bool power_key_pressed(void) {
  return HAL_GPIO_ReadPin(POWER_KEY_PORT, POWER_KEY_PIN) == GPIO_PIN_RESET;
}

static void power_off(ambire_power_t *p) {
  p->state = POWER_OFF;
  HAL_GPIO_WritePin(POWER_HOLD_PORT, POWER_HOLD_PIN, GPIO_PIN_RESET);
  backlight_set(0);
}

static void ambire_power_tick(void *context) {
  ambire_power_t *p = (ambire_power_t *)context;
  bool pressed = power_key_pressed();

#ifdef CELL_PLUS_ADC_CHANNEL
  ambire_cells_t *c = &g_ambire_cells;
  p->cells_ms += AMBIRE_POWER_POLL_MS;
  if (c->adc_ok && p->state != POWER_OFF &&
      p->cells_ms >= AMBIRE_CELLS_READ_MS) {
    p->cells_ms = 0;
    if (cells_check(c)) {
      LOG_WARN("cells empty (%ld mV + %ld mV, rail %ld mV): switching off",
               (long)c->cell_plus_mv, (long)c->cell_minus_mv,
               (long)c->vdda_mv);
      c->switched_off = true;
      power_off(p);
    }
  }
#endif

  if (pressed) {
    p->released_ms = 0;
  } else {
    p->released_ms += AMBIRE_POWER_POLL_MS;
  }

  switch (p->state) {
    case POWER_WAIT_RELEASE:
      if (p->released_ms >= AMBIRE_POWER_ARM_MS) {
        p->state = POWER_ARMED;
        p->held_ms = 0;
      }
      break;

    case POWER_ARMED:
      p->held_ms = pressed ? p->held_ms + AMBIRE_POWER_POLL_MS : 0;
      if (p->held_ms >= AMBIRE_POWER_OFF_HOLD_MS) {
        power_off(p);
      }
      break;

    case POWER_OFF:
      // The UI may still fade the backlight in while the rail falls
      backlight_set(0);
      if (p->released_ms >= AMBIRE_POWER_RESTART_MS) {
        NVIC_SystemReset();
      }
      break;
  }
}

void ambire_power_init(void) {
  GPIO_InitTypeDef gpio = {0};

  POWER_KEY_CLK_EN();
  gpio.Mode = GPIO_MODE_INPUT;
  gpio.Pull = GPIO_PULLUP;
  gpio.Speed = GPIO_SPEED_FREQ_LOW;
  gpio.Pin = POWER_KEY_PIN;
  HAL_GPIO_Init(POWER_KEY_PORT, &gpio);

  g_ambire_power = (ambire_power_t){.state = POWER_WAIT_RELEASE};

#ifdef CELL_PLUS_ADC_CHANNEL
  g_ambire_cells = (ambire_cells_t){.lowest_cell_mv = INT32_MAX};
  g_ambire_cells.adc_ok = cells_adc_init();
#endif

  systimer_t *timer = systimer_create(ambire_power_tick, &g_ambire_power);
  if (timer == NULL) {
    // No timer free: the device stays on as before (unplug the cell to turn
    // it off); logged rather than stopping the kernel over a missing feature
    LOG_ERR("no systimer free, power-off key disabled");
    return;
  }
  systimer_set_periodic(timer, AMBIRE_POWER_POLL_MS);
}

#endif  // KERNEL_MODE
