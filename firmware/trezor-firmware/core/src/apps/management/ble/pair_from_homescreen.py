# Ambire boards: Bluetooth pairing from the home screen. Delizia has no device
# menu (Eckhart's "Pair new device" lives there), so a swipe up on the home
# screen starts the same flow (on every Delizia board with [ble], Trezor's T3T2
# devkit included). Delizia's Bluetooth texts are empty: English here.

import trezorble as ble
from storage import device as storage_device
from trezor import log, utils
from trezor.ui.layouts import confirm_action, show_error_and_raise
from trezor.wire import ActionCancelled

from .pair_new_device import pair_new_device


async def pair_from_homescreen() -> None:
    try:
        if not ble.get_enabled():
            await confirm_action(
                "enable_bluetooth",
                "Bluetooth",
                description="Bluetooth must be on to pair a new device. Turn it on?",
                verb="Turn on",
            )
            ble.set_enabled(True)
            storage_device.set_ble(True)

        if not ble.is_started():
            # The radio is not answering: on Ambire boards the airgap switch has
            # it off (Trezor's T3T2 devkit shares this Delizia flow)
            if utils.MODEL_FULL_NAME == "Ambire":
                hint = "Slide the airgap switch away from AIR to use Bluetooth."
            else:
                hint = "Bluetooth is not responding."
            await show_error_and_raise(
                "bluetooth_radio_off",
                hint,
                subheader="Bluetooth is off",
                button="Close",
            )

        if len(ble.get_bonds()) >= ble.MAX_BONDS:
            await show_error_and_raise(
                "device_pair",
                "Forget a paired device from the app first.",
                subheader="Limit of paired devices reached",
                button="Close",
            )

        await pair_new_device()
    except ActionCancelled:
        # Cancelled or closed on the device: back to the home screen
        log.debug(__name__, "Bluetooth pairing cancelled")
