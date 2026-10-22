import { W_PWR, W_PWR_LITE } from "../widths"

const S = "Power"

/**
 * Power tree. One AAA cell of any common chemistry in clips on the front face,
 * a boost that runs from all of them, and USB that powers the board and charges
 * a cell only once the MCU has qualified it as NiMH.
 *
 *   USB-C VBUS -> TLV62569P buck -> V3V3_USB --VIN1--+
 *                                       |  PG -> PR1  | TPS2116 (USB first) -> V3V3
 *                                       \-> BQ25172 --+-> BAT (NiMH only, MCU-gated)
 *   AAA: BAT -> LTC3539-2 boost, SHDN = soft latch -> V_BOOST --VIN2--+
 *        BAT_N -> AO3416 (reverse-cell blocker) -> GND
 *   cell voltage at both clips and two NTCs (charger TS, MCU) -> MCU ADC
 *
 * The backlight pump and everything else run from V3V3; there is no battery
 * gauge (a Li-ion gauge cannot read a NiMH or alkaline cell): firmware reads the
 * cell's voltage and temperature itself.
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
        the cell takes over before the USB rail has sagged to 3.0 V. */}
    <resistor name="R_PG" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={-3} schY={12} schRotation="90deg" schSectionName={S} />
    <trace from=".R_PG > .pin1" to="net.USB_PG" />
    <trace from=".R_PG > .pin2" to="net.V3V3_USB" />

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

    {/* ---------------- The cell: one AAA in two clips on the front face ---------------- */}
    {/* MYOUNG MY-AAA-19-J (spring, -) and MY-AAA-20-J (leaf, +): SMT clips that grip
        the cell body, on the board's otherwise empty front face across the chin. The
        cell may be NiMH (1.0-1.45 V), alkaline or lithium (Li-FeS2) primary (0.9-1.8 V),
        or a 10440 Li-ion (2.5-4.2 V): the boost runs from all of them, the charger only
        ever charges a cell the MCU has qualified as NiMH. */}
    <chip name="BT_N" footprint="pinrow1" pinLabels={{ pin1: "C" }}
      manufacturerPartNumber="MY-AAA-19-J" supplierPartNumbers={{ jlcpcb: ["C6937104"] }}
      schX={-20} schY={4} schSectionName={S} />
    <chip name="BT_P" footprint="pinrow1" pinLabels={{ pin1: "C" }}
      manufacturerPartNumber="MY-AAA-20-J" supplierPartNumbers={{ jlcpcb: ["C6937107"] }}
      schX={-20} schY={6} schSectionName={S} />
    <trace from=".BT_P > .C" to="net.BAT" width={W_PWR} />
    <trace from=".BT_N > .C" to="net.BAT_N" width={W_PWR} />
    {/* Reverse cell: nothing stops a cell going in backwards, and the boost's VIN is
        rated to -0.3 V, so a reversed AAA would discharge through its internal diodes.
        AO3416 in the cell's low side: its body diode conducts a correctly inserted cell
        from the first instant, the gate follows the cell (Vth 0.4-1.0 V) and, once the
        boost runs, the boost's own output through D_REVG (~3 V: ~25 mOhm). Reversed,
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
    <chip name="D_REVG" footprint="pinrow2"
      manufacturerPartNumber="RB521S30T1G" supplierPartNumbers={{ jlcpcb: ["C145179"] }}
      pinLabels={{ pin1: "K", pin2: "A" }}
      schX={-16} schY={0} schSectionName={S} />
    <trace from=".D_REVG > .A" to="net.V_BOOST" />
    <trace from=".D_REVG > .K" to="net.REV_G" />

    {/* Cell voltage at the + clip (route its own trace from the clip pad: no load
        current in it) and at the - clip, so firmware reads the cell itself, not the
        reverse FET: 1M/1M halves the + side (a 10440 reads 2.1 V); 100k into the -
        side limits what a reversed cell can push into the pin. 100 nF each: the ADC
        sees a stiff source. Always on: 0.6 uA. */}
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

    {/* ---------------- Soft latch ---------------- */}
    {/* The power button pulls the boost's SHDN up to the cell through one diode of a
        BAT54C; the MCU holds it through the other (PWR_HOLD) and lets go to power off.
        SHDN's guaranteed high is 0.88 V: the button starts the board from a cell resting
        at >= ~1.05 V (the BAT54's ~0.1 V at microamps), well below where firmware calls
        a NiMH or alkaline cell empty. An NPN (not a FET: it must switch from a 0.9 V
        cell) turns the button into BTN_N for the MCU, so the cell (up to 4.2 V) never
        touches an MCU pin, and an unpowered MCU is never back-fed through a GPIO. */}
    <chip name="SW_PWR" footprint="pinrow3"
      manufacturerPartNumber="SKRTLAE010" supplierPartNumbers={{ jlcpcb: ["C110293"] }}
      pinLabels={{ pin1: "A", pin2: "B", pin3: "MP" }}
      schX={-2} schY={14} schSectionName={S} />
    <trace from=".SW_PWR > .A" to="net.BAT" width={W_PWR_LITE} />
    <trace from=".SW_PWR > .B" to="net.BTN" width={W_PWR_LITE} />
    <trace from=".SW_PWR > .MP" to="net.GND" />
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
        0.88 V ~1 s through any MCU reset (watchdog, brown-out, update, SWD
        connect-under-reset). From a button press on a 1.2 V cell it holds ~0.25 s: the
        boardloader takes PWR_HOLD within milliseconds. Power-off = release PWR_HOLD and
        wait ~2 s. */}
    <capacitor name="C_EN" capacitance="1uF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C52923"] }}
      schX={5} schY={14} schRotation="90deg" schSectionName={S} />
    <trace from=".C_EN > .pin1" to="net.PWR_EN" />
    <trace from=".C_EN > .pin2" to="net.GND" />
    {/* Button -> BTN_N (active low) for the MCU's WKUP1 pin: ~5 uA of base current from a
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
        6 TMR, 7 VSET, 8 OUT, EP = GND. Constant current only, ends on its safety timer or
        when the cell passes VOUT_OVP (1.65-1.75 V): a Li-ion or lithium primary cell is
        never charged, by construction. Fed from the USB 3.3 V rail, not from VBUS: at
        250 mA it dissipates (3.3 - 1.4) x 0.25 = 0.48 W instead of 0.9 W.
        The MCU decides whether a cell is NiMH (resting voltage, internal resistance
        from an 83 -> 250 mA step, a timed trial) and terminates on the cell's own signs
        (zero/negative dV, dT/dt); the IC is the hardware backstop:
          - TS held low by Q_CHGOFF (gate pulled up) = charging OFF by default, and off
            again whenever the MCU is in reset or unpowered; the MCU enables it by
            driving CHG_OFF low;
          - TS's own NTC on the cell: suspends outside 0-45 C;
          - TMR 36k: 4 h, ~125 % of an 800 mAh cell at 250 mA (a 0.1 C charge restarts
            it by toggling TS: NiMH takes 0.1 C overcharge);
          - the IC will not start on a cell resting above ~1.33 V (VRECHG). */}
    <chip name="U1" footprint="pinrow9"
      pinLabels={{ pin1: "IN", pin2: "ISET", pin3: "TS", pin4: "GND", pin5: "STAT", pin6: "TMR", pin7: "VSET",
        pin8: "OUT", pin9: "EP" }}
      manufacturerPartNumber="BQ25172DSGR" supplierPartNumbers={{ jlcpcb: ["C5189434"] }}
      schX={-8} schY={2} schSectionName={S} />
    <trace from=".U1 > .IN" to="net.V3V3_USB" width={W_PWR_LITE} />
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
    <trace from=".C_CHGIN > .pin1" to="net.V3V3_USB" />
    <trace from=".C_CHGIN > .pin2" to="net.GND" />
    <capacitor name="C_BAT" capacitance="10uF" footprint="0805" supplierPartNumbers={{ jlcpcb: ["C15850"] }}
      schX={-10} schY={0} schRotation="90deg" schSectionName={S} />
    <trace from=".C_BAT > .pin1" to="net.BAT" width={W_PWR} />
    <trace from=".C_BAT > .pin2" to="net.GND" width={W_PWR} />
    {/* ICHG = 300 A*Ohm / RISET. 3.6k alone = 83 mA, ~0.1 C: pre-qualification of a
        deeply discharged cell, the resistance test's low step, and the standard charge
        Panasonic rates eneloop with (0.1 C for 15-16 h, which an LSD NiMH tolerates as
        overcharge). With 1.8k switched in parallel by CHG_HI, 1.2k = 250 mA (~0.3 C),
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
    <chip name="Q_CHGHI" footprint="sot23"
      manufacturerPartNumber="2N7002" supplierPartNumbers={{ jlcpcb: ["C8545"] }}
      pinLabels={{ pin1: "G", pin2: "S", pin3: "D" }}
      schX={-4} schY={-2} schSectionName={S} />
    <trace from=".Q_CHGHI > .D" to="net.CHG_ISETH" />
    <trace from=".Q_CHGHI > .S" to="net.GND" />
    <trace from=".Q_CHGHI > .G" to="net.CHG_HI" />
    <resistor name="R_CHGHI" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={-3} schY={-2} schRotation="90deg" schSectionName={S} />
    <trace from=".R_CHGHI > .pin1" to="net.CHG_HI" />
    <trace from=".R_CHGHI > .pin2" to="net.GND" />
    {/* TS: 10k NTC (B 3380) touching the cell's heat path; ITS 38 uA trips at ~0 and ~45 C. */}
    <resistor name="RT_CHG" resistance="10k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C77131"] }}
      schX={-7} schY={-2} schRotation="90deg" schSectionName={S} />
    <trace from=".RT_CHG > .pin1" to="net.CHG_TS" />
    <trace from=".RT_CHG > .pin2" to="net.GND" />
    <chip name="Q_CHGOFF" footprint="sot23"
      manufacturerPartNumber="2N7002" supplierPartNumbers={{ jlcpcb: ["C8545"] }}
      pinLabels={{ pin1: "G", pin2: "S", pin3: "D" }}
      schX={-8} schY={-4} schSectionName={S} />
    <trace from=".Q_CHGOFF > .D" to="net.CHG_TS" />
    <trace from=".Q_CHGOFF > .S" to="net.GND" />
    <trace from=".Q_CHGOFF > .G" to="net.CHG_OFF" />
    <resistor name="R_CHGOFF" resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
      schX={-9} schY={-4} schRotation="90deg" schSectionName={S} />
    <trace from=".R_CHGOFF > .pin1" to="net.CHG_OFF" />
    <trace from=".R_CHGOFF > .pin2" to="net.V3V3" />
    {/* 1 % parts (SLUSDY5: +-1 %, <= 200 ppm/C). */}
    <resistor name="R_TMR" resistance="36k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C43676"] }}
      schX={-11} schY={-4} schRotation="90deg" schSectionName={S} />
    <trace from=".R_TMR > .pin1" to="net.CHG_TMR" />
    <trace from=".R_TMR > .pin2" to="net.GND" />
    <resistor name="R_VSET" resistance="62k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C37825"] }}
      schX={-12} schY={-4} schRotation="90deg" schSectionName={S} />
    <trace from=".R_VSET > .pin1" to="net.CHG_VSET" />
    <trace from=".R_VSET > .pin2" to="net.GND" />
    {/* STAT open drain: low = charging, 1 Hz blink = fault. */}
    <resistor name="R_STAT" resistance="10k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25744"] }}
      schX={-5} schY={4} schRotation="90deg" schSectionName={S} />
    <trace from=".R_STAT > .pin1" to="net.CHG_STAT" />
    <trace from=".R_STAT > .pin2" to="net.V3V3" />
    {/* The MCU's own cell thermometer (dT/dt termination), independent of the charger's. */}
    <resistor name="RT_CELL" resistance="10k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C77131"] }}
      schX={-13} schY={-6} schRotation="90deg" schSectionName={S} />
    <trace from=".RT_CELL > .pin1" to="net.CELL_NTC" />
    <trace from=".RT_CELL > .pin2" to="net.GND" />
    <resistor name="R_NTCB" resistance="10k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25744"] }}
      schX={-14} schY={-6} schRotation="90deg" schSectionName={S} />
    <trace from=".R_NTCB > .pin1" to="net.V3V3" />
    <trace from=".R_NTCB > .pin2" to="net.CELL_NTC" />

    {/* ---------------- Buzzer ---------------- */}
    {/* KELIKING KLJ-7525-3627: magnetic transducer, 7.5 x 7.5 x 2.5 mm, side sound port,
        2.73 kHz, 85 dB at 10 cm, 16 Ohm, 90 mA at 3.6 Vp-p (pad 1 +, pad 2 -, pads 3/4
        mechanical). Low side switched by an AO3416 from a timer PWM at 50 % duty; the
        1N4148WS takes the coil's flyback. */}
    <chip name="LS1" footprint="pinrow4"
      pinLabels={{ pin1: "P", pin2: "N", pin3: "MP1", pin4: "MP2" }}
      manufacturerPartNumber="KLJ-7525-3627" supplierPartNumbers={{ jlcpcb: ["C189208"] }}
      schX={10} schY={2} schSectionName={S} />
    <trace from=".LS1 > .P" to="net.V3V3" width={W_PWR_LITE} />
    <trace from=".LS1 > .N" to="net.BUZ_SW" width={W_PWR_LITE} />
    <trace from=".LS1 > .MP1" to="net.NC_BUZ_MP1" />
    <trace from=".LS1 > .MP2" to="net.NC_BUZ_MP2" />
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

    {/* ---------------- Edge buttons (optional, not fitted) ---------------- */}
    {/* Four side-push buttons on the board edges, the power button's part, for a
        game-console style of input. No JLC number = not fitted (DNP); the lands are
        there, and each line idles on the MCU's internal pull-up. */}
    {[["SW_1", "BTN_1"], ["SW_2", "BTN_2"], ["SW_3", "BTN_3"], ["SW_4", "BTN_4"]].map(([sw, net], i) => (
      <group key={sw}>
        <chip name={sw} footprint="pinrow3" manufacturerPartNumber="SKRTLAE010"
          pinLabels={{ pin1: "A", pin2: "B", pin3: "MP" }}
          schX={-2 + 2 * i} schY={20} schSectionName={S} />
        <trace from={`.${sw} > .A`} to={`net.${net}`} />
        <trace from={`.${sw} > .B`} to="net.GND" />
        <trace from={`.${sw} > .MP`} to="net.GND" />
      </group>
    ))}
  </group>
)
