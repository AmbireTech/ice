/*
 * Ambire boards: the nRF driver (io/nrf.h) over a 2-wire UART.
 *
 * Trezor's nRF link (nrf.c, nrf_spi.c, nrf_update.c) is SPI with the STM32 as
 * slave plus five handshake GPIOs (reset, SPI ready/request, stay-in-
 * bootloader, reserved). Our board wires only a UART (no RTS/CTS) and a
 * power gate (RADIO_OFF) to its nRF52840, behind the airgap switch. This file
 * replaces those three files for such boards (NRF_UART_LINK, selected by
 * `[nrf] link = "io/nrf_uart_link"` in the board file) and implements the
 * same API, so ble.c and everything above it run unchanged.
 *
 * Frame (both directions), Trezor's SPI packet without the padding:
 *
 *   [0xA0 | service_id] [msg_len 0..251] [msg_len payload bytes] [crc8]
 *
 * crc8 = io/nrf/crc8.c with polynomial 0x07, init 0x00, not reflected, over
 * every byte before it. USART at 1,000,000 baud 8N1, no flow control. The
 * receiver resynchronises by dropping one byte from the front whenever byte0's
 * high nibble is not 0xA, msg_len > 251 or the crc does not match. No acks or
 * retransmissions at this layer.
 *
 * RX: GPDMA circular (linked-list item that reloads itself) into a ring; the
 * ring is parsed on the USART's idle-line interrupt, on the DMA's half and
 * full transfer interrupts, and from a 50 ms timer.
 * TX: one frame at a time from a queue, by GPDMA (normal mode).
 *
 * Radio present or not: the airgap switch can cut the radio's power at any
 * time, and there is no line to tell. The link counts the radio as running
 * while it has received a valid frame in the last RADIO_SILENT_MS (ble.c pings
 * the radio for its status every 100 ms, so a live radio is never silent that
 * long). While it is silent, the link itself sends a management INFO request
 * every PROBE_PERIOD_MS, so a radio that comes back is noticed. Nothing waits
 * on the radio without a timeout.
 *
 * Not available over this link: the nRF firmware update from the STM32 (SMP,
 * on Trezor's boards a second UART with flow control plus the reset and
 * stay-in-bootloader lines), the nRF pairing authentication (nrf_auth: a
 * secret provisioned into both chips at the factory), the prodtest helpers
 * (nrf_test_*, DTM).
 */

#ifdef KERNEL_MODE

#include <trezor_bsp.h>
#include <trezor_rtl.h>

#include <io/nrf.h>
#include <io/tsqueue.h>
#include <sys/irq.h>
#include <sys/logging.h>
#include <sys/mpu.h>
#include <sys/systick.h>
#include <sys/systimer.h>

#include "../crc8.h"
#include "../nrf_internal.h"

LOG_DECLARE(nrf_uart_link)

#define LINK_BAUD 1000000U

#define FRAME_START 0xA0
#define FRAME_START_MASK 0xF0
#define FRAME_HEADER_SIZE 2
#define FRAME_MAX_PAYLOAD MAX_SPI_DATA_SIZE  // 251
#define FRAME_MAX_SIZE (FRAME_HEADER_SIZE + FRAME_MAX_PAYLOAD + 1)

// 2048 bytes = 20 ms at 1 Mbaud; the DMA interrupts every 10 ms of traffic
#define RX_RING_SIZE 2048U
_Static_assert((RX_RING_SIZE & (RX_RING_SIZE - 1)) == 0,
               "RX_RING_SIZE must be a power of two");

#define LINK_TX_QUEUE_LEN 8

#define LINK_TIMER_PERIOD_MS 50
// A live radio answers ble.c's status ping every 100 ms
#define RADIO_SILENT_MS 500
#define PROBE_PERIOD_MS 200
// Power off long enough for the radio's rail to drop (not measured on a board)
#define RADIO_POWER_CYCLE_MS 100
// Waits for an answer, as Trezor's driver does
#define INFO_TIMEOUT_MS 100
#define TX_DRAIN_TIMEOUT_MS 100
#define DMA_SUSPEND_SPINS 10000U

typedef struct {
  uint8_t frame[FRAME_MAX_SIZE];
  uint16_t len;
  nrf_tx_callback_t callback;
  void *context;
} link_tx_request_t;

