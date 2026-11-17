// Host build of the device's QR decoder: systick_ms() from the host clock
#pragma once
#include <stdint.h>
#include <time.h>
static inline uint32_t systick_ms(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (uint32_t)(t.tv_sec * 1000 + t.tv_nsec / 1000000);
}
