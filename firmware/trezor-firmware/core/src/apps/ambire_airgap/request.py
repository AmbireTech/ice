# Ambire boards: the eth-sign-request the Ambire app shows as a QR code
# (Keystone's registry type 401), parsed strictly, and the transactions in it
# turned into the messages Trezor's Ethereum flows take.
#
#   {1: #6.37(request id, 16 bytes), 2: sign data, 3: data type,
#    ? 4: chain id, 5: #6.304({1: path, 2: master fingerprint}),
#    6: address (20 bytes), ? 7: origin}

from micropython import const
from typing import TYPE_CHECKING

from trezor.wire import DataError

from .cbor import Reader, Tagged, encode

if TYPE_CHECKING:
    from buffer_types import AnyBytes
    from typing import Any

    from trezor.messages import EthereumSignTx, EthereumSignTxEIP1559

    RlpItem = Any  # bytes | list[RlpItem]

DATA_TYPE_TX = const(1)
DATA_TYPE_TYPED_DATA = const(2)
DATA_TYPE_PERSONAL = const(3)
DATA_TYPE_TX_TYPED = const(4)

_HARDENED = const(0x8000_0000)
# Trezor's own address index range (apps.common.paths, "address_index")
_MAX_ADDRESS_INDEX = const(1_000_000)
# The request's CBOR is bounded by the QR transport (16 KiB)
_MAX_SIGN_DATA = const(16384)
# Trezor's EthereumSignTx sends the first 1024 bytes of calldata inline
DATA_CHUNK = const(1024)
# Trezor's sign_tx returns the full EIP-155 v only up to this chain id
# (apps/ethereum/sign_tx.py), the parity bit above it
_MAX_CHAIN_ID = const((0xFFFF_FFFF - 36) // 2)


class SignRequest:
    def __init__(self) -> None:
        self.request_id = b""
        self.sign_data = b""
        self.data_type = 0
        self.chain_id: int | None = None
        self.address_n: list[int] = []
        self.source_fingerprint = 0
        self.address = b""


def _keypath(r: Reader) -> tuple[list[int], int]:
    """#6.304 crypto-keypath: the path (index, hardened pairs) and the
    master fingerprint; a depth (key 3), if given, must be the path's"""
    r.tag(304)
    path: list[int] | None = None
    fingerprint: int | None = None
    for key in r.keys(r.map(3)):
        if key == 1:
            n = r.array(2 * 10)
            if n % 2:
                raise DataError("Malformed derivation path")
            path = []
            for _ in range(n // 2):
                index = r.uint(_HARDENED - 1)
                path.append(index | _HARDENED if r.boolean() else index)
        elif key == 2:
            fingerprint = r.uint(0xFFFF_FFFF)
        elif key == 3:
            if path is None or r.uint() != len(path):
                raise DataError("Malformed derivation path")
        else:
            raise DataError("Malformed derivation path")
    if path is None or fingerprint is None:
        raise DataError("The request names no wallet")
    return path, fingerprint


def parse(data: AnyBytes) -> SignRequest:
    req = SignRequest()
    r = Reader(data)
    seen = 0
    for key in r.keys(r.map(7)):
        seen |= 1 << key
        if key == 1:
            r.tag(37)  # uuid
            req.request_id = r.byte_string(16)
            if len(req.request_id) != 16:
                raise DataError("Malformed request id")
        elif key == 2:
            req.sign_data = r.byte_string(_MAX_SIGN_DATA)
        elif key == 3:
            req.data_type = r.uint(DATA_TYPE_TX_TYPED)
        elif key == 4:
            req.chain_id = r.uint()
        elif key == 5:
            req.address_n, req.source_fingerprint = _keypath(r)
        elif key == 6:
            req.address = r.byte_string(20)
            if len(req.address) != 20:
                raise DataError("Malformed address")
        elif key == 7:
            r.text_string(64)  # origin: the app's name, not used
        else:
            raise DataError("Unknown field in the request")
    r.end()

    # Request id, data, data type, path and address are required
    if seen & 0b0110_1110 != 0b0110_1110:
        raise DataError("Incomplete request")
    if not req.sign_data:
        raise DataError("Nothing to sign")
    if req.data_type < DATA_TYPE_TX:
        raise DataError("Unknown data type")
    if req.chain_id == 0:
        raise DataError("Invalid chain id")

    # m/44'/60'/0'/0/i, as the app derives its accounts from the key it paired
    path = req.address_n
    if (
        len(path) != 5
        or path[:4] != [44 | _HARDENED, 60 | _HARDENED, 0 | _HARDENED, 0]
        or path[4] > _MAX_ADDRESS_INDEX
    ):
        raise DataError("Unsupported derivation path")
    return req


# --- RLP -------------------------------------------------------------------------

# Lists nest at most this deep in a transaction: the transaction, its access
# list, an entry, the entry's storage keys
_RLP_DEPTH = const(4)


def _rlp_item(data: AnyBytes, pos: int, end: int, depth: int) -> tuple[RlpItem, int]:
    """Decodes the item at `pos` (ending before `end`); returns it and where
    the next one starts. Shortest forms only."""

    def fail() -> DataError:
        return DataError("Malformed transaction")

    if pos >= end:
        raise fail()
    b = data[pos]
    if b < 0x80:
        return bytes(data[pos : pos + 1]), pos + 1
    if b < 0xB8 or 0xC0 <= b < 0xF8:
        n = (b - 0x80) if b < 0xB8 else (b - 0xC0)
        start = pos + 1
    else:
        size = (b - 0xB7) if b < 0xC0 else (b - 0xF7)
        start = pos + 1 + size
        if start > end or data[pos + 1] == 0:
            raise fail()
        n = int.from_bytes(data[pos + 1 : start], "big")
        if n <= 55:
            raise fail()
    if start + n > end:
        raise fail()
    if b < 0xC0:
        if n == 1 and data[start] < 0x80:
            # One byte below 0x80 is its own encoding
            raise fail()
        return bytes(data[start : start + n]), start + n
    if depth >= _RLP_DEPTH:
        raise fail()
    items = []
    p = start
    while p < start + n:
        item, p = _rlp_item(data, p, start + n, depth + 1)
        items.append(item)
    return items, start + n


def _uint(item: RlpItem) -> bytes:
    """A quantity: at most 32 bytes, no leading zero"""
    if not isinstance(item, bytes) or len(item) > 32 or item[:1] == b"\x00":
        raise DataError("Malformed transaction")
    return item


def _to(item: RlpItem) -> str:
    """The recipient: 20 bytes, or none (contract creation)"""
    if not isinstance(item, bytes) or len(item) not in (0, 20):
        raise DataError("Malformed transaction")
    return "0x" + item.hex() if item else ""


def _chain_id(item: RlpItem, req: SignRequest) -> int:
    chain_id = int.from_bytes(_uint(item), "big")
    if chain_id == 0 or chain_id > 0xFFFF_FFFF_FFFF_FFFF:
        raise DataError("Invalid chain id")
    if chain_id != req.chain_id:
        raise DataError("The transaction's chain differs from the request's")
    return chain_id


def transaction(req: SignRequest) -> tuple[Any, bytes]:
    """The EthereumSignTx (legacy, EIP-155) or EthereumSignTxEIP1559 message
    for the request's transaction, and its calldata:

        [nonce, gasPrice, gas, to, value, data, chainId, 0, 0]
        0x02 || [chainId, nonce, maxPriorityFee, maxFee, gas, to, value, data,
                 accessList]

    The message, encoded back the way Trezor's flow hashes it, must give the
    request's bytes exactly: what the device shows is what the app asked for.
    """
    from trezor import messages
    from trezor.crypto import rlp

    sign_data = req.sign_data
    legacy = req.data_type == DATA_TYPE_TX
    if not legacy and sign_data[0] != 2:
        raise DataError("Unsupported transaction type")
    f, end = _rlp_item(sign_data, 0 if legacy else 1, len(sign_data), 0)
    if not isinstance(f, list) or len(f) != 9 or end != len(sign_data):
        raise DataError("Malformed transaction")

    if legacy:
        nonce, gas_price, gas, to, value, data, chain_id, r, s = f
        # EIP-155: the chain id, and empty r and s
        if r != b"" or s != b"":
            raise DataError("Not an unsigned EIP-155 transaction")
    else:
        chain_id, nonce, max_priority_fee, max_fee, gas, to, value, data, access = f
    if not isinstance(data, bytes):
        raise DataError("Malformed transaction")
    common = {
        "address_n": req.address_n,
        "chain_id": _chain_id(chain_id, req),
        "nonce": _uint(nonce),
        "gas_limit": _uint(gas),
        "to": _to(to),
        "value": _uint(value),
        "data_initial_chunk": data[:DATA_CHUNK],
        "data_length": len(data),
    }

    if legacy:
        msg = messages.EthereumSignTx(gas_price=_uint(gas_price), **common)
        fields: list = [msg.nonce, msg.gas_price, msg.gas_limit, to, msg.value]
        fields += [data, msg.chain_id, 0, 0]
    else:
        access_list = []
        if not isinstance(access, list):
            raise DataError("Malformed transaction")
        for entry in access:
            if (
                not isinstance(entry, list)
                or len(entry) != 2
                or not isinstance(entry[0], bytes)
                or len(entry[0]) != 20
                or not isinstance(entry[1], list)
            ):
                raise DataError("Malformed access list")
            for key in entry[1]:
                if not isinstance(key, bytes) or len(key) != 32:
                    raise DataError("Malformed access list")
            access_list.append(
                messages.EthereumAccessList(
                    address="0x" + entry[0].hex(), storage_keys=entry[1]
                )
            )
        msg = messages.EthereumSignTxEIP1559(
            max_priority_fee=_uint(max_priority_fee),
            max_gas_fee=_uint(max_fee),
            access_list=access_list,
            **common,
        )
        fields = [msg.chain_id, msg.nonce, msg.max_priority_fee, msg.max_gas_fee]
        fields += [msg.gas_limit, to, msg.value, data, access]

    w = bytearray(b"" if legacy else b"\x02")
    rlp.write(w, fields)
    if w != sign_data:
        raise DataError("Malformed transaction")
    return msg, data


# --- What the device shows -------------------------------------------------------


def hdkey_cbor(public_key: bytes, chain_code: bytes, master: int, parent: int) -> bytes:
    """crypto-hdkey (Keystone's registry type 303) of m/44'/60'/0', with its
    children 0/*: the app derives m/44'/60'/0'/0/i from it, and echoes the
    master fingerprint in every request"""
    if master == 0 or parent == 0:
        # The app takes a zero fingerprint for a missing one (1 in 2^32)
        raise DataError("This wallet's key cannot be shown to the app.")
    return encode(
        {
            3: public_key,
            4: chain_code,
            6: Tagged(304, {1: [44, True, 60, True, 0, True], 2: master}),
            7: Tagged(304, {1: [0, False, [], False]}),
            8: parent,
            9: "Ambire",
        }
    )


def signature_cbor(req: SignRequest, signature: bytes) -> bytes:
    """eth-signature (registry type 402): the request id, tagged, and
    r || s || v"""
    return encode({1: Tagged(37, req.request_id), 2: signature})


def tx_signature(req: SignRequest, chain_id: int, v: int, r: bytes, s: bytes) -> bytes:
    """r || s || v for a transaction, v as Keystone sends it: EIP-155's for a
    legacy transaction, the y parity for EIP-1559. `v` is what Trezor's flow
    returned: EIP-155's up to _MAX_CHAIN_ID, the parity above it."""
    if req.data_type == DATA_TYPE_TX:
        parity = v if chain_id > _MAX_CHAIN_ID else v - 35 - 2 * chain_id
        if parity not in (0, 1):
            raise DataError("Unexpected signature")
        v = 35 + 2 * chain_id + parity
    elif v not in (0, 1):
        raise DataError("Unexpected signature")
    if len(r) != 32 or len(s) != 32:
        raise DataError("Unexpected signature")
    return bytes(r) + bytes(s) + v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big")
