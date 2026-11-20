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
 */

#include <trezor_bsp.h>
#include <trezor_rtl.h>

#include <io/backlight.h>
#include <sys/logging.h>
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
} ambire_power_t;

static ambire_power_t g_ambire_power;

static bool power_key_pressed(void) {
  return HAL_GPIO_ReadPin(POWER_KEY_PORT, POWER_KEY_PIN) == GPIO_PIN_RESET;
}

static void ambire_power_tick(void *context) {
  ambire_power_t *p = (ambire_power_t *)context;
  bool pressed = power_key_pressed();

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
        p->state = POWER_OFF;
        HAL_GPIO_WritePin(POWER_HOLD_PORT, POWER_HOLD_PIN, GPIO_PIN_RESET);
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
