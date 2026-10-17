import { W_PWR_LITE } from "../widths"

const S = "Secure"

/**
 * NXP SE050E2HQ1/Z01Z3Z secure element, HX2QFN20 (SOT1969-1) — the current
 * generation (AN12436 Rev 2.4 Table 2 files the SE050C1/C2 under "Previous
 * Generation"). Same data sheet, pin table and land as the SE050C1 (SE050 data
 * sheet Rev 3.8 Table 5; AN13014 2.1 "pin-to-pin and package compatible").
 * Fallback on the same land: SE051C2HQ1/Z01XDZ (field-updatable applet).
 *
 * Keys are generated inside it and are not extractable: secp256k1 (AN12436
 * Rev 2.4 Table 1, "Koblitz 160-256" ticked for E2; AN12413 Table 37 curve 0x10)
 * for a plain EOA owner on a Safe, NIST P-256 for passkey-style owners, Ed25519.
 * Firmware (applet 7.2, AN13014): I2C at <= 1 MHz, no clock stretching by
 * default; UserID (PIN) objects at least 4 bytes; the E2's own SCP03 platform
 * keys (AN12436 Table 5); new attestation format. The PIN and its retry counter are SE050 authentication objects, so the
 * MCU never holds anything worth extracting.
 *
 * Pins (SE050 data sheet Rev 3.8, Table 5):
 *   1 LB, 17 LA        contactless antenna, unused -> VSS
 *   3 IO1, 16 IO2, 13 CLK   ISO7816 / I2C controller, unused -> n.c. (recommended)
 *   14 RST_N           ISO7816 reset, only active with ISO7816 enabled -> VSS
 *   9 SDA, 10 SCL      host I2C (target, address 0x48), referenced to VIN
 *   11 ENA             deep power-down, active LOW, referenced to VIN
 *   12 VIN             I2C pad supply and DPD logic supply
 *   15 VOUT, 18 VCC    tied together: the Deep Power-down wiring (AN12395 Fig. 6),
 *                      so the MCU can power the secure core down and back up
 *                      (a full reset if the I2C link ever wedges)
 *   19 VSS, EP         EP is not connected inside; tied to GND for heat
 *   2,4-8,20           n.c.
 *
 * Second secure element, a different vendor (Infineon OPTIGA Trust M V3,
 * SLS32AIA010MK, CC EAL6+ hardware, PG-USON-10 3x3 mm): it holds an independent
 * NIST P-256 key generated inside it, so one vendor's silicon break does not give
 * up the device's signing authority (the account counts the device as both keys).
 * V1 SLS32AIA010MH (C539250) is the same package and pinout.
 * Pins (OPTIGA Trust M data sheet Rev 3.10, Table 6; Figure 2 integration):
 *   1 GND, 3 SDA, 8 SCL, 9 RST (active low), 10 VCC; 2, 4-7 NC.
 *   The centre pad is not in the contact table: soldered, left unconnected.
 * I2C address 0x30 beside the SE050's 0x48. RST from the MCU (warm reset,
 * Table 14: >= 10 us low, first transfer >= 15 ms after release), pulled high so
 * the chip runs while the MCU pin is unconfigured.
 *
 * Both secure elements share their own I2C bus (I2C4), not shared with the
 * display/camera peripherals; each has its own reset (SE050 ENA, OPTIGA RST).
 */
export const SecureBlock = () => (
  <group>
    <chip name="U5" footprint="pinrow21"
      manufacturerPartNumber="SE050E2HQ1/Z01Z3Z" supplierPartNumbers={{ jlcpcb: ["C5190917"] }}
      pinLabels={{ pin1: "LB", pin2: "NC2", pin3: "IO1", pin4: "NC4", pin5: "NC5", pin6: "NC6", pin7: "NC7", pin8: "NC8",
        pin9: "SDA", pin10: "SCL", pin11: "ENA", pin12: "VIN", pin13: "CLK", pin14: "RST_N", pin15: "VOUT",
        pin16: "IO2", pin17: "LA", pin18: "VCC", pin19: "VSS", pin20: "NC20", pin21: "EP" }}
      schX={0} schY={-10} schSectionName={S} />
    <trace from=".U5 > .LB" to="net.GND" />
    <trace from=".U5 > .LA" to="net.GND" />
    <trace from=".U5 > .RST_N" to="net.GND" />
    <trace from=".U5 > .VSS" to="net.GND" />
    <trace from=".U5 > .EP" to="net.GND" />
    <trace from=".U5 > .SDA" to="net.SE_SDA" />
    <trace from=".U5 > .SCL" to="net.SE_SCL" />
    <trace from=".U5 > .ENA" to="net.SE_ENA" />
    <trace from=".U5 > .VIN" to="net.V3V3" width={W_PWR_LITE} />
    <trace from=".U5 > .VOUT" to="net.SE_VCC" width={W_PWR_LITE} />
    <trace from=".U5 > .VCC" to="net.SE_VCC" width={W_PWR_LITE} />

    <chip name="U16" footprint="pinrow11"
      manufacturerPartNumber="SLS32AIA010MK" supplierPartNumbers={{ jlcpcb: ["C1518668"] }}
      pinLabels={{ pin1: "GND", pin2: "NC2", pin3: "SDA", pin4: "NC4", pin5: "NC5", pin6: "NC6", pin7: "NC7",
        pin8: "SCL", pin9: "RST", pin10: "VCC", pin11: "EP" }}
      schX={0} schY={-16} schSectionName={S} />
    <trace from=".U16 > .GND" to="net.GND" />
    <trace from=".U16 > .SDA" to="net.SE_SDA" />
    <trace from=".U16 > .SCL" to="net.SE_SCL" />
    <trace from=".U16 > .RST" to="net.OPTIGA_RST" />
    <trace from=".U16 > .VCC" to="net.V3V3" width={W_PWR_LITE} />

    {/* NXP specifies no decoupling; 100 nF on each supply node is the OM-SE050ARD practice. */}
    {/* OPTIGA: 100 nF at VCC (data sheet Figure 2). */}
    {[["C_SEIN", "V3V3"], ["C_SEVCC", "SE_VCC"], ["C_OPT", "V3V3"]].map(([c, net]) => (
      <group key={c}>
        <capacitor name={c} capacitance="100nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C1525"] }}
          schX={3} schY={-10} schRotation="90deg" schSectionName={S} />
        <trace from={`.${c} > .pin1`} to={`net.${net}`} />
        <trace from={`.${c} > .pin2`} to="net.GND" />
      </group>
    ))}
    {/* External pull-ups are required (the pads' own ~180 uA pulls are weak).
        ENA pulled high: the secure element runs unless the MCU parks it. */}
    {[["R_SESCL", "SE_SCL", "4.7k", "C25900"], ["R_SESDA", "SE_SDA", "4.7k", "C25900"],
      ["R_SEENA", "SE_ENA", "100k", "C25741"], ["R_OPTRST", "OPTIGA_RST", "100k", "C25741"]].map(([r, net, v, jlc]) => (
      <group key={r}>
        <resistor name={r} resistance={v} footprint="0402" supplierPartNumbers={{ jlcpcb: [jlc] }}
          schX={-3} schY={-10} schRotation="90deg" schSectionName={S} />
        <trace from={`.${r} > .pin1`} to={`net.${net}`} />
        <trace from={`.${r} > .pin2`} to="net.V3V3" />
      </group>
    ))}
  </group>
)
