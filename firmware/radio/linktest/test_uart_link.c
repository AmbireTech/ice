/* Host test of the radio's framed UART link (uart_link.c): framing, resync,
 * TX queue, against the link contract. The expected bytes come from an
 * encoder written from the contract (spec_crc8 below), not from the code under
 * test. uart_link.c's crc8() is linked from the STM32 side's crc8.c, the same
 * bitwise algorithm as Zephyr's crc8(). Run with run.sh. */
#include "zstub.h"

#include "uart_link.c"

int g_log_warnings;
int g_heap_live;
const struct device g_fake_uart;

/* CRC-8, poly 0x07, init 0x00, MSB first, no final xor (CRC-8/SMBUS). */
static uint8_t spec_crc8(const uint8_t *d, size_t n) {
  uint8_t c = 0;
  for (size_t i = 0; i < n; i++) {
    c ^= d[i];
    for (int b = 0; b < 8; b++) c = (uint8_t)((c & 0x80) ? (c << 1) ^ 0x07 : c << 1);
  }
  return c;
}

static uart_callback_t g_cb;
int uart_callback_set(const struct device *d, uart_callback_t cb, void *ud) { (void)d; (void)ud; g_cb = cb; return 0; }

/* TX capture */
static uint8_t g_wire[1 << 16];
static size_t g_wire_len;
static int g_tx_calls;
static const uint8_t *g_tx_buf; static size_t g_tx_len;
int uart_tx(const struct device *d, const uint8_t *buf, size_t len, int32_t t) {
  (void)d; (void)t; g_tx_calls++; g_tx_buf = buf; g_tx_len = len;
  memcpy(&g_wire[g_wire_len], buf, len); g_wire_len += len; return 0;
}
static void tx_done(void) {
  struct uart_event e = {.type = UART_TX_DONE};
  e.data.tx.buf = g_tx_buf; e.data.tx.len = g_tx_len; g_cb(&g_fake_uart, &e, NULL);
}
static int g_rx_enables;
int uart_rx_enable(const struct device *d, uint8_t *b, size_t l, int32_t t) { (void)d; (void)b; (void)l; (void)t; g_rx_enables++; return 0; }
int uart_rx_buf_rsp(const struct device *d, uint8_t *b, size_t l) { (void)d; (void)b; (void)l; return 0; }

/* RX capture */
typedef struct { uint8_t svc; uint8_t len; uint8_t data[256]; } got_t;
static got_t g_got[4096]; static int g_ngot;
void process_rx_msg(uint8_t service_id, uint8_t *data, uint32_t len) {
  got_t *g = &g_got[g_ngot++]; g->svc = service_id; g->len = (uint8_t)len; memcpy(g->data, data, len);
}

static void feed(const uint8_t *b, size_t n, size_t chunk) {
  while (n) {
    size_t c = n < chunk ? n : chunk;
    struct uart_event e = {.type = UART_RX_RDY};
    e.data.rx.buf = (uint8_t *)b; e.data.rx.offset = 0; e.data.rx.len = c;
    g_cb(&g_fake_uart, &e, NULL);
    rx_process(false);
    b += c; n -= c;
  }
}

/* Contract encoder, written from the spec. */
static size_t encode(uint8_t *out, uint8_t svc, const uint8_t *d, uint8_t len) {
  out[0] = 0xA0 | svc; out[1] = len; memcpy(&out[2], d, len);
  out[2 + len] = spec_crc8(out, 2 + len);
  return 3 + len;
}

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

static void reset_rx(void) { g_ngot = 0; g_acc_len = 0; }

