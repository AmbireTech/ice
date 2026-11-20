import { W_PWR_LITE } from "../widths"

const S = "Camera"

/**
 * QR camera: a GC2145 24-pin 0.5 mm FPC DVP module in the ESP32-CAM pinout
 * (Camemake CM_DVP_GC2145_UNIVERSAL, DigiKey / JLC Global Sourcing), the commodity
 * successor to the end-of-life OV2640. Its ~1.4 mm, 115 deg lens needs no refocus;
 * firmware reads the sensor's centre crop window (P0:0x90-0x98, 640x480, ~48 deg
 * across), which decoded phone QR as well as a refocused OV2640 in simulation.
 * An OV2640 or OV5640 module fits the same connector and rails. Lens out through the clear back; not a JLC part, it plugs
 * into J_CAM after assembly. Flex length and head height: measure on samples.
 *
 * Connector pin order is the Ai-Thinker ESP32-CAM V1.6 schematic's (Espressif's
 * ESP32-S3-EYE numbers the same contacts from the other end, pin N <-> 25 - N;
 * the signal order along the cable is identical). The module sits like it does
 * on an ESP32-CAM, mirrored onto this board's component face: lens away from
 * the board, tail flat into a bottom-contact connector on the same face. Which
 * end is pad 1 on the board is fixed in board/footprints.json and is an
 * explicit bring-up check with the chosen module in hand (docs/design.md).
 *
 * Supplies: AVDD and DOVDD 2.8 V from one LDO (as Espressif's ESP32-S3-EYE and
 * Waveshare's ESP32-S3-CAM feed them), DVDD per sensor. DOVDD was 3.3 V (the MCU's I/O
 * level) since proto 0, in the OV2640's range (1.71-3.3 V) but not the GC2145's
 * (IOVDD 1.7-3.0 V; its module maker asks 1.8 or 2.8 V, 2026-11-19); 2.8 V is inside
 * all three (OV5640 DOVDD 1.8/2.8 V nominal). The sensor's outputs at 2.8 V read high
 * on the MCU's 3.3 V inputs; the MCU's two push-pull outputs into it (XCLK, RESET) go
 * through a dual buffer on the 2.8 V rail, and the SCCB pull-ups go to 2.8 V. DVDD,
 * the sensor's core, differs per sensor and per module (a module may or may not
 * regulate its own core): OV2640 1.14-1.26 V (data sheet v1.6; v2.2 1.24-1.36 V),
 * OV5640 1.5 V +-5%, GC2145 1.7-1.9 V. So DVDD is set by firmware: an adjustable
 * LDO whose feedback node takes a current from a filtered PWM (CAM_DVDD_PWM, TIM4),
 * stepless from below 1.2 V up to the ceiling. The ceiling is by construction: PWM at
 * 0 % (or the pin stuck low) gives 1.84 V nominal, 1.79-1.88 V across 1 % parts and
 * VFB, under the GC2145's 1.9 V and far under the OVs' 3 V absolute maximum; PWM
 * stuck high pulls the output to 0 V. All three
 * rails are switched by CAM_EN: the sensor is wholly unpowered while the camera is off.
 */
const CAM_PINS = {
  pin1: "Y0", pin2: "Y1", pin3: "Y4", pin4: "Y3", pin5: "Y5", pin6: "Y2", pin7: "Y6", pin8: "PCLK",
  pin9: "Y7", pin10: "DGND", pin11: "Y8", pin12: "XCLK", pin13: "Y9", pin14: "DOVDD", pin15: "DVDD", pin16: "HREF",
  pin17: "PWDN", pin18: "VSYNC", pin19: "RESET", pin20: "SIOC", pin21: "AVDD", pin22: "SIOD", pin23: "AGND", pin24: "NC",
  pin25: "MP1", pin26: "MP2",
} as const

