// Host build of the device's QR decoder: systick_ms() from the host clock,
// or from the function named by HOST_SYSTICK_FN (the scan simulation's
// clock runs at the device's speed)
#pragma once
#include <stdint.h>
#include <time.h>
#ifdef HOST_SYSTICK_FN
uint32_t HOST_SYSTICK_FN(void);
static inline uint32_t systick_ms(void) { return HOST_SYSTICK_FN(); }
#else
static inline uint32_t systick_ms(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (uint32_t)(t.tv_sec * 1000 + t.tv_nsec / 1000000);
}
#endif
