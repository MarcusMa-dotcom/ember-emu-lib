"""BER encoding and decoding, as far as Ember+ uses it.

Ember+ encodes its data with the Basic Encoding Rules of ASN.1 (X.690). This
module knows the handful of types the Glow schema needs: BOOLEAN, INTEGER,
REAL, UTF8String, OCTET STRING, NULL, RELATIVE-OID and the constructed
containers.

Decoding accepts definite and indefinite lengths, because the Lawo libraries
write containers with indefinite length. Encoding always writes definite
lengths, which every Ember+ implementation reads.

REAL values follow the convention of the Lawo libraries, which differs from
X.690: the mantissa is read as 1.xxx, so its highest set bit has the weight
``2 ** exponent``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Tag classes
UNIVERSAL = 0
APPLICATION = 1
CONTEXT = 2
PRIVATE = 3

# Universal types
BOOLEAN = 1
INTEGER = 2
OCTET_STRING = 4
NULL = 5
REAL = 9
UTF8_STRING = 12
RELATIVE_OID = 13
SEQUENCE = 16
SET = 17

MAX_DEPTH = 200          # nesting of containers a decoder follows
MAX_REAL_MANTISSA = 16   # bytes


class BerError(ValueError):
    """The bytes are not valid BER, or use something Ember+ does not."""


@dataclass(frozen=True)
class Tlv:
    """One decoded BER value: its tag and either content bytes or children."""

    cls: int
    number: int
    constructed: bool
    content: bytes = b""
    children: tuple["Tlv", ...] = ()

    def is_universal(self, number: int) -> bool:
        """True if this is a universal value of the given type number."""
        return self.cls == UNIVERSAL and self.number == number

    def is_application(self, number: int) -> bool:
        """True if this value carries the application tag ``number``."""
        return self.cls == APPLICATION and self.number == number

    def is_context(self, number: int) -> bool:
        """True if this value carries the context tag ``[number]``."""
        return self.cls == CONTEXT and self.number == number


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def encode_tag(cls: int, number: int, constructed: bool) -> bytes:
    """The tag bytes for a class, a number and the constructed flag."""
    first = (cls << 6) | (0x20 if constructed else 0)
    if number < 31:
        return bytes([first | number])
    parts = [number & 0x7F]
    number >>= 7
    while number:
        parts.append((number & 0x7F) | 0x80)
        number >>= 7
    return bytes([first | 0x1F]) + bytes(reversed(parts))


def encode_length(length: int) -> bytes:
    """The length bytes: short form up to 127, long form above."""
    if length < 0x80:
        return bytes([length])
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def encode_tlv(cls: int, number: int, constructed: bool, content: bytes) -> bytes:
    """One complete value: tag, length and content."""
    return encode_tag(cls, number, constructed) + encode_length(len(content)) + content


def integer_content(value: int) -> bytes:
    """Two's complement, as short as possible."""
    bits = value.bit_length() if value >= 0 else (~value).bit_length()
    return value.to_bytes(bits // 8 + 1, "big", signed=True)


def real_content(value: float) -> bytes:
    """The content bytes of a REAL, in the binary form the Lawo libraries write."""
    if value == 0:
        return b"" if math.copysign(1.0, value) > 0 else b"\x43"
    if math.isnan(value):
        return b"\x42"
    if math.isinf(value):
        return b"\x40" if value > 0 else b"\x41"
    fraction, exponent = math.frexp(abs(value))      # fraction in [0.5, 1)
    mantissa = int(fraction * (1 << 53))             # exact: at most 53 bits
    mantissa >>= (mantissa & -mantissa).bit_length() - 1   # drop trailing zero bits
    exponent_bytes = integer_content(exponent - 1)
    first = 0x80 | (len(exponent_bytes) - 1) | (0x40 if value < 0 else 0)
    # Written like a signed number (leading zero where the top bit is set): the
    # Lawo C# library does the same, because the older EmberLib expects it.
    return bytes([first]) + exponent_bytes + integer_content(mantissa)


def relative_oid_content(numbers) -> bytes:
    """The content bytes of a RELATIVE-OID: every number in base 128.

    Raises:
        BerError: A number is negative.
    """
    out = bytearray()
    for number in numbers:
        if number < 0:
            raise BerError("A path must not contain negative numbers")
        parts = [number & 0x7F]
        number >>= 7
        while number:
            parts.append((number & 0x7F) | 0x80)
            number >>= 7
        out.extend(reversed(parts))
    return bytes(out)


def boolean(value: bool) -> bytes:
    """A BOOLEAN value."""
    return encode_tlv(UNIVERSAL, BOOLEAN, False, b"\xff" if value else b"\x00")


def integer(value: int) -> bytes:
    """An INTEGER value."""
    return encode_tlv(UNIVERSAL, INTEGER, False, integer_content(int(value)))


def real(value: float) -> bytes:
    """A REAL value."""
    return encode_tlv(UNIVERSAL, REAL, False, real_content(float(value)))


def utf8_string(value: str) -> bytes:
    """A UTF8String value."""
    return encode_tlv(UNIVERSAL, UTF8_STRING, False, value.encode("utf-8"))


def octet_string(value: bytes) -> bytes:
    """An OCTET STRING value."""
    return encode_tlv(UNIVERSAL, OCTET_STRING, False, bytes(value))


def null() -> bytes:
    """A NULL value."""
    return encode_tlv(UNIVERSAL, NULL, False, b"")


def relative_oid(numbers) -> bytes:
    """A RELATIVE-OID value, which Ember+ uses for paths."""
    return encode_tlv(UNIVERSAL, RELATIVE_OID, False, relative_oid_content(numbers))


def sequence(content: bytes) -> bytes:
    """A SEQUENCE around already encoded content."""
    return encode_tlv(UNIVERSAL, SEQUENCE, True, content)


def set_of(content: bytes) -> bytes:
    """A SET around already encoded content."""
    return encode_tlv(UNIVERSAL, SET, True, content)


def context(number: int, content: bytes) -> bytes:
    """An explicit context tag ``[number]`` around an already encoded value."""
    return encode_tlv(CONTEXT, number, True, content)


def application(number: int, content: bytes) -> bytes:
    """An implicit application tag ``[APPLICATION number]`` around the fields."""
    return encode_tlv(APPLICATION, number, True, content)


def value(item) -> bytes:
    """Encode a Python value as the Glow type ``Value``.

    ``bool`` becomes BOOLEAN, ``int`` INTEGER, ``float`` REAL, ``str``
    UTF8String, ``bytes`` OCTET STRING and ``None`` NULL.
    """
    if item is None:
        return null()
    if isinstance(item, bool):
        return boolean(item)
    if isinstance(item, int):
        return integer(item)
    if isinstance(item, float):
        return real(item)
    if isinstance(item, str):
        return utf8_string(item)
    if isinstance(item, (bytes, bytearray, memoryview)):
        return octet_string(bytes(item))
    raise BerError(f"A value of type {type(item).__name__} cannot be sent")


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def _read_header(data: bytes, pos: int, end: int) -> tuple[int, int, bool, int | None, int]:
    """Read tag and length. Returns (class, number, constructed, length, position)."""
    if pos >= end:
        raise BerError("Data ends where a tag is expected")
    first = data[pos]
    pos += 1
    cls = first >> 6
    constructed = bool(first & 0x20)
    number = first & 0x1F
    if number == 0x1F:
        number = 0
        for _ in range(6):
            if pos >= end:
                raise BerError("Data ends inside a tag")
            byte = data[pos]
            pos += 1
            number = (number << 7) | (byte & 0x7F)
            if not byte & 0x80:
                break
        else:
            raise BerError("Tag number is too large")
    if pos >= end:
        raise BerError("Data ends where a length is expected")
    first = data[pos]
    pos += 1
    if first < 0x80:
        return cls, number, constructed, first, pos
    if first == 0x80:
        if not constructed:
            raise BerError("Indefinite length on a primitive value")
        return cls, number, constructed, None, pos
    count = first & 0x7F
    if count > 8 or pos + count > end:
        raise BerError("Length field is too long or cut off")
    return cls, number, constructed, int.from_bytes(data[pos:pos + count], "big"), pos + count


def _parse(data: bytes, pos: int, end: int, depth: int) -> tuple[Tlv, int]:
    """Decode the value that starts at ``pos``. Returns it and the position behind it."""
    if depth > MAX_DEPTH:
        raise BerError("Data is nested too deeply")
    cls, number, constructed, length, pos = _read_header(data, pos, end)
    if length is not None:
        stop = pos + length
        if stop > end:
            raise BerError("A value is longer than the data that holds it")
        if not constructed:
            return Tlv(cls, number, False, bytes(data[pos:stop])), stop
        children = []
        while pos < stop:
            child, pos = _parse(data, pos, stop, depth + 1)
            children.append(child)
        return Tlv(cls, number, True, b"", tuple(children)), stop
    # Indefinite length: children up to the end-of-contents marker 00 00.
    children = []
    while True:
        if pos + 2 > end:
            raise BerError("Data ends before the end-of-contents marker")
        if data[pos] == 0 and data[pos + 1] == 0:
            return Tlv(cls, number, True, b"", tuple(children)), pos + 2
        child, pos = _parse(data, pos, end, depth + 1)
        children.append(child)


def decode(data: bytes) -> Tlv:
    """Decode exactly one BER value. Raises :class:`BerError` on bad input."""
    data = bytes(data)
    tlv, pos = _parse(data, 0, len(data), 0)
    if pos != len(data):
        raise BerError("Unexpected data after the value")
    return tlv


def to_integer(tlv: Tlv) -> int:
    """The number in an INTEGER.

    Raises:
        BerError: The value is constructed.
    """
    if tlv.constructed:
        raise BerError("INTEGER must be primitive")
    return int.from_bytes(tlv.content, "big", signed=True) if tlv.content else 0


def to_boolean(tlv: Tlv) -> bool:
    """The truth value in a BOOLEAN. Any byte other than zero counts as true."""
    if tlv.constructed:
        raise BerError("BOOLEAN must be primitive")
    return any(tlv.content)


def to_string(tlv: Tlv) -> str:
    """The text in a UTF8String. Bytes that are not valid UTF-8 become replacement characters."""
    if tlv.constructed:
        raise BerError("UTF8String must be primitive")
    return tlv.content.decode("utf-8", errors="replace")


def to_relative_oid(tlv: Tlv) -> tuple[int, ...]:
    """The numbers in a RELATIVE-OID.

    Raises:
        BerError: The value is cut off, or a number exceeds 32 bits.
    """
    if tlv.constructed:
        raise BerError("RELATIVE-OID must be primitive")
    numbers = []
    current = 0
    pending = False
    for byte in tlv.content:
        current = (current << 7) | (byte & 0x7F)
        pending = True
        if current > 0xFFFFFFFF:
            raise BerError("A path number is too large")
        if not byte & 0x80:
            numbers.append(current)
            current = 0
            pending = False
    if pending:
        raise BerError("RELATIVE-OID is cut off")
    return tuple(numbers)


def to_real(tlv: Tlv) -> float:
    """The number in a REAL.

    Reads the binary form with the Lawo convention for the mantissa, the special
    values for infinity, NaN and minus zero, and the decimal form.

    Raises:
        BerError: The value is cut off or uses a form that cannot be read.
    """
    if tlv.constructed:
        raise BerError("REAL must be primitive")
    content = tlv.content
    if not content:
        return 0.0
    first = content[0]
    if len(content) == 1 and first in (0x40, 0x41, 0x42, 0x43):
        return (math.inf, -math.inf, math.nan, -0.0)[first - 0x40]
    if first & 0x80:  # binary encoding
        exponent_length = (first & 0x03) + 1
        offset = 1
        if exponent_length == 4:
            if len(content) < 2:
                raise BerError("REAL is cut off")
            exponent_length = content[1]
            offset = 2
        if exponent_length < 1 or offset + exponent_length > len(content):
            raise BerError("REAL is cut off")
        exponent = int.from_bytes(content[offset:offset + exponent_length], "big", signed=True)
        raw = content[offset + exponent_length:]
        if len(raw) > MAX_REAL_MANTISSA:
            raise BerError("REAL mantissa is too long")
        mantissa = int.from_bytes(raw, "big")
        sign = -1.0 if first & 0x40 else 1.0
        if mantissa == 0:
            return sign * 0.0
        exponent *= (1, 3, 4, 1)[(first >> 4) & 0x03]  # base 2, 8 or 16
        try:
            # The highest set bit of the mantissa has the weight 2 ** exponent.
            return sign * math.ldexp(mantissa, exponent - (mantissa.bit_length() - 1))
        except OverflowError:
            return sign * math.inf
    if first & 0xC0 == 0:  # decimal encoding (ISO 6093), not used by Ember+ but valid BER
        try:
            return float(content[1:].decode("ascii").strip().replace(",", "."))
        except ValueError:
            raise BerError("REAL in decimal form cannot be read") from None
    raise BerError("REAL uses an unknown special value")


def to_value(tlv: Tlv):
    """Decode the Glow type ``Value`` into a Python value."""
    if tlv.cls != UNIVERSAL:
        raise BerError("A value must have a universal type")
    if tlv.number == INTEGER:
        return to_integer(tlv)
    if tlv.number == REAL:
        return to_real(tlv)
    if tlv.number == UTF8_STRING:
        return to_string(tlv)
    if tlv.number == BOOLEAN:
        return to_boolean(tlv)
    if tlv.number == OCTET_STRING:
        if tlv.constructed:
            raise BerError("OCTET STRING must be primitive")
        return tlv.content
    if tlv.number == NULL:
        return None
    raise BerError(f"A value of universal type {tlv.number} is not part of Ember+")
