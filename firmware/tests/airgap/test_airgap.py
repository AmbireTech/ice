"""Host tests of the firmware's airgap package (src/apps/ambire_airgap), under
CPython, against the vectors the Ambire app's own libraries made
(vectors.js) and against hostile input.

The firmware modules run as they are, on stubs of what they need from the
firmware (messages, hashing, the wire context). The digests the device would
sign come from Trezor's own Ethereum code (sign_tx, sign_tx_eip1559,
sign_message, sign_typed_data), fed through the airgap package's answers, and
must equal ethers' and eth-sig-util's.

    python3 test_airgap.py vectors.json out_dir
"""

import importlib.abc
import importlib.util
import json
import random
import sys
import types
from pathlib import Path

from Crypto.Hash import keccak as _keccak

SRC = Path(__file__).resolve().parents[2] / "trezor-firmware/core/src"

# --- Stubs of the firmware ---------------------------------------------------------


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    m.__path__ = []  # a package, so that submodules resolve
    sys.modules[name] = m
    return m


_module("micropython", const=lambda x: x)


class HashWriter:
    def __init__(self, ctx):
        self.ctx = ctx

    def append(self, b):
        self.ctx.update(bytes([b]))

    def extend(self, buf):
        self.ctx.update(bytes(buf))

    write = extend

    def get_digest(self):
        return self.ctx.digest()


class _Keccak:
    def __init__(self, data=None, keccak=True):
        assert keccak
        self.h = _keccak.new(digest_bits=256)
        if data:
            self.h.update(bytes(data))

    def update(self, data):
        self.h.update(data)

    def digest(self):
        return self.h.digest()


class _TR:
    def __getattr__(self, name):
        return name


utils = _module(
    "trezor.utils",
    USE_THP=True,
    BITCOIN_ONLY=False,
    HashWriter=HashWriter,
    empty_bytearray=lambda n: bytearray(),
)
trezor = _module("trezor", TR=_TR(), utils=utils)
_module("trezor.log", debug=lambda *a, **k: None, warning=lambda *a, **k: None)
_module("trezor.crypto")
_module("trezor.crypto.hashlib", sha3_256=_Keccak)
_module("trezor.enums")


class Message:
    """A protobuf message: keyword fields, unset ones None (lists empty)"""

    LISTS = ("access_list", "members", "member_path", "storage_keys")

    def __init__(self, **kw):
        for f in self.LISTS:
            setattr(self, f, [])
        self.__dict__.update(kw)

    @classmethod
    def is_type_of(cls, msg):
        return type(msg) is cls

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


