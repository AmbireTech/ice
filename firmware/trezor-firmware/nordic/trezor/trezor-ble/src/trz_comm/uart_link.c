/*
 * This file is part of the Trezor project, https://trezor.io/
 *
 * Copyright (c) SatoshiLabs
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

/*
 * Framed 2-wire UART link to the MCU (CONFIG_TREZOR_UART_LINK), replacing
 * spi.c and its GPIO handshake on boards that have only TX and RX.
 *
 * Frame (spi.c's packet, unpadded):
 *
 *   [0xA0 | service_id] [msg_len 0..251] [msg_len bytes] [crc8]
 *
 * crc8 = poly 0x07, init 0x00, not reflected, over every byte before it.
 * The line runs at 1 Mbaud 8N1 without flow control (set in devicetree).
 *
 * Receiving: if the first byte's high nibble is not 0xA, or msg_len > 251, or
 * the crc does not match, one byte is dropped from the front and the rest is
 * scanned again. A candidate frame that stays incomplete for RX_STALL_TIMEOUT
 * is stale (the sender restarted mid-frame, or noise started it): it is
 * resolved the same way, a byte at a time, so frames behind it still get out.
 * No acks or retries at this layer.
 *
 * Either side may be absent: frames are sent at line rate whether or not
 * anyone listens, and receive errors (a line held low while the other side's
 * TX is not driven reads as a break) only restart reception.
 */

#include <errno.h>
#include <stdbool.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/pm/device_runtime.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/crc.h>
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/types.h>

#include <trz_comm/trz_comm.h>

#include "trz_comm_internal.h"

#define LOG_MODULE_NAME trz_comm_uart_link
LOG_MODULE_REGISTER(LOG_MODULE_NAME);

static const struct device *const uart =
    DEVICE_DT_GET(DT_CHOSEN(trezor_trz_uart));

#define FRAME_SOF 0xA0
#define FRAME_SOF_MASK 0xF0
#define FRAME_SERVICE_MASK 0x0F
#define FRAME_HEADER_SIZE 2
#define FRAME_CRC_SIZE 1
#define FRAME_MAX_DATA SPI_TX_DATA_LEN
#define FRAME_MAX_SIZE (FRAME_HEADER_SIZE + FRAME_MAX_DATA + FRAME_CRC_SIZE)

BUILD_ASSERT(FRAME_MAX_DATA == 251, "link contract: 251 payload bytes max");
BUILD_ASSERT(FRAME_MAX_SIZE <= PACKET_DATA_SIZE, "frame must fit a packet");

/* RX DMA: a ring of buffers handed to the driver in turn, each copied into
 * rx_ring by the callback as it fills. */
#define RX_DMA_BUF_SIZE 128
#define RX_DMA_BUF_COUNT 3
/* Idle time after which the driver reports what it has. */
#define RX_IDLE_TIMEOUT_US 100
/* Pause before restarting reception after an error: a line held low (the
 * MCU's TX not driven yet) would otherwise raise a break every frame time. */
#define RX_RESTART_DELAY K_MSEC(1)
#define RX_RESTART_RETRY K_MSEC(10)
/* An incomplete candidate frame older than this is stale. A full frame takes
 * 2.5 ms at 1 Mbaud. */
#define RX_STALL_TIMEOUT K_MSEC(20)

static atomic_t g_link_up = ATOMIC_INIT(0);

static uint32_t frame_crc(const uint8_t *data, size_t len) {
  return crc8(data, len, 0x07, 0x00, false);
}

/* ------------------------------------------------------------------ TX -- */

static K_FIFO_DEFINE(fifo_tx);
static atomic_t g_tx_busy = ATOMIC_INIT(0);
static trz_packet_t *g_tx_current;

/* Starts the next queued frame unless one is on the wire. Called from
 * threads and from the UART callback; the busy flag gives one owner. */
static void tx_kick(void) {
  if (!atomic_get(&g_link_up)) {
    return;
  }

  while (!k_fifo_is_empty(&fifo_tx) && atomic_cas(&g_tx_busy, 0, 1)) {
    trz_packet_t *tx = k_fifo_get(&fifo_tx, K_NO_WAIT);
    if (tx == NULL) {
      atomic_clear(&g_tx_busy);
      continue;
    }

    g_tx_current = tx;
    int err = uart_tx(uart, tx->data, tx->len, SYS_FOREVER_US);
    if (err != 0) {
      LOG_WRN("UART TX failed (err %d), frame for service %u dropped", err,
              tx->data[0] & FRAME_SERVICE_MASK);
      g_tx_current = NULL;
      k_free(tx);
      atomic_clear(&g_tx_busy);
    }
  }
}

