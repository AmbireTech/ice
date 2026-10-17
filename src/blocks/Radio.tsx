import { W_PWR_LITE } from "../widths"

const S = "Radio"

/**
 * Airgap switch, BLE radio and the USB data path.
 *
 * The switch is the authority, not the firmware. One net, AIRGAP (high =
 * airgapped), goes from the slide switch straight to the two places where the
 * outside world enters:
 *
 *   AIRGAP --> TS3USB221A /OE ............ high = USB D+/D- disconnected (charging unaffected)
 *          --> 74LVC1G02 NOR ---> TPS22917 ON --> RADIO_3V3 (the whole BLE module)
 *   RADIO_OFF (MCU) --^              NOR: the MCU can turn the radio OFF, never ON
 *
 *   MCU USART3 <-> TXU0202 (B side and OE on RADIO_3V3) <-> module UART
 *
 * Why each piece is there:
 *  - The MCU only senses AIRGAP through 1M (AIRGAP_SENSE). Against the 100k
 *    fail-safe pull-up (switch between detents) a GPIO driven low still leaves
 *    the net at 3.0 V, a solid high; in the AIR detent the switch holds it through
 *    1k. A 10k here let firmware pull a floating switch to 0.3 V (review M1).
 *  - With RADIO_3V3 at 0 V the TXU0202 is high-impedance on both ports (TI
 *    SCES942A 9.3.4, VCC isolation), so firmware driving the UART pins cannot
 *    back-power the module through its I/O clamps.
 *  - The pull-up on AIRGAP makes "airgapped" the state of a switch that is
 *    between detents or broken.
 *  - RADIO_LED hangs on RADIO_3V3 itself: it is lit exactly when the radio has
 *    power, whatever the firmware says.
 */
