import { W_PWR_LITE } from "../widths"

const S = "IMU"

/**
 * Bosch BMI270 6-axis IMU (accelerometer + gyroscope), LGA-14 2.5 x 3 mm, the
 * GridCore Nano's part. Uses: pick-up-to-wake during the asleep window (its
 * any-motion interrupt wakes the MCU from Stop), and orientation. Suspend draws
 * 3.5 uA (BST-BMI270-DS000-08 Table 6).
 *
 * Pins (BST-BMI270-DS000-08 Table 22, I2C column):
 *   1 SDO              I2C address bit 0: GND -> 0x68 (touch is 0x38 on this bus)
 *   2 ASDx, 3 ASCx     secondary interface, unused: "VDDIO or left unconnected.
 *                      Do not connect to GND." Left unconnected: tying them to
 *                      VDDIO costs a via each between the INT1/VDDIO lands.
 *   4 INT1             any-motion / data-ready interrupt -> MCU EXTI
 *   5 VDDIO, 8 VDD     both 3.3 V, each with its own 100 nF (the section 7 connection diagrams)
 *   6 GNDIO, 7 GND     both ground; not tied inside the part
 *   9 INT2             unused: DNC
 *   10 OCSB, 11 OSDO   OIS interface, unused: DNC
 *   12 CSB             VDDIO selects I2C (a rising edge on CSB latches SPI until
 *                      the next power-on reset, so it must never float low)
 *   13 SCx, 14 SDx     I2C3, shared with the touch controller and its pull-ups
 */
export const ImuBlock = () => (
  <group>
    <chip name="U15" footprint="pinrow14"
      manufacturerPartNumber="BMI270" supplierPartNumbers={{ jlcpcb: ["C2836813"] }}
      pinLabels={{ pin1: "SDO", pin2: "ASDX", pin3: "ASCX", pin4: "INT1", pin5: "VDDIO", pin6: "GNDIO", pin7: "GND",
        pin8: "VDD", pin9: "INT2", pin10: "OCSB", pin11: "OSDO", pin12: "CSB", pin13: "SCX", pin14: "SDX" }}
      schX={0} schY={-2} schSectionName={S} />
    <trace from=".U15 > .VDD" to="net.V3V3" width={W_PWR_LITE} />
    <trace from=".U15 > .VDDIO" to="net.V3V3" width={W_PWR_LITE} />
    <trace from=".U15 > .GND" to="net.GND" width={W_PWR_LITE} />
    <trace from=".U15 > .GNDIO" to="net.GND" width={W_PWR_LITE} />
    <trace from=".U15 > .CSB" to="net.V3V3" />
    <trace from=".U15 > .SDO" to="net.GND" />
    <trace from=".U15 > .SCX" to="net.I2C_SCL" />
    <trace from=".U15 > .SDX" to="net.I2C_SDA" />
    <trace from=".U15 > .INT1" to="net.IMU_INT" />

    <capacitor name="C_IMU1" capacitance="100nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C1525"] }}
      schX={-2} schY={-4} schRotation="90deg" schSectionName={S} />
    <capacitor name="C_IMU2" capacitance="100nF" footprint="0402" supplierPartNumbers={{ jlcpcb: ["C1525"] }}
      schX={2} schY={-4} schRotation="90deg" schSectionName={S} />
    {["C_IMU1", "C_IMU2"].map((c) => (
      <group key={c}>
        <trace from={`.${c} > .pin1`} to="net.V3V3" />
        <trace from={`.${c} > .pin2`} to="net.GND" />
      </group>
    ))}
  </group>
)