typedef struct {
  bool initialized;
  bool comm_running;
  bool radio_alive;
  bool radio_heard;
  uint32_t last_rx_ms;
  uint32_t last_probe_ms;

  // RX: DMA ring and its self-reloading linked-list item (CBR1, CDAR, CLLR)
  uint8_t rx_ring[RX_RING_SIZE];
  uint32_t rx_lli[3] __attribute__((aligned(16)));
  uint32_t rx_rd;
  uint32_t rx_wr_at_last_tick;
  uint8_t rx_frame[FRAME_MAX_SIZE];

  // TX
  uint8_t tx_buffers[LINK_TX_QUEUE_LEN][sizeof(link_tx_request_t)];
  tsqueue_entry_t tx_queue_entries[LINK_TX_QUEUE_LEN];
  tsqueue_t tx_queue;
  link_tx_request_t tx_current;
  bool tx_busy;

  nrf_rx_callback_t service_listeners[NRF_SERVICE_CNT];

  bool info_valid;
  nrf_info_t info;

  systimer_t *timer;

  // Diagnostics (read them with a debugger)
  uint32_t rx_frames;
  uint32_t rx_dropped_bytes;
  uint32_t uart_errors;
  uint32_t dma_errors;
} nrf_link_t;

static nrf_link_t g_nrf_link = {0};

// ---------------------------------------------------------------------------
// DMA helpers (register level: the channels are the kernel's, non-secure)

#define DMA_ALL_FLAGS                                                    \
  (DMA_CFCR_TCF | DMA_CFCR_HTF | DMA_CFCR_DTEF | DMA_CFCR_ULEF |         \
   DMA_CFCR_USEF | DMA_CFCR_SUSPF | DMA_CFCR_TOF)
#define DMA_ERROR_FLAGS (DMA_CSR_DTEF | DMA_CSR_ULEF | DMA_CSR_USEF)

static void dma_channel_stop(DMA_Channel_TypeDef *ch) {
  if ((ch->CCR & DMA_CCR_EN) != 0) {
    ch->CCR |= DMA_CCR_SUSP;
    uint32_t spins = DMA_SUSPEND_SPINS;
    while ((ch->CSR & DMA_CSR_SUSPF) == 0 && --spins != 0) {
    }
    if (spins == 0) {
      LOG_WARN("DMA channel did not suspend; resetting it anyway");
    }
  }
  ch->CCR = DMA_CCR_RESET;
  ch->CFCR = DMA_ALL_FLAGS;
}

static void rx_dma_start(nrf_link_t *drv) {
  DMA_Channel_TypeDef *ch = RADIO_UART_DMA_RX_CHANNEL;

  dma_channel_stop(ch);

  uint32_t lli_addr = (uint32_t)drv->rx_lli;
  uint32_t cllr = DMA_CLLR_UB1 | DMA_CLLR_UDA | DMA_CLLR_ULL |
                  (lli_addr & DMA_CLLR_LA);

  // At the end of each pass the channel reloads the block size and the ring's
  // start from this item, which points to itself: a circular transfer
  drv->rx_lli[0] = RX_RING_SIZE;           // CBR1
  drv->rx_lli[1] = (uint32_t)drv->rx_ring;  // CDAR
  drv->rx_lli[2] = cllr;                    // CLLR
  __DSB();

  ch->CLBAR = lli_addr & DMA_CLBAR_LBA;
  // Byte to byte, peripheral on port 0, memory on port 1 (as Trezor's HAL
  // setups allocate them)
  ch->CTR1 = DMA_CTR1_DINC | DMA_CTR1_DAP;
  ch->CTR2 = (RADIO_UART_DMA_RX_REQUEST << DMA_CTR2_REQSEL_Pos) &
             DMA_CTR2_REQSEL;
  ch->CBR1 = RX_RING_SIZE;
  ch->CSAR = (uint32_t)&RADIO_UART->RDR;
  ch->CDAR = (uint32_t)drv->rx_ring;
  ch->CLLR = cllr;
  ch->CCR = DMA_CCR_LAP | DMA_CCR_HTIE | DMA_CCR_TCIE | DMA_CCR_DTEIE |
            DMA_CCR_ULEIE | DMA_CCR_USEIE;
  ch->CCR |= DMA_CCR_EN;

  drv->rx_rd = 0;
  drv->rx_wr_at_last_tick = 0;
}

