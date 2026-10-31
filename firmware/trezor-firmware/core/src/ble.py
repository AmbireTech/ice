# BLE setup - called from main.py near the end of firmware startup
# please note BLE may already be set up by bootloader

import utime

import storage.device
import trezorble as ble
from trezor import log, utils

try:
    enable = storage.device.get_ble()
    ble.set_enabled(enable)
    ble.start_comm()
    if enable:
        start_ms = utime.ticks_ms()
        # Ambire boards: the radio is powered from the kernel's start, seconds
        # before this runs (welcome screen, PIN); no answer by now means the
        # airgap switch has it off, which must not hold the boot up for 5 s
        wait_ms = 500 if utils.MODEL_FULL_NAME == "Ambire" else 5000

        while utime.ticks_diff(utime.ticks_ms(), start_ms) < wait_ms:
            if utils.EMULATOR or ble.is_started():
                break

        # allow connections from bonded peers if any
        if ble.peer_count() > 0:
            ble.start_advertising(True, storage.device.get_label())
except Exception as e:
    if __debug__:
        log.exception(__name__, e)
