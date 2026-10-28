import { PowerBlock } from "./src/blocks/Power"
import { McuBlock } from "./src/blocks/Mcu"
import { DisplayBlock } from "./src/blocks/Display"
import { SecureBlock } from "./src/blocks/Secure"
import { RadioBlock } from "./src/blocks/Radio"
import { CameraBlock } from "./src/blocks/Camera"

/**
 * Ambire Ice — the NETLIST.
 *
 * Same split as the GridCore Nano and the OBD2 adapter: the circuit lives here,
 * the board lives in KiCad (board/wallet.kicad_pcb), placed by script once and
 * routed by hand. Nothing in this file carries a position. scripts/syncBoard.py
 * pushes the netlist into the board; footprints are chosen in
 * board/footprints.json.
 *
 * A part is fitted by JLC exactly when it carries a JLC part number.
 */
export default () => (
  <board width="50mm" height="86mm" routingDisabled>
    <net name="GND" />
    <net name="V3V3" />
    <net name="BAT" />
    <net name="V_BOOST" />
    <net name="V3V3_USB" />
    <net name="VBUS" />
    <net name="AIRGAP" />
    <net name="RADIO_3V3" />

    <schematicsection name="Power" schX={-12} schY={8} />
    <schematicsection name="MCU" schX={0} schY={8} />
    <schematicsection name="Display" schX={12} schY={-4} />
    <schematicsection name="Secure" schX={0} schY={-10} />
    <schematicsection name="Radio" schX={0} schY={-24} />
    <schematicsection name="Camera" schX={0} schY={-36} />

    <PowerBlock />
    <McuBlock />
    <DisplayBlock />
    <SecureBlock />
    <RadioBlock />
    <CameraBlock />
  </board>
)