static uint32_t rx_write_index(void) {
  uint32_t remaining = RADIO_UART_DMA_RX_CHANNEL->CBR1 & DMA_CBR1_BNDT;
  return (RX_RING_SIZE - remaining) & (RX_RING_SIZE - 1);
}

// ---------------------------------------------------------------------------
// RX parsing

static void link_note_radio_heard(nrf_link_t *drv) {
  drv->last_rx_ms = systick_ms();
  drv->radio_heard = true;
  if (!drv->radio_alive) {
    drv->radio_alive = true;
    LOG_INF("radio link up");
  }
}

static void link_management_rx_cb(const uint8_t *data, uint32_t len) {
  nrf_link_t *drv = &g_nrf_link;

  if (len < 1) {
    return;
  }

  switch (data[0]) {
    case MGMT_RESP_INFO:
      if (len < 1 + sizeof(nrf_info_t)) {
        LOG_WARN("short INFO response: %d bytes", (int)len);
        break;
      }
      memcpy(&drv->info, &data[1], sizeof(nrf_info_t));
      drv->info_valid = true;
      break;
    default:
      // MGMT_RESP_AUTH_RESPONSE: no nrf_auth over this link
      break;
  }
}

// Parses every complete frame in the ring. Must run with interrupts locked:
// the UART, DMA and timer interrupts all call it.
static void link_rx_process(nrf_link_t *drv) {
  uint32_t wr = rx_write_index();

  for (;;) {
    uint32_t avail = (wr - drv->rx_rd) & (RX_RING_SIZE - 1);
    if (avail < FRAME_HEADER_SIZE) {
      return;
    }

    uint8_t start = drv->rx_ring[drv->rx_rd];
    uint8_t msg_len = drv->rx_ring[(drv->rx_rd + 1) & (RX_RING_SIZE - 1)];

    if ((start & FRAME_START_MASK) != FRAME_START ||
        msg_len > FRAME_MAX_PAYLOAD) {
      drv->rx_rd = (drv->rx_rd + 1) & (RX_RING_SIZE - 1);
      drv->rx_dropped_bytes++;
      continue;
    }

    uint32_t frame_len = FRAME_HEADER_SIZE + msg_len + 1;
    if (avail < frame_len) {
      // Incomplete: wait for the rest (the timer drops a stale start byte)
      return;
    }

    for (uint32_t i = 0; i < frame_len; i++) {
      drv->rx_frame[i] = drv->rx_ring[(drv->rx_rd + i) & (RX_RING_SIZE - 1)];
    }

    uint8_t crc = crc8(drv->rx_frame, frame_len - 1, 0x07, 0x00, false);
    if (crc != drv->rx_frame[frame_len - 1]) {
      drv->rx_rd = (drv->rx_rd + 1) & (RX_RING_SIZE - 1);
      drv->rx_dropped_bytes++;
      continue;
    }

    drv->rx_rd = (drv->rx_rd + frame_len) & (RX_RING_SIZE - 1);
    drv->rx_frames++;
    link_note_radio_heard(drv);

    uint8_t service = start & 0x0F;
    if (drv->comm_running && service < NRF_SERVICE_CNT &&
        drv->service_listeners[service] != NULL) {
      drv->service_listeners[service](&drv->rx_frame[FRAME_HEADER_SIZE],
                                      msg_len);
    }
  }
}

// ---------------------------------------------------------------------------
// TX

static void tx_dma_start(const uint8_t *data, uint16_t len) {
  DMA_Channel_TypeDef *ch = RADIO_UART_DMA_TX_CHANNEL;

  // The previous transfer has completed (the channel disabled itself)
  ch->CCR = DMA_CCR_RESET;
  ch->CFCR = DMA_ALL_FLAGS;
  ch->CTR1 = DMA_CTR1_SINC | DMA_CTR1_SAP;
  ch->CTR2 = ((RADIO_UART_DMA_TX_REQUEST << DMA_CTR2_REQSEL_Pos) &
              DMA_CTR2_REQSEL) |
             DMA_CTR2_DREQ;
  ch->CBR1 = len;
  ch->CSAR = (uint32_t)data;
  ch->CDAR = (uint32_t)&RADIO_UART->TDR;
  ch->CLLR = 0;
  ch->CCR = DMA_CCR_TCIE | DMA_CCR_DTEIE | DMA_CCR_ULEIE | DMA_CCR_USEIE;
  ch->CCR |= DMA_CCR_EN;
}

