# Ambire boards: EIP-712 typed data from an eth-sign-request (JSON), answered
# to Trezor's sign_typed_data flow the way trezorlib's host side answers it,
# with MetaMask's signTypedData_v4 value rules (what the app hashes with).
#
# The JSON is hostile: a strict parser (RFC 8259, integers only: EIP-712 has
# no fractions), bounded nesting, no duplicate keys; then every type is
# resolved and every value checked before it is encoded. MicroPython's json
# module is not built into the firmware.

from micropython import const
from typing import TYPE_CHECKING

from trezor.enums import EthereumDataType
from trezor.wire import DataError

if TYPE_CHECKING:
    from buffer_types import AnyBytes
    from typing import Any

    from trezor.messages import (
        EthereumFieldType,
        EthereumTypedDataStructAck,
        EthereumTypedDataValueAck,
    )

_MAX_DEPTH = const(16)
_MAX_NUMBER_DIGITS = const(80)
_MAX_NAME = const(64)


def _fail(what: str = "Malformed typed data") -> DataError:
    return DataError(what)


# --- JSON --------------------------------------------------------------------------


class _Json:
    def __init__(self, data: AnyBytes) -> None:
        self.s = data
        self.i = 0

    def ws(self) -> None:
        s = self.s
        while self.i < len(s) and s[self.i] in b" \t\r\n":
            self.i += 1

    def peek(self) -> int:
        if self.i >= len(self.s):
            raise _fail()
        return self.s[self.i]

    def expect(self, c: int) -> None:
        if self.peek() != c:
            raise _fail()
        self.i += 1

    def value(self, depth: int) -> Any:
        if depth >= _MAX_DEPTH:
            raise _fail("Typed data nested too deeply")
        self.ws()
        c = self.peek()
        if c == 0x7B:  # {
            self.i += 1
            obj = {}
            self.ws()
            if self.peek() == 0x7D:
                self.i += 1
                return obj
            while True:
                self.ws()
                key = self.string()
                if key in obj:
                    raise _fail("Duplicate key in typed data")
                self.ws()
                self.expect(0x3A)  # :
                obj[key] = self.value(depth + 1)
                self.ws()
                c = self.peek()
                self.i += 1
                if c == 0x7D:
                    return obj
                if c != 0x2C:  # ,
                    raise _fail()
        if c == 0x5B:  # [
            self.i += 1
            arr = []
            self.ws()
            if self.peek() == 0x5D:
                self.i += 1
                return arr
            while True:
                arr.append(self.value(depth + 1))
                self.ws()
                c = self.peek()
                self.i += 1
                if c == 0x5D:
                    return arr
                if c != 0x2C:
                    raise _fail()
        if c == 0x22:  # "
            return self.string()
        for word, val in ((b"true", True), (b"false", False), (b"null", None)):
            if self.s[self.i : self.i + len(word)] == word:
                self.i += len(word)
                return val
        return self.number()

    def number(self) -> int:
        s = self.s
        start = self.i
        if self.peek() == 0x2D:  # -
            self.i += 1
        digits = self.i
        while self.i < len(s) and 0x30 <= s[self.i] <= 0x39:
            self.i += 1
        n = self.i - digits
        if n == 0 or n > _MAX_NUMBER_DIGITS or (n > 1 and s[digits] == 0x30):
            raise _fail()
        if self.i < len(s) and s[self.i] in b".eE":
            raise _fail("Fractions are not EIP-712 values")
        return int(bytes(s[start : self.i]).decode())

    def string(self) -> str:
        s = self.s
        self.expect(0x22)
        out = bytearray()
        while True:
            c = self.peek()
            self.i += 1
            if c == 0x22:
                break
            if c < 0x20:
                raise _fail()
            if c != 0x5C:  # backslash
                out.append(c)
                continue
            e = self.peek()
            self.i += 1
            simple = b'"\\/bfnrt'.find(bytes([e]))
            if simple >= 0:
                out.append(b'"\\/\b\f\n\r\t'[simple])
                continue
            if e != 0x75:  # u
                raise _fail()
            cp = self.hex4()
            if 0xD800 <= cp < 0xDC00:
                # A high surrogate: its low one must follow
                if s[self.i : self.i + 2] != b"\\u":
                    raise _fail()
                self.i += 2
                low = self.hex4()
                if not 0xDC00 <= low < 0xE000:
                    raise _fail()
                cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00)
            elif 0xDC00 <= cp < 0xE000:
                raise _fail()
            out.extend(chr(cp).encode())
        try:
            return bytes(out).decode()
        except UnicodeError:
            raise _fail("Typed data is not UTF-8")

    def hex4(self) -> int:
        h = self.s[self.i : self.i + 4]
        if len(h) != 4 or not all(c in b"0123456789abcdefABCDEF" for c in h):
            raise _fail()
        self.i += 4
        return int(bytes(h).decode(), 16)


