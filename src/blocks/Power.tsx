import { W_PWR, W_PWR_LITE } from "../widths"

const S = "Power"

/**
 * Power tree. The charger, the 3.3 V buck-boost and the always-on LDO are the
 * GridCore Nano's parts and values, already proven on that board.
 *
 *   USB-C VBUS -> BQ25185 IN -> SYS ---------------> TPS63802 VIN, EN = soft latch -> V3V3
 *                       |        \--> TPS60230 backlight pump (EN from the MCU)
 *                       \-- BAT <- JST-PH <- protected 1S LiPo pouch
 *                            \--> MAX17048 battery gauge (I2C)
 *
 * Soft latch: the power button pulls TPS63802 EN to SYS through one diode of a
 * BAT54C; the MCU holds it on through the other (PWR_HOLD) and lets go to power
 * off. A 2N7002 turns the button into BTN_N for the MCU, so the button node
 * (up to 5 V on USB) never touches an MCU pin directly, and an unpowered MCU is
 * never back-fed through a GPIO.
 *
 * The cell is a PROTECTED pouch (PCM in the pack), as on the Nano: no second
 * protection FET stack on the board.
 */
export const PowerBlock = () => (
  <group>
    {/* ---------------- USB-C sink (USB 2.0 device) ---------------- */}
    <chip name="J1" footprint="pinrow13"
      manufacturerPartNumber="TYPE-C-31-M-12" supplierPartNumbers={{ jlcpcb: ["C165948"] }}
      pinLabels={{ pin1: "VBUS_A", pin2: "VBUS_B", pin3: "GND_A", pin4: "GND_B", pin5: "CC1", pin6: "CC2",
        pin7: "DP1", pin8: "DN1", pin9: "DP2", pin10: "DN2", pin11: "SBU1", pin12: "SBU2", pin13: "SHIELD" }}
      schX={-14} schY={10} schSectionName={S} />
    <trace from=".J1 > .VBUS_A" to="net.VBUS" width={W_PWR} />
    <trace from=".J1 > .VBUS_B" to="net.VBUS" width={W_PWR} />
    <trace from=".J1 > .GND_A" to="net.GND" width={W_PWR} />
    <trace from=".J1 > .GND_B" to="net.GND" width={W_PWR} />
    <trace from=".J1 > .SHIELD" to="net.GND" width={W_PWR} />
    {/* A6/B6 are both D+, A7/B7 both D-: the receptacle is reversible only if both pairs are joined. */}
    <trace from=".J1 > .DP1" to="net.USB_DP" />
    <trace from=".J1 > .DP2" to="net.USB_DP" />
    <trace from=".J1 > .DN1" to="net.USB_DN" />
    <trace from=".J1 > .DN2" to="net.USB_DN" />
    {/* SBU unused; each on its own net so nothing routes them together. */}
    <trace from=".J1 > .SBU1" to="net.NC_SBU1" />
    <trace from=".J1 > .SBU2" to="net.NC_SBU2" />
    <resistor name="R_CC1" resistance="5.1k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25905"] }}
      schX={-17} schY={12} schRotation="90deg" schSectionName={S} />
    <resistor name="R_CC2" resistance="5.1k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25905"] }}
      schX={-11} schY={12} schRotation="90deg" schSectionName={S} />
    <trace from=".J1 > .CC1" to="net.CC1" />
    <trace from=".R_CC1 > .pin1" to="net.CC1" />
    <trace from=".R_CC1 > .pin2" to="net.GND" />
    <trace from=".J1 > .CC2" to="net.CC2" />
    <trace from=".R_CC2 > .pin1" to="net.CC2" />
    <trace from=".R_CC2 > .pin2" to="net.GND" />

    {/* D+/D- ESD on the connector side of the airgap switch. */}
    <chip name="D_ESD" footprint="pinrow6"
      pinLabels={{ pin1: "IO1", pin2: "GND", pin3: "IO2", pin4: "IO2B", pin5: "VBUS", pin6: "IO1B" }}
      manufacturerPartNumber="USBLC6-2SC6" supplierPartNumbers={{ jlcpcb: ["C7519"] }}
      schX={-17} schY={7} schSectionName={S} />
    <trace from=".D_ESD > .IO1" to="net.USB_DP" />
    <trace from=".D_ESD > .IO1B" to="net.USB_DP" />
    <trace from=".D_ESD > .IO2" to="net.USB_DN" />
    <trace from=".D_ESD > .IO2B" to="net.USB_DN" />
    <trace from=".D_ESD > .GND" to="net.GND" />
    <trace from=".D_ESD > .VBUS" to="net.VBUS" />

    {/* VBUS presence for the USB device stack: 5 V -> 3.4 V on the OTG_FS_VBUS pin. */}
    <resistor name="R_VB1" resistance="47k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25792"] }}
      schX={-14} schY={4} schRotation="90deg" schSectionName={S} />
    <resistor name="R_VB2" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={-14} schY={2} schRotation="90deg" schSectionName={S} />
    <trace from=".R_VB1 > .pin1" to="net.VBUS" />
    <trace from=".R_VB1 > .pin2" to="net.VBUS_SENSE" />
    <trace from=".R_VB2 > .pin1" to="net.VBUS_SENSE" />
    <trace from=".R_VB2 > .pin2" to="net.GND" />

    {/* ---------------- Charger (BQ25185, as on the Nano) ---------------- */}
    <chip name="U1" footprint="pinrow11"
      pinLabels={{ pin1: "SYS", pin2: "BAT", pin3: "STAT2", pin4: "nCE", pin5: "GND", pin6: "TS_MR",
        pin7: "ILIM_VSET", pin8: "ISET", pin9: "STAT1", pin10: "IN", pin11: "EP" }}
      manufacturerPartNumber="BQ25185DLHR" supplierPartNumbers={{ jlcpcb: ["C19725033"] }}
      schX={-8} schY={10} schSectionName={S} />
    <trace from=".U1 > .IN" to="net.VBUS" width={W_PWR} />
    <trace from=".U1 > .SYS" to="net.SYS" width={W_PWR} />
    <trace from=".U1 > .BAT" to="net.BAT" width={W_PWR} />
    <trace from=".U1 > .GND" to="net.GND" width={W_PWR} />
    <trace from=".U1 > .EP" to="net.GND" width={W_PWR} />
    {/* IN needs >= 1 uF after derating (SLUSF65B); USB allows <= 10 uF. */}
    <capacitor name="C_VBUS" capacitance="4.7uF" footprint="0603" supplierPartNumbers={{ jlcpcb: ["C19666"] }}
      schX={-11} schY={7} schRotation="90deg" schSectionName={S} />
    <trace from=".C_VBUS > .pin1" to="net.VBUS" width={W_PWR} />
    <trace from=".C_VBUS > .pin2" to="net.GND" width={W_PWR} />
    {/* 13k: VBATREG 4.2 V, 1.1 A input limit (Table 8-1), the Nano's value. */}
    <resistor name="R_ILIM" resistance="13k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25754"] }}
      schX={-5} schY={12} schRotation="90deg" schSectionName={S} />
    <trace from=".U1 > .ILIM_VSET" to="net.CHG_ILIM" />
    <trace from=".R_ILIM > .pin1" to="net.CHG_ILIM" />
    <trace from=".R_ILIM > .pin2" to="net.GND" />
    {/* ICHG = 300 A*Ohm / RISET: 1.2k -> 250 mA, 0.5 C for a ~500 mAh pouch and
        far above the 264 Ohm short-detect trip. */}
    <resistor name="R_ISET" resistance="1.2k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25862"] }}
      schX={-3} schY={12} schRotation="90deg" schSectionName={S} />
    <trace from=".U1 > .ISET" to="net.CHG_ISET" />
    <trace from=".R_ISET > .pin1" to="net.CHG_ISET" />
    <trace from=".R_ISET > .pin2" to="net.GND" />
    {/* 10k to GND = "no NTC" (pouch without thermistor). */}
    <resistor name="R_TS" resistance="10k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25744"] }}
      schX={-5} schY={8} schRotation="90deg" schSectionName={S} />
    <trace from=".U1 > .TS_MR" to="net.CHG_TS" />
    <trace from=".R_TS > .pin1" to="net.CHG_TS" />
    <trace from=".R_TS > .pin2" to="net.GND" />
    {/* /CE: pulled down (charging on), the MCU can pause charging. */}
    <resistor name="R_CE" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={-7} schY={6} schRotation="90deg" schSectionName={S} />
    <trace from=".U1 > .nCE" to="net.CHG_CE" />
    <trace from=".R_CE > .pin1" to="net.CHG_CE" />
    <trace from=".R_CE > .pin2" to="net.GND" />
    {/* STAT1/STAT2 are open drain (Table 4-1: 1k-20k pull-up); both read, unlike the Nano. */}
    <resistor name="R_STAT1" resistance="10k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25744"] }}
      schX={-5} schY={4} schRotation="90deg" schSectionName={S} />
    <resistor name="R_STAT2" resistance="10k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25744"] }}
      schX={-3} schY={4} schRotation="90deg" schSectionName={S} />
    <trace from=".U1 > .STAT1" to="net.CHG_STAT1" />
    <trace from=".U1 > .STAT2" to="net.CHG_STAT2" />
    <trace from=".R_STAT1 > .pin1" to="net.CHG_STAT1" />
    <trace from=".R_STAT2 > .pin1" to="net.CHG_STAT2" />
    <trace from=".R_STAT1 > .pin2" to="net.V3V3" />
    <trace from=".R_STAT2 > .pin2" to="net.V3V3" />
    {/* SYS 1-100 uF (7.2.2.3). */}
    <capacitor name="C_SYS" capacitance="22uF" footprint="0805" supplierPartNumbers={{ jlcpcb: ["C45783"] }}
      schX={-8} schY={7} schRotation="90deg" schSectionName={S} />
    <trace from=".C_SYS > .pin1" to="net.SYS" width={W_PWR} />
    <trace from=".C_SYS > .pin2" to="net.GND" width={W_PWR} />
    <capacitor name="C_BAT" capacitance="10uF" footprint="0805" supplierPartNumbers={{ jlcpcb: ["C15850"] }}
      schX={-10} schY={7} schRotation="90deg" schSectionName={S} />
    <trace from=".C_BAT > .pin1" to="net.BAT" width={W_PWR} />
    <trace from=".C_BAT > .pin2" to="net.GND" width={W_PWR} />

    {/* Cell on a 2-pin JST-PH pigtail; the lead wraps the board edge onto the
        component face, the pouch itself sits under the display. */}
    <chip name="J_BAT" footprint="pinrow4"
      manufacturerPartNumber="S2B-PH-SM4-TB" supplierPartNumbers={{ jlcpcb: ["C295747"] }}
      pinLabels={{ pin1: "POS", pin2: "NEG", pin3: "MP1", pin4: "MP2" }}
      schX={-20} schY={4} schSectionName={S} />
    <trace from=".J_BAT > .POS" to="net.BAT" width={W_PWR} />
    <trace from=".J_BAT > .NEG" to="net.GND" width={W_PWR} />
    <trace from=".J_BAT > .MP1" to="net.GND" />
    <trace from=".J_BAT > .MP2" to="net.GND" />

    {/* ---------------- Soft latch + 3.3 V buck-boost ---------------- */}
    <chip name="SW_PWR" footprint="pinrow3"
      manufacturerPartNumber="SKRTLAE010" supplierPartNumbers={{ jlcpcb: ["C110293"] }}
      pinLabels={{ pin1: "A", pin2: "B", pin3: "MP" }}
      schX={-2} schY={14} schSectionName={S} />
    <trace from=".SW_PWR > .A" to="net.SYS" width={W_PWR_LITE} />
    <trace from=".SW_PWR > .B" to="net.BTN" width={W_PWR_LITE} />
    <trace from=".SW_PWR > .MP" to="net.GND" />
    <resistor name="R_BTN" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={0} schY={14} schRotation="90deg" schSectionName={S} />
    <trace from=".R_BTN > .pin1" to="net.BTN" />
    <trace from=".R_BTN > .pin2" to="net.GND" />
    {/* BAT54C: common cathode on EN; anode 1 = button, anode 2 = PWR_HOLD. */}
    <chip name="D_OR" footprint="sot23"
      manufacturerPartNumber="BAT54C,215" supplierPartNumbers={{ jlcpcb: ["C37704"] }}
      pinLabels={{ pin1: "A1", pin2: "A2", pin3: "K" }}
      schX={2} schY={14} schSectionName={S} />
    <trace from=".D_OR > .A1" to="net.BTN" />
    <trace from=".D_OR > .A2" to="net.PWR_HOLD" />
    <trace from=".D_OR > .K" to="net.PWR_EN" />
    <resistor name="R_EN" resistance="1M" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C26083"] }}
      schX={4} schY={14} schRotation="90deg" schSectionName={S} />
    <trace from=".R_EN > .pin1" to="net.PWR_EN" />
    <trace from=".R_EN > .pin2" to="net.GND" />
    {/* 1 uF x 1M = ~1 s hold-up: every MCU reset (watchdog, brown-out, update,
        SWD connect-under-reset) floats PWR_HOLD, and without this the 3.3 V rail
        would drop before firmware could re-assert it. Power-off = release
        PWR_HOLD and wait ~2 s. */}
    <capacitor name="C_EN" capacitance="1uF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C52923"] }}
      schX={5} schY={14} schRotation="90deg" schSectionName={S} />
    <trace from=".C_EN > .pin1" to="net.PWR_EN" />
    <trace from=".C_EN > .pin2" to="net.GND" />
    {/* Button -> BTN_N (active low) for the MCU's WKUP1 pin. */}
    <chip name="Q_BTN" footprint="sot23"
      manufacturerPartNumber="2N7002" supplierPartNumbers={{ jlcpcb: ["C8545"] }}
      pinLabels={{ pin1: "G", pin2: "S", pin3: "D" }}
      schX={2} schY={17} schSectionName={S} />
    <trace from=".Q_BTN > .G" to="net.BTN" />
    <trace from=".Q_BTN > .S" to="net.GND" />
    <trace from=".Q_BTN > .D" to="net.BTN_N" />
    {/* BTN_N's pull-up is PA0's own (PUPDR, kept through Standby/Shutdown with
        PWR_PUCRA + APC): an external one only mattered while the MCU is unpowered,
        and then nothing reads the pin. */}
    {/* PWR_HOLD low until firmware drives it: the board stays off unless the MCU asks. */}
    <resistor name="R_HOLD" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={4} schY={12} schRotation="90deg" schSectionName={S} />
    <trace from=".R_HOLD > .pin1" to="net.PWR_HOLD" />
    <trace from=".R_HOLD > .pin2" to="net.GND" />

    <chip name="U2" footprint="pinrow10"
      pinLabels={{ pin1: "EN", pin2: "MODE", pin3: "AGND", pin4: "FB", pin5: "PG", pin6: "VOUT",
        pin7: "L2", pin8: "GND", pin9: "L1", pin10: "VIN" }}
      manufacturerPartNumber="TPS63802DLAR" supplierPartNumbers={{ jlcpcb: ["C2845237"] }}
      schX={6} schY={10} schSectionName={S} />
    <trace from=".U2 > .VIN" to="net.SYS" width={W_PWR} />
    <trace from=".U2 > .EN" to="net.PWR_EN" />
    <trace from=".U2 > .MODE" to="net.GND" />
    <trace from=".U2 > .AGND" to="net.GND" />
    <trace from=".U2 > .GND" to="net.GND" width={W_PWR} />
    <trace from=".U2 > .VOUT" to="net.V3V3" width={W_PWR} />
    {/* Vout = 0.5 V * (1 + 560k/100k) = 3.3 V (the Nano's divider). */}
    <resistor name="R_FB1" resistance="560k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C132339"] }}
      schX={9} schY={8} schRotation="90deg" schSectionName={S} />
    <resistor name="R_FB2" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={9} schY={6} schRotation="90deg" schSectionName={S} />
    <trace from=".R_FB1 > .pin1" to="net.V3V3" />
    <trace from=".R_FB1 > .pin2" to="net.FB_3V3" />
    <trace from=".R_FB2 > .pin1" to="net.FB_3V3" />
    <trace from=".R_FB2 > .pin2" to="net.GND" />
    <trace from=".U2 > .FB" to="net.FB_3V3" />
    {/* 0.47 uH: the TPS63802 wants 0.37-0.57 uH effective (SLVSEU9D Table 10-1). */}
    <chip name="L_BUCK" footprint="pinrow2"
      manufacturerPartNumber="DFE201612E-R47M=P2" supplierPartNumbers={{ jlcpcb: ["C668312"] }}
      pinLabels={{ pin1: "pin1", pin2: "pin2" }}
      schX={8} schY={12} schSectionName={S} />
    <trace from=".U2 > .L1" to="net.L1_3V3" width={W_PWR} />
    <trace from=".L_BUCK > .pin1" to="net.L1_3V3" width={W_PWR} />
    <trace from=".L_BUCK > .pin2" to="net.L2_3V3" width={W_PWR} />
    <trace from=".U2 > .L2" to="net.L2_3V3" width={W_PWR} />
    <capacitor name="C_IN_BUCK" capacitance="10uF" footprint="0805" supplierPartNumbers={{ jlcpcb: ["C15850"] }}
      schX={4} schY={8} schRotation="90deg" schSectionName={S} />
    <trace from=".C_IN_BUCK > .pin1" to="net.SYS" width={W_PWR} />
    <trace from=".C_IN_BUCK > .pin2" to="net.GND" width={W_PWR} />
    <capacitor name="C_3V3" capacitance="22uF" footprint="0805" supplierPartNumbers={{ jlcpcb: ["C45783"] }}
      schX={11} schY={10} schRotation="90deg" schSectionName={S} />
    <trace from=".C_3V3 > .pin1" to="net.V3V3" width={W_PWR} />
    <trace from=".C_3V3 > .pin2" to="net.GND" width={W_PWR} />

    {/* ---------------- Battery gauge ---------------- */}
    {/* MAX17048G+T10, TDFN-8 2x2 (Maxim 19-6171 Rev 7, Pin Description):
        1 CTG -> GND, 2 CELL (not connected inside the 17048; tied to the cell as in
        Fig. 14), 3 VDD = supply + sense, 4 GND, 5 ALRT (open drain), 6 QSTRT -> GND,
        7 SCL, 8 SDA, EP -> GND. Powered from the cell. With the 3.3 V rail off its
        bus reads low; it only sleeps on that if firmware set MODE.EnSleep, so
        firmware puts it in hibernate (~3 uA, still gauging) before power-off. */}
    <chip name="U15" footprint="pinrow9"
      manufacturerPartNumber="MAX17048G+T10" supplierPartNumbers={{ jlcpcb: ["C2682616"] }}
      pinLabels={{ pin1: "CTG", pin2: "CELL", pin3: "VDD", pin4: "GND", pin5: "ALRT", pin6: "QSTRT",
        pin7: "SCL", pin8: "SDA", pin9: "EP" }}
      schX={-14} schY={-2} schSectionName={S} />
    <trace from=".U15 > .CTG" to="net.GND" />
    <trace from=".U15 > .CELL" to="net.BAT" />
    <trace from=".U15 > .VDD" to="net.BAT" />
    <trace from=".U15 > .GND" to="net.GND" />
    <trace from=".U15 > .QSTRT" to="net.GND" />
    <trace from=".U15 > .EP" to="net.GND" />
    {/* ALRT unused: firmware polls the gauge (it is read with every redraw anyway). */}
    <trace from=".U15 > .ALRT" to="net.NC_GAUGE_ALRT" />
    <trace from=".U15 > .SCL" to="net.I2C_SCL" />
    <trace from=".U15 > .SDA" to="net.I2C_SDA" />
    <capacitor name="C_GAUGE" capacitance="100nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C1525"] }}
      schX={-12} schY={-2} schRotation="90deg" schSectionName={S} />
    <trace from=".C_GAUGE > .pin1" to="net.BAT" />
    <trace from=".C_GAUGE > .pin2" to="net.GND" />

  </group>
)
