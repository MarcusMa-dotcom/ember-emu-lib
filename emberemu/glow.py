"""Glow, the data schema of Ember+: what travels inside an S101 message.

This module turns the bytes of one message into plain Python objects and
back. It follows the Glow DTD 2.50 from the Ember+ documentation. It keeps
exactly the fields that were sent, so "contents with only a value" stays
distinguishable from "contents with everything".

Most programs do not need this module. :class:`emberemu.Provider` and
:class:`emberemu.Consumer` work with the tree classes of ``emberemu.model``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from . import ber
from .ber import BerError, Tlv

# Application tags of the Glow types
PARAMETER = 1
COMMAND = 2
NODE = 3
ELEMENT_COLLECTION = 4
STREAM_ENTRY = 5
STREAM_COLLECTION = 6
STRING_INTEGER_PAIR = 7
STRING_INTEGER_COLLECTION = 8
QUALIFIED_PARAMETER = 9
QUALIFIED_NODE = 10
ROOT_ELEMENT_COLLECTION = 11
STREAM_DESCRIPTION = 12
MATRIX = 13
TARGET = 14
SOURCE = 15
CONNECTION = 16
QUALIFIED_MATRIX = 17
LABEL = 18
FUNCTION = 19
QUALIFIED_FUNCTION = 20
TUPLE_ITEM_DESCRIPTION = 21
INVOCATION = 22
INVOCATION_RESULT = 23
TEMPLATE = 24
QUALIFIED_TEMPLATE = 25
ROOT = 0

MAX_PATH_LENGTH = 100   # numbers in the path of an element


class GlowError(ValueError):
    """The data is valid BER but not a valid Glow message."""


class ParameterType(IntEnum):
    """The type of a parameter value."""
    NULL = 0
    INTEGER = 1
    REAL = 2
    STRING = 3
    BOOLEAN = 4
    TRIGGER = 5
    ENUM = 6
    OCTETS = 7


class Access(IntEnum):
    """What consumers may do with a parameter."""
    NONE = 0
    READ = 1
    WRITE = 2
    READ_WRITE = 3


class CommandType(IntEnum):
    """The commands a consumer can send."""
    SUBSCRIBE = 30
    UNSUBSCRIBE = 31
    GET_DIRECTORY = 32
    INVOKE = 33


class FieldFlags(IntEnum):
    """Which properties a GetDirectory asks for."""

    SPARSE = -2
    ALL = -1
    DEFAULT = 0
    IDENTIFIER = 1
    DESCRIPTION = 2
    TREE = 3
    VALUE = 4
    CONNECTIONS = 5


class MatrixType(IntEnum):
    """How many sources a target takes, and how many targets a source may feed."""
    ONE_TO_N = 0
    ONE_TO_ONE = 1
    N_TO_N = 2


class MatrixAddressingMode(IntEnum):
    """Whether signals are numbered 0 to count - 1 (linear) or listed one by one (non-linear)."""
    LINEAR = 0
    NON_LINEAR = 1


class ConnectionOperation(IntEnum):
    """What a connection request does with the sources it names."""
    ABSOLUTE = 0
    CONNECT = 1
    DISCONNECT = 2


class ConnectionDisposition(IntEnum):
    """What a reported connection means."""
    TALLY = 0
    MODIFIED = 1
    PENDING = 2
    LOCKED = 3


class StreamFormat(IntEnum):
    """How a number is stored inside the byte block of a stream."""
    UINT8 = 0
    UINT16_BE = 2
    UINT16_LE = 3
    UINT32_BE = 4
    UINT32_LE = 5
    UINT64_BE = 6
    UINT64_LE = 7
    INT8 = 8
    INT16_BE = 10
    INT16_LE = 11
    INT32_BE = 12
    INT32_LE = 13
    INT64_BE = 14
    INT64_LE = 15
    FLOAT32_BE = 20
    FLOAT32_LE = 21
    FLOAT64_BE = 22
    FLOAT64_LE = 23


@dataclass
class Connection:
    """The sources connected to one target of a matrix."""

    target: int
    sources: tuple[int, ...] | None = None
    operation: int | None = None
    disposition: int | None = None


@dataclass
class Invocation:
    """A function call: its id and the argument values."""
    invocation_id: int | None = None
    arguments: list = field(default_factory=list)


@dataclass
class InvocationResult:
    """The answer to a function call."""
    invocation_id: int
    success: bool = True
    result: list = field(default_factory=list)


@dataclass
class StreamEntry:
    """One value of a stream, addressed by its stream identifier."""
    stream_identifier: int
    value: Any = None


@dataclass
class GlowElement:
    """One element as it appears on the wire.

    ``kind`` is ``"node"``, ``"parameter"``, ``"function"``, ``"matrix"``,
    ``"command"`` or ``"template"``. A qualified element has ``path`` set and
    ``number`` empty, a plain one the other way round. ``contents`` holds only
    the fields that were sent, ``None`` if there was no contents block.
    """

    kind: str
    number: int | None = None
    path: tuple[int, ...] | None = None
    contents: dict[str, Any] | None = None
    children: list["GlowElement"] | None = None
    targets: list[int] | None = None
    sources: list[int] | None = None
    connections: list[Connection] | None = None
    dir_field_mask: int | None = None
    invocation: Invocation | None = None


@dataclass
class Root:
    """One Glow message: elements, stream values or the result of a function call."""

    elements: list[GlowElement] | None = None
    streams: list[StreamEntry] | None = None
    invocation_result: InvocationResult | None = None


# Contents fields: tag number -> (name, kind)
NODE_FIELDS = {
    0: ("identifier", "string"),
    1: ("description", "string"),
    2: ("is_root", "boolean"),
    3: ("is_online", "boolean"),
    4: ("schema_identifiers", "string"),
    5: ("template_reference", "path"),
}
PARAMETER_FIELDS = {
    0: ("identifier", "string"),
    1: ("description", "string"),
    2: ("value", "value"),
    3: ("minimum", "value"),
    4: ("maximum", "value"),
    5: ("access", "integer"),
    6: ("format", "string"),
    7: ("enumeration", "string"),
    8: ("factor", "integer"),
    9: ("is_online", "boolean"),
    10: ("formula", "string"),
    11: ("step", "integer"),
    12: ("default", "value"),
    13: ("type", "integer"),
    14: ("stream_identifier", "integer"),
    15: ("enum_map", "enum_map"),
    16: ("stream_descriptor", "stream_descriptor"),
    17: ("schema_identifiers", "string"),
    18: ("template_reference", "path"),
}
MATRIX_FIELDS = {
    0: ("identifier", "string"),
    1: ("description", "string"),
    2: ("type", "integer"),
    3: ("addressing_mode", "integer"),
    4: ("target_count", "integer"),
    5: ("source_count", "integer"),
    6: ("maximum_total_connects", "integer"),
    7: ("maximum_connects_per_target", "integer"),
    8: ("parameters_location", "parameters_location"),
    9: ("gain_parameter_number", "integer"),
    10: ("labels", "labels"),
    11: ("schema_identifiers", "string"),
    12: ("template_reference", "path"),
}
FUNCTION_FIELDS = {
    0: ("identifier", "string"),
    1: ("description", "string"),
    2: ("arguments", "tuple_description"),
    3: ("result", "tuple_description"),
    4: ("template_reference", "path"),
}
FIELDS = {"node": NODE_FIELDS, "parameter": PARAMETER_FIELDS,
          "matrix": MATRIX_FIELDS, "function": FUNCTION_FIELDS}
_TAGS = {kind: {name: (tag, how) for tag, (name, how) in table.items()}
         for kind, table in FIELDS.items()}

_PLAIN = {"node": NODE, "parameter": PARAMETER, "matrix": MATRIX, "function": FUNCTION,
          "template": TEMPLATE}
_QUALIFIED = {"node": QUALIFIED_NODE, "parameter": QUALIFIED_PARAMETER,
              "matrix": QUALIFIED_MATRIX, "function": QUALIFIED_FUNCTION,
              "template": QUALIFIED_TEMPLATE}
_KINDS = {tag: (kind, False) for kind, tag in _PLAIN.items()}
_KINDS.update({tag: (kind, True) for kind, tag in _QUALIFIED.items()})


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def _encode_field(how: str, item: Any) -> bytes:
    """Encode one contents field according to its kind."""
    if how == "string":
        return ber.utf8_string(str(item))
    if how == "integer":
        return ber.integer(int(item))
    if how == "boolean":
        return ber.boolean(bool(item))
    if how == "value":
        return ber.value(item)
    if how == "path":
        return ber.relative_oid(item)
    if how == "enum_map":
        pairs = b"".join(
            ber.context(0, ber.application(
                STRING_INTEGER_PAIR,
                ber.context(0, ber.utf8_string(str(name))) + ber.context(1, ber.integer(int(number)))))
            for name, number in item)
        return ber.application(STRING_INTEGER_COLLECTION, pairs)
    if how == "stream_descriptor":
        stream_format, offset = item
        return ber.application(
            STREAM_DESCRIPTION,
            ber.context(0, ber.integer(int(stream_format))) + ber.context(1, ber.integer(int(offset))))
    if how == "parameters_location":
        return ber.integer(item) if isinstance(item, int) else ber.relative_oid(item)
    if how == "labels":
        return ber.sequence(b"".join(
            ber.context(0, ber.application(
                LABEL, ber.context(0, ber.relative_oid(path)) + ber.context(1, ber.utf8_string(str(text)))))
            for path, text in item))
    if how == "tuple_description":
        items = []
        for item_type, name in item:
            body = ber.context(0, ber.integer(int(item_type)))
            if name is not None:
                body += ber.context(1, ber.utf8_string(str(name)))
            items.append(ber.context(0, ber.application(TUPLE_ITEM_DESCRIPTION, body)))
        return ber.sequence(b"".join(items))
    raise GlowError(f"Unknown field kind {how!r}")


def _encode_contents(kind: str, contents: dict[str, Any]) -> bytes:
    """Encode a contents block as a SET, with the fields ordered by tag."""
    tags = _TAGS[kind]
    parts = []
    for name, item in contents.items():
        if name not in tags:
            raise GlowError(f"A {kind} has no field {name!r}")
        tag, how = tags[name]
        parts.append((tag, ber.context(tag, _encode_field(how, item))))
    parts.sort(key=lambda part: part[0])
    return ber.set_of(b"".join(data for _, data in parts))


def _encode_tuple(values) -> bytes:
    """Encode values as the Glow type Tuple."""
    return ber.sequence(b"".join(ber.context(0, ber.value(item)) for item in values))


def _encode_signals(tag: int, numbers) -> bytes:
    """Encode the target or source numbers of a matrix."""
    return ber.sequence(b"".join(
        ber.context(0, ber.application(tag, ber.context(0, ber.integer(number)))) for number in numbers))


def _encode_connection(connection: Connection) -> bytes:
    """Encode one connection of a matrix."""
    body = ber.context(0, ber.integer(connection.target))
    if connection.sources is not None:
        body += ber.context(1, ber.relative_oid(connection.sources))
    if connection.operation is not None:
        body += ber.context(2, ber.integer(int(connection.operation)))
    if connection.disposition is not None:
        body += ber.context(3, ber.integer(int(connection.disposition)))
    return ber.application(CONNECTION, body)


def _encode_command(element: GlowElement) -> bytes:
    """Encode a command with its field mask or its invocation."""
    if element.number is None:
        raise GlowError("A command needs its number")
    body = ber.context(0, ber.integer(int(element.number)))
    if element.dir_field_mask is not None:
        body += ber.context(1, ber.integer(int(element.dir_field_mask)))
    elif element.invocation is not None:
        invocation = b""
        if element.invocation.invocation_id is not None:
            invocation += ber.context(0, ber.integer(element.invocation.invocation_id))
        if element.invocation.arguments:
            invocation += ber.context(1, _encode_tuple(element.invocation.arguments))
        body += ber.context(2, ber.application(INVOCATION, invocation))
    return ber.application(COMMAND, body)


def encode_element(element: GlowElement) -> bytes:
    """Encode one element with everything it carries.

    Raises:
        GlowError: The element has neither number nor path, or holds a field its kind does not have.
    """
    if element.kind == "command":
        return _encode_command(element)
    if element.kind not in _PLAIN or element.kind == "template":
        raise GlowError(f"An element of kind {element.kind!r} cannot be sent")
    if element.path is not None:
        tag = _QUALIFIED[element.kind]
        body = ber.context(0, ber.relative_oid(element.path))
    elif element.number is not None:
        tag = _PLAIN[element.kind]
        body = ber.context(0, ber.integer(element.number))
    else:
        raise GlowError("An element needs a number or a path")
    if element.contents is not None:
        body += ber.context(1, _encode_contents(element.kind, element.contents))
    if element.children is not None:
        children = b"".join(ber.context(0, encode_element(child)) for child in element.children)
        body += ber.context(2, ber.application(ELEMENT_COLLECTION, children))
    if element.kind == "matrix":
        if element.targets is not None:
            body += ber.context(3, _encode_signals(TARGET, element.targets))
        if element.sources is not None:
            body += ber.context(4, _encode_signals(SOURCE, element.sources))
        if element.connections is not None:
            body += ber.context(5, ber.sequence(b"".join(
                ber.context(0, _encode_connection(connection)) for connection in element.connections)))
    return ber.application(tag, body)


def encode_root(root: Root) -> bytes:
    """The bytes of one Glow message, ready for :func:`emberemu.s101.encode_message`."""
    if root.invocation_result is not None:
        result = root.invocation_result
        body = ber.context(0, ber.integer(result.invocation_id))
        body += ber.context(1, ber.boolean(result.success))
        if result.result:
            body += ber.context(2, _encode_tuple(result.result))
        inner = ber.application(INVOCATION_RESULT, body)
    elif root.streams is not None:
        inner = ber.application(STREAM_COLLECTION, b"".join(
            ber.context(0, ber.application(
                STREAM_ENTRY,
                ber.context(0, ber.integer(entry.stream_identifier)) + ber.context(1, ber.value(entry.value))))
            for entry in root.streams))
    else:
        inner = ber.application(ROOT_ELEMENT_COLLECTION, b"".join(
            ber.context(0, encode_element(element)) for element in root.elements or []))
    return ber.application(ROOT, inner)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def _fields(tlv: Tlv) -> list[tuple[int, Tlv]]:
    """The context-tagged fields of a sequence or set: (tag number, inner value)."""
    if not tlv.constructed:
        raise GlowError("A container is expected, a single value was sent")
    found = []
    for child in tlv.children:
        if child.cls != ber.CONTEXT or not child.constructed or len(child.children) != 1:
            raise GlowError("A field must be one value inside a context tag")
        found.append((child.number, child.children[0]))
    return found


def _integer(tlv: Tlv) -> int:
    """The number in a field that must be an INTEGER."""
    if not tlv.is_universal(ber.INTEGER):
        raise GlowError("A whole number is expected")
    return ber.to_integer(tlv)


def _string(tlv: Tlv) -> str:
    """The text in a field that must be a UTF8String."""
    if not tlv.is_universal(ber.UTF8_STRING):
        raise GlowError("Text is expected")
    return ber.to_string(tlv)


def _path(tlv: Tlv) -> tuple[int, ...]:
    """The numbers in a field that must be a RELATIVE-OID."""
    if not tlv.is_universal(ber.RELATIVE_OID):
        raise GlowError("A path is expected")
    return ber.to_relative_oid(tlv)


def _decode_field(how: str, tlv: Tlv) -> Any:
    """Decode one contents field according to its kind."""
    if how == "string":
        return _string(tlv)
    if how == "integer":
        return _integer(tlv)
    if how == "boolean":
        if not tlv.is_universal(ber.BOOLEAN):
            raise GlowError("True or false is expected")
        return ber.to_boolean(tlv)
    if how == "value":
        return ber.to_value(tlv)
    if how == "path":
        return _path(tlv)
    if how == "enum_map":
        pairs = []
        for _, pair in _fields(tlv):
            entry = dict(_fields(pair))
            if 0 not in entry or 1 not in entry:
                raise GlowError("An enumeration entry needs a name and a number")
            pairs.append((_string(entry[0]), _integer(entry[1])))
        return pairs
    if how == "stream_descriptor":
        entry = dict(_fields(tlv))
        if 0 not in entry or 1 not in entry:
            raise GlowError("A stream description needs a format and an offset")
        return (_integer(entry[0]), _integer(entry[1]))
    if how == "parameters_location":
        return _integer(tlv) if tlv.is_universal(ber.INTEGER) else _path(tlv)
    if how == "labels":
        labels = []
        for _, label in _fields(tlv):
            entry = dict(_fields(label))
            if 0 not in entry:
                raise GlowError("A label needs a path")
            labels.append((_path(entry[0]), _string(entry[1]) if 1 in entry else ""))
        return labels
    if how == "tuple_description":
        items = []
        for _, item in _fields(tlv):
            entry = dict(_fields(item))
            if 0 not in entry:
                raise GlowError("A function argument needs a type")
            items.append((_integer(entry[0]), _string(entry[1]) if 1 in entry else None))
        return items
    raise GlowError(f"Unknown field kind {how!r}")


def _decode_contents(kind: str, tlv: Tlv) -> dict[str, Any]:
    """Decode a contents block into a dict of the fields that were sent."""
    table = FIELDS[kind]
    contents: dict[str, Any] = {}
    for tag, inner in _fields(tlv):
        if tag in table:  # fields of a newer schema are skipped
            name, how = table[tag]
            contents[name] = _decode_field(how, inner)
    return contents


def _decode_tuple(tlv: Tlv) -> list:
    """Decode the Glow type Tuple into a list of values."""
    return [ber.to_value(inner) for _, inner in _fields(tlv)]


def _decode_signals(tlv: Tlv) -> list[int]:
    """Decode the target or source numbers of a matrix."""
    numbers = []
    for _, signal in _fields(tlv):
        entry = dict(_fields(signal))
        if 0 not in entry:
            raise GlowError("A signal needs its number")
        numbers.append(_integer(entry[0]))
    return numbers


def _decode_connection(tlv: Tlv) -> Connection:
    """Decode one connection of a matrix."""
    entry = dict(_fields(tlv))
    if 0 not in entry:
        raise GlowError("A connection needs its target")
    return Connection(
        target=_integer(entry[0]),
        sources=_path(entry[1]) if 1 in entry else None,
        operation=_integer(entry[2]) if 2 in entry else None,
        disposition=_integer(entry[3]) if 3 in entry else None,
    )


def _decode_command(tlv: Tlv) -> GlowElement:
    """Decode a command with its field mask or its invocation."""
    entry = dict(_fields(tlv))
    if 0 not in entry:
        raise GlowError("A command needs its number")
    element = GlowElement("command", number=_integer(entry[0]))
    if 1 in entry:
        element.dir_field_mask = _integer(entry[1])
    if 2 in entry:
        invocation = dict(_fields(entry[2]))
        element.invocation = Invocation(
            invocation_id=_integer(invocation[0]) if 0 in invocation else None,
            arguments=_decode_tuple(invocation[1]) if 1 in invocation else [],
        )
    return element


def decode_element(tlv: Tlv) -> GlowElement:
    """Decode one element from its BER value.

    Raises:
        GlowError: The value is not a Glow element.
    """
    if tlv.cls != ber.APPLICATION:
        raise GlowError("An element must have an application tag")
    if tlv.number == COMMAND:
        return _decode_command(tlv)
    if tlv.number not in _KINDS:
        raise GlowError(f"Application tag {tlv.number} is not an element")
    kind, qualified = _KINDS[tlv.number]
    element = GlowElement(kind)
    seen_address = False
    for tag, inner in _fields(tlv):
        if tag == 0:
            seen_address = True
            if qualified:
                element.path = _path(inner)
            else:
                element.number = _integer(inner)
        elif kind == "template":
            continue  # templates are recognised but their content is not used
        elif tag == 1:
            element.contents = _decode_contents(kind, inner)
        elif tag == 2:
            element.children = [decode_element(child) for _, child in _fields(inner)]
        elif kind == "matrix" and tag == 3:
            element.targets = _decode_signals(inner)
        elif kind == "matrix" and tag == 4:
            element.sources = _decode_signals(inner)
        elif kind == "matrix" and tag == 5:
            element.connections = [_decode_connection(item) for _, item in _fields(inner)]
    if not seen_address:
        raise GlowError(f"A {kind} needs a number or a path")
    if qualified and not element.path:
        raise GlowError(f"A qualified {kind} needs a path that is not empty")
    if qualified and len(element.path) > MAX_PATH_LENGTH:
        raise GlowError(f"The path of a {kind} has more than {MAX_PATH_LENGTH} numbers")
    return element


def decode_root(data: bytes) -> Root:
    """Decode the bytes of one Glow message.

    Raises :class:`emberemu.ber.BerError` or :class:`GlowError` (both are
    ``ValueError``) if the data cannot be read.
    """
    tlv = ber.decode(data)
    if not tlv.is_application(ROOT) or len(tlv.children) != 1:
        raise GlowError("A Glow message must be one collection inside the root tag")
    inner = tlv.children[0]
    if inner.is_application(ROOT_ELEMENT_COLLECTION) or inner.is_application(ELEMENT_COLLECTION):
        return Root(elements=[decode_element(child) for _, child in _fields(inner)])
    if inner.is_application(STREAM_COLLECTION):
        streams = []
        for _, item in _fields(inner):
            entry = dict(_fields(item))
            if 0 not in entry or 1 not in entry:
                raise GlowError("A stream entry needs an identifier and a value")
            streams.append(StreamEntry(_integer(entry[0]), ber.to_value(entry[1])))
        return Root(streams=streams)
    if inner.is_application(INVOCATION_RESULT):
        entry = dict(_fields(inner))
        if 0 not in entry:
            raise GlowError("The result of a function call needs its invocation id")
        success = True
        if 1 in entry:
            if not entry[1].is_universal(ber.BOOLEAN):
                raise GlowError("True or false is expected")
            success = ber.to_boolean(entry[1])
        return Root(invocation_result=InvocationResult(
            invocation_id=_integer(entry[0]),
            success=success,
            result=_decode_tuple(entry[2]) if 2 in entry else [],
        ))
    raise GlowError("The root holds neither elements nor streams nor a function result")


__all__ = [
    "Access", "BerError", "CommandType", "Connection", "ConnectionDisposition", "ConnectionOperation",
    "FieldFlags", "GlowElement", "GlowError", "Invocation", "InvocationResult", "MatrixAddressingMode",
    "MatrixType", "ParameterType", "Root", "StreamEntry", "StreamFormat", "decode_element",
    "decode_root", "encode_element", "encode_root",
]