// Starts the next queued frame if the transmitter is free. Interrupts locked.
static void tx_start_next(nrf_link_t *drv) {
  if (drv->tx_busy) {
    return;
  }
  if (tsqueue_dequeue(&drv->tx_queue, (uint8_t *)&drv->tx_current,
                      sizeof(link_tx_request_t), NULL, NULL)) {
    drv->tx_busy = true;
    tx_dma_start(drv->tx_current.frame, drv->tx_current.len);
  }
}

static void tx_complete_current(nrf_link_t *drv, nrf_status_t status) {
  drv->tx_busy = false;
  if (drv->tx_current.callback != NULL) {
    drv->tx_current.callback(status, drv->tx_current.context);
  }
  memset(&drv->tx_current, 0, sizeof(drv->tx_current));
}

static void tx_abort_all(nrf_link_t *drv) {
  dma_channel_stop(RADIO_UART_DMA_TX_CHANNEL);
  if (drv->tx_busy) {
    tx_complete_current(drv, NRF_STATUS_ABORTED);
  }
  link_tx_request_t req;
  while (tsqueue_dequeue(&drv->tx_queue, (uint8_t *)&req, sizeof(req), NULL,
                         NULL)) {
    if (req.callback != NULL) {
      req.callback(NRF_STATUS_ABORTED, req.context);
    }
  }
  tsqueue_reset(&drv->tx_queue);
}

// Queues a frame regardless of whether the radio is answering (the probe
// itself goes through here)
static bool link_enqueue(nrf_link_t *drv, nrf_service_id_t service,
                         const uint8_t *data, uint32_t len,
                         nrf_tx_callback_t callback, void *context) {
  link_tx_request_t req;
  req.callback = callback;
  req.context = context;
  req.frame[0] = FRAME_START | (uint8_t)service;
  req.frame[1] = (uint8_t)len;
  memcpy(&req.frame[FRAME_HEADER_SIZE], data, len);
  req.frame[FRAME_HEADER_SIZE + len] =
      crc8(req.frame, FRAME_HEADER_SIZE + len, 0x07, 0x00, false);
  req.len = (uint16_t)(FRAME_HEADER_SIZE + len + 1);

  if (!tsqueue_enqueue(&drv->tx_queue, (uint8_t *)&req, sizeof(req), NULL)) {
    return false;
  }

  irq_key_t key = irq_lock();
  tx_start_next(drv);
  irq_unlock(key);
  return true;
}

static bool link_tx_idle(nrf_link_t *drv) {
  irq_key_t key = irq_lock();
  bool idle = !drv->tx_busy && tsqueue_empty(&drv->tx_queue);
  irq_unlock(key);
  return idle;
}

static void link_wait_tx_drained(nrf_link_t *drv) {
  uint32_t deadline = ticks_timeout(TX_DRAIN_TIMEOUT_MS);
  while (!link_tx_idle(drv)) {
    if (ticks_expired(deadline)) {
      LOG_WARN("TX queue not drained within %d ms", TX_DRAIN_TIMEOUT_MS);
      return;
    }
  }
  // The DMA has handed the last byte to the USART; let it leave the wire
  uint32_t spins = DMA_SUSPEND_SPINS * 10;
  while ((RADIO_UART->ISR & USART_ISR_TC) == 0 && --spins != 0) {
  }
}

// ---------------------------------------------------------------------------
// Timer: stale input, radio liveness, probe

