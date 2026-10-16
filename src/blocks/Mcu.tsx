import { STM32U585_PINS } from "../parts/stm32u585Pins"
import assign from "../parts/mcuAssign.json"
import { W_PWR, W_PWR_LITE } from "../widths"

const S = "MCU"

const SIGNALS = Object.entries(assign).filter(([k]) => !k.startsWith("_")) as [string, [string, string]][]

const VDD = ["VDD_11", "VDD_27", "VDD_51", "VDD_75", "VDD_100"]
const VSS = ["VSS_10", "VSS_26", "VSS_50", "VSS_74", "VSS_99"]

const cap = (name: string, value: string, net: string, jlc: string, fp = "0402", x = 0, y = 0) => (
  <group key={name}>
    <capacitor name={name} capacitance={value} footprint={fp} supplierPartNumbers={{ jlcpcb: [jlc] }}
      schX={x} schY={y} schRotation="90deg" schSectionName={S} />
    <trace from={`.${name} > .pin1`} to={`net.${net}`} />
    <trace from={`.${name} > .pin2`} to="net.GND" />
  </group>
)

/**
 * STM32U585VIT6Q: Cortex-M33 + TrustZone, 2 MB flash, 786 KB SRAM, hardware
 * AES/SAES (hardware unique key), PKA, HASH, TRNG, DCMI for the camera, USB FS.
 * LQFP100, not the WLCSP: a bare die is light-sensitive and the case is clear.
 *
 * Signal pins come from src/parts/mcuAssign.json (checked against ST's pin
 * database by scripts/checkMcuAf.py); everything else on this sheet is supply,
 * clock, reset and debug.
 */
export const McuBlock = () => (
  <group>
    <chip name="U4" footprint="pinrow100" pinLabels={STM32U585_PINS}
      manufacturerPartNumber="STM32U585VIT6Q" supplierPartNumbers={{ jlcpcb: ["C5270986"] }}
      schX={0} schY={10} schSectionName={S} />

    {SIGNALS.map(([pin, [net]]) => (
      <trace key={pin} from={`.U4 > .${pin}`} to={`net.${net}`} />
    ))}

    {VDD.map((p) => <trace key={p} from={`.U4 > .${p}`} to="net.V3V3" width={W_PWR_LITE} />)}
    {VSS.map((p) => <trace key={p} from={`.U4 > .${p}`} to="net.GND" width={W_PWR_LITE} />)}
    <trace from=".U4 > .VSSA" to="net.GND" />
    <trace from=".U4 > .VDDA" to="net.V3V3" />
    <trace from=".U4 > .VREFP" to="net.V3V3" />
    <trace from=".U4 > .VDDUSB" to="net.V3V3" />
    {/* No backup domain across power-off (DS13086 3.9.1: VBAT only matters "when VDD
        is not present"): a Safe transaction carries no timestamp and the PIN retry
        counter lives in the SE050, so VBAT simply rides on VDD. */}
    <trace from=".U4 > .VBAT" to="net.V3V3" />

    {/* Core supply from the internal LDO. DS13086 3.9.1: "When the SMPS is not
        used, it is recommended to connect both VDDSMPS and VLXSMPS to GND." The
        SMPS would save a few mA at full speed (small next to the ~115 mA
        backlight), and its inductor and input cap had to sit in the 2 mm between
        the package's south pins and the TFT connector, exactly where the display
        SPI has to cross: placement for routing won. The VDD11 capacitors are the
        same for either regulator (Figure 25 note). */}
    <trace from=".U4 > .VDDSMPS" to="net.GND" width={W_PWR_LITE} />
    <trace from=".U4 > .VSSSMPS" to="net.GND" width={W_PWR_LITE} />
    <trace from=".U4 > .VLXSMPS" to="net.GND" width={W_PWR_LITE} />
    <trace from=".U4 > .VDD11_49" to="net.VDD11" width={W_PWR_LITE} />
    <trace from=".U4 > .VDD11_98" to="net.VDD11" width={W_PWR_LITE} />

    {/* Decoupling per DS13086 Rev 8 Figure 25 (STM32U585xQ): VDD n x 100 nF + 10 uF;
        VDDA 100 nF + 1 uF, shared with VREF+ (pins 20/21 are neighbours on one net; the
        ADC is unused); VDDUSB 100 nF; VDD11 2 x 2.2 uF (4.7 uF total typ., 3.9.1), one at
        each VDD11 pin, without the optional 100 nF. VBAT on VDD keeps its 100 nF. */}
    {VDD.map((p, i) => cap(`C_VDD${i + 1}`, "100nF", "V3V3", "C1525", "0402", -4 + i, 6))}
    {cap("C_VDDB", "10uF", "V3V3", "C19702", "0603", 2, 6)}
    {cap("C_VDDA1", "1uF", "V3V3", "C52923", "0402", 3, 6)}
    {cap("C_VDDA2", "100nF", "V3V3", "C1525", "0402", 4, 6)}
    {cap("C_VUSB", "100nF", "V3V3", "C1525", "0402", 6, 6)}
    {cap("C_VBAT", "100nF", "V3V3", "C1525", "0402", 7, 6)}
    {/* COUT rated >= 10 V (DS13086 5.1.6): 2.2 uF X5R 10 V. */}
    {cap("C_VDD11A", "2.2uF", "VDD11", "C107369", "0402", 9, 6)}
    {cap("C_VDD11B", "2.2uF", "VDD11", "C107369", "0402", 10, 6)}

    {/* No LSE crystal: USB runs on HSI48 trimmed by the CRS from SOF (DS13086 3.12),
        timeouts and the auto-lock on the LSI. */}

    {/* NRST: 100 nF (ST AN5373). BOOT0 (PH3) tied straight to GND: the ROM bootloader
        is reached through the option bytes (nSWBOOT0) over SWD, so the pin only ever
        needs to sit low, and a pull-down resistor's link would cross the camera pins
        on either side of pin 94. */}
    <trace from=".U4 > .NRST" to="net.NRST" />
    {cap("C_NRST", "100nF", "NRST", "C1525", "0402", -6, 8)}
    <trace from=".U4 > .PH3" to="net.GND" />

    {/* SWD pogo pads (TC2030-NL pattern would cost three NPTH holes; plain pads do). */}
    {[["TP_3V3", "V3V3"], ["TP_SWDIO", "SWDIO"], ["TP_SWCLK", "SWCLK"], ["TP_NRST", "NRST"],
      ["TP_GND", "GND"]].map(([tp, net], i) => (
      <group key={tp}>
        <chip name={tp} footprint="pinrow1" pinLabels={{ pin1: "P" }} schX={-10} schY={4 + i} schSectionName={S} />
        <trace from={`.${tp} > .P`} to={`net.${net}`} />
      </group>
    ))}
  </group>
)
