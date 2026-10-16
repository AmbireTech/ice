import { W_PWR_LITE, W_SIG } from "../widths"

const S = "Display"

/**
 * Newhaven NHD-2.4-240320AF-CSXP-CTP, Rev 2B — the GridCore Nano's panel (2.4"
 * 240x320 IPS, ST7789VI, 4-wire SPI, capacitive touch). Pin table, IM straps and
 * the backlight pump are the Nano's, already checked against the Newhaven
 * datasheet there (hardware/nano/design.md, "Display").
 *
 * Mounting: the panel sits over the board's plain face with the pouch cell
 * between them; every part is on the other face. The TFT tail leaves the panel's
 * south edge, wraps the board's south edge and plugs into the Molex 54132-4062
 * on the component face, opening south. Worked through in docs/design.md: after
 * the two 90-degree bends the tail's contacts face the board (the 54132 is
 * bottom-contact) and panel pin 1 is still at the west end, which on the flipped
 * footprint is pad 40, so connector pad = 41 - panel pin, as on the Nano.
 */
const pad = (panelPin: number) => `.J_TFT > .pin${41 - panelPin}`

export const DisplayBlock = () => (
  <group>
    <chip name="J_TFT" footprint="pinrow42"
      manufacturerPartNumber="54132-4062" supplierPartNumbers={{ jlcpcb: ["C505102"] }}
      schX={2} schY={-8} schSectionName={S} />
    {[1, 39].map((p) => <trace key={`g${p}`} from={pad(p)} to="net.GND" width={W_PWR_LITE} />)}
    {[41, 42].map((n) => <trace key={`mp${n}`} from={`.J_TFT > .pin${n}`} to="net.GND" width={W_PWR_LITE} />)}
    {[2, 3, 4, 5].map((p) => <trace key={`nc${p}`} from={pad(p)} to={`net.NC_TFT${p}`} />)}
    <trace from={pad(6)} to="net.NC_TFT_SDO" />
    <trace from={pad(7)} to="net.V3V3" width={W_PWR_LITE} />
    <trace from={pad(8)} to="net.V3V3" width={W_PWR_LITE} />
    <trace from={pad(9)} to="net.TFT_MOSI" width={W_SIG} />
    <trace from={pad(10)} to="net.TFT_CS" width={W_SIG} />
    {/* In SPI mode pin 11 is the clock and 12 is D/C (Newhaven Rev 2B). */}
    <trace from={pad(11)} to="net.TFT_SCK" width={W_SIG} />
    <trace from={pad(12)} to="net.TFT_DC" width={W_SIG} />
    {Array.from({ length: 17 }, (_, i) => i + 13).map((p) => (
      <trace key={`db${p}`} from={pad(p)} to="net.GND" />
    ))}
    <trace from={pad(30)} to="net.TFT_RST" width={W_SIG} />
    {/* IM0/IM1/IM2 = 0/1/1: 4-wire SPI. */}
    <trace from={pad(31)} to="net.GND" />
    <trace from={pad(32)} to="net.V3V3" />
    <trace from={pad(33)} to="net.V3V3" />
    {[34, 35, 36, 37].map((p) => <trace key={`k${p}`} from={pad(p)} to="net.BL_K" width={W_PWR_LITE} />)}
    <trace from={pad(38)} to="net.BL_A" width={W_PWR_LITE} />
    {/* TE: tear-effect, so firmware can update the frame without visible tearing. */}
    <trace from={pad(40)} to="net.TFT_TE" />

    <capacitor name="C_TFT1" capacitance="1uF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C52923"] }}
      schX={5} schY={-6} schRotation="90deg" schSectionName={S} />
    <capacitor name="C_TFT2" capacitance="100nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C1525"] }}
      schX={6} schY={-6} schRotation="90deg" schSectionName={S} />
    {["C_TFT1", "C_TFT2"].map((c) => (
      <group key={c}>
        <trace from={`.${c} > .pin1`} to="net.V3V3" />
        <trace from={`.${c} > .pin2`} to="net.GND" />
      </group>
    ))}

    {/*
      Touch tail (FT5426, 6-pin 1.0 mm: 1 VDD, 2 GND, 3 SCL, 4 SDA, 5 /INT,
      6 /RESET per Newhaven). Connector and pad order are set in
      board/footprints.json once the tail's contact face is fixed for this
      wrap-around mount (docs/design.md, open items).
    */}
    <chip name="J_CTP" footprint="pinrow8"
      manufacturerPartNumber="HC-FPC-1.0-6P-CSH25" supplierPartNumbers={{ jlcpcb: ["C49166860"] }}
      pinLabels={{ pin1: "VDD", pin2: "GND", pin3: "SCL", pin4: "SDA", pin5: "INT", pin6: "RST", pin7: "MP1", pin8: "MP2" }}
      schX={2} schY={-12} schSectionName={S} />
    <trace from=".J_CTP > .VDD" to="net.V3V3" />
    <trace from=".J_CTP > .GND" to="net.GND" />
    <trace from=".J_CTP > .SCL" to="net.I2C_SCL" />
    <trace from=".J_CTP > .SDA" to="net.I2C_SDA" />
    <trace from=".J_CTP > .INT" to="net.CTP_INT" />
    <trace from=".J_CTP > .RST" to="net.CTP_RST" />
    <trace from=".J_CTP > .MP1" to="net.GND" />
    <trace from=".J_CTP > .MP2" to="net.GND" />

    {/* Peripheral I2C (touch 0x38, battery gauge 0x36). */}
    <resistor name="R_SCL" resistance="4.7k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25900"] }}
      schX={4} schY={-14} schRotation="90deg" schSectionName={S} />
    <resistor name="R_SDA" resistance="4.7k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25900"] }}
      schX={5} schY={-14} schRotation="90deg" schSectionName={S} />
    <trace from=".R_SCL > .pin1" to="net.I2C_SCL" />
    <trace from=".R_SDA > .pin1" to="net.I2C_SDA" />
    <trace from=".R_SCL > .pin2" to="net.V3V3" />
    <trace from=".R_SDA > .pin2" to="net.V3V3" />

    {/* Backlight: 8 LEDs common anode (panel 80-200 mA). R_BLSET 6.8k gives
        260 x 0.6 V / 6.8k = 23 mA per sink x 5 = ~115 mA at full PWM; 4.87k gives
        the panel's 160 mA typical. TPS60230 1x/1.5x pump from
        SYS, the Nano's circuit (SLVS516A Terminal Functions: 1 ISET, 2-6 D5..D1,
        7 PGND, 8 VOUT, 9 C2+, 10 C1+, 11 C1-, 12 C2-, 13 VIN, 14 GND, 15 EN1,
        16 EN2, 17 pad). EN1/EN2 on the MCU's TIM1_CH1 PWM. */}
    <chip name="U13" footprint="pinrow17"
      manufacturerPartNumber="TPS60230RGTR" supplierPartNumbers={{ jlcpcb: ["C1848364"] }}
      pinLabels={{ pin1: "ISET", pin2: "D5", pin3: "D4", pin4: "D3", pin5: "D2", pin6: "D1", pin7: "PGND", pin8: "VOUT",
        pin9: "C2P", pin10: "C1P", pin11: "C1N", pin12: "C2N", pin13: "VIN", pin14: "GND", pin15: "EN1", pin16: "EN2", pin17: "EP" }}
      schX={8} schY={-10} schSectionName={S} />
    {["D1", "D2", "D3", "D4", "D5"].map((d) => (
      <trace key={d} from={`.U13 > .${d}`} to="net.BL_K" width={W_PWR_LITE} />
    ))}
    <trace from=".U13 > .VOUT" to="net.BL_A" width={W_PWR_LITE} />
    <trace from=".U13 > .VIN" to="net.SYS" width={W_PWR_LITE} />
    <trace from=".U13 > .PGND" to="net.GND" width={W_PWR_LITE} />
    <trace from=".U13 > .GND" to="net.GND" />
    <trace from=".U13 > .EP" to="net.GND" />
    <trace from=".U13 > .EN1" to="net.BL_EN" />
    <trace from=".U13 > .EN2" to="net.BL_EN" />
    <trace from=".U13 > .ISET" to="net.BL_ISET" />
    <trace from=".U13 > .C1P" to="net.BL_C1P" />
    <trace from=".U13 > .C1N" to="net.BL_C1N" />
    <trace from=".U13 > .C2P" to="net.BL_C2P" />
    <trace from=".U13 > .C2N" to="net.BL_C2N" />
    <resistor name="R_BLSET" resistance="6.8k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25917"] }}
      schX={8} schY={-13} schRotation="90deg" schSectionName={S} />
    <trace from=".R_BLSET > .pin1" to="net.BL_ISET" />
    <trace from=".R_BLSET > .pin2" to="net.GND" />
    {/* EN held low while the MCU is off or in reset: no backlight from a floating pin. */}
    <resistor name="R_BLPD" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={10} schY={-13} schRotation="90deg" schSectionName={S} />
    <trace from=".R_BLPD > .pin1" to="net.BL_EN" />
    <trace from=".R_BLPD > .pin2" to="net.GND" />
    <resistor name="R_BLEN" resistance="1k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C11702"] }}
      schX={10} schY={-15} schRotation="90deg" schSectionName={S} />
    <trace from=".R_BLEN > .pin1" to="net.TFT_BL" />
    <trace from=".R_BLEN > .pin2" to="net.BL_EN" />
    <capacitor name="C_BLIN" capacitance="1uF" footprint="0603" supplierPartNumbers={{ jlcpcb: ["C15849"] }}
      schX={12} schY={-10} schRotation="90deg" schSectionName={S} />
    <trace from=".C_BLIN > .pin1" to="net.SYS" width={W_PWR_LITE} />
    <trace from=".C_BLIN > .pin2" to="net.GND" width={W_PWR_LITE} />
    <capacitor name="C_BLOUT" capacitance="1uF" footprint="0603" supplierPartNumbers={{ jlcpcb: ["C15849"] }}
      schX={13} schY={-10} schRotation="90deg" schSectionName={S} />
    <trace from=".C_BLOUT > .pin1" to="net.BL_A" width={W_PWR_LITE} />
    <trace from=".C_BLOUT > .pin2" to="net.GND" width={W_PWR_LITE} />
    <capacitor name="C_BLF1" capacitance="470nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C106255"] }}
      schX={14} schY={-10} schSectionName={S} />
    <trace from=".C_BLF1 > .pin1" to="net.BL_C1P" />
    <trace from=".C_BLF1 > .pin2" to="net.BL_C1N" />
    <capacitor name="C_BLF2" capacitance="470nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C106255"] }}
      schX={14} schY={-12} schSectionName={S} />
    <trace from=".C_BLF2 > .pin1" to="net.BL_C2P" />
    <trace from=".C_BLF2 > .pin2" to="net.BL_C2N" />
  </group>
)