static void link_timer_callback(void *context) {
  nrf_link_t *drv = (nrf_link_t *)context;
  if (!drv->initialized) {
    return;
  }

  irq_key_t key = irq_lock();

  link_rx_process(drv);

  // Bytes left over with nothing new for a whole period: a stale start byte
  // (noise that looked like a header) holds them up. Drop bytes from the front
  // until the rest parses.
  uint32_t wr = rx_write_index();
  if (wr == drv->rx_wr_at_last_tick) {
    while (drv->rx_rd != wr) {
      drv->rx_rd = (drv->rx_rd + 1) & (RX_RING_SIZE - 1);
      drv->rx_dropped_bytes++;
      link_rx_process(drv);
    }
  }
  drv->rx_wr_at_last_tick = wr;

  uint32_t now = systick_ms();
  if (drv->radio_alive && (now - drv->last_rx_ms) >= RADIO_SILENT_MS) {
    drv->radio_alive = false;
    drv->info_valid = false;
    LOG_WARN("radio silent for %d ms: link down", RADIO_SILENT_MS);
  }

  bool probe = drv->comm_running && !drv->radio_alive &&
               (now - drv->last_probe_ms) >= PROBE_PERIOD_MS;
  if (probe) {
    drv->last_probe_ms = now;
  }

  irq_unlock(key);

  if (probe) {
    uint8_t cmd = MGMT_CMD_INFO;
    if (!link_enqueue(drv, NRF_SERVICE_MANAGEMENT, &cmd, 1, NULL, NULL)) {
      LOG_WARN("probe not queued: TX queue full");
    }
  }
}

// ---------------------------------------------------------------------------
// Interrupts

void RADIO_UART_IRQHandler(void) {
  IRQ_LOG_ENTER();
  mpu_mode_t mpu_mode = mpu_reconfig(MPU_MODE_DEFAULT);

  nrf_link_t *drv = &g_nrf_link;
  uint32_t isr = RADIO_UART->ISR;

  if ((isr & (USART_ISR_FE | USART_ISR_NE | USART_ISR_PE | USART_ISR_ORE)) !=
      0) {
    // A bad byte: the frame around it fails its crc and is resynchronised
    RADIO_UART->ICR =
        USART_ICR_FECF | USART_ICR_NECF | USART_ICR_PECF | USART_ICR_ORECF;
    drv->uart_errors++;
  }
  if ((isr & USART_ISR_IDLE) != 0) {
    RADIO_UART->ICR = USART_ICR_IDLECF;
  }

  if (drv->initialized) {
    irq_key_t key = irq_lock();
    link_rx_process(drv);
    irq_unlock(key);
  }

  mpu_restore(mpu_mode);
  IRQ_LOG_EXIT();
}

void RADIO_UART_DMA_RX_IRQHandler(void) {
  IRQ_LOG_ENTER();
  mpu_mode_t mpu_mode = mpu_reconfig(MPU_MODE_DEFAULT);

  nrf_link_t *drv = &g_nrf_link;
  DMA_Channel_TypeDef *ch = RADIO_UART_DMA_RX_CHANNEL;
  uint32_t csr = ch->CSR;
  ch->CFCR = DMA_CFCR_TCF | DMA_CFCR_HTF;

  if (drv->initialized) {
    irq_key_t key = irq_lock();
    link_rx_process(drv);
    if ((csr & DMA_ERROR_FLAGS) != 0) {
      // The channel stopped itself: start over with an empty ring
      drv->dma_errors++;
      LOG_ERR("RX DMA error, CSR 0x%08x: restarting", (unsigned)csr);
      rx_dma_start(drv);
    }
    irq_unlock(key);
  } else {
    ch->CFCR = DMA_ALL_FLAGS;
  }

  mpu_restore(mpu_mode);
  IRQ_LOG_EXIT();
}

void RADIO_UART_DMA_TX_IRQHandler(void) {
  IRQ_LOG_ENTER();
  mpu_mode_t mpu_mode = mpu_reconfig(MPU_MODE_DEFAULT);

  nrf_link_t *drv = &g_nrf_link;
  DMA_Channel_TypeDef *ch = RADIO_UART_DMA_TX_CHANNEL;
  uint32_t csr = ch->CSR;
  ch->CFCR = DMA_ALL_FLAGS;

  if (drv->initialized && drv->tx_busy &&
      (csr & (DMA_CSR_TCF | DMA_ERROR_FLAGS)) != 0) {
    nrf_status_t status = NRF_STATUS_OK;
    if ((csr & DMA_ERROR_FLAGS) != 0) {
      drv->dma_errors++;
      LOG_ERR("TX DMA error, CSR 0x%08x: frame dropped", (unsigned)csr);
      ch->CCR = DMA_CCR_RESET;
      status = NRF_STATUS_ERROR;
    }
    irq_key_t key = irq_lock();
    tx_complete_current(drv, status);
    tx_start_next(drv);
    irq_unlock(key);
  }

  mpu_restore(mpu_mode);
  IRQ_LOG_EXIT();
}

