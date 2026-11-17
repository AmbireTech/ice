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

# The radio boots on its own, and the boot waits only 0.5 s for it (src/ble.py):
# a swipe up soon after boot can find it not answering yet
RADIO_WAIT_MS = 5000


async def _wait_for_radio() -> bool:
    """True once the radio answers; a progress bar while it starts"""
    import utime

    from trezor import loop
    from trezor.ui.layouts.progress import progress

    if ble.is_started():
        return True
    log.info(__name__, "radio not started yet, waiting")
    bar = progress("Starting Bluetooth")
    start = utime.ticks_ms()
    try:
        while True:
            if ble.is_started():
                return True
            elapsed = utime.ticks_diff(utime.ticks_ms(), start)
            if elapsed >= RADIO_WAIT_MS:
                log.warning(__name__, "radio did not start in %d ms", RADIO_WAIT_MS)
                return False
            bar.report(elapsed * 1000 // RADIO_WAIT_MS)
            await loop.sleep(100)
    finally:
        bar.stop()


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

        if not await _wait_for_radio():
            # Still not answering after the wait: on Ambire boards the airgap
            # switch may have it off (Trezor's T3T2 devkit shares this flow)
            if utils.MODEL_FULL_NAME == "Ambire":
                hint = "Check that the airgap switch is not on AIR, then try again."
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
