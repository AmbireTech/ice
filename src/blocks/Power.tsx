import { W_PWR, W_PWR_LITE } from "../widths"

const S = "Power"

/**
 * Power tree. Two AAA cells in series, of any common chemistry, in a pack on the
 * back that a magnetic pogo pair connects (docs/pack.md); a boost that runs from
 * all of them, and USB that powers the board and charges the pack only once the
 * MCU has qualified both cells as NiMH.
 *
 *   USB-C VBUS -> TLV62569P buck -> V3V3_USB --VIN1--+
 *          |                            PG -> PR1    | TPS2116 (USB first) -> V3V3
 *          \-> BQ25172 (2 cells) ---------------------+-> BAT (NiMH only, MCU-gated)
 *   pack: BAT -> LTC3539-2 boost, SHDN = soft latch -> V_BOOST --VIN2--+
 *         BAT_N -> AO3416 (reverse-cell blocker) -> GND
 *   the pack's plus, minus and mid-point, and its NTC (TS) -> MCU ADC
 *
 * The backlight pump and everything else run from V3V3; there is no battery
 * gauge (a Li-ion gauge cannot read a NiMH or alkaline cell): firmware reads the
 * cells' voltages and the pack's temperature itself.
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

    {/* ---------------- USB 3.3 V: buck (TLV62569P) ---------------- */}
    {/* TLV62569PDDC, SOT-23-6 (SLVSDT2 Pin Functions): 1 EN, 2 GND, 3 SW, 4 VIN, 5 PG, 6 FB.
        Runs whenever VBUS is there: on USB the board is powered, and the MCU decides
        whether the screen comes on. C_VBUS is its input cap and the only one on VBUS
        (USB allows <= 10 uF). */}
    <chip name="U3" footprint="pinrow6"
      pinLabels={{ pin1: "EN", pin2: "GND", pin3: "SW", pin4: "VIN", pin5: "PG", pin6: "FB" }}
      manufacturerPartNumber="TLV62569PDDCR" supplierPartNumbers={{ jlcpcb: ["C398365"] }}
      schX={-8} schY={10} schSectionName={S} />
    <trace from=".U3 > .VIN" to="net.VBUS" width={W_PWR} />
    <trace from=".U3 > .EN" to="net.VBUS" />
    <trace from=".U3 > .GND" to="net.GND" width={W_PWR} />
    <trace from=".U3 > .SW" to="net.SW_USB" width={W_PWR} />
    <trace from=".U3 > .FB" to="net.FB_USB" />
    <trace from=".U3 > .PG" to="net.USB_PG" />
    <capacitor name="C_VBUS" capacitance="4.7uF" footprint="0603" supplierPartNumbers={{ jlcpcb: ["C19666"] }}
      schX={-11} schY={7} schRotation="90deg" schSectionName={S} />
    <trace from=".C_VBUS > .pin1" to="net.VBUS" width={W_PWR} />
    <trace from=".C_VBUS > .pin2" to="net.GND" width={W_PWR} />
    {/* 2.2 uH, 1.3 A sat (the datasheet's 2.2 uH, 0.5 A here at most: rail + 250 mA charge). */}
    <chip name="L_BUCK" footprint="pinrow2"
      manufacturerPartNumber="CKCSA201610-2.2uH/M" supplierPartNumbers={{ jlcpcb: ["C5291913"] }}
      pinLabels={{ pin1: "pin1", pin2: "pin2" }}
      schX={-6} schY={12} schSectionName={S} />
    <trace from=".L_BUCK > .pin1" to="net.SW_USB" width={W_PWR} />
    <trace from=".L_BUCK > .pin2" to="net.V3V3_USB" width={W_PWR} />
    {/* Vout = 0.6 V * (1 + 150k/33k) = 3.33 V. */}
    <resistor name="R_FBU1" resistance="150k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25755"] }}
      schX={-5} schY={8} schRotation="90deg" schSectionName={S} />
    <resistor name="R_FBU2" resistance="33k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25779"] }}
      schX={-5} schY={6} schRotation="90deg" schSectionName={S} />
    <trace from=".R_FBU1 > .pin1" to="net.V3V3_USB" />
    <trace from=".R_FBU1 > .pin2" to="net.FB_USB" />
    <trace from=".R_FBU2 > .pin1" to="net.FB_USB" />
    <trace from=".R_FBU2 > .pin2" to="net.GND" />
    <capacitor name="C_USB3V3" capacitance="22uF" footprint="0805" supplierPartNumbers={{ jlcpcb: ["C45783"] }}
      schX={-4} schY={10} schRotation="90deg" schSectionName={S} />
    <trace from=".C_USB3V3 > .pin1" to="net.V3V3_USB" width={W_PWR} />
    <trace from=".C_USB3V3 > .pin2" to="net.GND" width={W_PWR} />
    {/* PG (open drain, high at 95 % of Vout, low under 90 %) selects USB in the mux:
        the cell takes over before the USB rail has sagged to 3.0 V. PG's pull-up is a
        divider from VBUS, not a resistor to V3V3_USB: PG goes high-impedance once VIN
        falls under 1.4 V (SLVSDG1C Table 1, "Power Supply Removal"), and a pull-up to the
        slower-decaying V3V3_USB would then lift PR1 over VREF (0.92-1.08 V) and switch the
        mux back to the dead USB input, sagging V3V3 until it reset the MCU (schematic
        review, 2026-10-22). 150k/100k: PR1 1.76 V at a 4.4 V VBUS, 0.56 V at 1.4 V. */}
    <resistor name="R_PG" resistance="150k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25755"] }}
      schX={-3} schY={12} schRotation="90deg" schSectionName={S} />
    <trace from=".R_PG > .pin1" to="net.USB_PG" />
    <trace from=".R_PG > .pin2" to="net.VBUS" />
    <resistor name="R_PGB" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={-2} schY={12} schRotation="90deg" schSectionName={S} />
    <trace from=".R_PGB > .pin1" to="net.USB_PG" />
    <trace from=".R_PGB > .pin2" to="net.GND" />

    {/* ---------------- Rail select (TPS2116) ---------------- */}
    {/* TPS2116DRL, SOT-583 (SLVSFG1A Table 5-1): 1 GND, 2/7 VOUT, 3 VIN1, 4 PR1, 5 MODE,
        6 VIN2, 8 ST. Priority mode (MODE on VIN1): USB (VIN1) whenever PR1 > 1.0 V, else the
        boosted cell (VIN2). Blocks reverse current from VOUT into either input, so the
        buck and the boost both sit at 3.3 V with no setpoint gap and nothing back-feeds
        VBUS or the cell. ST (USB not in use) is left open: VBUS_SENSE already tells. */}
    <chip name="U10" footprint="pinrow8"
      pinLabels={{ pin1: "GND", pin2: "VOUT_A", pin3: "VIN1", pin4: "PR1", pin5: "MODE", pin6: "VIN2", pin7: "VOUT_B", pin8: "ST" }}
      manufacturerPartNumber="TPS2116DRLR" supplierPartNumbers={{ jlcpcb: ["C3235557"] }}
      schX={0} schY={10} schSectionName={S} />
    <trace from=".U10 > .GND" to="net.GND" width={W_PWR} />
    <trace from=".U10 > .VOUT_A" to="net.V3V3" width={W_PWR} />
    <trace from=".U10 > .VOUT_B" to="net.V3V3" width={W_PWR} />
    <trace from=".U10 > .VIN1" to="net.V3V3_USB" width={W_PWR} />
    <trace from=".U10 > .MODE" to="net.V3V3_USB" />
    <trace from=".U10 > .PR1" to="net.USB_PG" />
    <trace from=".U10 > .VIN2" to="net.V_BOOST" width={W_PWR} />
    <trace from=".U10 > .ST" to="net.NC_MUX_ST" />
    <capacitor name="C_3V3" capacitance="22uF" footprint="0805" supplierPartNumbers={{ jlcpcb: ["C45783"] }}
      schX={2} schY={10} schRotation="90deg" schSectionName={S} />
    <trace from=".C_3V3 > .pin1" to="net.V3V3" width={W_PWR} />
    <trace from=".C_3V3 > .pin2" to="net.GND" width={W_PWR} />

    {/* ---------------- The pack: two AAA in series, on the back ---------------- */}
    {/* Xinyangze YZ103915020T-04025-02 (C6276862): 4-pin SMT magnetic pogo receptacle on
        the back, east of the MCU; the pack's magnetic male (pack/) lands on it and their
        magnets hold the pack. Contacts north to south: the pack's plus, its mid-point,
        its thermistor, its minus. Each cell may be NiMH (1.0-1.45 V) or an alkaline or
        lithium (Li-FeS2) primary (0.9-1.8 V): the pack is 1.8-3.6 V; the boost runs
        from all of them, the charger only ever charges a pack whose two cells the MCU
        has qualified as NiMH. */}
    <chip name="J_PACK" footprint="pinrow4"
      pinLabels={{ pin1: "BAT_P", pin2: "MID", pin3: "NTC", pin4: "BAT_M" }}
      manufacturerPartNumber="YZ103915020T-04025-02" supplierPartNumbers={{ jlcpcb: ["C6276862"] }}
      schX={-20} schY={4} schSectionName={S} />
    <trace from=".J_PACK > .BAT_P" to="net.BAT" width={W_PWR} />
    <trace from=".J_PACK > .BAT_M" to="net.BAT_N" width={W_PWR} />
    <trace from=".J_PACK > .MID" to="net.CELL_MID" />
    <trace from=".J_PACK > .NTC" to="net.CHG_TS" />
    {/* Reverse cell: nothing stops a cell going in backwards, and the boost's VIN is
        rated to -0.3 V, so a reversed AAA would discharge through its internal diodes.
        AO3416 in the cell's low side: its body diode conducts a correctly inserted cell
        from the first instant, the gate follows the cell (Vth 0.4-1.1 V) and, once the
        boost runs, the boost's own output through D_REVG (~2.7 V: ~25 mOhm). Reversed,
        the gate sits at or below the source and the body diode blocks. The boost output
        is used, not the 3.3 V rail: on USB the rail is up even with a reversed cell, and
        driving the gate from it would connect that cell. While the boost runs, R_REVG
        returns ~1.2 uA from the gate into the cell, under the 2 uA reverse current
        Energizer allows a lithium primary (L92 datasheet); the sense divider draws
        0.9 uA back out. */}
    <chip name="Q_REV" footprint="sot23"
      manufacturerPartNumber="AO3416" supplierPartNumbers={{ jlcpcb: ["C43307"] }}
      pinLabels={{ pin1: "G", pin2: "S", pin3: "D" }}
      schX={-18} schY={2} schSectionName={S} />
    <trace from=".Q_REV > .D" to="net.BAT_N" width={W_PWR} />
    <trace from=".Q_REV > .S" to="net.GND" width={W_PWR} />
    <trace from=".Q_REV > .G" to="net.REV_G" />
    <resistor name="R_REVG" resistance="1M" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C26083"] }}
      schX={-16} schY={2} schRotation="90deg" schSectionName={S} />
    <trace from=".R_REVG > .pin1" to="net.BAT" />
    <trace from=".R_REVG > .pin2" to="net.REV_G" />
    {/* BAS116 (SOT-23: 1 anode, 2 n.c., 3 cathode), not a Schottky: its reverse leakage
        is picoamps, where an RB521S30 leaks ~0.5 uA from the cell into the boost output
        while the board is off, and ~25x that when warm. */}
    <chip name="D_REVG" footprint="sot23"
      manufacturerPartNumber="BAS116,215" supplierPartNumbers={{ jlcpcb: ["C48502"] }}
      pinLabels={{ pin1: "A", pin2: "NC", pin3: "K" }}
      schX={-16} schY={0} schSectionName={S} />
    <trace from=".D_REVG > .A" to="net.V_BOOST" />
    <trace from=".D_REVG > .K" to="net.REV_G" />
    <trace from=".D_REVG > .NC" to="net.NC_REVG" />

    {/* The pack's voltage at the receptacle's + contact (route its own trace from the
        pad: no load current in it) and at its - contact, so firmware reads the cells
        themselves, not the reverse FET: 1M/1M halves the + side (two lithium cells, 3.6 V,
        read 1.8 V); 100k into the - side limits what a reversed pack can push into the
        pin. 100 nF each: the ADC sees a stiff source. Always on: 0.9 uA. */}
    <resistor name="R_CS1" resistance="1M" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C26083"] }}
      schX={-14} schY={0} schRotation="90deg" schSectionName={S} />
    <resistor name="R_CS2" resistance="1M" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C26083"] }}
      schX={-14} schY={-2} schRotation="90deg" schSectionName={S} />
    <trace from=".R_CS1 > .pin1" to="net.BAT" />
    <trace from=".R_CS1 > .pin2" to="net.CELL_SENSE" />
    <trace from=".R_CS2 > .pin1" to="net.CELL_SENSE" />
    <trace from=".R_CS2 > .pin2" to="net.GND" />
    <capacitor name="C_CS" capacitance="100nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C1525"] }}
      schX={-13} schY={-2} schRotation="90deg" schSectionName={S} />
    <trace from=".C_CS > .pin1" to="net.CELL_SENSE" />
    <trace from=".C_CS > .pin2" to="net.GND" />
    <resistor name="R_CNS" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={-12} schY={0} schRotation="90deg" schSectionName={S} />
    <trace from=".R_CNS > .pin1" to="net.BAT_N" />
    <trace from=".R_CNS > .pin2" to="net.CELL_N_SENSE" />
    <capacitor name="C_CNS" capacitance="100nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C1525"] }}
      schX={-11} schY={-2} schRotation="90deg" schSectionName={S} />
    <trace from=".C_CNS > .pin1" to="net.CELL_N_SENSE" />
    <trace from=".C_CNS > .pin2" to="net.GND" />
    {/* The mid-point between the two cells, halved the same way, so firmware reads each
        cell on its own (mid - minus, plus - mid). It draws 0.4-0.9 uA from the minus
        cell only. With a cell in backwards the mid-point can sit below GND: 1M limits
        what flows out of the pin's clamp to under 1 uA. */}
    <resistor name="R_MS1" resistance="1M" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C26083"] }}
      schX={-10} schY={-6} schRotation="90deg" schSectionName={S} />
    <resistor name="R_MS2" resistance="1M" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C26083"] }}
      schX={-10} schY={-8} schRotation="90deg" schSectionName={S} />
    <trace from=".R_MS1 > .pin1" to="net.CELL_MID" />
    <trace from=".R_MS1 > .pin2" to="net.CELL_MID_SENSE" />
    <trace from=".R_MS2 > .pin1" to="net.CELL_MID_SENSE" />
    <trace from=".R_MS2 > .pin2" to="net.GND" />
    <capacitor name="C_MS" capacitance="100nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C1525"] }}
      schX={-9} schY={-8} schRotation="90deg" schSectionName={S} />
    <trace from=".C_MS > .pin1" to="net.CELL_MID_SENSE" />
    <trace from=".C_MS > .pin2" to="net.GND" />

    {/* ---------------- Cell boost (LTC3539-2) ---------------- */}
    {/* LTC3539-2, DCB8 (datasheet Pin Functions): 1 SW, 2 PGND, 3 GND, 4 VIN, 5 SHDN,
        6 FB, 7 MODE, 8 VOUT, 9 EP = GND. Starts at 0.7 V (0.88 V max), runs down to
        0.5 V, 2 A switch, true output disconnect, and keeps regulating when VIN > VOUT
        ("VIN > VOUT Operation"), so a 10440 Li-ion at 4.2 V never reaches the rail.
        No Schottky SW-VOUT: it would defeat exactly that. MODE on VOUT: Burst Mode at
        light load (10 uA). Stays on while USB is in (when the MCU holds PWR_HOLD), so
        pulling the cable hands over to the cell without a gap. */}
    <chip name="U2" footprint="pinrow9"
      pinLabels={{ pin1: "SW", pin2: "PGND", pin3: "GND", pin4: "VIN", pin5: "SHDN", pin6: "FB", pin7: "MODE",
        pin8: "VOUT", pin9: "EP" }}
      manufacturerPartNumber="LTC3539EDCB-2#TRPBF" supplierPartNumbers={{ jlcpcb: ["C462652"] }}
      schX={6} schY={10} schSectionName={S} />
    <trace from=".U2 > .VIN" to="net.BAT" width={W_PWR} />
    <trace from=".U2 > .SW" to="net.SW_BOOST" width={W_PWR} />
    <trace from=".U2 > .PGND" to="net.GND" width={W_PWR} />
    <trace from=".U2 > .GND" to="net.GND" />
    <trace from=".U2 > .EP" to="net.GND" width={W_PWR} />
    <trace from=".U2 > .SHDN" to="net.PWR_EN" />
    <trace from=".U2 > .FB" to="net.FB_BOOST" />
    <trace from=".U2 > .MODE" to="net.V_BOOST" />
    <trace from=".U2 > .VOUT" to="net.V_BOOST" width={W_PWR} />
    {/* 2.2 uH (1.5-2.5 uH for the -2), 48 mOhm: at ~1.2 A from one cell every 10 mOhm
        in this path costs ~1 % of runtime. */}
    <chip name="L_BOOST" footprint="pinrow2"
      manufacturerPartNumber="WPN4020H2R2MT" supplierPartNumbers={{ jlcpcb: ["C98361"] }}
      pinLabels={{ pin1: "pin1", pin2: "pin2" }}
      schX={8} schY={12} schSectionName={S} />
    <trace from=".L_BOOST > .pin1" to="net.BAT" width={W_PWR} />
    <trace from=".L_BOOST > .pin2" to="net.SW_BOOST" width={W_PWR} />
    {/* Vout = 1.2 V * (1 + 1M/562k) = 3.34 V (the datasheet's 3.3 V divider). */}
    <resistor name="R_FB1" resistance="1M" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C26083"] }}
      schX={9} schY={8} schRotation="90deg" schSectionName={S} />
    <resistor name="R_FB2" resistance="562k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C48838"] }}
      schX={9} schY={6} schRotation="90deg" schSectionName={S} />
    <trace from=".R_FB1 > .pin1" to="net.V_BOOST" />
    <trace from=".R_FB1 > .pin2" to="net.FB_BOOST" />
    <trace from=".R_FB2 > .pin1" to="net.FB_BOOST" />
    <trace from=".R_FB2 > .pin2" to="net.GND" />
    <capacitor name="C_IN_BOOST" capacitance="22uF" footprint="0805" supplierPartNumbers={{ jlcpcb: ["C45783"] }}
      schX={4} schY={8} schRotation="90deg" schSectionName={S} />
    <trace from=".C_IN_BOOST > .pin1" to="net.BAT" width={W_PWR} />
    <trace from=".C_IN_BOOST > .pin2" to="net.GND" width={W_PWR} />
    <capacitor name="C_BOOST" capacitance="22uF" footprint="0805" supplierPartNumbers={{ jlcpcb: ["C45783"] }}
      schX={11} schY={10} schRotation="90deg" schSectionName={S} />
    <trace from=".C_BOOST > .pin1" to="net.V_BOOST" width={W_PWR} />
    <trace from=".C_BOOST > .pin2" to="net.GND" width={W_PWR} />
    {/* The LTC3539's loop wants >= 22 uF of real output capacitance and a 33 pF
        feedforward across the top divider resistor ("usually required", 35392fc
        Output and Input Capacitor Selection); one 22 uF 0805 keeps ~11 uF at 3.3 V. */}
    <capacitor name="C_BOOST2" capacitance="22uF" footprint="0805" supplierPartNumbers={{ jlcpcb: ["C45783"] }}
      schX={12} schY={10} schRotation="90deg" schSectionName={S} />
    <trace from=".C_BOOST2 > .pin1" to="net.V_BOOST" width={W_PWR} />
    <trace from=".C_BOOST2 > .pin2" to="net.GND" width={W_PWR} />
    <capacitor name="C_FFB" capacitance="33pF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C1562"] }}
      schX={10} schY={8} schRotation="90deg" schSectionName={S} />
    <trace from=".C_FFB > .pin1" to="net.V_BOOST" />
    <trace from=".C_FFB > .pin2" to="net.FB_BOOST" />

    {/* ---------------- Soft latch ---------------- */}
    {/* Power is the joystick's centre press, held: there is no separate power button.
        The press grounds JOY_CEN, which turns on Q_PWRJ, a PNP from the cell into BTN;
        BTN pulls the boost's SHDN up to the cell through one diode of a BAT54C; the MCU
        holds it through the other (PWR_HOLD) and lets go to power off. SHDN's guaranteed
        high is 0.88 V: the board starts from a cell resting at >= ~1.05 V, well below
        where firmware calls a NiMH or alkaline cell empty. An NPN (not a FET: it must
        switch from a 0.9 V cell) turns BTN into BTN_N for the MCU, which is therefore
        also the centre press while running (short press = select, hold = power off).
        The cell (up to 4.2 V) never touches an MCU pin, and an unpowered MCU is never
        back-fed through a GPIO. Off, nothing here draws more than nanoamps.
        With the pack off (USB only) Q_PWRJ has no emitter supply, so the centre press is
        dead: no select, no power off; the device stays on while USB is in. Intended
        (owner, 2026-10-31); firmware must not rely on the centre press on USB only. */}
    <chip name="Q_PWRJ" footprint="sot23"
      manufacturerPartNumber="MMBT3906" supplierPartNumbers={{ jlcpcb: ["C2143"] }}
      pinLabels={{ pin1: "B", pin2: "E", pin3: "C" }}
      schX={-2} schY={14} schSectionName={S} />
    <trace from=".Q_PWRJ > .E" to="net.BAT" width={W_PWR_LITE} />
    <trace from=".Q_PWRJ > .C" to="net.BTN" width={W_PWR_LITE} />
    <trace from=".Q_PWRJ > .B" to="net.PWRJ_B" />
    {/* 1M base-emitter: off unless pressed, and dirt across the contact (> ~5 Mohm)
        cannot turn it on; 100k to the contact: ~4 uA of base current from a 1.0 V cell,
        enough for ~200 uA of collector current into BTN and the 1 uF on SHDN. */}
    <resistor name="R_PWRJBE" resistance="1M" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C26083"] }}
      schX={-3} schY={12} schRotation="90deg" schSectionName={S} />
    <trace from=".R_PWRJBE > .pin1" to="net.BAT" />
    <trace from=".R_PWRJBE > .pin2" to="net.PWRJ_B" />
    <resistor name="R_PWRJB" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={-1} schY={12} schRotation="90deg" schSectionName={S} />
    <trace from=".R_PWRJB > .pin1" to="net.PWRJ_B" />
    <trace from=".R_PWRJB > .pin2" to="net.JOY_CEN" />
    <resistor name="R_BTN" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={0} schY={14} schRotation="90deg" schSectionName={S} />
    <trace from=".R_BTN > .pin1" to="net.BTN" />
    <trace from=".R_BTN > .pin2" to="net.GND" />
    {/* BAT54C: common cathode on SHDN; anode 1 = button, anode 2 = PWR_HOLD. */}
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
    {/* 1 uF x (1M || SHDN's internal 4M) = 0.8 s: from PWR_HOLD's ~3.0 V, SHDN stays over
        0.88 V ~0.6-0.7 s (the 0402's ~0.5-0.7 uF at 3 V) through any MCU reset (watchdog, brown-out, update, SWD
        connect-under-reset). From a button press on a 1.2 V cell it holds ~0.25 s: the
        boardloader takes PWR_HOLD within milliseconds. Power-off = release PWR_HOLD and
        wait ~2 s. */}
    <capacitor name="C_EN" capacitance="1uF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C52923"] }}
      schX={5} schY={14} schRotation="90deg" schSectionName={S} />
    <trace from=".C_EN > .pin1" to="net.PWR_EN" />
    <trace from=".C_EN > .pin2" to="net.GND" />
    {/* BTN -> BTN_N (active low) for the MCU's WKUP1 pin: ~5 uA of base current from a
        0.9 V cell sinks far more than PA0's pull-up sources. */}
    <chip name="Q_BTN" footprint="sot23"
      manufacturerPartNumber="MMBT3904" supplierPartNumbers={{ jlcpcb: ["C20526"] }}
      pinLabels={{ pin1: "B", pin2: "E", pin3: "C" }}
      schX={2} schY={17} schSectionName={S} />
    <resistor name="R_BTNB" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={0} schY={17} schRotation="90deg" schSectionName={S} />
    <trace from=".R_BTNB > .pin1" to="net.BTN" />
    <trace from=".R_BTNB > .pin2" to="net.BTN_B" />
    <trace from=".Q_BTN > .B" to="net.BTN_B" />
    <trace from=".Q_BTN > .E" to="net.GND" />
    <trace from=".Q_BTN > .C" to="net.BTN_N" />
    {/* BTN_N's pull-up is PA0's own (PUPDR, kept through Standby/Shutdown with
        PWR_PUCRA + APC). */}
    {/* PWR_HOLD low until firmware drives it: the board stays off unless the MCU asks. */}
    <resistor name="R_HOLD" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={4} schY={12} schRotation="90deg" schSectionName={S} />
    <trace from=".R_HOLD > .pin1" to="net.PWR_HOLD" />
    <trace from=".R_HOLD > .pin2" to="net.GND" />

    {/* ---------------- NiMH charger (BQ25172) ---------------- */}
    {/* BQ25172DSG, WSON-8 2x2 (SLUSDY5 Table 5-1): 1 IN, 2 ISET, 3 TS, 4 GND, 5 STAT,
        6 TMR, 7 VSET, 8 OUT, EP = GND. Set for two NiMH cells (VSET). Constant current
        only, ends on its safety timer or when the pack passes VOUT_OVP (1.65-1.75 V a
        cell, 3.3-3.5 V): lithium primary cells are never charged, by construction. Fed
        from VBUS: a full two-cell pack (2.9-3.0 V) leaves too little headroom under the
        3.3 V rail for the charge path (0.21-0.36 V at 250 mA, plus 95-175 mV to leave
        sleep). From 5 V it burns about (5 - 2.4) x 0.25 = 0.65 W at the start of a
        charge; it regulates its own die at 125 C (200 mA is the fallback if it runs hot).
        The MCU decides whether both cells are NiMH and match (resting voltages, internal
        resistance from an 83 -> 250 mA step, a timed trial) and terminates on the pack's
        own signs (zero/negative dV, dT/dt from the TS node); the IC is the hardware backstop:
          - TS held low by Q_CHG's FET B (gate pulled up) = charging OFF by default, and off
            again whenever the MCU is in reset or unpowered; the MCU enables it by
            driving CHG_OFF low;
          - TS's NTC in the pack: suspends outside 0-45 C; with the pack off TS floats to
            its 2.3-2.9 V clamp, which reads as too cold: no charging, by construction;
          - TMR 36k: 4 h, ~125 % of an 800 mAh cell at 250 mA (a 0.1 C charge restarts
            it by toggling TS: NiMH takes 0.1 C overcharge);
          - the IC will not start on a pack resting above its recharge threshold (VRECHG,
            ~1.33 V a cell). */}
    <chip name="U1" footprint="pinrow9"
      pinLabels={{ pin1: "IN", pin2: "ISET", pin3: "TS", pin4: "GND", pin5: "STAT", pin6: "TMR", pin7: "VSET",
        pin8: "OUT", pin9: "EP" }}
      manufacturerPartNumber="BQ25172DSGR" supplierPartNumbers={{ jlcpcb: ["C5189434"] }}
      schX={-8} schY={2} schSectionName={S} />
    <trace from=".U1 > .IN" to="net.VBUS" width={W_PWR_LITE} />
    <trace from=".U1 > .OUT" to="net.BAT" width={W_PWR_LITE} />
    <trace from=".U1 > .GND" to="net.GND" />
    <trace from=".U1 > .EP" to="net.GND" />
    <trace from=".U1 > .ISET" to="net.CHG_ISET" />
    <trace from=".U1 > .TS" to="net.CHG_TS" />
    <trace from=".U1 > .STAT" to="net.CHG_STAT" />
    <trace from=".U1 > .TMR" to="net.CHG_TMR" />
    <trace from=".U1 > .VSET" to="net.CHG_VSET" />
    {/* IN and OUT >= 1 uF each, close to the IC. */}
    <capacitor name="C_CHGIN" capacitance="1uF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C52923"] }}
      schX={-10} schY={4} schRotation="90deg" schSectionName={S} />
    <trace from=".C_CHGIN > .pin1" to="net.VBUS" />
    <trace from=".C_CHGIN > .pin2" to="net.GND" />
    <capacitor name="C_BAT" capacitance="10uF" footprint="0805" supplierPartNumbers={{ jlcpcb: ["C15850"] }}
      schX={-10} schY={0} schRotation="90deg" schSectionName={S} />
    <trace from=".C_BAT > .pin1" to="net.BAT" width={W_PWR} />
    <trace from=".C_BAT > .pin2" to="net.GND" width={W_PWR} />
    {/* ICHG = 300 A*Ohm / RISET. 3.6k alone = 83 mA, ~0.1 C: pre-qualification of a
        deeply discharged cell, the resistance test's low step, and the standard charge
        Panasonic rates eneloop with (0.1 C for 15-16 h, which an LSD NiMH tolerates as
        overcharge). With 1.8k switched in parallel by Q_CHG's FET A (CHG_HI), 1.2k = 250 mA (~0.3 C),
        the faster charge that firmware ends on the cell's signs, with the 4 h timer
        and the 45 C NTC behind it. ISET is live: a change applies at once. */}
    <resistor name="R_ISET" resistance="3.6k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25891"] }}
      schX={-6} schY={0} schRotation="90deg" schSectionName={S} />
    <trace from=".R_ISET > .pin1" to="net.CHG_ISET" />
    <trace from=".R_ISET > .pin2" to="net.GND" />
    <resistor name="R_ISETH" resistance="1.8k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25871"] }}
      schX={-5} schY={0} schRotation="90deg" schSectionName={S} />
    <trace from=".R_ISETH > .pin1" to="net.CHG_ISET" />
    <trace from=".R_ISETH > .pin2" to="net.CHG_ISETH" />
    {/* Both charger switches in one dual 2N7002 (SOT-363; pins per the LCSC symbol:
        FET A 1 S, 2 G, 6 D = the 250 mA step; FET B 4 S, 5 G, 3 D = TS hold). */}
    <chip name="Q_CHG" footprint="pinrow6"
      manufacturerPartNumber="2N7002DW-7-F" supplierPartNumbers={{ jlcpcb: ["C83571"] }}
      pinLabels={{ pin1: "S_A", pin2: "G_A", pin3: "D_B", pin4: "S_B", pin5: "G_B", pin6: "D_A" }}
      schX={-4} schY={-2} schSectionName={S} />
    <trace from=".Q_CHG > .D_A" to="net.CHG_ISETH" />
    <trace from=".Q_CHG > .S_A" to="net.GND" />
    <trace from=".Q_CHG > .G_A" to="net.CHG_HI" />
    <resistor name="R_CHGHI" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={-3} schY={-2} schRotation="90deg" schSectionName={S} />
    <trace from=".R_CHGHI > .pin1" to="net.CHG_HI" />
    <trace from=".R_CHGHI > .pin2" to="net.GND" />
    {/* TS: the pack's 10k NTC (B 3380), between its NTC contact and its minus; ITS 38 uA
        trips at ~0 and ~45 C. The MCU reads the same node (CHG_TS, an ADC pin in analog
        mode: high impedance, so the charger's own limits are untouched) for its dT/dt
        termination: while charging, the 38 uA bias makes V_TS = 38 uA x R_NTC. An open
        TS line tells firmware the pack is off. */}
    <trace from=".Q_CHG > .D_B" to="net.CHG_TS" />
    <trace from=".Q_CHG > .S_B" to="net.GND" />
    <trace from=".Q_CHG > .G_B" to="net.CHG_OFF" />
    <resistor name="R_CHGOFF" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={-9} schY={-4} schRotation="90deg" schSectionName={S} />
    <trace from=".R_CHGOFF > .pin1" to="net.CHG_OFF" />
    {/* to the USB 3.3 V rail, not V3V3: the hold is on as soon as the buck starts, also
        before V3V3 comes up. Not VBUS, though the charger runs from it: PB0 is an FT pin,
        rated VDD + 4 V, so 5 V through 100k into an unpowered MCU is outside it; 3.3 V
        is within it. The cost: from plug-in until the buck's output rises (ms), TS is
        not held, and the charger could start at its 83 mA base current. */}
    <trace from=".R_CHGOFF > .pin2" to="net.V3V3_USB" />
    {/* 1 % parts (SLUSDY5: +-1 %, <= 200 ppm/C). */}
    <resistor name="R_TMR" resistance="36k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C43676"] }}
      schX={-11} schY={-4} schRotation="90deg" schSectionName={S} />
    <trace from=".R_TMR > .pin1" to="net.CHG_TMR" />
    <trace from=".R_TMR > .pin2" to="net.GND" />
    {/* 36k: two cells (SLUSDY5's VSET table). */}
    <resistor name="R_VSET" resistance="36k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C43676"] }}
      schX={-12} schY={-4} schRotation="90deg" schSectionName={S} />
    <trace from=".R_VSET > .pin1" to="net.CHG_VSET" />
    <trace from=".R_VSET > .pin2" to="net.GND" />
    {/* STAT open drain: low = charging, 1 Hz blink = fault. */}
    <resistor name="R_STAT" resistance="10k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25744"] }}
      schX={-5} schY={4} schRotation="90deg" schSectionName={S} />
    <trace from=".R_STAT > .pin1" to="net.CHG_STAT" />
    <trace from=".R_STAT > .pin2" to="net.V3V3" />


    {/* ---------------- Buzzer ---------------- */}
    {/* Huaneng MLT-5020: magnetic transducer, 5.0 x 5.5 x 2.0 mm, sound port on top
        (it faces the back of the case: a hole there), 4 kHz, 75 dB at 10 cm, 12 Ohm,
        2-4 Vo-p, 100 mA max (pad 1 +, pad 2 -, pad 3 not connected), in the board's
        north-east corner. The 7.5 mm KLJ-7525 (85 dB) did not fit there: its land
        is 9 mm square and the corner between hole H2 and the touch I2C is 8.6 mm.
        Low side switched by an AO3416 from a timer PWM at 50 % duty (LPTIM3: TIM3
        already clocks the camera); the 1N4148WS takes the coil's flyback. */}
    <chip name="LS1" footprint="pinrow3"
      pinLabels={{ pin1: "P", pin2: "N", pin3: "NC" }}
      manufacturerPartNumber="MLT-5020" supplierPartNumbers={{ jlcpcb: ["C94598"] }}
      schX={10} schY={2} schSectionName={S} />
    <trace from=".LS1 > .P" to="net.V3V3" width={W_PWR_LITE} />
    <trace from=".LS1 > .N" to="net.BUZ_SW" width={W_PWR_LITE} />
    <trace from=".LS1 > .NC" to="net.NC_BUZ" />
    <chip name="Q_BUZ" footprint="sot23"
      manufacturerPartNumber="AO3416" supplierPartNumbers={{ jlcpcb: ["C43307"] }}
      pinLabels={{ pin1: "G", pin2: "S", pin3: "D" }}
      schX={12} schY={0} schSectionName={S} />
    <trace from=".Q_BUZ > .D" to="net.BUZ_SW" width={W_PWR_LITE} />
    <trace from=".Q_BUZ > .S" to="net.GND" width={W_PWR_LITE} />
    <trace from=".Q_BUZ > .G" to="net.BUZ_G" />
    <resistor name="R_BUZG" resistance="100" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25076"] }}
      schX={14} schY={0} schRotation="90deg" schSectionName={S} />
    <trace from=".R_BUZG > .pin1" to="net.BUZZER" />
    <trace from=".R_BUZG > .pin2" to="net.BUZ_G" />
    <resistor name="R_BUZPD" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={14} schY={-2} schRotation="90deg" schSectionName={S} />
    <trace from=".R_BUZPD > .pin1" to="net.BUZ_G" />
    <trace from=".R_BUZPD > .pin2" to="net.GND" />
    <chip name="D_BUZ" footprint="pinrow2"
      manufacturerPartNumber="1N4148WS" supplierPartNumbers={{ jlcpcb: ["C2128"] }}
      pinLabels={{ pin1: "K", pin2: "A" }}
      schX={12} schY={2} schSectionName={S} />
    <trace from=".D_BUZ > .K" to="net.V3V3" />
    <trace from=".D_BUZ > .A" to="net.BUZ_SW" />
    <capacitor name="C_BUZ" capacitance="10uF" footprint="0603" supplierPartNumbers={{ jlcpcb: ["C19702"] }}
      schX={10} schY={0} schRotation="90deg" schSectionName={S} />
    <trace from=".C_BUZ > .pin1" to="net.V3V3" />
    <trace from=".C_BUZ > .pin2" to="net.GND" />

    {/* ---------------- Joystick ---------------- */}
    {/* ALPS SKRHAAE010: four directions + centre push, 7.5 x 7.5 x 5.1 mm, SMD (reflowed
        by JLC on the front face, below the cell, its stem through the case). Pins (LCSC
        symbol): 1 A, 2 push (CEN), 3 C, 4 D, 5 common, 6 B, 7/8 mounting; common to GND.
        Every line between the chin and the MCU crosses one of three narrow bridges beside
        the display slots, so the four directions share ONE line: each switches its own
        resistor (15k/27k/47k/100k) from JOY_DIR, pulled up by 15k, to GND, and the MCU's
        ADC (referenced to the same 3.3 V: ratiometric) tells every single direction and
        every pair apart: 110 mV between neighbours, 39 mV of margin with all 1 % parts
        at their limits. The centre press is the power key (Soft latch, above): the MCU
        reads it as BTN_N. */}
    <chip name="SW_JOY" footprint="pinrow8" manufacturerPartNumber="SKRHAAE010"
      supplierPartNumbers={{ jlcpcb: ["C202423"] }}
      pinLabels={{ pin1: "A", pin2: "CEN", pin3: "C", pin4: "B", pin5: "COM", pin6: "D", pin7: "MP1", pin8: "MP2" }}
      schX={2} schY={20} schSectionName={S} />
    <trace from=".SW_JOY > .CEN" to="net.JOY_CEN" />
    <trace from=".SW_JOY > .COM" to="net.GND" />
    <trace from=".SW_JOY > .MP1" to="net.GND" />
    <trace from=".SW_JOY > .MP2" to="net.GND" />
    {[["A", "R_JA", "15k", "C25756"], ["B", "R_JB", "27k", "C25771"], ["C", "R_JC", "47k", "C25792"],
      ["D", "R_JD", "100k", "C25741"]].map(([pin, r, v, jlc], i) => (
      <group key={r}>
        <trace from={`.SW_JOY > .${pin}`} to={`net.JOY_${pin}`} />
        <resistor name={r} resistance={v} footprint="0402" supplierPartNumbers={{ jlcpcb: [jlc] }}
          schX={4 + i} schY={22} schRotation="90deg" schSectionName={S} />
        <trace from={`.${r} > .pin1`} to="net.JOY_DIR" />
        <trace from={`.${r} > .pin2`} to={`net.JOY_${pin}`} />
      </group>
    ))}
    <resistor name="R_JPU" resistance="15k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25756"] }}
      schX={8} schY={22} schRotation="90deg" schSectionName={S} />
    <trace from=".R_JPU > .pin1" to="net.V3V3" />
    <trace from=".R_JPU > .pin2" to="net.JOY_DIR" />
  </group>
)