def parse_json(data: AnyBytes) -> Any:
    j = _Json(data)
    value = j.value(0)
    j.ws()
    if j.i != len(data):
        raise _fail()
    return value


# --- Types -------------------------------------------------------------------------


def _is_array(type_name: str) -> bool:
    return type_name.endswith("]")


def _array_entry(type_name: str) -> str:
    return type_name[: type_name.rfind("[")]


def _array_size(type_name: str) -> int | None:
    n = type_name[type_name.rfind("[") + 1 : -1]
    if not n:
        return None
    if not n.isdigit() or n[0] == "0" or len(n) > 5:
        raise _fail("Invalid array size")
    return int(n)


def _bits(type_name: str, prefix: str) -> int | None:
    """N of "<prefix>N", or None if `type_name` is not one"""
    if not type_name.startswith(prefix):
        return None
    n = type_name[len(prefix) :]
    if not n.isdigit() or n[0] == "0":
        return None
    return int(n)


def _hex(value: Any) -> bytes:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) % 2:
        raise _fail("Expected a 0x-prefixed hex string")
    digits = value[2:]
    if not all(c in "0123456789abcdefABCDEF" for c in digits):
        raise _fail("Expected a 0x-prefixed hex string")
    return bytes.fromhex(digits)


def _integer(value: Any) -> int:
    # signTypedData_v4 takes numbers, decimal strings and 0x hex strings
    if isinstance(value, bool):
        raise _fail("Expected an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        neg = value.startswith("-")
        body = value[1:] if neg else value
        if body.startswith("0x"):
            digits, base = body[2:], 16
            ok = all(c in "0123456789abcdefABCDEF" for c in digits)
        else:
            digits, base = body, 10
            ok = digits.isdigit()
        if ok and 0 < len(digits) <= _MAX_NUMBER_DIGITS:
            v = int(digits, base)
            return -v if neg else v
    raise _fail("Expected an integer")


class TypedData:
    def __init__(self, data: AnyBytes) -> None:
        root = parse_json(data)
        if not isinstance(root, dict) or sorted(root.keys()) != [
            "domain",
            "message",
            "primaryType",
            "types",
        ]:
            raise _fail("Typed data needs exactly types, primaryType, domain, message")
        types = root["types"]
        if not isinstance(types, dict):
            raise _fail()
        # As signTypedData_v4 (and trezorlib) do: no EIP712Domain type, an
        # empty domain
        if "EIP712Domain" not in types:
            types["EIP712Domain"] = []
        for name, members in types.items():
            if not name or len(name) > _MAX_NAME or not isinstance(members, list):
                raise _fail("Invalid type")
            names = []
            for m in members:
                if (
                    not isinstance(m, dict)
                    or sorted(m.keys()) != ["name", "type"]
                    or not isinstance(m["name"], str)
                    or not isinstance(m["type"], str)
                    or not m["name"]
                    or len(m["name"]) > _MAX_NAME
                    or m["name"] in names
                ):
                    raise _fail("Invalid type member")
                names.append(m["name"])
        self.types: dict[str, list[dict[str, str]]] = types
        # Every member type must resolve
        for members in types.values():
            for m in members:
                self.field_type(m["type"])

        self.primary_type = root["primaryType"]
        if not isinstance(self.primary_type, str) or self.primary_type not in types:
            raise _fail("Unknown primary type")
        self.domain = root["domain"]
        self.message = root["message"]
        if not isinstance(self.domain, dict) or not isinstance(self.message, dict):
            raise _fail()

    def domain_chain_id(self) -> int | None:
        chain_id = self.domain.get("chainId")
        return None if chain_id is None else _integer(chain_id)

    def field_type(self, type_name: str) -> EthereumFieldType:
        """As trezorlib's get_field_type, with every size checked"""
        from trezor.messages import EthereumFieldType

        EDT = EthereumDataType
        size = None
        entry_type = None
        struct_name = None
        if len(type_name) > _MAX_NAME + 8:
            raise _fail("Invalid type")
        if _is_array(type_name):
            data_type = EDT.ARRAY
            size = _array_size(type_name)
            entry_type = self.field_type(_array_entry(type_name))
            if entry_type.data_type == EDT.ARRAY:
                raise _fail("Nested arrays are not supported")
        elif (bits := _bits(type_name, "uint")) is not None:
            data_type = EDT.UINT
            size = bits // 8
        elif (bits := _bits(type_name, "int")) is not None:
            data_type = EDT.INT
            size = bits // 8
        elif type_name == "bytes":
            data_type = EDT.BYTES
        elif (bits := _bits(type_name, "bytes")) is not None:
            data_type = EDT.BYTES
            size = bits
            if size > 32:
                raise _fail("Invalid type")
        elif type_name == "string":
            data_type = EDT.STRING
        elif type_name == "bool":
            data_type = EDT.BOOL
        elif type_name == "address":
            data_type = EDT.ADDRESS
        elif type_name in self.types:
            data_type = EDT.STRUCT
            size = len(self.types[type_name])
            struct_name = type_name
        else:
            raise _fail("Unknown type")
        if data_type in (EDT.UINT, EDT.INT) and (
            bits is None or bits % 8 or not 8 <= bits <= 256
        ):
            raise _fail("Invalid integer type")
        return EthereumFieldType(
            data_type=data_type,
            size=size,
            entry_type=entry_type,
            struct_name=struct_name,
        )

    def struct_ack(self, name: str) -> EthereumTypedDataStructAck:
        from trezor.messages import EthereumStructMember, EthereumTypedDataStructAck

        if name not in self.types:
            raise _fail("Unknown type")
        return EthereumTypedDataStructAck(
            members=[
                EthereumStructMember(type=self.field_type(m["type"]), name=m["name"])
                for m in self.types[name]
            ]
        )

    def value_ack(self, member_path: list[int]) -> EthereumTypedDataValueAck:
        from trezor.messages import EthereumTypedDataValueAck

        if not member_path or member_path[0] not in (0, 1):
            raise _fail("Invalid member path")
        if member_path[0] == 0:
            type_name, value = "EIP712Domain", self.domain
        else:
            type_name, value = self.primary_type, self.message
        for index in member_path[1:]:
            if _is_array(type_name):
                size = _array_size(type_name)
                if not isinstance(value, list) or (size is not None and len(value) != size):
                    raise _fail("Array does not match its type")
                if index >= len(value):
                    raise _fail("Invalid member path")
                type_name, value = _array_entry(type_name), value[index]
            elif type_name in self.types:
                members = self.types[type_name]
                if not isinstance(value, dict) or index >= len(members):
                    raise _fail("Struct does not match its type")
                name = members[index]["name"]
                if name not in value:
                    raise _fail("Missing value: " + name)
                type_name, value = members[index]["type"], value[name]
            else:
                raise _fail("Invalid member path")

        if _is_array(type_name):
            # The flow asks for a dynamic array's length first
            size = _array_size(type_name)
            if not isinstance(value, list) or (size is not None and len(value) != size):
                raise _fail("Array does not match its type")
            if len(value) > 0xFFFF:
                raise _fail("Array too long")
            return EthereumTypedDataValueAck(value=len(value).to_bytes(2, "big"))
        return EthereumTypedDataValueAck(value=self.encode(type_name, value))

    def encode(self, type_name: str, value: Any) -> bytes:
        """An atomic value, as trezorlib's encode_data sends it"""
        EDT = EthereumDataType
        field = self.field_type(type_name)
        t = field.data_type
        if t == EDT.BYTES:
            data = _hex(value)
            if field.size is not None and len(data) != field.size:
                raise _fail("Wrong length for " + type_name)
            return data
        if t == EDT.STRING:
            if not isinstance(value, str):
                raise _fail("Expected a string")
            return value.encode()
        if t in (EDT.UINT, EDT.INT):
            assert field.size is not None
            v = _integer(value)
            bits = 8 * field.size
            if t == EDT.UINT:
                ok = 0 <= v < 1 << bits
            else:
                ok = -(1 << (bits - 1)) <= v < 1 << (bits - 1)
                if v < 0:
                    v += 1 << bits  # two's complement
            if not ok:
                raise _fail("Value out of range for " + type_name)
            return v.to_bytes(field.size, "big")
        if t == EDT.BOOL:
            if not isinstance(value, bool):
                raise _fail("Expected true or false")
            return b"\x01" if value else b"\x00"
        if t == EDT.ADDRESS:
            data = _hex(value)
            if len(data) != 20:
                raise _fail("Invalid address")
            return data
        # Structs and arrays are never asked for whole
        raise _fail("Invalid member path")
