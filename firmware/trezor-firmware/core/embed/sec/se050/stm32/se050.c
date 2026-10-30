/*
 * Ambire Ice: NXP SE050 secure element, random number generator only.
 * Not part of Trezor's tree; GPL-3.0-or-later like the rest of it.
 *
 * The SE050 speaks ISO 7816-3 T=1 over I2C in NXP's UM11225 flavour, with
 * the IoT applet on top. This driver does only what GetRandom needs: power
 * up, resynchronise, read the ATR, select the applet, exchange short APDUs.
 * No chaining, no R-block recovery: any protocol error fails the exchange,
 * and the caller power-cycles the chip and tries once more.
 *
 * Every protocol constant below comes from NXP's Plug & Trust middleware,
 * github.com/NXP/plug-and-trust at master 7102413 (2026-09-24). Paths are
 * relative to its hostlib/hostLib/ unless they start with sss/.
 */

#ifdef SECURE_MODE

#include <trezor_bsp.h>
#include <trezor_rtl.h>

#include <sec/se050.h>
#include <sys/i2c_bus.h>
#include <sys/logging.h>
#include <sys/rng_use_flags.h>
#include <sys/systick.h>

#include "memzero.h"

LOG_DECLARE(se050)

// libCommon/smCom/T1oI2C/phNxpEsePal_i2c.h: SMCOM_I2C_ADDRESS 0x90 (8-bit)
#define SE050_I2C_ADDRESS 0x48

// libCommon/smCom/T1oI2C/phNxpEsePal_i2c.h: MAX_RETRY_COUNT, retried on NACK
// with a back-off growing by 1 ms per retry (phNxpEsePal_i2c.c,
// phPalEse_i2c_back_off_delay_wait)
#define SE050_I2C_RETRIES 8
// Per I2C transfer (frames are at most 259 bytes); the OPTIGA driver uses 25
#define SE050_I2C_TIMEOUT_MS 25

// platform/rsp/se05x_reset.c: axReset_ResetPulseDUT holds ENA low 2 ms;
// se05x_ic_reset then waits 3 ms; phNxpEseProto7816_3.c
// phNxpEseProto7816_Open waits WAKE_UP_DELAY_MS (5 ms, phNxpEsePal_i2c.h)
// before the first frame
#define SE050_ENA_LOW_MS 2
#define SE050_POWER_UP_MS (3 + 5)

// phNxpEsePal_i2c.h: the SE NACKs its address while busy; poll every
// ESE_POLL_DELAY_MS (1 ms). "As Max WTX timeout is 1sec ... select target
// value 2 sec": no frame (not even a WTX request) within 2 s is a failure.
#define SE050_POLL_DELAY_MS 1
#define SE050_FRAME_TIMEOUT_MS 2000

// T=1 framing, T1oI2C_UM11225 (the variant simw_lib.cmake defines):
// phNxpEseProto7816_3.h SEND_PACKET_SOF, phNxpEse_Api.c RECIEVE_PACKET_SOF
#define T1_NAD_TX 0x5A
#define T1_NAD_RX 0xA5
// PH_PROTO_7816_HEADER_LEN (NAD, PCB, one LEN byte), PH_PROTO_7816_CRC_LEN
#define T1_HEADER_LEN 3
#define T1_CRC_LEN 2
// IFSC_SIZE_SEND; the LEN byte caps a received frame the same way
#define T1_MAX_INF 254
#define T1_MAX_FRAME (T1_HEADER_LEN + T1_MAX_INF + T1_CRC_LEN)

