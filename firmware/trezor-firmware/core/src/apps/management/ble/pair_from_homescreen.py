# Ambire boards: Bluetooth pairing from the home screen. Delizia has no device
# menu (Eckhart's "Pair new device" lives there), so a swipe up on the home
# screen starts the same flow (on every Delizia board with [ble], Trezor's T3T2
# devkit included). Delizia's Bluetooth texts are empty: English here.

import trezorble as ble
import trezorui_api
from storage import device as storage_device
from trezor import log, utils
from trezor.ui.layouts import confirm_action, show_error_and_raise
from trezor.wire import ActionCancelled

from .pair_new_device import pair_new_device

# The radio boots on its own, and the boot waits only 0.5 s for it (src/ble.py):
# a swipe up soon after boot can find it not answering yet
RADIO_WAIT_MS = 5000


# How often the flow looks at the airgap switch and the radio
_POLL_MS = 200


def _airgapped() -> bool:
    return trezorui_api.ambire_airgap_engaged()


def _radio_ready() -> bool:
    return not _airgapped() and ble.is_started()


async def _until(condition) -> None:
    from trezor import loop

    while not condition():
        await loop.sleep(_POLL_MS)


async def _wait_for_radio() -> bool:
    """True once the radio answers; a progress bar while it starts"""
    import utime

    from trezor import loop
    from trezor.ui.layouts.progress import progress

    if ble.is_started():
        return True
    if _airgapped():
        # On AIR the radio has no power: nothing to wait for
        return False
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
    from trezor import loop

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
            # Moving the switch off AIR while this shows goes on to pairing
            await loop.race(
                show_error_and_raise(
                    "bluetooth_radio_off",
                    hint,
                    subheader="Bluetooth is off",
                    button="Close",
                ),
                _until(_radio_ready),
            )
            log.info(__name__, "radio up after the switch moved, pairing")

        if len(ble.get_bonds()) >= ble.MAX_BONDS:
            await show_error_and_raise(
                "device_pair",
                "Forget a paired device from the app first.",
                subheader="Limit of paired devices reached",
                button="Close",
            )

        # Moving the switch to AIR cuts the radio: pairing ends there, back to
        # the home screen (whose swipe up then opens the airgap menu)
        await loop.race(pair_new_device(), _until(_airgapped))
        if _airgapped():
            log.info(__name__, "airgap switch moved to AIR, pairing ended")
    except ActionCancelled:
        # Cancelled or closed on the device: back to the home screen
        log.debug(__name__, "Bluetooth pairing cancelled")
