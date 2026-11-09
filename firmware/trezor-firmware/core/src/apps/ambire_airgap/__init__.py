# Ambire boards ([ambire_camera]): airgapped signing over QR codes with the
# Ambire app, behind the swipe up on the home screen while the airgap switch
# is on AIR. The protocol is Keystone's (BC-UR): the device shows a
# crypto-hdkey to connect, scans eth-sign-request codes and shows
# eth-signature codes back. Delizia's texts are empty for this: English here.

from typing import TYPE_CHECKING

import trezorui_api
from trezor import log, wire
from trezor.ui.layouts.common import interact

if TYPE_CHECKING:
    from buffer_types import AnyBytes

_TITLE_ERROR = "Request rejected"


async def _show_ur(title: str, ur_type: str, cbor: AnyBytes) -> None:
    with trezorui_api.ambire_show_ur(title=title, ur_type=ur_type, cbor=cbor) as layout:
        await interact(layout, None, raise_on_cancel=None)


async def _show_error(subheader: str, content: str) -> None:
    with trezorui_api.show_error(
        title=subheader, description=content, button="Close", allow_cancel=False
    ) as layout:
        await interact(layout, None, raise_on_cancel=None)


async def connect() -> None:
    """Shows the account key the app pairs with (see request.hdkey_cbor)"""
    from trezor.crypto import bip32

    from .request import hdkey_cbor
    from .sign import ACCOUNT_PATH, get_seed, master_fingerprint

    root = bip32.from_seed(await get_seed(), "secp256k1")
    master = master_fingerprint(root)
    node = root.clone()
    node.derive_path(ACCOUNT_PATH)
    cbor = hdkey_cbor(node.public_key(), node.chain_code(), master, node.fingerprint())
    del node, root
    await _show_ur("Scan with Ambire", "crypto-hdkey", cbor)


async def scan_request() -> None:
    from .sign import sign_request

    with trezorui_api.ambire_scan_ur() as layout:
        res = await interact(layout, None, raise_on_cancel=None)
    if res is trezorui_api.CANCELLED:
        return
    if isinstance(res, str):
        # The scan's own reason (malformed or inconsistent codes)
        await _show_error("Scan failed", res)
        return
    ur_type, cbor = res
    if ur_type != "eth-sign-request":
        await _show_error(_TITLE_ERROR, "This code is not a signing request.")
        return
    signature = await sign_request(cbor)
    await _show_ur("Scan with Ambire", "eth-signature", signature)


async def camera_test() -> None:
    with trezorui_api.ambire_camera_test() as layout:
        await interact(layout, None, raise_on_cancel=None)


async def airgap_menu() -> None:
    entries = (
        ("Scan request", scan_request),
        ("Connect to Ambire", connect),
        ("Camera test", camera_test),
    )
    current = 0
    while True:
        with trezorui_api.select_menu(
            items=[(name, trezorui_api.MenuItemIntent.STANDARD) for name, _ in entries],
            current=current,
        ) as layout:
            choice = await interact(layout, None, raise_on_cancel=None)
        if not isinstance(choice, int):
            # Closed: back to the home screen
            return
        current = choice
        try:
            await entries[choice][1]()
        except wire.ActionCancelled:
            log.debug(__name__, "cancelled on the device")
        except wire.Error as e:
            # A request the device refuses (or a flow's own error), and why
            log.warning(__name__, "rejected: %s", e.message)
            await _show_error(_TITLE_ERROR, e.message)
        except Exception as e:
            # A fault of ours: say so instead of dropping back to the home
            # screen without a word
            log.exception(__name__, e)
            await _show_error("Something went wrong", e.__class__.__name__)