// PCB. I-block: bit 7 clear, N(S) in bit 6, more-data in bit 5
// (phNxpEseProto7816_SendIframe, PH_PROTO_7816_CHAINING). S-blocks:
// PH_PROTO_7816_S_BLOCK_REQ 0xC0 / _RSP 0xE0 or'ed with the type,
// PH_PROTO_7816_S_RESYNCH 0x00, _S_WTX 0x03, _S_GET_ATR 0x07.
#define T1_PCB_BLOCK_MASK 0x80
#define T1_PCB_I_SEQ_SHIFT 6
#define T1_PCB_I_MORE 0x20
#define T1_PCB_S_RESYNCH_REQ 0xC0
#define T1_PCB_S_RESYNCH_RSP 0xE0
#define T1_PCB_S_WTX_REQ 0xC3
#define T1_PCB_S_WTX_RSP 0xE3
#define T1_PCB_S_ATR_REQ 0xC7
#define T1_PCB_S_ATR_RSP 0xE7

// phNxpEseProto7816_SendSFrame, WTX_RSP: INF is the single byte 0x01.
// phNxpEseProto7816_DecodeFrame waits DELAY_ERROR_RECOVERY (3500 us, used as
// 3 ms) before answering a WTX request.
#define T1_WTX_RSP_INF 0x01
#define T1_WTX_DELAY_MS 3
// SELECT and GetRandom never need a waiting-time extension; the middleware
// allows 500 (PH_PROTO_WTX_DEFAULT_COUNT), this driver gives up after 10.
#define T1_WTX_MAX 10

// libCommon/infra/global_platf.c GP_Select: CLA_ISO7816 0x00,
// INS_GP_SELECT 0xA4 (global_platf.h), P1 0x04, P2 0x00, Lc, AID, Le 0x00.
// AID: inc/sm_const.h APPLET_NAME for SSS_HAVE_APPLET_SE05X_IOT.
static const uint8_t SELECT_IOT_APPLET[] = {
    0x00, 0xA4, 0x04, 0x00, 0x10,                    // header, Lc
    0xA0, 0x00, 0x00, 0x03, 0x96, 0x54, 0x53, 0x00,  // AID
    0x00, 0x00, 0x01, 0x03, 0x00, 0x00, 0x00, 0x00,  //
    0x00,                                            // Le
};

// se05x_03_xx_xx/se05x_APDU_impl.h Se05x_API_GetRandom: kSE05x_CLA 0x80
// (inc/se05x_tlv.h), kSE05x_INS_MGMT 0x04, kSE05x_P1_DEFAULT 0x00,
// kSE05x_P2_RANDOM 0x49 (inc/se05x_enums.h), one TLV kSE05x_TAG_1 (0x41)
// holding the size as a big-endian U16 (se05x/src/se05x_tlv.c tlvSet_U16).
// Sent as case 4 extended (DoAPDUTxRx_s_Case4_ext; the encoding is
// sss/src/se05x/fsl_sss_se05x_apis.c sss_se05x_channel_txnRaw with hasle:
// Lc 00 hi lo, Le 00 00). Response: 41, length, the bytes, SW 90 00.
#define SE05X_TAG_1 0x41
#define SE050_RANDOM_CHUNK 32U  // keeps the response TLV's length one byte

static const uint8_t SW_OK[2] = {0x90, 0x00};

typedef struct {
  i2c_bus_t *bus;
  bool open;
  // N(S) of our next I-block and of the SE's next one; RESYNCH resets both
  uint8_t tx_seq;
  uint8_t rx_seq;
  // One frame buffer for both directions: it carries random bytes, so it
  // is wiped after every exchange.
  uint8_t frame[T1_MAX_FRAME];
} se050_driver_t;

static se050_driver_t g_se050 = {0};

// libCommon/smCom/T1oI2C/phNxpEseProto7816_3.c phNxpEseProto7816_ComputeCRC:
// CRC-16 with the reflected polynomial 0x8408, initial value 0xFFFF and a
// final xor of 0xFFFF (CRC-16/X-25), over NAD to the end of INF.
static uint16_t t1_crc(const uint8_t *data, size_t size) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < size; i++) {
    crc ^= data[i];
    for (int bit = 0; bit < 8; bit++) {
      crc = (crc & 1) ? (crc >> 1) ^ 0x8408 : crc >> 1;
    }
  }
  return crc ^ 0xFFFF;
}

