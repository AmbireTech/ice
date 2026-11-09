# Ambire boards: sign an eth-sign-request scanned from the Ambire app, and
# answer with an eth-signature QR code.
#
# The signing itself is Trezor's: the request becomes the message a host
# would send (EthereumSignTx, EthereumSignTxEIP1559, EthereumSignMessage,
# EthereumSignTypedData) and runs through the same handler, with every
# confirmation screen and check, inside a loopback wire context that answers
# the handler's follow-up requests (calldata chunks, typed-data structs and
# values) from the request instead of from a host.

from typing import TYPE_CHECKING

from trezor import utils
from trezor.wire import DataError

from . import request as rq

if TYPE_CHECKING:
    from buffer_types import AnyBytes
    from typing import Any, Callable

    from trezor import protobuf
    from trezor.crypto import bip32

_HARDENED = 0x8000_0000

# The account the app pairs with: m/44'/60'/0'
ACCOUNT_PATH = [44 | _HARDENED, 60 | _HARDENED, 0 | _HARDENED]


async def get_seed() -> bytes:
    """The wallet's seed; asks for the passphrase on the device when the
    passphrase is on (every time: the device keeps no session here)"""
    from apps.common import mnemonic, passphrase
    from apps.common.seed import _get_seed_without_passphrase, raise_if_not_initialized

    raise_if_not_initialized()
    if passphrase.is_enabled():
        from trezor.ui.layouts import request_passphrase_on_device

        phrase = await request_passphrase_on_device(passphrase.MAX_PASSPHRASE_LEN)
        return mnemonic.get_seed(phrase)
    # Cached across workflows, as for FIDO and SLIP-21 keys
    return _get_seed_without_passphrase()


def master_fingerprint(root: bip32.HDNode) -> int:
    child = root.clone()
    child.derive(0 | _HARDENED)
    return child.fingerprint()  # its parent's: the master's


class _Loopback:
    """The wire context the Ethereum handlers run in: no host, every request
    answered by `answer`, the seed in a cache of its own"""

    def __init__(self, seed: bytes, answer: Callable[[Any], Any]) -> None:
        from storage.cache_common import APP_COMMON_SEED

        if utils.USE_THP:
            from storage.cache_thp import SessionThpCache as Cache
        else:
            from storage.cache_codec import SessionCache as Cache

        self.cache = Cache()
        self.cache.set(APP_COMMON_SEED, seed)
        self.answer = answer
        # Layouts send no ButtonRequests: nobody would acknowledge them
        self.button_request_handler = None

    async def call(self, msg: protobuf.MessageType, expected_type: Any) -> Any:
        resp = self.answer(msg)
        if not expected_type.is_type_of(resp):
            raise DataError("Unexpected request from the signing flow")
        return resp

    async def read(self, *args: Any) -> Any:
        raise DataError("Unexpected read in the signing flow")

    async def write(self, msg: protobuf.MessageType) -> None:
        raise DataError("Unexpected write in the signing flow")

    def release(self) -> None:
        self.cache.clear()


def _answer_tx(data: bytes) -> Callable[[Any], Any]:
    """Serves the calldata after the first chunk, in the sizes asked for"""
    from trezor.messages import EthereumTxAck, EthereumTxRequest

    sent = [min(len(data), rq.DATA_CHUNK)]

    def answer(msg: Any) -> Any:
        if not EthereumTxRequest.is_type_of(msg) or msg.data_length is None:
            raise DataError("Unexpected request from the signing flow")
        start = sent[0]
        if msg.data_length <= 0 or start + msg.data_length > len(data):
            raise DataError("The signing flow asked for more data than there is")
        sent[0] = start + msg.data_length
        return EthereumTxAck(data_chunk=data[start : sent[0]])

    return answer


def _answer_typed(typed: Any) -> Callable[[Any], Any]:
    from trezor.messages import (
        EthereumTypedDataStructRequest,
        EthereumTypedDataValueRequest,
    )

    def answer(msg: Any) -> Any:
        if EthereumTypedDataStructRequest.is_type_of(msg):
            return typed.struct_ack(msg.name)
        if EthereumTypedDataValueRequest.is_type_of(msg):
            return typed.value_ack(msg.member_path)
        raise DataError("Unexpected request from the signing flow")

    return answer


def _no_requests(msg: Any) -> Any:
    raise DataError("Unexpected request from the signing flow")


async def sign_request(cbor: AnyBytes) -> bytes:
    """Checks the request, has the user confirm it in Trezor's Ethereum
    flow, and returns the eth-signature CBOR to show"""
    from trezor.crypto import bip32
    from trezor.wire import context

    req = rq.parse(cbor)
    # Everything is parsed before the passphrase is asked for
    data_type = req.data_type
    if data_type in (rq.DATA_TYPE_TX, rq.DATA_TYPE_TX_TYPED):
        msg, data = rq.transaction(req)
        answer = _answer_tx(data)
        if data_type == rq.DATA_TYPE_TX:
            from apps.ethereum.sign_tx import sign_tx as handler
        else:
            from apps.ethereum.sign_tx_eip1559 import sign_tx_eip1559 as handler
    elif data_type == rq.DATA_TYPE_PERSONAL:
        from trezor.messages import EthereumSignMessage

        from apps.ethereum.sign_message import sign_message as handler

        msg = EthereumSignMessage(address_n=req.address_n, message=req.sign_data)
        answer = _no_requests
    else:
        from trezor.messages import EthereumSignTypedData

        from apps.ethereum.sign_typed_data import sign_typed_data as handler

        from .typed_data import TypedData

        typed = TypedData(req.sign_data)
        typed.check_chain(req.chain_id)
        msg = EthereumSignTypedData(
            address_n=req.address_n,
            primary_type=typed.primary_type,
            metamask_v4_compat=True,
        )
        answer = _answer_typed(typed)

    seed = await get_seed()
    root = bip32.from_seed(seed, "secp256k1")
    if master_fingerprint(root) != req.source_fingerprint:
        raise DataError(
            "This request is for another wallet. Check the passphrase, or connect this device to Ambire again."
        )
    node = root.clone()
    node.derive_path(req.address_n)
    if node.ethereum_pubkeyhash() != req.address:
        raise DataError("The request's address is not this wallet's at its path.")
    del node, root

    ctx = _Loopback(seed, answer)
    try:
        res = await context.with_context(ctx, handler(msg))
    finally:
        ctx.release()

    if data_type in (rq.DATA_TYPE_TX, rq.DATA_TYPE_TX_TYPED):
        signature = rq.tx_signature(
            req, msg.chain_id, res.signature_v, res.signature_r, res.signature_s
        )
    else:
        # r || s || v, v 27 or 28
        signature = bytes(res.signature)
        if len(signature) != 65:
            raise DataError("Unexpected signature")
    return rq.signature_cbor(req, signature)
