# Ambire boards: the CBOR the airgapped QR flow reads and writes.
#
# The reader is for hostile input: definite lengths only, every head in its
# shortest form, no floats, simple values other than true/false refused, and
# every length checked against what is left. It reads one item at a time, so
# the caller walks the structure it expects (no recursion, no generic decode).
# The encoder writes canonical CBOR (RFC 8949, 4.2.1): shortest heads, map
# keys in order. apps.common.cbor is not used: it writes True as 1 (bool is an
# int in MicroPython) and its decoder takes indefinite lengths and any nesting.

from micropython import const
from typing import TYPE_CHECKING

from trezor.wire import DataError

if TYPE_CHECKING:
    from buffer_types import AnyBytes
    from typing import Any

_UINT = const(0)
_BYTES = const(2)
_TEXT = const(3)
_ARRAY = const(4)
_MAP = const(5)
_TAG = const(6)
_SIMPLE = const(7)


class Tagged:
    def __init__(self, tag: int, value: Any) -> None:
        self.tag = tag
        self.value = value


def _head(out: bytearray, major: int, arg: int) -> None:
    m = major << 5
    if arg < 24:
        out.append(m | arg)
    elif arg < 0x100:
        out.append(m | 24)
        out.append(arg)
    elif arg < 0x1_0000:
        out.append(m | 25)
        out.extend(arg.to_bytes(2, "big"))
    elif arg < 0x1_0000_0000:
        out.append(m | 26)
        out.extend(arg.to_bytes(4, "big"))
    else:
        out.append(m | 27)
        out.extend(arg.to_bytes(8, "big"))


def _encode(out: bytearray, value: Any) -> None:
    # bool first: it is an int too
    if isinstance(value, bool):
        out.append(0xF5 if value else 0xF4)
    elif isinstance(value, int):
        if value < 0:
            raise ValueError  # not used here
        _head(out, _UINT, value)
    elif isinstance(value, (bytes, bytearray)):
        _head(out, _BYTES, len(value))
        out.extend(value)
    elif isinstance(value, str):
        data = value.encode()
        _head(out, _TEXT, len(data))
        out.extend(data)
    elif isinstance(value, (list, tuple)):
        _head(out, _ARRAY, len(value))
        for item in value:
            _encode(out, item)
    elif isinstance(value, dict):
        # Small unsigned keys only: their order is numeric order
        _head(out, _MAP, len(value))
        for key in sorted(value):
            _encode(out, key)
            _encode(out, value[key])
    elif isinstance(value, Tagged):
        _head(out, _TAG, value.tag)
        _encode(out, value.value)
    else:
        raise TypeError


def encode(value: Any) -> bytes:
    out = bytearray()
    _encode(out, value)
    return bytes(out)


class Reader:
    def __init__(self, data: AnyBytes) -> None:
        self.data = data
        self.pos = 0

    def _fail(self) -> DataError:
        return DataError("Malformed request")

    def _head(self, major: int) -> int:
        data = self.data
        if self.pos >= len(data):
            raise self._fail()
        first = data[self.pos]
        self.pos += 1
        if first >> 5 != major:
            raise self._fail()
        info = first & 0x1F
        if info < 24:
            return info
        if info > 27:
            # Indefinite lengths and reserved values
            raise self._fail()
        size = 1 << (info - 24)
        end = self.pos + size
        if end > len(data):
            raise self._fail()
        arg = int.from_bytes(data[self.pos : end], "big")
        self.pos = end
        # Shortest form only
        if arg < (24 if size == 1 else 1 << (4 * size)):
            raise self._fail()
        return arg

    def uint(self, limit: int = 0xFFFF_FFFF_FFFF_FFFF) -> int:
        v = self._head(_UINT)
        if v > limit:
            raise self._fail()
        return v

    def _string(self, major: int, max_len: int) -> AnyBytes:
        n = self._head(major)
        if n > max_len or self.pos + n > len(self.data):
            raise self._fail()
        s = self.data[self.pos : self.pos + n]
        self.pos += n
        return s

    def byte_string(self, max_len: int) -> bytes:
        return bytes(self._string(_BYTES, max_len))

    def text_string(self, max_len: int) -> str:
        try:
            return bytes(self._string(_TEXT, max_len)).decode()
        except UnicodeError:
            raise self._fail()

    def boolean(self) -> bool:
        v = self._head(_SIMPLE)
        if v not in (20, 21):
            raise self._fail()
        return v == 21

    def tag(self, tag: int) -> None:
        if self._head(_TAG) != tag:
            raise self._fail()

    def array(self, max_items: int) -> int:
        n = self._head(_ARRAY)
        if n > max_items:
            raise self._fail()
        return n

    def map(self, max_entries: int) -> int:
        n = self._head(_MAP)
        if n > max_entries:
            raise self._fail()
        return n

    def keys(self, count: int) -> Any:
        """Yields `count` map keys: small unsigned ints, strictly ascending
        (canonical order: no duplicates)"""
        last = -1
        for _ in range(count):
            key = self.uint(0xFFFF)
            if key <= last:
                raise self._fail()
            last = key
            yield key

    def end(self) -> None:
        if self.pos != len(self.data):
            raise self._fail()
