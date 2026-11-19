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


# Never show a layout through interact() (show_error_and_raise, confirm_*,
# pair_new_device ...) inside a loop.race child. interact() starts with
# workflow.close_others(), which closes the home screen's task unless that task
# is the one running, and a race child is a task of its own: the flow closes
# its own parent, the close fails ("generator already executing"), and nothing
# restarts the home screen. The backlight is already off by then: a black
# screen that takes no input (2026-11-17 to 2026-11-19).


async def _show_until(layout_ctx, condition):
    """Shows a layout from this task and returns its result, or None once
    condition() holds. Only the layout's result is raced, never its start."""
    from trezor import loop, ui, workflow

    workflow.close_others()
    with layout_ctx as layout_obj:
        layout = ui.Layout(layout_obj)
        layout.start()
        return await loop.race(layout.get_result(), _until(condition))


async def _end_flow_when(condition) -> None:
    """Closes the flow on the home screen's task once condition() holds: the
    flow's finally blocks run and the home screen starts again."""
    from trezor import workflow

    await _until(condition)
    log.info(__name__, "airgap switch moved to AIR, pairing ended")
    workflow.kill_default()


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
            closed = await _show_until(
                trezorui_api.show_error(
                    title="Bluetooth is off",
                    description=hint,
                    button="Close",
                    allow_cancel=False,
                ),
                _radio_ready,
            )
            if closed is not None:
                raise ActionCancelled
            log.info(__name__, "radio up after the switch moved, pairing")

        if len(ble.get_bonds()) >= ble.MAX_BONDS:
            await show_error_and_raise(
                "device_pair",
                "Forget a paired device from the app first.",
                subheader="Limit of paired devices reached",
                button="Close",
            )

        # Moving the switch to AIR cuts the radio: pairing ends there, back to
        # the home screen (whose swipe up then opens the airgap menu). The
        # pairing screens run on this task; a watcher task ends it.
        watcher = loop.spawn(_end_flow_when(_airgapped))
        try:
            await pair_new_device()
        finally:
            # The watcher itself may be what is closing this flow
            if not watcher.is_running():
                watcher.close()
    except ActionCancelled:
        # Cancelled or closed on the device: back to the home screen
        log.debug(__name__, "Bluetooth pairing cancelled")