static void tx_finished(void) {
  trz_packet_t *tx = g_tx_current;
  g_tx_current = NULL;
  k_free(tx);
  atomic_clear(&g_tx_busy);
  tx_kick();
}

bool uart_link_send(uint8_t service_id, const uint8_t *data, uint32_t len) {
  if (service_id > FRAME_SERVICE_MASK || len > FRAME_MAX_DATA) {
    LOG_WRN("Cannot frame service %u, %u bytes", service_id, len);
    return false;
  }

  trz_packet_t *tx = k_malloc(sizeof(*tx));
  if (tx == NULL) {
    LOG_WRN("Not able to allocate UART link send buffer");
    return false;
  }

  tx->data[0] = FRAME_SOF | service_id;
  tx->data[1] = (uint8_t)len;
  if (data != NULL) {
    memcpy(&tx->data[FRAME_HEADER_SIZE], data, len);
  } else {
    memset(&tx->data[FRAME_HEADER_SIZE], 0, len);
  }
  tx->data[FRAME_HEADER_SIZE + len] =
      (uint8_t)frame_crc(tx->data, FRAME_HEADER_SIZE + len);
  tx->len = FRAME_HEADER_SIZE + len + FRAME_CRC_SIZE;

  k_fifo_put(&fifo_tx, tx);
  tx_kick();
  return true;
}

/* ------------------------------------------------------------------ RX -- */

static uint8_t g_rx_dma[RX_DMA_BUF_COUNT][RX_DMA_BUF_SIZE];
static uint8_t g_rx_dma_next;

RING_BUF_DECLARE(g_rx_ring, 1024);
static K_SEM_DEFINE(rx_data_sem, 0, 1);
static struct k_work_delayable rx_restart_work;

static atomic_t g_rx_overruns = ATOMIC_INIT(0);
static atomic_t g_rx_line_errors = ATOMIC_INIT(0);
static atomic_t g_rx_last_error = ATOMIC_INIT(0);

static uint8_t *rx_dma_take(void) {
  uint8_t *buf = g_rx_dma[g_rx_dma_next];
  g_rx_dma_next = (g_rx_dma_next + 1) % RX_DMA_BUF_COUNT;
  return buf;
}

static void rx_restart(struct k_work *work) {
  ARG_UNUSED(work);

  int err = uart_rx_enable(uart, rx_dma_take(), RX_DMA_BUF_SIZE,
                           RX_IDLE_TIMEOUT_US);
  if (err == -EBUSY) {
    /* Already receiving. */
    return;
  }
  if (err != 0) {
    LOG_ERR("Cannot enable UART link RX (err %d), retrying", err);
    k_work_reschedule(&rx_restart_work, RX_RESTART_RETRY);
  }
}

static void uart_link_cb(const struct device *dev, struct uart_event *evt,
                         void *user_data) {
  ARG_UNUSED(user_data);

  switch (evt->type) {
    case UART_TX_DONE:
    case UART_TX_ABORTED:
      tx_finished();
      break;

    case UART_RX_RDY: {
      uint32_t put =
          ring_buf_put(&g_rx_ring, &evt->data.rx.buf[evt->data.rx.offset],
                       evt->data.rx.len);
      if (put < evt->data.rx.len) {
        atomic_inc(&g_rx_overruns);
      }
      k_sem_give(&rx_data_sem);
      break;
    }

    case UART_RX_BUF_REQUEST: {
      int err = uart_rx_buf_rsp(dev, rx_dma_take(), RX_DMA_BUF_SIZE);
      if (err != 0) {
        /* RX is being disabled; UART_RX_DISABLED restarts it. */
        LOG_WRN("UART link RX buffer not taken (err %d)", err);
      }
      break;
    }

    case UART_RX_BUF_RELEASED:
      break;

    case UART_RX_STOPPED:
      atomic_inc(&g_rx_line_errors);
      atomic_set(&g_rx_last_error, evt->data.rx_stop.reason);
      break;

    case UART_RX_DISABLED:
      k_work_reschedule(&rx_restart_work, RX_RESTART_DELAY);
      break;

    default:
      break;
  }
}