static i2c_status_t se050_i2c_transfer(uint16_t flags, uint8_t *data,
                                       size_t size) {
  i2c_op_t ops[] = {
      {
          .flags = flags,
          .size = size,
          .ptr = data,
      },
  };

  i2c_packet_t pkt = {
      .address = SE050_I2C_ADDRESS,
      .timeout = SE050_I2C_TIMEOUT_MS,
      .op_count = ARRAY_LENGTH(ops),
      .ops = ops,
  };

  return i2c_bus_submit_and_wait(g_se050.bus, &pkt);
}

// phPalEse_i2c_read / phPalEse_i2c_write: a NACK means "busy", retried
// with a growing back-off; any other bus error ends the transfer.
static bool se050_i2c_retry(uint16_t flags, uint8_t *data, size_t size) {
  i2c_status_t status = I2C_STATUS_ERROR;
  for (int retry = 0; retry <= SE050_I2C_RETRIES; retry++) {
    if (retry > 0) {
      systick_delay_ms(retry);
    }
    status = se050_i2c_transfer(flags, data, size);
    if (status != I2C_STATUS_NACK) {
      break;
    }
  }
  if (status != I2C_STATUS_OK) {
    LOG_WARN("I2C %s of %d bytes failed: status %d",
             (flags & I2C_FLAG_RX) ? "read" : "write", (int)size, status);
    return false;
  }
  return true;
}

static bool t1_send(uint8_t pcb, const uint8_t *inf, size_t inf_size) {
  if (inf_size > T1_MAX_INF) {
    LOG_WARN("frame of %d bytes is too long", (int)inf_size);
    return false;
  }
  uint8_t *frame = g_se050.frame;
  frame[0] = T1_NAD_TX;
  frame[1] = pcb;
  frame[2] = (uint8_t)inf_size;
  if (inf_size > 0) {
    memcpy(&frame[T1_HEADER_LEN], inf, inf_size);
  }
  size_t size = T1_HEADER_LEN + inf_size;
  // T1oI2C_UM11225 sends the CRC's low byte first
  uint16_t crc = t1_crc(frame, size);
  frame[size++] = crc & 0xFF;
  frame[size++] = crc >> 8;
  return se050_i2c_retry(I2C_FLAG_TX, frame, size);
}

// libCommon/smCom/T1oI2C/phNxpEse_Api.c phNxpEse_readPacket: poll two bytes
// until the SE answers with its NAD, either first ("A5 PCB") or second
// ("xx A5"), then read the rest of the header, then INF and CRC. The SE
// keeps its frame across these separate reads.
static bool t1_receive(void) {
  uint8_t *frame = g_se050.frame;
  uint32_t start = systick_ms();
  for (;;) {
    uint8_t head[2] = {0};
    if (se050_i2c_transfer(I2C_FLAG_RX, head, sizeof(head)) == I2C_STATUS_OK) {
      if (head[0] == T1_NAD_RX) {
        frame[0] = head[0];
        frame[1] = head[1];
        if (!se050_i2c_retry(I2C_FLAG_RX, &frame[2], 1)) {
          return false;
        }
        break;
      }
      if (head[1] == T1_NAD_RX) {
        frame[0] = head[1];
        if (!se050_i2c_retry(I2C_FLAG_RX, &frame[1], 2)) {
          return false;
        }
        break;
      }
    }
    if (systick_ms() - start >= SE050_FRAME_TIMEOUT_MS) {
      LOG_WARN("no frame within %d ms", SE050_FRAME_TIMEOUT_MS);
      return false;
    }
    systick_delay_ms(SE050_POLL_DELAY_MS);
  }

  size_t inf_size = frame[2];
  if (!se050_i2c_retry(I2C_FLAG_RX, &frame[T1_HEADER_LEN],
                       inf_size + T1_CRC_LEN)) {
    return false;
  }
  size_t size = T1_HEADER_LEN + inf_size;
  uint16_t crc = t1_crc(frame, size);
  if (frame[size] != (crc & 0xFF) || frame[size + 1] != (crc >> 8)) {
    LOG_WARN("CRC mismatch, PCB 0x%02x", frame[1]);
    return false;
  }
  return true;
}