export const CameraBlock = () => (
  <group>
    <chip name="J_CAM" footprint="pinrow26" pinLabels={CAM_PINS}
      manufacturerPartNumber="AFC01-S24FCA-00" supplierPartNumbers={{ jlcpcb: ["C262669"] }}
      schX={0} schY={-36} schSectionName={S} />
    {/* 8-bit DVP uses the sensor's top byte Y2..Y9; Y0/Y1 are the 10-bit LSBs. */}
    <trace from=".J_CAM > .Y0" to="net.NC_CAM_Y0" />
    <trace from=".J_CAM > .Y1" to="net.NC_CAM_Y1" />
    {(["Y2", "Y3", "Y4", "Y5", "Y6", "Y7", "Y8", "Y9"] as const).map((y, i) => (
      <trace key={y} from={`.J_CAM > .${y}`} to={`net.CAM_D${i}`} />
    ))}
    <trace from=".J_CAM > .PCLK" to="net.CAM_PCLK" />
    <trace from=".J_CAM > .XCLK" to="net.CAM_XCLK_S" />
    <trace from=".J_CAM > .HREF" to="net.CAM_HREF" />
    <trace from=".J_CAM > .VSYNC" to="net.CAM_VSYNC" />
    {/* PWDN tied low: the sensor is only ever powered up and down whole, by CAM_EN. */}
    <trace from=".J_CAM > .PWDN" to="net.GND" />
    <trace from=".J_CAM > .RESET" to="net.CAM_RST_S" />
    <trace from=".J_CAM > .SIOC" to="net.CAM_SCL" />
    <trace from=".J_CAM > .SIOD" to="net.CAM_SDA" />
    <trace from=".J_CAM > .DGND" to="net.GND" />
    <trace from=".J_CAM > .AGND" to="net.GND" />
    <trace from=".J_CAM > .MP1" to="net.GND" />
    <trace from=".J_CAM > .MP2" to="net.GND" />
    <trace from=".J_CAM > .NC" to="net.NC_CAM24" />
    <trace from=".J_CAM > .DOVDD" to="net.CAM_AVDD" width={W_PWR_LITE} />
    <trace from=".J_CAM > .AVDD" to="net.CAM_AVDD" width={W_PWR_LITE} />
    <trace from=".J_CAM > .DVDD" to="net.CAM_DVDD" width={W_PWR_LITE} />

    {/* AVDD and DOVDD, 2.8 V (net CAM_AVDD): ME6211C28M5G-N (SOT-23-5: 1 VIN, 2 VSS, 3 CE,
        4 NC, 5 VOUT), CE active high, 500 mA: the sensor's analog and I/O draw tens of mA. */}
    <chip name="U12" footprint="pinrow5"
      manufacturerPartNumber="ME6211C28M5G-N" supplierPartNumbers={{ jlcpcb: ["C53099"] }}
      pinLabels={{ pin1: "VIN", pin2: "VSS", pin3: "CE", pin4: "NC", pin5: "VOUT" }}
      schX={6} schY={-34} schSectionName={S} />
    <trace from=".U12 > .VIN" to="net.V3V3" />
    <trace from=".U12 > .VSS" to="net.GND" />
    <trace from=".U12 > .CE" to="net.CAM_EN" />
    <trace from=".U12 > .VOUT" to="net.CAM_AVDD" />
    {/* DVDD: TLV75801P DBV, adjustable (SBVS351D Pin Functions, DBV: 1 IN, 2 GND, 3 EN,
        4 FB, 5 OUT; VFB 0.55 V, IFB <= 0.1 uA, COUT >= 1 uF). The TLV70312's land, with
        pin 4 now FB instead of NC. Divider R_DVT 33k / R_DVB 21k: 0.55 x (1 + 33/21)
        = 1.41 V with no current through R_DVSET. R_DVSET (33k) + R_DVPWM (10k) from the
        PWM (average Vp): VOUT = 1.414 + (0.55 - Vp) x 33/43. Vp 0 V -> 1.84 V (the
        ceiling; 1.88 V worst case, under the GC2145's 1.9: R_DVB was 20k, 1.93 V worst
        case, audit 2026-11-19); 0.07 V -> 1.78 V (GC2145); 0.44 V -> 1.50 V (OV5640);
        0.80 V -> 1.22 V (OV2640). PWM ~39 kHz, 10k x 1 uF: ~1 mV ripple at the node,
        tau ~8 ms. Firmware charges the node before CAM_EN rises, so DVDD starts at its
        setting, not ~0.1 V over it.
        Placement: R_DVPWM at the MCU pin (the long run then carries the filtered
        level, not PWM edges); C_DVSET, R_DVSET and the divider at U14's FB pin, the
        FB node as short as possible. */}
    <chip name="U14" footprint="pinrow5"
      manufacturerPartNumber="TLV75801PDBVR" supplierPartNumbers={{ jlcpcb: ["C2877852"] }}
      pinLabels={{ pin1: "IN", pin2: "GND", pin3: "EN", pin4: "FB", pin5: "OUT" }}
      schX={6} schY={-38} schSectionName={S} />
    <trace from=".U14 > .IN" to="net.V3V3" />
    <trace from=".U14 > .GND" to="net.GND" />
    <trace from=".U14 > .EN" to="net.CAM_EN" />
    <trace from=".U14 > .FB" to="net.CAM_DVDD_FB" />
    <trace from=".U14 > .OUT" to="net.CAM_DVDD" />
    {[["R_DVT", "33k", "C25779", "CAM_DVDD", "CAM_DVDD_FB"],
      ["R_DVB", "21k", "C25766", "CAM_DVDD_FB", "GND"],
      ["R_DVSET", "33k", "C25779", "CAM_DVDD_FB", "CAM_DVDD_SET"],
      ["R_DVPWM", "10k", "C25744", "CAM_DVDD_PWM", "CAM_DVDD_SET"]].map(([r, v, jlc, a, b], i) => (
      <group key={r}>
        <resistor name={r} resistance={v} footprint="0402" supplierPartNumbers={{ jlcpcb: [jlc] }}
          schX={8 + i} schY={-40} schRotation="90deg" schSectionName={S} />
        <trace from={`.${r} > .pin1`} to={`net.${a}`} />
        <trace from={`.${r} > .pin2`} to={`net.${b}`} />
      </group>
    ))}
    <capacitor name="C_DVSET" capacitance="1uF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C52923"] }}
      schX={12} schY={-40} schRotation="90deg" schSectionName={S} />
    <trace from=".C_DVSET > .pin1" to="net.CAM_DVDD_SET" />
    <trace from=".C_DVSET > .pin2" to="net.GND" />
    {/* 1 uF in (shared by the two LDOs, which sit side by side), 1 uF on each output
        (ME6211 typical circuit; TLV758P COUT >= 1 uF), plus 100 nF at the sensor's
        DOVDD pin (J_CAM pad 14), apart from AVDD's 1 uF at the LDO. */}
    {[["C_AVIN", "V3V3", "1uF", "C52923"], ["C_AVOUT", "CAM_AVDD", "1uF", "C52923"],
      ["C_DVOUT", "CAM_DVDD", "1uF", "C52923"],
      ["C_DOVDD", "CAM_AVDD", "100nF", "C1525"]].map(([c, net, v, jlc], i) => (
      <group key={c}>
        <capacitor name={c} capacitance={v} footprint="0402" supplierPartNumbers={{ jlcpcb: [jlc] }}
          schX={12 + i} schY={-36} schRotation="90deg" schSectionName={S} />
        <trace from={`.${c} > .pin1`} to={`net.${net}`} />
        <trace from={`.${c} > .pin2`} to="net.GND" />
      </group>
    ))}
    {/* CAM_EN low (every camera rail off) until firmware drives it: while the MCU is in
        reset CAM_EN's pull-down holds the sensor and the buffer unpowered. SCCB pull-ups
        on its own bus, to the sensor's 2.8 V I/O rail. R_AVBLEED: the ME6211 has no
        output discharge, so the 2.8 V rail bleeds through 4.7k (0.6 mA on), down in
        well under the 250 ms firmware waits before a reopen: a clean power-on reset. */}
    {[["R_CAMEN", "CAM_EN", "GND", "100k", "C25741"], ["R_AVBLEED", "CAM_AVDD", "GND", "4.7k", "C25900"]].map(([r, net, to, v, jlc], i) => (
      <group key={r}>
        <resistor name={r} resistance={v} footprint="0402" supplierPartNumbers={{ jlcpcb: [jlc] }}
          schX={-4} schY={-36 - 2 * i} schRotation="90deg" schSectionName={S} />
        <trace from={`.${r} > .pin1`} to={`net.${net}`} />
        <trace from={`.${r} > .pin2`} to={`net.${to}`} />
      </group>
    ))}
    {[["R_CSCL", "CAM_SCL"], ["R_CSDA", "CAM_SDA"]].map(([r, net]) => (
      <group key={r}>
        <resistor name={r} resistance="4.7k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25900"] }}
          schX={-6} schY={-36} schRotation="90deg" schSectionName={S} />
        <trace from={`.${r} > .pin1`} to={`net.${net}`} />
        <trace from={`.${r} > .pin2`} to="net.CAM_AVDD" />
      </group>
    ))}
    {/* XCLK and RESET to 2.8 V: SN74LVC2G34 dual buffer (TI SCES359J Pin Functions, DCK:
        1 1A, 2 GND, 3 2A, 4 2Y, 5 VCC, 6 1Y) on the 2.8 V rail. Its inputs take 5.5 V at any
        VCC and Ioff keeps them high-impedance while it is unpowered, so the MCU's 3.3 V
        outputs go straight in; VIH 0.7 x VCC = 1.96 V. 20 MHz XCLK: well inside its speed.
        The firmware holds RESET low and XCLK stopped while CAM_EN rises. Place it at J_CAM,
        100 nF at its VCC. */}
    <chip name="U17" footprint="pinrow6"
      manufacturerPartNumber="SN74LVC2G34DCKR" supplierPartNumbers={{ jlcpcb: ["C14461"] }}
      pinLabels={{ pin1: "A1", pin2: "GND", pin3: "A2", pin4: "Y2", pin5: "VCC", pin6: "Y1" }}
      schX={-8} schY={-36} schSectionName={S} />
    <trace from=".U17 > .A1" to="net.CAM_XCLK" />
    <trace from=".U17 > .Y1" to="net.CAM_XCLK_S" />
    <trace from=".U17 > .A2" to="net.CAM_RST" />
    <trace from=".U17 > .Y2" to="net.CAM_RST_S" />
    <trace from=".U17 > .VCC" to="net.CAM_AVDD" />
    <trace from=".U17 > .GND" to="net.GND" />
    <capacitor name="C_CAMBUF" capacitance="100nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C1525"] }}
      schX={-10} schY={-36} schRotation="90deg" schSectionName={S} />
    <trace from=".C_CAMBUF > .pin1" to="net.CAM_AVDD" />
    <trace from=".C_CAMBUF > .pin2" to="net.GND" />
  </group>
)