/* Bytes received but not yet framed. Never longer than one frame: bytes are
 * only added while the front candidate is incomplete. */
static uint8_t g_acc[FRAME_MAX_SIZE];
static size_t g_acc_len;

static void acc_drop(size_t n) {
  memmove(g_acc, &g_acc[n], g_acc_len - n);
  g_acc_len -= n;
}

/* Delivers every complete frame at the front of g_acc and drops garbage, a
 * byte at a time; stops at an incomplete candidate. */
static void acc_parse(void) {
  while (g_acc_len > 0) {
    if ((g_acc[0] & FRAME_SOF_MASK) != FRAME_SOF) {
      acc_drop(1);
      continue;
    }
    if (g_acc_len < FRAME_HEADER_SIZE) {
      return;
    }
    uint8_t msg_len = g_acc[1];
    if (msg_len > FRAME_MAX_DATA) {
      acc_drop(1);
      continue;
    }
    size_t frame_len = FRAME_HEADER_SIZE + msg_len + FRAME_CRC_SIZE;
    if (g_acc_len < frame_len) {
      return;
    }
    if (frame_crc(g_acc, frame_len - FRAME_CRC_SIZE) !=
        g_acc[frame_len - FRAME_CRC_SIZE]) {
      acc_drop(1);
      continue;
    }

    process_rx_msg(g_acc[0] & FRAME_SERVICE_MASK, &g_acc[FRAME_HEADER_SIZE],
                   msg_len);
    acc_drop(frame_len);
  }
}

static void rx_report_errors(void) {
  static atomic_val_t reported_overruns;
  static atomic_val_t reported_line_errors;

  atomic_val_t overruns = atomic_get(&g_rx_overruns);
  atomic_val_t line_errors = atomic_get(&g_rx_line_errors);

  if (overruns != reported_overruns) {
    LOG_WRN("UART link RX ring overruns: %ld", (long)overruns);
    reported_overruns = overruns;
  }
  if (line_errors != reported_line_errors) {
    LOG_WRN("UART link RX line errors: %ld (last reason %ld)",
            (long)line_errors, (long)atomic_get(&g_rx_last_error));
    reported_line_errors = line_errors;
  }
}

/* Frames what has arrived. With stalled (nothing arrived for
 * RX_STALL_TIMEOUT), what is held is stale: it is resolved a byte at a time,
 * delivering any whole frame behind a false start. */
static void rx_process(bool stalled) {
  if (stalled) {
    LOG_DBG("UART link: resolving %u stale bytes", (unsigned)g_acc_len);
    while (g_acc_len > 0) {
      acc_drop(1);
      acc_parse();
    }
    return;
  }

  for (;;) {
    unsigned int key = irq_lock();
    uint32_t got = ring_buf_get(&g_rx_ring, &g_acc[g_acc_len],
                                sizeof(g_acc) - g_acc_len);
    irq_unlock(key);

    if (got == 0) {
      break;
    }
    g_acc_len += got;
    acc_parse();
  }

  rx_report_errors();
}

static void uart_link_rx_thread(void) {
  for (;;) {
    int ret = k_sem_take(&rx_data_sem,
                         g_acc_len > 0 ? RX_STALL_TIMEOUT : K_FOREVER);
    rx_process(ret == -EAGAIN);
  }
}

K_THREAD_DEFINE(uart_link_rx_thread_id, CONFIG_DEFAULT_THREAD_STACK_SIZE,
                uart_link_rx_thread, NULL, NULL, NULL, 1, 0, 0);

/* ---------------------------------------------------------------- init -- */

void uart_link_init(void) {
  if (!device_is_ready(uart)) {
    LOG_ERR("UART link device not ready");
    return;
  }

  k_work_init_delayable(&rx_restart_work, rx_restart);

  /* Keep the UART powered for good: suspended, it would release its TX pin,
   * and the translator's pull-down would pull the MCU's RX into a break. */
  int err = pm_device_runtime_get(uart);
  if (err < 0) {
    LOG_WRN("Cannot hold the UART link powered (err %d)", err);
  }

  err = uart_callback_set(uart, uart_link_cb, NULL);
  if (err != 0) {
    LOG_ERR("Cannot set UART link callback (err %d)", err);
    return;
  }

  atomic_set(&g_link_up, 1);

  rx_restart(NULL);
  tx_kick();
}
