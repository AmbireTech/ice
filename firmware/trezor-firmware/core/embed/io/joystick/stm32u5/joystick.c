/*
 * Ambire boards, [ambire_joystick]: reads the joystick's ladder (JOY_DIR, an
 * ADC1 input) and its centre press (the power key, BTN_N).
 *
 * ADC1 is set up on first use with the LL driver (header only, no HAL ADC
 * module): kernel clock HCLK / 4, 14 bits, one conversion per read.
 */

#include <trezor_bsp.h>
#include <trezor_rtl.h>

#include <io/joystick.h>
#include <stm32u5xx_ll_adc.h>

#ifdef KERNEL_MODE

#define JOYSTICK_ADC_FULL_SCALE 16383U  // 14 bits
// Waits for the ADC, in polls of its status register: each takes microseconds,
// so this bounds a stuck ADC rather than timing anything
#define JOYSTICK_ADC_WAIT_POLLS 200000U

// Each pattern's voltage on JOY_DIR as a fraction of the supply, x1000:
// R / (R + 15k), R the pressed contacts' resistors in parallel
static const struct {
  uint16_t level;
  uint8_t contacts;
} g_joystick_levels[] = {
    {1000, 0},
    {870, JOYSTICK_D},
    {758, JOYSTICK_C},
    {681, JOYSTICK_C | JOYSTICK_D},
    {643, JOYSTICK_B},
    {586, JOYSTICK_B | JOYSTICK_D},
    {533, JOYSTICK_B | JOYSTICK_C},
    {500, JOYSTICK_A},
    {465, JOYSTICK_A | JOYSTICK_D},
    {431, JOYSTICK_A | JOYSTICK_C},
    {391, JOYSTICK_A | JOYSTICK_B},
};

typedef enum {
  JOYSTICK_ADC_OFF,
  JOYSTICK_ADC_READY,
  JOYSTICK_ADC_FAILED,
} joystick_adc_state_t;

static joystick_adc_state_t g_joystick_adc;

static bool wait_flag(volatile uint32_t *reg, uint32_t mask, bool set) {
  for (uint32_t i = 0; i < JOYSTICK_ADC_WAIT_POLLS; i++) {
    if (((*reg & mask) != 0) == set) {
      return true;
    }
  }
  return false;
}

static bool joystick_adc_init(void) {
  GPIO_InitTypeDef gpio = {0};
  JOYSTICK_ADC_CLK_EN();
  gpio.Pin = JOYSTICK_ADC_PIN;
  gpio.Mode = GPIO_MODE_ANALOG;
  gpio.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(JOYSTICK_ADC_PORT, &gpio);

  __HAL_RCC_ADC12_CLK_ENABLE();
  __HAL_RCC_ADCDAC_CONFIG(RCC_ADCDACCLKSOURCE_HCLK);
  LL_ADC_SetCommonClock(ADC12_COMMON, LL_ADC_CLOCK_ASYNC_DIV4);

  LL_ADC_DisableDeepPowerDown(ADC1);
  LL_ADC_EnableInternalRegulator(ADC1);
  if (!wait_flag(&ADC1->ISR, ADC_ISR_LDORDY, true)) {
    return false;
  }

  LL_ADC_StartCalibration(ADC1, LL_ADC_CALIB_OFFSET);
  if (!wait_flag(&ADC1->CR, ADC_CR_ADCAL, false)) {
    return false;
  }

  LL_ADC_SetResolution(ADC1, LL_ADC_RESOLUTION_14B);
  LL_ADC_SetChannelPreselection(ADC1, JOYSTICK_ADC_CHANNEL);
  LL_ADC_REG_SetSequencerLength(ADC1, LL_ADC_REG_SEQ_SCAN_DISABLE);
  LL_ADC_REG_SetSequencerRanks(ADC1, LL_ADC_REG_RANK_1, JOYSTICK_ADC_CHANNEL);
  // The ladder's node is 15k or less behind no capacitor: 68 cycles at
  // 40 MHz (1.7 us) settle it
  LL_ADC_SetChannelSamplingTime(ADC1, JOYSTICK_ADC_CHANNEL,
                                LL_ADC_SAMPLINGTIME_68CYCLES);

  LL_ADC_ClearFlag_ADRDY(ADC1);
  LL_ADC_Enable(ADC1);
  return wait_flag(&ADC1->ISR, ADC_ISR_ADRDY, true);
}

static uint32_t joystick_read_contacts(void) {
  if (g_joystick_adc == JOYSTICK_ADC_OFF) {
    g_joystick_adc =
        joystick_adc_init() ? JOYSTICK_ADC_READY : JOYSTICK_ADC_FAILED;
  }
  if (g_joystick_adc != JOYSTICK_ADC_READY) {
    // No ADC: the directions read as released, the centre press still works
    return 0;
  }

  LL_ADC_ClearFlag_EOC(ADC1);
  LL_ADC_REG_StartConversion(ADC1);
  if (!wait_flag(&ADC1->ISR, ADC_ISR_EOC, true)) {
    return 0;
  }
  uint32_t level =
      LL_ADC_REG_ReadConversionData32(ADC1) * 1000U / JOYSTICK_ADC_FULL_SCALE;

  uint32_t best = 0;
  uint32_t best_distance = UINT32_MAX;
  for (size_t i = 0; i < ARRAY_LENGTH(g_joystick_levels); i++) {
    uint32_t l = g_joystick_levels[i].level;
    uint32_t distance = level > l ? level - l : l - level;
    if (distance < best_distance) {
      best_distance = distance;
      best = g_joystick_levels[i].contacts;
    }
  }
  return best;
}

uint32_t joystick_read(void) {
  uint32_t contacts = joystick_read_contacts();
  // The power key's pin is set up by ambire_power.c (input, pull-up)
  if (HAL_GPIO_ReadPin(POWER_KEY_PORT, POWER_KEY_PIN) == GPIO_PIN_RESET) {
    contacts |= JOYSTICK_CENTER;
  }
  return contacts;
}

#endif  // KERNEL_MODE