class _Messages(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        cls = type(name, (Message,), {})
        setattr(self, name, cls)
        return cls


sys.modules["trezor.messages"] = _Messages("trezor.messages")
trezor.messages = sys.modules["trezor.messages"]

# What the loopback context answers: set by each test
ANSWER = [None]


async def _call(msg, expected_type):
    resp = ANSWER[0](msg)
    assert expected_type.is_type_of(resp), (msg, resp)
    return resp


_module("trezor.wire.context", call=_call)
_module("trezor.ui")
_module("trezor.ui.layouts")


class _Progress:
    def report(self, *a):
        pass

    def stop(self):
        pass


_module("trezor.ui.layouts.progress", progress=lambda *a, **k: _Progress())


async def _no(*a, **k):
    return False


sys.modules["trezor.ui.layouts"].confirm_properties = _no
sys.modules["trezor.ui.layouts"].show_warning = _no
_module("apps")
_module("apps.ethereum")
_module("apps.ethereum.networks", UNKNOWN_NETWORK=None)
_module(
    "apps.ethereum.keychain",
    PATTERNS_ADDRESS=(),
    with_keychain_from_path=lambda *p: (lambda f: f),
    with_keychain_from_chain_id=lambda f: f,
)
_module(
    "apps.ethereum.layout",
    should_show_struct=_no,
    should_show_array=_no,
    confirm_empty_typed_message=_no,
    confirm_message_hash=_no,
    confirm_typed_data_final=_no,
    confirm_typed_value=_no,
    extract_properties=lambda *a: None,
)


class _SourceFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Loads these firmware modules from the source tree, with postponed
    annotations (the firmware writes `int | None` for MicroPython)"""

    MODULES = {
        "trezor.crypto.rlp": "trezor/crypto/rlp.py",
        "trezor.enums.EthereumDataType": "trezor/enums/EthereumDataType.py",
        "trezor.enums.FailureType": "trezor/enums/FailureType.py",
        "trezor.enums.ButtonRequestType": "trezor/enums/ButtonRequestType.py",
        "trezor.wire.errors": "trezor/wire/errors.py",
        "apps.ethereum.helpers": "apps/ethereum/helpers.py",
        "apps.ethereum.sign_tx": "apps/ethereum/sign_tx.py",
        "apps.ethereum.sign_tx_eip1559": "apps/ethereum/sign_tx_eip1559.py",
        "apps.ethereum.sign_message": "apps/ethereum/sign_message.py",
        "apps.ethereum.sign_typed_data": "apps/ethereum/sign_typed_data.py",
        "apps.ambire_airgap.cbor": "apps/ambire_airgap/cbor.py",
        "apps.ambire_airgap.request": "apps/ambire_airgap/request.py",
        "apps.ambire_airgap.typed_data": "apps/ambire_airgap/typed_data.py",
        "apps.ambire_airgap.sign": "apps/ambire_airgap/sign.py",
    }

    def find_spec(self, name, path, target=None):
        if name in self.MODULES:
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        path = SRC / self.MODULES[module.__name__]
        code = "from __future__ import annotations\n" + path.read_text()
        module.__file__ = str(path)
        exec(compile(code, str(path), "exec"), module.__dict__)


sys.meta_path.insert(0, _SourceFinder())
_module("apps.ambire_airgap")
wire = _module("trezor.wire")
errors = importlib.import_module("trezor.wire.errors")
wire.__dict__.update({k: getattr(errors, k) for k in dir(errors) if not k.startswith("_")})
sys.modules["trezor.enums"].EthereumDataType = importlib.import_module("trezor.enums.EthereumDataType")
sys.modules["trezor.enums"].FailureType = importlib.import_module("trezor.enums.FailureType")
sys.modules["trezor.enums"].ButtonRequestType = importlib.import_module("trezor.enums.ButtonRequestType")
trezor.crypto = sys.modules["trezor.crypto"]
sys.modules["trezor.crypto"].rlp = importlib.import_module("trezor.crypto.rlp")

DataError = errors.DataError
cbor = importlib.import_module("apps.ambire_airgap.cbor")
rq = importlib.import_module("apps.ambire_airgap.request")
td = importlib.import_module("apps.ambire_airgap.typed_data")
sign = importlib.import_module("apps.ambire_airgap.sign")
sign_tx = importlib.import_module("apps.ethereum.sign_tx")
sign_tx_eip1559 = importlib.import_module("apps.ethereum.sign_tx_eip1559")
sign_message = importlib.import_module("apps.ethereum.sign_message")
sign_typed_data = importlib.import_module("apps.ethereum.sign_typed_data")

H = 0x8000_0000


def run(coro):
    """Drives a coroutine that never really waits"""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value
    raise AssertionError("the coroutine waited")


# --- Tests ------------------------------------------------------------------------------

FAILURES = []
COUNT = [0]


def check(cond, what):
    COUNT[0] += 1
    if not cond:
        FAILURES.append(what)
        print("FAIL:", what)


def expect_data_error(fn, what):
    try:
        fn()
    except DataError:
        COUNT[0] += 1
        return
    except Exception as e:  # anything else is a bug in the strict parsers
        FAILURES.append(f"{what}: {e.__class__.__name__}: {e}")
        print("FAIL:", what, repr(e))
        return
    FAILURES.append(f"{what}: accepted")
    print("FAIL:", what, "accepted")


def firmware_tx_digest(req, msg, data):
    ANSWER[0] = sign._answer_tx(data)
    if req.data_type == rq.DATA_TYPE_TX:
        sha = sign_tx._start_digest(msg)
    else:
        sha = sign_tx_eip1559._start_digest(msg, [])
    initial = run(sign_tx.request_initial_data(msg, sha))
    loader = sign_tx.create_data_chunk_loader(sha)
    left = msg.data_length - len(initial)
    while left > 0:
        left -= len(run(loader(left)))
    if req.data_type == rq.DATA_TYPE_TX:
        return sign_tx._finish_digest(msg, sha)
    return sign_tx_eip1559._finish_digest(msg, [], sha)


def firmware_typed_digest(sign_data):
    typed = td.TypedData(sign_data)
    ANSWER[0] = sign._answer_typed(typed)
    return run(sign_typed_data._generate_typed_data_hash(typed.primary_type, True, None))


def test_requests(v):
    for r in v["requests"]:
        name = r["name"]
        data = bytes.fromhex(r["cbor"])
        if name == "oversize":
            continue
        req = rq.parse(data)
        check(req.request_id.hex() == v["requestId"], f"{name}: request id")
        check(req.data_type == r["dataType"], f"{name}: data type")
        check(req.chain_id == r["chainId"], f"{name}: chain id")
        check(req.address_n == [44 | H, 60 | H, H, 0, r["index"]], f"{name}: path")
        check(req.source_fingerprint == int(v["masterFingerprint"], 16), f"{name}: fingerprint")
        check(req.address.hex() == r["address"][2:], f"{name}: address")
        check(req.sign_data.hex() == r["signData"], f"{name}: sign data")

        if req.data_type in (rq.DATA_TYPE_TX, rq.DATA_TYPE_TX_TYPED):
            msg, calldata = rq.transaction(req)
            digest = firmware_tx_digest(req, msg, calldata)
            check(digest.hex() == r["digest"], f"{name}: firmware tx digest")
            # The chain in the request must be the transaction's
            req.chain_id += 1
            expect_data_error(lambda: rq.transaction(req), f"{name}: chain mismatch")
        elif req.data_type == rq.DATA_TYPE_PERSONAL:
            digest = sign_message.message_digest(req.sign_data)
            check(digest.hex() == r["digest"], f"{name}: firmware message digest")
        elif name == "no-domain-type":
            # A domain value with no EIP712Domain type is hashed by nobody
            # (MetaMask v4 hashes an empty domain): the device refuses it
            # rather than sign something whose shown domain binds nothing
            expect_data_error(lambda: firmware_typed_digest(req.sign_data), f"{name}: refused")
        else:
            digest = firmware_typed_digest(req.sign_data)
            check(digest.hex() == r["digest"], f"{name}: firmware typed-data digest ({digest.hex()})")


def _reencode_request(fields, order=None):
    """A request map from (key, encoded value) pairs, in the given order"""
    out = bytearray()
    cbor._head(out, 5, len(fields))
    for key, val in fields if order is None else [fields[i] for i in order]:
        cbor._head(out, 0, key)
        out.extend(val)
    return bytes(out)


def test_hostile_requests(v):
    r0 = next(r for r in v["requests"] if r["name"] == "legacy")
    good = bytes.fromhex(r0["cbor"])
    rq.parse(good)

    # Every truncation, every trailing byte
    for n in range(len(good)):
        expect_data_error(lambda: rq.parse(good[:n]), f"truncated at {n}")
    expect_data_error(lambda: rq.parse(good + b"\x00"), "trailing byte")

    # Field by field, from a parse of the good one
    rid = cbor.encode(cbor.Tagged(37, bytes(range(16))))
    sd = cbor.encode(bytes.fromhex(r0["signData"]))
    path = lambda comps, fp=int(v["masterFingerprint"], 16): cbor.encode(  # noqa: E731
        cbor.Tagged(304, {1: comps, 2: fp})
    )
    good_path = [44, True, 60, True, 0, True, 0, False, 0, False]
    addr = cbor.encode(bytes.fromhex(r0["address"][2:]))
    base = [(1, rid), (2, sd), (3, cbor.encode(1)), (4, cbor.encode(1)), (5, path(good_path)), (6, addr)]
    rq.parse(_reencode_request(base))

    def variant(key, val):
        return [(k, val if k == key else x) for k, x in base]

    cases = {
        "request id untagged": variant(1, cbor.encode(bytes(16))),
        "request id 15 bytes": variant(1, cbor.encode(cbor.Tagged(37, bytes(15)))),
        "request id other tag": variant(1, cbor.encode(cbor.Tagged(38, bytes(16)))),
        "sign data as text": variant(2, cbor.encode("abc")),
        "sign data empty": variant(2, cbor.encode(b"")),
        "data type 0": variant(3, cbor.encode(0)),
        "data type 5": variant(3, cbor.encode(5)),
        "chain id 0": variant(4, cbor.encode(0)),
        "chain id 2^64": variant(4, b"\x1b" + (2**64 - 1).to_bytes(8, "big")[:-1] + b"\xff" if False else b"\x3b" + bytes(8)),
        "path untagged": variant(5, cbor.encode({1: good_path, 2: 1})),
        "path account 1": variant(5, path([44, True, 60, True, 1, True, 0, False, 0, False])),
        "path change 1": variant(5, path([44, True, 60, True, 0, True, 1, False, 0, False])),
        "path ledger live": variant(5, path([44, True, 60, True, 0, True, 0, False])),
        "path coin 1": variant(5, path([44, True, 1, True, 0, True, 0, False, 0, False])),
        "path hardened index": variant(5, path([44, True, 60, True, 0, True, 0, False, 0, True])),
        "path index 1000001": variant(5, path([44, True, 60, True, 0, True, 0, False, 1000001, False])),
        "path odd": variant(5, path([44, True, 60, True, 0, True, 0, False, 0])),
        "path index as bool": variant(5, path([44, True, 60, True, 0, True, 0, False, True, False])),
        "path 2^31": variant(5, path([44, True, 60, True, 0, True, 0, False, 2**31, False])),
        "path no fingerprint": variant(5, cbor.encode(cbor.Tagged(304, {1: good_path}))),
        "path wrong depth": variant(5, cbor.encode(cbor.Tagged(304, {1: good_path, 2: 1, 3: 4}))),
        "path unknown key": variant(5, cbor.encode(cbor.Tagged(304, {1: good_path, 2: 1, 4: 0}))),
        "fingerprint 2^32": variant(5, cbor.encode(cbor.Tagged(304, {1: good_path, 2: 2**32}))),
        "address 19 bytes": variant(6, cbor.encode(bytes(19))),
        "address 21 bytes": variant(6, cbor.encode(bytes(21))),
    }
    for what, fields in cases.items():
        expect_data_error(lambda: rq.parse(_reencode_request(fields)), what)
    for drop in (1, 2, 3, 5, 6):
        fields = [f for f in base if f[0] != drop]
        expect_data_error(lambda: rq.parse(_reencode_request(fields)), f"missing key {drop}")
    expect_data_error(lambda: rq.parse(_reencode_request(base + [(8, cbor.encode(1))])), "unknown key 8")
    expect_data_error(lambda: rq.parse(_reencode_request(base, [1, 0, 2, 3, 4, 5])), "keys out of order")
    expect_data_error(lambda: rq.parse(_reencode_request(base + [(6, addr)])), "duplicate key")
    # Indefinite-length map, non-shortest head, float key
    expect_data_error(lambda: rq.parse(b"\xbf" + good[1:]), "indefinite map")
    expect_data_error(lambda: rq.parse(b"\xb8\x06" + good[1:]), "non-shortest map head")
    expect_data_error(lambda: rq.parse(b"\xa1\xf9\x00\x00\x01"), "float key")
    expect_data_error(lambda: rq.parse(b"\xa1\x01\xd8\x25\x5b" + bytes(8)), "64-bit length")


def test_fuzz(v):
    rnd = random.Random(1)
    reqs = [r for r in v["requests"] if r["name"] != "oversize"]
    for _ in range(4000):
        r = rnd.choice(reqs)
        data = bytearray(bytes.fromhex(r["cbor"]))
        for _ in range(rnd.randint(1, 4)):
            at = rnd.randrange(len(data))
            op = rnd.random()
            if op < 0.6:
                data[at] = rnd.randrange(256)
            elif op < 0.8:
                del data[at]
            else:
                data.insert(at, rnd.randrange(256))

        def go():
            req = rq.parse(bytes(data))
            if req.data_type in (rq.DATA_TYPE_TX, rq.DATA_TYPE_TX_TYPED):
                msg, calldata = rq.transaction(req)
            elif req.data_type == rq.DATA_TYPE_TYPED_DATA:
                typed = td.TypedData(req.sign_data)
                firmware_typed_digest(req.sign_data)

        try:
            go()
        except DataError:
            pass
        except Exception as e:
            # Trezor's own flow may refuse what passed our checks with its own
            # DataError; anything else is a bug here
            FAILURES.append(f"fuzz: {e.__class__.__name__}: {e}")
            print("FAIL: fuzz", repr(e), bytes(data).hex()[:120])
            break
        COUNT[0] += 1


def test_rlp():
    req = rq.SignRequest()
    req.chain_id = 1
    req.address_n = [44 | H, 60 | H, H, 0, 0]

    def tx(data_type, payload):
        req.data_type = data_type
        req.sign_data = payload
        return rq.transaction(req)

    rlp = sys.modules["trezor.crypto.rlp"]

    def enc(items, prefix=b""):
        w = bytearray(prefix)
        rlp.write(w, items)
        return bytes(w)

    good = [b"\x07", b"\x04\xa8\x17\xc8\x00", b"\x52\x08", bytes(20), b"\x01", b"", 1, 0, 0]
    tx(rq.DATA_TYPE_TX, enc(good))
    bad = {
        "leading zero nonce": [b"\x00\x07"] + good[1:],
        "33-byte value": good[:4] + [bytes([1] * 33)] + good[5:],
        "19-byte to": good[:3] + [bytes(19)] + good[4:],
        "r set": good[:7] + [1, 0],
        "chain 2": good[:6] + [2, 0, 0],
        "chain 0": good[:6] + [0, 0, 0],
        "8 fields": good[:8],
        "data as list": good[:5] + [[b"\x01"]] + good[6:],
        "pre-EIP-155": good[:6],
    }
    for what, items in bad.items():
        expect_data_error(lambda: tx(rq.DATA_TYPE_TX, enc(items)), f"legacy: {what}")
    # Non-canonical encodings of the same transaction
    canon = enc(good)
    expect_data_error(lambda: tx(rq.DATA_TYPE_TX, canon + b"\x00"), "legacy: trailing byte")
    long_header = b"\xf8" + bytes([len(canon) - 1]) + canon[1:]
    expect_data_error(lambda: tx(rq.DATA_TYPE_TX, long_header), "legacy: long list header for a short list")
    expect_data_error(lambda: tx(rq.DATA_TYPE_TX, canon[:1] + b"\x81\x07" + canon[2:]), "legacy: 0x81 for a byte < 0x80")
    for n in range(len(canon)):
        expect_data_error(lambda: tx(rq.DATA_TYPE_TX, canon[:n]), f"legacy: truncated at {n}")
    # Deep nesting: refused, not recursed into
    deep = b"\xc1" * 2000 + b"\x80"
    expect_data_error(lambda: tx(rq.DATA_TYPE_TX, deep), "legacy: deep nesting")

    al = [[bytes(20), [bytes(32)]]]
    good2 = [1, b"\x07", b"\x01", b"\x02", b"\x52\x08", bytes(20), b"", b"\xaa", al]
    tx(rq.DATA_TYPE_TX_TYPED, enc(good2, b"\x02"))
    bad2 = {
        "type 1": (b"\x01", good2),
        "access key 31 bytes": (b"\x02", good2[:8] + [[[bytes(20), [bytes(31)]]]]),
        "access entry 3 items": (b"\x02", good2[:8] + [[[bytes(20), [], b""]]]),
        "access address 19": (b"\x02", good2[:8] + [[[bytes(19), []]]]),
        "access list bytes": (b"\x02", good2[:8] + [b"\x01"]),
        "chain 5": (b"\x02", [5] + good2[1:]),
        "10 fields": (b"\x02", good2 + [b""]),
    }
    for what, (prefix, items) in bad2.items():
        expect_data_error(lambda: tx(rq.DATA_TYPE_TX_TYPED, enc(items, prefix)), f"1559: {what}")


def test_json():
    ok = [
        b'{"a":[1,-2,0,true,false,null,"x"]}',
        b' { "s" : "\\u00e9\\ud83d\\ude00\\n\\"\\\\\\/" } ',
        '{"u":"Ünïcödé"}'.encode(),
        b"[]",
        b"123456789012345678901234567890",
    ]
    for doc in ok:
        check(td.parse_json(doc) == json.loads(doc), f"json {doc!r}")
    bad = [
        b'{"a":1,"a":2}',
        b'{"a":1,}',
        b"[1,]",
        b"[1 2]",
        b"1.5",
        b"1e3",
        b"-",
        b"01",
        b'"\\x"',
        b'"\\ud83d"',
        b'"\\ude00"',
        b'"\\u12"',
        b'"a\nb"',
        b'"unterminated',
        b"[" * 17 + b"]" * 17,
        b"tru",
        b"{} {}",
        b'"\xff"',
        b"",
        b"1" * 81,
    ]
    for doc in bad:
        expect_data_error(lambda: td.parse_json(doc), f"json {doc[:30]!r}")
    td.parse_json(b"[" * 16 + b"]" * 16)


def test_typed_values():
    def typed(types, message, primary="T", domain=None):
        doc = {"types": types, "primaryType": primary, "domain": domain or {}, "message": message}
        return json.dumps(doc).encode()

    def digest(*a, **k):
        return firmware_typed_digest(typed(*a, **k))

    T = lambda *fields: {"T": [{"name": n, "type": t} for n, t in fields]}  # noqa: E731
    digest(T(("a", "uint8")), {"a": 255})
    digest(T(("a", "uint8")), {"a": "0xff"})
    digest(T(("a", "int8")), {"a": -128})
    digest(T(("a", "bytes4")), {"a": "0x01020304"})
    digest(T(("a", "bool[2]")), {"a": [True, False]})
    bad = {
        "uint8 256": (T(("a", "uint8")), {"a": 256}),
        "uint8 -1": (T(("a", "uint8")), {"a": -1}),
        "int8 -129": (T(("a", "int8")), {"a": -129}),
        "int8 128": (T(("a", "int8")), {"a": "128"}),
        "uint as bool": (T(("a", "uint256")), {"a": True}),
        "uint as float string": (T(("a", "uint256")), {"a": "1.5"}),
        "uint7": (T(("a", "uint7")), {"a": 1}),
        "uint264": (T(("a", "uint264")), {"a": 1}),
        "bytes4 short": (T(("a", "bytes4")), {"a": "0x0102"}),
        "bytes33": (T(("a", "bytes33")), {"a": "0x00"}),
        "bytes odd hex": (T(("a", "bytes")), {"a": "0x123"}),
        "bytes no 0x": (T(("a", "bytes")), {"a": "1234"}),
        "address 19": (T(("a", "address")), {"a": "0x" + "00" * 19}),
        "bool as 1": (T(("a", "bool")), {"a": 1}),
        "string as number": (T(("a", "string")), {"a": 1}),
        "missing field": (T(("a", "uint8"), ("b", "uint8")), {"a": 1}),
        "fixed array short": (T(("a", "bool[2]")), {"a": [True]}),
        "fixed array long": (T(("a", "bool[2]")), {"a": [True, True, False]}),
        "array not a list": (T(("a", "bool[]")), {"a": True}),
        "nested arrays": (T(("a", "uint8[][]")), {"a": [[1]]}),
        "unknown type": (T(("a", "Foo")), {"a": {}}),
        "struct not a dict": ({"T": [{"name": "a", "type": "S"}], "S": [{"name": "x", "type": "uint8"}]}, {"a": 5}),
        "duplicate member": (T(("a", "uint8"), ("a", "uint8")), {"a": 1}),
        "member extra key": ({"T": [{"name": "a", "type": "uint8", "x": 1}]}, {"a": 1}),
        "array size 0": (T(("a", "bool[0]")), {"a": []}),
        "types not a dict": ([], {}),
    }
    for what, (types_, message) in bad.items():
        expect_data_error(lambda: digest(types_, message), f"typed: {what}")
    expect_data_error(lambda: digest(T(("a", "uint8")), {"a": 1}, primary="Nope"), "typed: unknown primary")
    expect_data_error(
        lambda: firmware_typed_digest(b'{"types":{},"primaryType":"T","domain":{},"message":{},"extra":1}'),
        "typed: extra top-level key",
    )


def test_domain_binding():
    """Audit finding: a domain value EIP712Domain does not declare is never
    hashed, so it must not pass for a chain binding (nor be accepted at all)"""

    def typed(domain_type, domain, request_chain=None):
        types = {"T": [{"name": "a", "type": "uint8"}]}
        if domain_type is not None:
            types["EIP712Domain"] = [{"name": n, "type": t} for n, t in domain_type]
        doc = {"types": types, "primaryType": "T", "domain": domain, "message": {"a": 1}}
        t = td.TypedData(json.dumps(doc).encode())
        t.check_chain(request_chain)
        return t

    contract = "0x" + "11" * 20
    declared = [("name", "string"), ("chainId", "uint256"), ("verifyingContract", "address")]
    t = typed(declared, {"name": "A", "chainId": 137, "verifyingContract": contract}, 137)
    check(t.domain_chain_id() == 137, "domain: declared chainId is read")
    typed(declared, {"name": "A", "chainId": 137, "verifyingContract": contract}, None)
    expect_data_error(
        lambda: typed([("name", "string"), ("verifyingContract", "address")],
                      {"name": "A", "verifyingContract": contract, "chainId": 137}, 137),
        "domain: undeclared chainId (the audit's attack)",
    )
    expect_data_error(
        lambda: typed([("name", "string")], {"name": "A", "verifyingContract": contract}),
        "domain: undeclared verifyingContract",
    )
    expect_data_error(lambda: typed(None, {"chainId": 1}), "domain: no EIP712Domain type, values present")
    expect_data_error(
        lambda: typed([("name", "string")], {"name": "A"}, 1),
        "domain: request names a network, domain signs none",
    )
    expect_data_error(
        lambda: typed(declared, {"name": "A", "chainId": 1, "verifyingContract": contract}, 137),
        "domain: chain differs from the request's",
    )
    deep = "uint8" + "[]" * 33
    expect_data_error(
        lambda: td.TypedData(json.dumps({"types": {"T": [{"name": "a", "type": deep}]},
                                         "primaryType": "T", "domain": {}, "message": {"a": []}}).encode()),
        "typed: deeply nested array type refused before recursing",
    )


def test_tx_signature():
    req = rq.SignRequest()
    r, s = bytes(range(32)), bytes(range(32, 64))
    req.data_type = rq.DATA_TYPE_TX
    check(rq.tx_signature(req, 1, 37, r, s)[64:] == bytes([37]), "legacy v chain 1")
    check(rq.tx_signature(req, 137, 35 + 274 + 1, r, s)[64:] == (310).to_bytes(2, "big"), "legacy v chain 137")
    big = 2**40
    check(rq.tx_signature(req, big, 1, r, s)[64:] == (35 + 2 * big + 1).to_bytes(6, "big"), "legacy v big chain")
    expect_data_error(lambda: rq.tx_signature(req, 1, 39, r, s), "legacy v off")
    req.data_type = rq.DATA_TYPE_TX_TYPED
    check(rq.tx_signature(req, 1, 0, r, s)[64:] == b"\x00", "1559 parity 0")
    check(rq.tx_signature(req, 1, 1, r, s)[64:] == b"\x01", "1559 parity 1")
    expect_data_error(lambda: rq.tx_signature(req, 1, 27, r, s), "1559 v 27")


def write_device_cbor(v, out):
    """The device's own CBOR, for ../run.sh to encode as URs (Rust) and decode
    with the app's libraries (JS)"""
    a = v["account"]
    hdkey = rq.hdkey_cbor(
        bytes.fromhex(a["publicKey"]),
        bytes.fromhex(a["chainCode"]),
        int(v["masterFingerprint"], 16),
        int(a["parentFingerprint"], 16),
    )
    req = rq.parse(bytes.fromhex(v["requests"][0]["cbor"]))
    sig = bytes(range(64)) + bytes([37])
    expect_data_error(lambda: rq.hdkey_cbor(bytes(33), bytes(32), 0, 1), "hdkey: zero master fingerprint")
    (out / "hdkey.hex").write_text(hdkey.hex())
    (out / "signature.hex").write_text(rq.signature_cbor(req, sig).hex())
    (out / "signature-input.json").write_text(json.dumps({"requestId": req.request_id.hex(), "signature": sig.hex()}))


def main():
    v = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for t in (test_requests, test_hostile_requests, test_fuzz):
        t(v)
    for t in (test_rlp, test_json, test_typed_values, test_domain_binding, test_tx_signature):
        t()
    write_device_cbor(v, out)
    print(f"{COUNT[0]} checks, {len(FAILURES)} failures")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