// ---------------------------------------------------------------------------
// Hardware setup

static void radio_power(bool on) {
  // Low allows the radio's power; the airgap switch still decides
  HAL_GPIO_WritePin(RADIO_OFF_PORT, RADIO_OFF_PIN,
                    on ? GPIO_PIN_RESET : GPIO_PIN_SET);
}

static void link_hw_init(nrf_link_t *drv) {
  GPIO_InitTypeDef gpio = {0};

  RADIO_OFF_CLK_EN();
  radio_power(true);
  gpio.Mode = GPIO_MODE_OUTPUT_PP;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_LOW;
  gpio.Pin = RADIO_OFF_PIN;
  HAL_GPIO_Init(RADIO_OFF_PORT, &gpio);

  RADIO_UART_PINS_CLK_EN();
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Speed = GPIO_SPEED_FREQ_MEDIUM;
  gpio.Alternate = RADIO_UART_PIN_AF;
  gpio.Pull = GPIO_NOPULL;
  gpio.Pin = RADIO_UART_TX_PIN;
  HAL_GPIO_Init(RADIO_UART_TX_PORT, &gpio);
  // Keeps RX idle-high while nothing drives it (radio unpowered)
  gpio.Pull = GPIO_PULLUP;
  gpio.Pin = RADIO_UART_RX_PIN;
  HAL_GPIO_Init(RADIO_UART_RX_PORT, &gpio);

  __HAL_RCC_GPDMA1_CLK_ENABLE();
  RADIO_UART_CLK_EN();
  RADIO_UART_FORCE_RESET();
  RADIO_UART_RELEASE_RESET();

  // USART3 runs on PCLK1 (its reset clock source), which Trezor's startup sets
  // equal to the core clock; the RCC HAL belongs to the secure monitor.
  // 160 MHz / 1 Mbaud = 160, exact at 16x oversampling.
  RADIO_UART->BRR = SystemCoreClock / LINK_BAUD;
  // DMA both ways; an overrun overwrites instead of stopping reception (the
  // crc catches the loss); errors and idle line interrupt
  RADIO_UART->CR3 =
      USART_CR3_DMAR | USART_CR3_DMAT | USART_CR3_OVRDIS | USART_CR3_EIE;

  rx_dma_start(drv);

  RADIO_UART->CR1 =
      USART_CR1_FIFOEN | USART_CR1_IDLEIE | USART_CR1_TE | USART_CR1_RE;
  RADIO_UART->CR1 |= USART_CR1_UE;

  NVIC_SetPriority(RADIO_UART_IRQn, IRQ_PRI_NORMAL);
  NVIC_EnableIRQ(RADIO_UART_IRQn);
  NVIC_SetPriority(RADIO_UART_DMA_RX_IRQn, IRQ_PRI_NORMAL);
  NVIC_EnableIRQ(RADIO_UART_DMA_RX_IRQn);
  NVIC_SetPriority(RADIO_UART_DMA_TX_IRQn, IRQ_PRI_NORMAL);
  NVIC_EnableIRQ(RADIO_UART_DMA_TX_IRQn);
}

static void link_hw_deinit(void) {
  NVIC_DisableIRQ(RADIO_UART_IRQn);
  NVIC_DisableIRQ(RADIO_UART_DMA_RX_IRQn);
  NVIC_DisableIRQ(RADIO_UART_DMA_TX_IRQn);

  dma_channel_stop(RADIO_UART_DMA_RX_CHANNEL);
  dma_channel_stop(RADIO_UART_DMA_TX_CHANNEL);

  RADIO_UART->CR1 = 0;
  RADIO_UART_FORCE_RESET();
  RADIO_UART_RELEASE_RESET();
  RADIO_UART_CLK_DIS();

  HAL_GPIO_DeInit(RADIO_UART_TX_PORT, RADIO_UART_TX_PIN);
  HAL_GPIO_DeInit(RADIO_UART_RX_PORT, RADIO_UART_RX_PIN);
  // RADIO_OFF stays as it is: deinit is not a power-off (nrf_system_off is)
}