// Sends one block and returns the SE's first block that is not a
// waiting-time extension request, which is answered on the way.
static bool t1_exchange(uint8_t pcb, const uint8_t *inf, size_t inf_size) {
  if (!t1_send(pcb, inf, inf_size)) {
    return false;
  }
  for (int wtx = 0;; wtx++) {
    if (!t1_receive()) {
      return false;
    }
    if (g_se050.frame[1] != T1_PCB_S_WTX_REQ) {
      return true;
    }
    if (wtx >= T1_WTX_MAX) {
      LOG_WARN("more than %d waiting-time extensions", T1_WTX_MAX);
      return false;
    }
    systick_delay_ms(T1_WTX_DELAY_MS);
    const uint8_t wtx_inf = T1_WTX_RSP_INF;
    if (!t1_send(T1_PCB_S_WTX_RSP, &wtx_inf, sizeof(wtx_inf))) {
      return false;
    }
  }
}

static bool t1_s_block(uint8_t pcb_req, uint8_t pcb_rsp) {
  bool ok = t1_exchange(pcb_req, NULL, 0);
  if (ok && g_se050.frame[1] != pcb_rsp) {
    LOG_WARN("S-block 0x%02x answered with PCB 0x%02x", pcb_req,
             g_se050.frame[1]);
    ok = false;
  }
  memzero(g_se050.frame, sizeof(g_se050.frame));
  return ok;
}

// One APDU in one I-block, one response in one I-block (no chaining either
// way). The response ends in its status word; it is checked to be 90 00.
static bool t1_apdu(const uint8_t *cmd, size_t cmd_size, uint8_t *rsp,
                    size_t rsp_max, size_t *rsp_size) {
  bool ok = false;
  uint8_t pcb = g_se050.tx_seq << T1_PCB_I_SEQ_SHIFT;
  if (!t1_exchange(pcb, cmd, cmd_size)) {
    goto cleanup;
  }

  const uint8_t *frame = g_se050.frame;
  uint8_t rx_pcb = frame[1];
  size_t inf_size = frame[2];
  if ((rx_pcb & T1_PCB_BLOCK_MASK) != 0 || (rx_pcb & T1_PCB_I_MORE) != 0 ||
      ((rx_pcb >> T1_PCB_I_SEQ_SHIFT) & 1) != g_se050.rx_seq) {
    LOG_WARN("unexpected block, PCB 0x%02x", rx_pcb);
    goto cleanup;
  }
  if (inf_size < sizeof(SW_OK) || inf_size > rsp_max) {
    LOG_WARN("response of %d bytes", (int)inf_size);
    goto cleanup;
  }
  const uint8_t *sw = &frame[T1_HEADER_LEN + inf_size - sizeof(SW_OK)];
  if (memcmp(sw, SW_OK, sizeof(SW_OK)) != 0) {
    LOG_WARN("status word %02x%02x", sw[0], sw[1]);
    goto cleanup;
  }

  memcpy(rsp, &frame[T1_HEADER_LEN], inf_size);
  *rsp_size = inf_size;
  g_se050.tx_seq ^= 1;
  g_se050.rx_seq ^= 1;
  ok = true;

cleanup:
  memzero(g_se050.frame, sizeof(g_se050.frame));
  return ok;
}

static void se050_close(void) {
  if (g_se050.bus != NULL) {
    i2c_bus_close(g_se050.bus);
  }
  memzero(&g_se050, sizeof(g_se050));
}