int main(void) {
  srand(1234);
  uart_link_init();
  CHECK(g_rx_enables == 1);

  /* crc8 known answer for "123456789" (CRC-8/SMBUS: 0xF4). */
  const uint8_t kat[] = "123456789";
  CHECK(crc8(kat, 9, 0x07, 0, false) == 0xF4);
  CHECK(spec_crc8(kat, 9) == 0xF4);

  /* 1. TX encoding matches the contract byte for byte; one frame on the wire
   *    at a time; queue drains in order on TX_DONE; nothing leaks. */
  uint8_t payload[251]; for (int i = 0; i < 251; i++) payload[i] = (uint8_t)(i * 7 + 3);
  uint8_t expect[4096]; size_t elen = 0;
  int lens[] = {0, 1, 2, 64, 250, 251};
  g_wire_len = 0; g_tx_calls = 0;
  for (int i = 0; i < 6; i++) {
    CHECK(uart_link_send(i % 5, payload, lens[i]));
    elen += encode(&expect[elen], i % 5, payload, lens[i]);
  }
  CHECK(g_tx_calls == 1);
  for (int i = 0; i < 6; i++) tx_done();
  CHECK(g_tx_calls == 6);
  CHECK(g_wire_len == elen && memcmp(g_wire, expect, elen) == 0);
  CHECK(g_heap_live == 0);
  CHECK(!uart_link_send(1, payload, 252));
  CHECK(!uart_link_send(16, payload, 1));
  CHECK(g_heap_live == 0);

  /* 2. RX of the same stream, whole and byte by byte. */
  for (size_t chunk = 1; chunk <= 300; chunk += 299) {
    reset_rx();
    feed(expect, elen, chunk);
    CHECK(g_ngot == 6);
    for (int i = 0; i < g_ngot && i < 6; i++)
      CHECK(g_got[i].svc == i % 5 && g_got[i].len == lens[i] && memcmp(g_got[i].data, payload, lens[i]) == 0);
    CHECK(g_acc_len == 0);
  }

  /* 3. Garbage before a frame, a corrupted frame, a bad length. */
  {
    reset_rx();
    uint8_t s[1024]; size_t n = 0;
    uint8_t junk[] = {0x00, 0x55, 0xFF, 0xA3, 0xFC, 0x12, 0xB0, 0xA0};  /* 0xA3 0xFC: len 252 */
    memcpy(s, junk, sizeof(junk)); n += sizeof(junk);
    size_t bad = n; n += encode(&s[n], 2, payload, 10); s[bad + 5] ^= 0x01; /* payload bit flip */
    n += encode(&s[n], 3, payload, 5);
    feed(s, n, 3);
    /* The trailing junk 0xA0 claims 0xA2 = 162 bytes: held until the stall. */
    CHECK(g_ngot == 0);
    rx_process(true);
    CHECK(g_ngot == 1 && g_got[0].svc == 3 && g_got[0].len == 5);
  }

  /* 4. False start that stays incomplete: frame behind it is delivered only
   *    once the stall resolves it, and nothing is held afterwards. */
  {
    reset_rx();
    uint8_t s[64]; size_t n = 0;
    s[n++] = 0xA1; s[n++] = 0xF0;              /* claims 240 bytes */
    n += encode(&s[n], 0, payload, 4);
    feed(s, n, n);
    CHECK(g_ngot == 0);
    rx_process(true);
    CHECK(g_ngot == 1 && g_got[0].svc == 0 && g_got[0].len == 4);
    CHECK(g_acc_len == 0);
  }

  /* 5. Fuzz: real frames in noise without 0xA_ bytes must all arrive, in
   *    order, with nothing spurious; with arbitrary noise, every real frame
   *    not overlapped by a spurious accept still arrives in order. */
  for (int mode = 0; mode < 2; mode++) {
    reset_rx();
    static uint8_t s[1 << 20]; size_t n = 0;
    static got_t sent[2000]; int nsent = 0;
    for (int f = 0; f < 2000; f++) {
      int nj = rand() % 8;
      for (int j = 0; j < nj; j++) {
        uint8_t b = (uint8_t)rand();
        if (mode == 0 && (b & 0xF0) == 0xA0) b ^= 0x10;
        s[n++] = b;
      }
      got_t *t = &sent[nsent++];
      t->svc = rand() % 5; t->len = rand() % 252;
      for (int j = 0; j < t->len; j++) t->data[j] = (uint8_t)rand();
      n += encode(&s[n], t->svc, t->data, t->len);
    }
    size_t off = 0;
    while (off < n) { size_t c = 1 + rand() % 200; if (c > n - off) c = n - off; feed(&s[off], c, c); off += c; }
    rx_process(true);
    int k = 0;
    for (int i = 0; i < g_ngot && k < nsent; i++)
      if (g_got[i].svc == sent[k].svc && g_got[i].len == sent[k].len && memcmp(g_got[i].data, sent[k].data, sent[k].len) == 0) k++;
    printf("fuzz mode %d: sent %d, delivered %d, matched in order %d\n", mode, nsent, g_ngot, k);
    if (mode == 0) CHECK(k == nsent && g_ngot == nsent);
    else CHECK(k >= nsent - 5);
  }

  /* 6. Line error: reception restarts (after a delay), nothing else. */
  {
    int before = rx_restart_work.scheduled;
    struct uart_event e = {.type = UART_RX_STOPPED}; e.data.rx_stop.reason = 8; g_cb(&g_fake_uart, &e, NULL);
    struct uart_event d = {.type = UART_RX_DISABLED}; g_cb(&g_fake_uart, &d, NULL);
    CHECK(rx_restart_work.scheduled == before + 1);
    rx_restart_work.fn(NULL);
    CHECK(g_rx_enables == 2);
  }

  printf(fails ? "FAILED (%d)\n" : "OK\n", fails);
  return fails != 0;
}