// ---------------------------------------------------------------------------
// io/nrf.h

void nrf_start(void) {
  nrf_link_t *drv = &g_nrf_link;
  if (!drv->initialized) {
    return;
  }
  tsqueue_reset(&drv->tx_queue);
  drv->comm_running = true;
}

void nrf_stop(void) {
  nrf_link_t *drv = &g_nrf_link;
  if (!drv->initialized) {
    return;
  }
  irq_key_t key = irq_lock();
  drv->comm_running = false;
  tx_abort_all(drv);
  irq_unlock(key);
}

void nrf_init(void) {
  nrf_link_t *drv = &g_nrf_link;

  if (drv->initialized) {
    return;
  }

  memset(drv, 0, sizeof(*drv));
  tsqueue_init(&drv->tx_queue, drv->tx_queue_entries,
               (uint8_t *)drv->tx_buffers, sizeof(link_tx_request_t),
               LINK_TX_QUEUE_LEN);

  drv->timer = systimer_create(link_timer_callback, drv);
  if (drv->timer == NULL) {
    LOG_ERR("no systimer left: radio link not started");
    return;
  }

  link_hw_init(drv);

  drv->service_listeners[NRF_SERVICE_MANAGEMENT] = link_management_rx_cb;
  // The first probe goes out on the first timer tick
  drv->last_probe_ms = systick_ms() - PROBE_PERIOD_MS;
  drv->initialized = true;

  nrf_start();
  systimer_set_periodic(drv->timer, LINK_TIMER_PERIOD_MS);
}

void nrf_deinit(void) {
  nrf_link_t *drv = &g_nrf_link;

  if (!drv->initialized) {
    return;
  }

  nrf_stop();
  systimer_delete(drv->timer);
  link_hw_deinit();
  drv->initialized = false;
}

void nrf_suspend(void) {
  nrf_link_t *drv = &g_nrf_link;
  if (!drv->initialized) {
    return;
  }

  uint8_t data[1] = {MGMT_CMD_SUSPEND};
  if (link_enqueue(drv, NRF_SERVICE_MANAGEMENT, data, 1, NULL, NULL)) {
    link_wait_tx_drained(drv);
  } else {
    LOG_WARN("SUSPEND not queued: TX queue full");
  }
  nrf_deinit();
}

void nrf_resume(void) {
  nrf_link_t *drv = &g_nrf_link;

  // ble_resume() re-runs ble_init(), which has already brought the link up
  nrf_init();

  if (!drv->initialized) {
    return;
  }
  uint8_t data[1] = {MGMT_CMD_RESUME};
  if (!link_enqueue(drv, NRF_SERVICE_MANAGEMENT, data, 1, NULL, NULL)) {
    LOG_WARN("RESUME not queued: TX queue full");
  }
}

bool nrf_is_running(void) {
  nrf_link_t *drv = &g_nrf_link;
  if (!drv->initialized) {
    return false;
  }
  return drv->comm_running && drv->radio_alive;
}

bool nrf_register_listener(nrf_service_id_t service,
                           nrf_rx_callback_t callback) {
  nrf_link_t *drv = &g_nrf_link;
  if (!drv->initialized) {
    return false;
  }
  if (service >= NRF_SERVICE_CNT) {
    return false;
  }
  if (drv->service_listeners[service] != NULL) {
    return false;
  }

  irq_key_t key = irq_lock();
  drv->service_listeners[service] = callback;
  irq_unlock(key);
  return true;
}

void nrf_unregister_listener(nrf_service_id_t service) {
  nrf_link_t *drv = &g_nrf_link;
  if (!drv->initialized) {
    return;
  }
  if (service >= NRF_SERVICE_CNT || service == NRF_SERVICE_MANAGEMENT) {
    return;
  }

  irq_key_t key = irq_lock();
  drv->service_listeners[service] = NULL;
  irq_unlock(key);
}