export const RadioBlock = () => (
  <group>
    {/* ---------------- Airgap slide switch (the Nano's MSK-12C02) ---------------- */}
    <chip name="SW_AIR" footprint="pinrow4"
      manufacturerPartNumber="MSK12C02" supplierPartNumbers={{ jlcpcb: ["C431540"] }}
      pinLabels={{ pin1: "AIR", pin2: "COM", pin3: "ONLINE", pin4: "TAB" }}
      schX={0} schY={-20} schSectionName={S} />
    {/* 1k in the AIR throw: the MSK12C02's data sheet does not say it breaks
        before it makes, and its other throw is GND. */}
    <trace from=".SW_AIR > .AIR" to="net.AIR_THROW" />
    <resistor name="R_AIRSER" resistance="1k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C11702"] }}
      schX={-2} schY={-20} schRotation="90deg" schSectionName={S} />
    <trace from=".R_AIRSER > .pin1" to="net.AIR_THROW" />
    <trace from=".R_AIRSER > .pin2" to="net.V3V3" />
    <trace from=".SW_AIR > .COM" to="net.AIRGAP" />
    <trace from=".SW_AIR > .ONLINE" to="net.GND" />
    <trace from=".SW_AIR > .TAB" to="net.GND" />
    <resistor name="R_AIRPU" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={2} schY={-20} schRotation="90deg" schSectionName={S} />
    <trace from=".R_AIRPU > .pin1" to="net.AIRGAP" />
    <trace from=".R_AIRPU > .pin2" to="net.V3V3" />
    <resistor name="R_AIRS" resistance="1M" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C26083"] }}
      schX={4} schY={-20} schSectionName={S} />
    <trace from=".R_AIRS > .pin1" to="net.AIRGAP" />
    <trace from=".R_AIRS > .pin2" to="net.AIRGAP_SENSE" />

    {/* ---------------- USB data switch ---------------- */}
    {/* TS3USB221A RSE (SCDS277C Table 4-1): 1 1D+, 2 1D-, 3 2D+, 4 2D-, 5 GND, 6 /OE,
        7 D-, 8 D+, 9 S, 10 VCC. S low = port 1. Powered from V3V3, so with the
        board off it is unpowered and the MCU is off the bus anyway. */}
    <chip name="U6" footprint="pinrow10"
      manufacturerPartNumber="TS3USB221ARSER" supplierPartNumbers={{ jlcpcb: ["C128396"] }}
      pinLabels={{ pin1: "D1P", pin2: "D1N", pin3: "D2P", pin4: "D2N", pin5: "GND", pin6: "OE_N",
        pin7: "DN", pin8: "DP", pin9: "S", pin10: "VCC" }}
      schX={-6} schY={-20} schSectionName={S} />
    <trace from=".U6 > .D1P" to="net.USB_MCU_DP" />
    <trace from=".U6 > .D1N" to="net.USB_MCU_DN" />
    <trace from=".U6 > .D2P" to="net.NC_USB_2DP" />
    <trace from=".U6 > .D2N" to="net.NC_USB_2DN" />
    <trace from=".U6 > .DP" to="net.USB_DP" />
    <trace from=".U6 > .DN" to="net.USB_DN" />
    <trace from=".U6 > .GND" to="net.GND" />
    <trace from=".U6 > .S" to="net.GND" />
    <trace from=".U6 > .OE_N" to="net.AIRGAP" />
    <trace from=".U6 > .VCC" to="net.V3V3" />
    <capacitor name="C_USBSW" capacitance="100nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C1525"] }}
      schX={-4} schY={-22} schRotation="90deg" schSectionName={S} />
    <trace from=".C_USBSW > .pin1" to="net.V3V3" />
    <trace from=".C_USBSW > .pin2" to="net.GND" />

    {/* ---------------- Radio power gate ---------------- */}
    {/* SN74LVC1G02 (UMW, C7394016): 1 B, 2 A, 3 GND, 4 Y, 5 VCC. Y = NOR(AIRGAP, RADIO_OFF). */}
    <chip name="U7" footprint="pinrow5"
      manufacturerPartNumber="SN74LVC1G02DBVR(UMW)" supplierPartNumbers={{ jlcpcb: ["C7394016"] }}
      pinLabels={{ pin1: "B", pin2: "A", pin3: "GND", pin4: "Y", pin5: "VCC" }}
      schX={6} schY={-20} schSectionName={S} />
    <trace from=".U7 > .A" to="net.AIRGAP" />
    <trace from=".U7 > .B" to="net.RADIO_OFF" />
    <trace from=".U7 > .Y" to="net.RADIO_EN" />
    <trace from=".U7 > .GND" to="net.GND" />
    <trace from=".U7 > .VCC" to="net.V3V3" />
    <capacitor name="C_NOR" capacitance="100nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C1525"] }}
      schX={8} schY={-22} schRotation="90deg" schSectionName={S} />
    <trace from=".C_NOR > .pin1" to="net.V3V3" />
    <trace from=".C_NOR > .pin2" to="net.GND" />
    {/* Radio off until firmware asks: RADIO_OFF pulled high. */}
    <resistor name="R_ROFF" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={8} schY={-18} schRotation="90deg" schSectionName={S} />
    <trace from=".R_ROFF > .pin1" to="net.RADIO_OFF" />
    <trace from=".R_ROFF > .pin2" to="net.V3V3" />

    {/* TPS22917 DBV (SLVSDW8B Table 6-1): 1 VIN, 2 GND, 3 ON (active high), 4 CT, 5 QOD, 6 VOUT.
        CT 1 nF (to VIN, Table 6-1): open, the 44 mV/us edge into ~10 uF of module
        bulk is ~0.45 A of inrush, enough to dip V3V3. QOD on VOUT = internal
        discharge, so the rail drops to 0 V promptly when the switch moves to AIRGAP. */}
    <chip name="U8" footprint="pinrow6"
      manufacturerPartNumber="TPS22917DBVR" supplierPartNumbers={{ jlcpcb: ["C2681320"] }}
      pinLabels={{ pin1: "VIN", pin2: "GND", pin3: "ON", pin4: "CT", pin5: "QOD", pin6: "VOUT" }}
      schX={10} schY={-20} schSectionName={S} />
    <trace from=".U8 > .VIN" to="net.V3V3" width={W_PWR_LITE} />
    <trace from=".U8 > .GND" to="net.GND" />
    <trace from=".U8 > .ON" to="net.RADIO_EN" />
    <trace from=".U8 > .CT" to="net.RADIO_CT" />
    <capacitor name="C_RCT" capacitance="1nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C14442"] }}
      schX={12} schY={-18} schRotation="90deg" schSectionName={S} />
    <trace from=".C_RCT > .pin1" to="net.V3V3" />
    <trace from=".C_RCT > .pin2" to="net.RADIO_CT" />
    <trace from=".U8 > .QOD" to="net.RADIO_3V3" />
    <trace from=".U8 > .VOUT" to="net.RADIO_3V3" width={W_PWR_LITE} />
    <capacitor name="C_RIN" capacitance="1uF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C52923"] }}
      schX={12} schY={-22} schRotation="90deg" schSectionName={S} />
    <trace from=".C_RIN > .pin1" to="net.V3V3" />
    <trace from=".C_RIN > .pin2" to="net.GND" />

    {/* Hardware truth light: lit exactly while the radio has power (~1 mA). */}
    <chip name="D_RADIO" footprint="pinrow2"
      manufacturerPartNumber="KT-0603G" supplierPartNumbers={{ jlcpcb: ["C12624"] }}
      pinLabels={{ pin1: "K", pin2: "A" }}
      schX={14} schY={-20} schSectionName={S} />
    <resistor name="R_RLED" resistance="1k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C11702"] }}
      schX={14} schY={-18} schSectionName={S} />
    <trace from=".R_RLED > .pin1" to="net.RADIO_3V3" />
    <trace from=".R_RLED > .pin2" to="net.RADIO_LED_A" />
    <trace from=".D_RADIO > .A" to="net.RADIO_LED_A" />
    <trace from=".D_RADIO > .K" to="net.GND" />

    {/* ---------------- UART isolation ---------------- */}
    {/* TXU0202 DCU (SCES942A Table 6-1): 1 B2, 2 GND, 3 VCCA, 4 A2Y, 5 A1, 6 OE, 7 VCCB, 8 B1Y.
        Fixed directions, one bit each way: A1 -> B1Y (MCU TX to module RX), B2 -> A2Y
        (module TX to MCU RX). A = MCU side (V3V3), B = radio side (RADIO_3V3).
        - Outputs go high-impedance when either supply is below 100 mV or floating
          (9.3.4, VCC isolation), and Ioff holds every pin at <= 2.5 uA (7.5): with
          RADIO_3V3 at 0 V (QOD) nothing the MCU drives reaches or back-powers the module.
        - OE on the radio's own rail: enabled only while the radio has power, so even the
          OE path needs no second net from the gate.
        - Inputs carry 5 Mohm pull-downs (9.3.1.1), so the MCU's unconfigured TX and the
          module's TX while it boots never float; both lines idle low until driven (one
          framing error to discard at start-up). The MCU's RX floats while the radio is
          off: firmware keeps the USART RX pin's internal pull-up on. */}
    <chip name="U9" footprint="pinrow8"
      manufacturerPartNumber="TXU0202DCUR" supplierPartNumbers={{ jlcpcb: ["C5186957"] }}
      pinLabels={{ pin1: "B2", pin2: "GND", pin3: "VCCA", pin4: "A2Y", pin5: "A1", pin6: "OE", pin7: "VCCB", pin8: "B1Y" }}
      schX={-6} schY={-26} schSectionName={S} />
    <trace from=".U9 > .VCCA" to="net.V3V3" />
    <trace from=".U9 > .GND" to="net.GND" />
    <trace from=".U9 > .A1" to="net.RADIO_TXD_MCU" />
    <trace from=".U9 > .B1Y" to="net.RADIO_RX" />
    <trace from=".U9 > .B2" to="net.RADIO_TX" />
    <trace from=".U9 > .A2Y" to="net.RADIO_RXD_MCU" />
    <trace from=".U9 > .OE" to="net.RADIO_3V3" />
    <trace from=".U9 > .VCCB" to="net.RADIO_3V3" />
    {/* 0.1 uF per supply pin (SCES942A 12.1). */}
    {[["C_U9A", "V3V3"], ["C_U9B", "RADIO_3V3"]].map(([c, net], i) => (
      <group key={c}>
        <capacitor name={c} capacitance="100nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C1525"] }}
          schX={-6 + i} schY={-29} schRotation="90deg" schSectionName={S} />
        <trace from={`.${c} > .pin1`} to={`net.${net}`} />
        <trace from={`.${c} > .pin2`} to="net.GND" />
      </group>
    ))}

    {/* ---------------- BLE module: Ebyte E73-2G4M08S1C (nRF52840) ---------------- */}
    {/* Pads per the E73-2G4M08S1C user manual v1.9 section 3. Only edge (castellated)
        pads are used for signals. UART on P1.09 (pad 17, module TX) and P0.05 (pad 15,
        module RX), a firmware choice (the nRF52840 maps its UARTE to any GPIO): the
        nearest edge pads to the TXU0202 that are full-speed I/O. Every GPIO on the
        module's north edge (P0.02/03/28/29/30/31, P1.10/11/13) is "standard drive, low
        frequency I/O only" (nRF52840 PS pin assignments: up to 10 kHz), kept away from
        anything that switches next to the radio. VDD and VDDH tied: normal-voltage
        supply mode at 3.3 V. DCCH, VBUS, USB, crystal and NFC pads unused. */}
    <chip name="U11" footprint="pinrow43"
      manufacturerPartNumber="E73-2G4M08S1C" supplierPartNumbers={{ jlcpcb: ["C356849"] }}
      pinLabels={{ pin1: "P1_11", pin2: "P1_10", pin3: "P0_03", pin4: "P0_28", pin5: "GND5", pin6: "P1_13",
        pin7: "P0_02", pin8: "P0_29", pin9: "P0_31", pin10: "P0_30", pin11: "XL1", pin12: "P0_26", pin13: "XL2",
        pin14: "P0_06", pin15: "P0_05", pin16: "P0_08", pin17: "P1_09", pin18: "P0_04", pin19: "VDD", pin20: "P0_12",
        pin21: "GND21", pin22: "P0_07", pin23: "VDDH", pin24: "GND24", pin25: "DCCH", pin26: "RESET", pin27: "VBUS",
        pin28: "P0_15", pin29: "USB_DN", pin30: "P0_17", pin31: "USB_DP", pin32: "P0_20", pin33: "P0_13", pin34: "P0_22",
        pin35: "P0_24", pin36: "P1_00", pin37: "SWDIO", pin38: "P1_02", pin39: "SWDCLK", pin40: "P1_04", pin41: "NFC1",
        pin42: "P1_06", pin43: "NFC2" }}
      schX={4} schY={-26} schSectionName={S} />
    <trace from=".U11 > .VDD" to="net.RADIO_3V3" width={W_PWR_LITE} />
    <trace from=".U11 > .VDDH" to="net.RADIO_3V3" width={W_PWR_LITE} />
    <trace from=".U11 > .GND5" to="net.GND" />
    <trace from=".U11 > .GND21" to="net.GND" />
    <trace from=".U11 > .GND24" to="net.GND" />
    <trace from=".U11 > .P1_09" to="net.RADIO_TX" />
    <trace from=".U11 > .P0_05" to="net.RADIO_RX" />
    <trace from=".U11 > .SWDIO" to="net.RADIO_SWDIO" />
    <trace from=".U11 > .SWDCLK" to="net.RADIO_SWCLK" />
    {[["C_RADIO1", "10uF", "0603", "C19702"], ["C_RADIO2", "100nF", "0402", "C1525"]].map(([c, v, fp, jlc], i) => (
      <group key={c}>
        <capacitor name={c} capacitance={v} footprint={fp} supplierPartNumbers={{ jlcpcb: [jlc] }}
          schX={8 + i} schY={-26} schRotation="90deg" schSectionName={S} />
        <trace from={`.${c} > .pin1`} to="net.RADIO_3V3" />
        <trace from={`.${c} > .pin2`} to="net.GND" />
      </group>
    ))}
    {/* Factory programming of the radio (it is updated over UART afterwards). SWD
        needs no reset line on the nRF52840, so its RESET pad stays unconnected.
        TP_RGND: a ground pad beside the others, so a probe or jig needs no lead
        to the MCU's TP_GND 47 mm away (pre-order audit). */}
    {[["TP_RSWDIO", "RADIO_SWDIO"], ["TP_RSWCLK", "RADIO_SWCLK"], ["TP_R3V3", "RADIO_3V3"], ["TP_RGND", "GND"]].map(([tp, net], i) => (
      <group key={tp}>
        <chip name={tp} footprint="pinrow1" pinLabels={{ pin1: "P" }} schX={12} schY={-28 + i} schSectionName={S} />
        <trace from={`.${tp} > .P`} to={`net.${net}`} />
      </group>
    ))}
  </group>
)