// Power-cycles the SE050 (ENA low = deep power-down), then follows the
// middleware's session open: phNxpEseProto7816_Open with interface reset
// (RESYNCH, then the ATR), then GP_Select of the IoT applet.
static bool se050_open(void) {
  se050_close();

  GPIO_InitTypeDef GPIO_InitStructure = {0};
  SE050_ENA_CLK_EN();
  GPIO_InitStructure.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStructure.Pull = GPIO_NOPULL;
  GPIO_InitStructure.Speed = GPIO_SPEED_FREQ_LOW;
  GPIO_InitStructure.Pin = SE050_ENA_PIN;
  HAL_GPIO_WritePin(SE050_ENA_PORT, SE050_ENA_PIN, GPIO_PIN_RESET);
  HAL_GPIO_Init(SE050_ENA_PORT, &GPIO_InitStructure);
  systick_delay_ms(SE050_ENA_LOW_MS);
  HAL_GPIO_WritePin(SE050_ENA_PORT, SE050_ENA_PIN, GPIO_PIN_SET);
  systick_delay_ms(SE050_POWER_UP_MS);

  g_se050.bus = i2c_bus_open(SE050_I2C_INSTANCE);
  if (g_se050.bus == NULL) {
    LOG_WARN("I2C bus %d did not open", SE050_I2C_INSTANCE);
    return false;
  }

  if (!t1_s_block(T1_PCB_S_RESYNCH_REQ, T1_PCB_S_RESYNCH_RSP) ||
      !t1_s_block(T1_PCB_S_ATR_REQ, T1_PCB_S_ATR_RSP)) {
    return false;
  }
  g_se050.tx_seq = 0;
  g_se050.rx_seq = 0;

  uint8_t rsp[T1_MAX_INF] = {0};
  size_t rsp_size = 0;
  if (!t1_apdu(SELECT_IOT_APPLET, sizeof(SELECT_IOT_APPLET), rsp, sizeof(rsp),
               &rsp_size)) {
    LOG_WARN("IoT applet not selected");
    return false;
  }

  g_se050.open = true;
  return true;
}

static bool se050_get_random(uint8_t *dest, size_t size) {
  const uint8_t cmd[] = {
      0x80, 0x04, 0x00, 0x49,      // CLA, INS_MGMT, P1_DEFAULT, P2_RANDOM
      0x00, 0x00, 0x04,            // extended Lc
      SE05X_TAG_1, 0x02, 0x00,     // TAG_1, length, size high
      (uint8_t)size, 0x00, 0x00};  // size low, extended Le

  uint8_t rsp[2 + SE050_RANDOM_CHUNK + sizeof(SW_OK)] = {0};
  size_t rsp_size = 0;
  bool ok = t1_apdu(cmd, sizeof(cmd), rsp, sizeof(rsp), &rsp_size);
  if (ok && (rsp_size != 2 + size + sizeof(SW_OK) || rsp[0] != SE05X_TAG_1 ||
             rsp[1] != size)) {
    LOG_WARN("GetRandom answered %d bytes, tag 0x%02x, length %d",
             (int)rsp_size, rsp[0], rsp[1]);
    ok = false;
  }
  if (ok) {
    memcpy(dest, &rsp[2], size);
  }
  memzero(rsp, sizeof(rsp));
  return ok;
}

bool se050_random_buffer(uint8_t *dest, size_t size) {
  while (size > 0) {
    size_t chunk = MIN(size, SE050_RANDOM_CHUNK);
    bool ok = false;
    // A failed exchange leaves the link in an unknown state: the second
    // attempt starts from a power-cycled chip.
    for (int attempt = 0; attempt < 2 && !ok; attempt++) {
      if (!g_se050.open && !se050_open()) {
        se050_close();
        continue;
      }
      ok = se050_get_random(dest, chunk);
      if (!ok) {
        LOG_WARN("GetRandom failed (attempt %d)", attempt + 1);
        se050_close();
      }
    }
    if (!ok) {
      return false;
    }
    dest += chunk;
    size -= chunk;
  }

  rng_use_flag_set(RNG_TYPE_SE050);
  return true;
}

#endif  // SECURE_MODE