bool nrf_send_msg(nrf_service_id_t service, const uint8_t *data, uint32_t len,
                  nrf_tx_callback_t callback, void *context) {
  nrf_link_t *drv = &g_nrf_link;
  if (!drv->initialized) {
    return false;
  }
  if (len > NRF_MAX_TX_DATA_SIZE) {
    return false;
  }
  if (service >= NRF_SERVICE_CNT) {
    return false;
  }
  // As Trezor's driver: nothing is queued for a radio that is not running
  // (here: not answering, e.g. switched off by the airgap switch)
  if (!nrf_is_running()) {
    return false;
  }

  return link_enqueue(drv, service, data, len, callback, context);
}

bool nrf_get_info(nrf_info_t *info) {
  nrf_link_t *drv = &g_nrf_link;
  if (!nrf_is_running()) {
    return false;
  }

  irq_key_t key = irq_lock();
  drv->info_valid = false;
  irq_unlock(key);

  uint8_t data[1] = {MGMT_CMD_INFO};
  if (!nrf_send_msg(NRF_SERVICE_MANAGEMENT, data, 1, NULL, NULL)) {
    return false;
  }

  uint32_t timeout = ticks_timeout(INFO_TIMEOUT_MS);
  while (!ticks_expired(timeout)) {
    key = irq_lock();
    if (drv->info_valid) {
      memcpy(info, &drv->info, sizeof(nrf_info_t));
      irq_unlock(key);
      return true;
    }
    irq_unlock(key);
  }

  return false;
}

uint32_t nrf_get_version(void) {
  nrf_info_t info = {0};
  if (!nrf_get_info(&info)) {
    return 0;
  }
  return ((uint32_t)info.version_major << 24) |
         ((uint32_t)info.version_minor << 16) |
         ((uint32_t)info.version_patch << 8) | (uint32_t)info.version_tweak;
}

bool nrf_system_off(void) {
  nrf_link_t *drv = &g_nrf_link;
  if (!drv->initialized) {
    return false;
  }

  uint8_t data[1] = {MGMT_CMD_SYSTEM_OFF};
  if (link_enqueue(drv, NRF_SERVICE_MANAGEMENT, data, 1, NULL, NULL)) {
    link_wait_tx_drained(drv);
  } else {
    LOG_WARN("SYSTEM_OFF not queued: TX queue full");
  }

  // Our radio can be switched off for real; nrf_reboot() powers it again
  radio_power(false);
  return true;
}

void nrf_reboot(void) {
  nrf_link_t *drv = &g_nrf_link;

  // No reset line: power-cycle the radio
  RADIO_OFF_CLK_EN();
  radio_power(false);
  systick_delay_ms(RADIO_POWER_CYCLE_MS);
  radio_power(true);

  if (drv->initialized) {
    irq_key_t key = irq_lock();
    drv->radio_alive = false;
    drv->info_valid = false;
    irq_unlock(key);
  }
}

bool nrf_authenticate(void) {
  // nrf_auth needs a pairing secret written into both chips at the factory;
  // this link has none. Say so rather than pretend.
  LOG_WARN("nRF authentication is not available over the UART link");
  return false;
}

bool nrf_update_required(const uint8_t *image_ptr, size_t image_len) {
  (void)image_ptr;
  (void)image_len;
  // The radio's firmware is not updated from the STM32 over this link
  return false;
}

bool nrf_update_with_progress(const uint8_t *image_ptr, size_t image_len,
                              nrf_progress_callback_t progress) {
  (void)image_ptr;
  (void)image_len;
  (void)progress;
  LOG_WARN("nRF firmware update is not available over the UART link");
  return false;
}

bool nrf_update(const uint8_t *image_ptr, size_t image_len) {
  return nrf_update_with_progress(image_ptr, image_len, NULL);
}

bool nrf_send_uart_data(const uint8_t *data, uint32_t len,
                        uint32_t timeout_ms) {
  (void)data;
  (void)len;
  (void)timeout_ms;
  // Raw UART (SMP) traffic shares the one UART with the framed link: refused
  LOG_WARN("raw nRF UART data is not available over the UART link");
  return false;
}

#endif  // KERNEL_MODE
