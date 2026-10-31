/* Host stand-ins for the few Zephyr APIs uart_link.c uses. */
#pragma once
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CONFIG_TREZOR_UART_LINK 1
#define CONFIG_DEFAULT_THREAD_STACK_SIZE 1024

#define ARG_UNUSED(x) (void)(x)
#define BUILD_ASSERT(c, msg) _Static_assert(c, msg)

#define LOG_MODULE_REGISTER(x)
extern int g_log_warnings;
#define LOG_WRN(...) (g_log_warnings++, (void)0)
#define LOG_ERR(...) (g_log_warnings++, (void)0)
#define LOG_INF(...) ((void)0)
#define LOG_DBG(...) ((void)0)

struct device { int unused; };
extern const struct device g_fake_uart;
#define DT_CHOSEN(x) 0
#define DEVICE_DT_GET(x) (&g_fake_uart)
static inline bool device_is_ready(const struct device *d) { (void)d; return true; }
static inline int pm_device_runtime_get(const struct device *d) { (void)d; return 0; }

typedef long atomic_t;
typedef long atomic_val_t;
#define ATOMIC_INIT(x) (x)
static inline atomic_val_t atomic_get(const atomic_t *a) { return *a; }
static inline atomic_val_t atomic_set(atomic_t *a, atomic_val_t v) { atomic_val_t o = *a; *a = v; return o; }
static inline atomic_val_t atomic_clear(atomic_t *a) { return atomic_set(a, 0); }
static inline atomic_val_t atomic_inc(atomic_t *a) { return (*a)++; }
static inline bool atomic_cas(atomic_t *a, atomic_val_t o, atomic_val_t n) { if (*a != o) return false; *a = n; return true; }

typedef struct { long ms; } k_timeout_t;
#define K_MSEC(x) ((k_timeout_t){(x)})
#define K_NO_WAIT ((k_timeout_t){0})
#define K_FOREVER ((k_timeout_t){-1})
#define SYS_FOREVER_US (-1)

extern int g_heap_live;
static inline void *k_malloc(size_t n) { g_heap_live++; return malloc(n); }
static inline void k_free(void *p) { if (p) { g_heap_live--; free(p); } }

struct k_fifo { void *head; void *tail; };
#define K_FIFO_DEFINE(name) struct k_fifo name = {0}
static inline void k_fifo_put(struct k_fifo *f, void *item) {
  *(void **)item = NULL;
  if (f->tail) *(void **)f->tail = item; else f->head = item;
  f->tail = item;
}
static inline void *k_fifo_get(struct k_fifo *f, k_timeout_t t) {
  (void)t; void *i = f->head; if (!i) return NULL;
  f->head = *(void **)i; if (!f->head) f->tail = NULL; return i;
}
static inline bool k_fifo_is_empty(struct k_fifo *f) { return f->head == NULL; }

struct k_sem { int count; };
#define K_SEM_DEFINE(name, init, lim) struct k_sem name = {init}
static inline void k_sem_give(struct k_sem *s) { s->count = 1; }
static inline int k_sem_take(struct k_sem *s, k_timeout_t t) { (void)t; if (s->count) { s->count = 0; return 0; } return -EAGAIN; }

struct k_work { int unused; };
struct k_work_delayable { void (*fn)(struct k_work *); int scheduled; };
static inline void k_work_init_delayable(struct k_work_delayable *w, void (*fn)(struct k_work *)) { w->fn = fn; w->scheduled = 0; }
static inline int k_work_reschedule(struct k_work_delayable *w, k_timeout_t t) { (void)t; w->scheduled++; return 0; }

#define K_THREAD_DEFINE(name, stack, fn, a, b, c, prio, opt, delay) void *name = (void *)(fn)

static inline unsigned int irq_lock(void) { return 0; }
static inline void irq_unlock(unsigned int k) { (void)k; }

struct ring_buf { uint8_t *buf; uint32_t size, head, tail; };
#define RING_BUF_DECLARE(name, sz) static uint8_t name##_data[sz]; struct ring_buf name = {name##_data, sz, 0, 0}
static inline uint32_t ring_buf_put(struct ring_buf *r, const uint8_t *d, uint32_t n) {
  uint32_t i = 0; for (; i < n && r->head - r->tail < r->size; i++) r->buf[(r->head++) % r->size] = d[i]; return i;
}
static inline uint32_t ring_buf_get(struct ring_buf *r, uint8_t *d, uint32_t n) {
  uint32_t i = 0; for (; i < n && r->tail != r->head; i++) d[i] = r->buf[(r->tail++) % r->size]; return i;
}

uint8_t crc8(const uint8_t *src, size_t len, uint8_t polynomial, uint8_t initial_value, bool reversed);

enum uart_event_type { UART_TX_DONE, UART_TX_ABORTED, UART_RX_RDY, UART_RX_BUF_REQUEST, UART_RX_BUF_RELEASED, UART_RX_DISABLED, UART_RX_STOPPED };
struct uart_event {
  enum uart_event_type type;
  union {
    struct { const uint8_t *buf; size_t len; } tx;
    struct { uint8_t *buf; size_t offset; size_t len; } rx;
    struct { uint8_t *buf; } rx_buf;
    struct { int reason; } rx_stop;
  } data;
};
typedef void (*uart_callback_t)(const struct device *dev, struct uart_event *evt, void *user_data);
int uart_callback_set(const struct device *dev, uart_callback_t cb, void *ud);
int uart_tx(const struct device *dev, const uint8_t *buf, size_t len, int32_t timeout);
int uart_rx_enable(const struct device *dev, uint8_t *buf, size_t len, int32_t timeout);
int uart_rx_buf_rsp(const struct device *dev, uint8_t *buf, size_t len);
