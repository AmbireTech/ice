import { W_PWR_LITE } from "../widths"

const S = "Camera"

/**
 * QR camera: an OV2640 24-pin 0.5 mm FPC DVP module (ESP32-CAM kind) with the
 * standard ~66 deg lens (not fisheye: wide lenses break decoding), ~21 mm flex,
 * lens refocused to ~15 cm (stock lenses are glued for far focus; order
 * "focus at 150 mm" from the module house, e.g. Sincere SF-AOV2640, or refocus).
 * Head 8.0 x 8.0 x 4.85 mm (Kailap JAL-KA4-OV2640 sheet). Lens out through the
 * clear back; not a JLC part, it plugs into J_CAM after assembly. The better
 * optical choice, the SC031GS global-shutter mono module Keycard Shell ships,
 * needs 1.8 V I/O and level translation this board does not have.
 *
 * Connector pin order is the Ai-Thinker ESP32-CAM V1.6 schematic's (Espressif's
 * ESP32-S3-EYE numbers the same contacts from the other end, pin N <-> 25 - N;
 * the signal order along the cable is identical). The module sits like it does
 * on an ESP32-CAM, mirrored onto this board's component face: lens away from
 * the board, tail flat into a bottom-contact connector on the same face. Which
 * end is pad 1 on the board is fixed in board/footprints.json and is an
 * explicit bring-up check with the chosen module in hand (docs/design.md).
 *
 * Supplies: AVDD 2.8 V, DOVDD 3.3 V (the MCU's I/O level, no shifting), DVDD
 * 1.3 V: the OV2640 data sheet v2.2 puts the core at 1.24-1.36 V (typ 1.3), so
 * the Ai-Thinker boards' 1.2 V is under its minimum. AVDD and DVDD are switched
 * by CAM_EN; DVDD is a fixed 1.3 V LDO.
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
      manufacturerPartNumber="AFC07-S24FCA-00" supplierPartNumbers={{ jlcpcb: ["C262567"] }}
      schX={0} schY={-36} schSectionName={S} />
    {/* 8-bit DVP uses the sensor's top byte Y2..Y9; Y0/Y1 are the 10-bit LSBs. */}
    <trace from=".J_CAM > .Y0" to="net.NC_CAM_Y0" />
    <trace from=".J_CAM > .Y1" to="net.NC_CAM_Y1" />
    {(["Y2", "Y3", "Y4", "Y5", "Y6", "Y7", "Y8", "Y9"] as const).map((y, i) => (
      <trace key={y} from={`.J_CAM > .${y}`} to={`net.CAM_D${i}`} />
    ))}
    <trace from=".J_CAM > .PCLK" to="net.CAM_PCLK" />
    <trace from=".J_CAM > .XCLK" to="net.CAM_XCLK" />
    <trace from=".J_CAM > .HREF" to="net.CAM_HREF" />
    <trace from=".J_CAM > .VSYNC" to="net.CAM_VSYNC" />
    {/* PWDN tied low: the sensor is only ever powered up and down whole, by CAM_EN. */}
    <trace from=".J_CAM > .PWDN" to="net.GND" />
    <trace from=".J_CAM > .RESET" to="net.CAM_RST" />
    <trace from=".J_CAM > .SIOC" to="net.CAM_SCL" />
    <trace from=".J_CAM > .SIOD" to="net.CAM_SDA" />
    <trace from=".J_CAM > .DGND" to="net.GND" />
    <trace from=".J_CAM > .AGND" to="net.GND" />
    <trace from=".J_CAM > .MP1" to="net.GND" />
    <trace from=".J_CAM > .MP2" to="net.GND" />
    <trace from=".J_CAM > .NC" to="net.NC_CAM24" />
    <trace from=".J_CAM > .DOVDD" to="net.V3V3" width={W_PWR_LITE} />
    <trace from=".J_CAM > .AVDD" to="net.CAM_AVDD" width={W_PWR_LITE} />
    <trace from=".J_CAM > .DVDD" to="net.CAM_DVDD" width={W_PWR_LITE} />

    {/* AVDD 2.8 V: ME6211C28M5G-N (SOT-23-5: 1 VIN, 2 VSS, 3 CE, 4 NC, 5 VOUT), CE active high. */}
    <chip name="U12" footprint="pinrow5"
      manufacturerPartNumber="ME6211C28M5G-N" supplierPartNumbers={{ jlcpcb: ["C53099"] }}
      pinLabels={{ pin1: "VIN", pin2: "VSS", pin3: "CE", pin4: "NC", pin5: "VOUT" }}
      schX={6} schY={-34} schSectionName={S} />
    <trace from=".U12 > .VIN" to="net.V3V3" />
    <trace from=".U12 > .VSS" to="net.GND" />
    <trace from=".U12 > .CE" to="net.CAM_EN" />
    <trace from=".U12 > .VOUT" to="net.CAM_AVDD" />
    {/* DVDD 1.3 V: TLV70313 DBV (SBVS305 Pin Functions: 1 IN, 2 GND, 3 EN, 4 NC, 5 OUT),
        fixed output, 1 uF in and out. Another sensor's core voltage is another TLV703xx
        on the same land. */}
    <chip name="U14" footprint="pinrow5"
      manufacturerPartNumber="TLV70313DBVR" supplierPartNumbers={{ jlcpcb: ["C2868385"] }}
      pinLabels={{ pin1: "IN", pin2: "GND", pin3: "EN", pin4: "NC", pin5: "OUT" }}
      schX={6} schY={-38} schSectionName={S} />
    <trace from=".U14 > .IN" to="net.V3V3" />
    <trace from=".U14 > .GND" to="net.GND" />
    <trace from=".U14 > .EN" to="net.CAM_EN" />
    <trace from=".U14 > .NC" to="net.GND" />
    <trace from=".U14 > .OUT" to="net.CAM_DVDD" />
    {/* 1 uF in (shared by the two LDOs, which sit side by side), 1 uF on each output
        (ME6211 typical circuit; TLV703 COUT >= 0.1 uF effective), plus 100 nF on the
        sensor's I/O rail. */}
    {[["C_AVIN", "V3V3", "1uF", "C52923"], ["C_AVOUT", "CAM_AVDD", "1uF", "C52923"],
      ["C_DVOUT", "CAM_DVDD", "1uF", "C52923"],
      ["C_DOVDD", "V3V3", "100nF", "C1525"]].map(([c, net, v, jlc], i) => (
      <group key={c}>
        <capacitor name={c} capacitance={v} footprint="0402" supplierPartNumbers={{ jlcpcb: [jlc] }}
          schX={12 + i} schY={-36} schRotation="90deg" schSectionName={S} />
        <trace from={`.${c} > .pin1`} to={`net.${net}`} />
        <trace from={`.${c} > .pin2`} to="net.GND" />
      </group>
    ))}
    {/* CAM_EN low (AVDD and DVDD off) until firmware drives it; DOVDD is the always-on
        3.3 V. RESETB needs no pull-up: the sensor's own keeps it released, and while
        the MCU is in reset CAM_EN's pull-down holds the core unpowered. SCCB pull-ups
        on its own bus. */}
    {[["R_CAMEN", "CAM_EN", "GND"]].map(([r, net, to]) => (
      <group key={r}>
        <resistor name={r} resistance="100k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25741"] }}
          schX={-4} schY={-36} schRotation="90deg" schSectionName={S} />
        <trace from={`.${r} > .pin1`} to={`net.${net}`} />
        <trace from={`.${r} > .pin2`} to={`net.${to}`} />
      </group>
    ))}
    {[["R_CSCL", "CAM_SCL"], ["R_CSDA", "CAM_SDA"]].map(([r, net]) => (
      <group key={r}>
        <resistor name={r} resistance="4.7k" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C25900"] }}
          schX={-6} schY={-36} schRotation="90deg" schSectionName={S} />
        <trace from={`.${r} > .pin1`} to={`net.${net}`} />
        <trace from={`.${r} > .pin2`} to="net.V3V3" />
      </group>
    ))}
  </group>
)
