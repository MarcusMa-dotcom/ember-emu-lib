"""The tree: nodes, parameters, functions and matrices.

A provider builds a tree out of these classes and serves it. A consumer
receives a tree made of the same classes. Every element has a ``number``
that is unique among its siblings, an ``identifier`` and an optional
``description``. The numbers from the root down to an element form its
``path``, for example ``(1, 2, 3)``, written ``"1.2.3"``.
"""

from __future__ import annotations

import math
import re
import struct
import threading
from typing import Any, Callable, Iterator

from .glow import (Access, ConnectionOperation, MatrixAddressingMode, MatrixType, ParameterType,
                   StreamFormat)

MAX_INTEGER = 2 ** 63 - 1
MIN_INTEGER = -2 ** 63
MAX_NUMBER = 2 ** 31 - 1
_IDENTIFIER = re.compile(r"[A-Za-z_][^/]*")
# A format arrives from the provider. One like "%999999999d" would make the shown text huge.
MAX_FORMAT_LENGTH = 100
MAX_FORMAT_WIDTH = 40
_FORMAT_SPEC = re.compile(r"%[-+ #0]*(\*|\d*)(?:\.(\*|\d*))?[hlL]?[a-zA-Z%]")
_NUMERIC_PATH = re.compile(r"\d+(\.\d+)*")

_STREAM_STRUCT = {
    StreamFormat.UINT8: ">B", StreamFormat.UINT16_BE: ">H", StreamFormat.UINT16_LE: "<H",
    StreamFormat.UINT32_BE: ">I", StreamFormat.UINT32_LE: "<I", StreamFormat.UINT64_BE: ">Q",
    StreamFormat.UINT64_LE: "<Q", StreamFormat.INT8: ">b", StreamFormat.INT16_BE: ">h",
    StreamFormat.INT16_LE: "<h", StreamFormat.INT32_BE: ">i", StreamFormat.INT32_LE: "<i",
    StreamFormat.INT64_BE: ">q", StreamFormat.INT64_LE: "<q", StreamFormat.FLOAT32_BE: ">f",
    StreamFormat.FLOAT32_LE: "<f", StreamFormat.FLOAT64_BE: ">d", StreamFormat.FLOAT64_LE: "<d",
}


def parse_path(text: str) -> tuple[int, ...]:
    """``"1.2.3"`` as ``(1, 2, 3)``. An empty string is the root, ``()``."""
    text = text.strip()
    if not text:
        return ()
    if not _NUMERIC_PATH.fullmatch(text):
        raise ValueError(f"{text!r} is not a path of numbers such as 1.2.3")
    return tuple(int(part) for part in text.split("."))


def format_path(path) -> str:
    """``(1, 2, 3)`` as ``"1.2.3"``."""
    return ".".join(str(number) for number in path)


def check_identifier(identifier: Any) -> str:
    """Ember+ rule: starts with a letter or underscore and contains no slash."""
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        raise ValueError("An identifier must start with a letter or an underscore "
                         "and must not contain a slash")
    return identifier


def format_is_safe(text: str) -> bool:
    """True if a display format is short and asks for no excessive width or precision."""
    if len(text) > MAX_FORMAT_LENGTH:
        return False
    for match in _FORMAT_SPEC.finditer(text):
        for part in match.groups():
            if part == "*" or (part and int(part) > MAX_FORMAT_WIDTH):
                return False
    return True


def _enum(enum_class, item, label: str):
    """Accept an enum member, its number or its name (``"read_write"``)."""
    if isinstance(item, enum_class):
        return item
    if isinstance(item, str):
        try:
            return enum_class[item.strip().upper().replace("-", "_").replace(" ", "_")]
        except KeyError:
            names = ", ".join(member.name.lower() for member in enum_class)
            raise ValueError(f"{label} must be one of {names}") from None
    if isinstance(item, int) and not isinstance(item, bool):
        try:
            return enum_class(item)
        except ValueError:
            raise ValueError(f"{label} {item} is not known") from None
    raise ValueError(f"{label} must be a name or a number")


class _NoLock:
    """Stands in for a lock while a tree belongs to no provider or consumer."""
    def __enter__(self):
        """Nothing to acquire."""
        return self

    def __exit__(self, *exc):
        """Nothing to release."""
        return False


_NO_LOCK = _NoLock()


class Element:
    """Base class of everything in a tree."""

    kind = "element"

    def __init__(self, identifier: str, *, description: str | None = None,
                 number: int | None = None):
        """Create an element that is not part of a tree yet.

        Args:
            identifier: Name of the element, unique among its siblings.
            description: Text for people. User interfaces show it in place of the identifier.
            number: Number among its siblings. Without one, :meth:`add` gives it the next free number.
        """
        self.identifier = identifier
        self.description = description
        self.number = number
        self.parent: Element | None = None
        self.children_loaded = False   # consumer side: the provider has listed the children
        self._children: dict[int, Element] = {}
        self._owner: Any = None        # the Provider or Consumer this tree belongs to (set on roots)
        self._updates = 0              # consumer side: how often the provider reported this element

    # -- position --------------------------------------------------------

    @property
    def path(self) -> tuple[int, ...]:
        """The numbers from the root down to this element."""
        numbers = []
        element: Element | None = self
        while element is not None and element.number is not None:
            numbers.append(element.number)
            element = element.parent
        return tuple(reversed(numbers))

    @property
    def path_text(self) -> str:
        """The path as text, for example ``"1.2.3"``."""
        return format_path(self.path)

    @property
    def identifier_path(self) -> str:
        """The identifiers from the root down, joined with slashes."""
        names = []
        element: Element | None = self
        while element is not None and element.number is not None:
            names.append(element.identifier or str(element.number))
            element = element.parent
        return "/".join(reversed(names))

    @property
    def root(self) -> "Element":
        """The top of the tree this element belongs to, or the element itself."""
        element = self
        while element.parent is not None:
            element = element.parent
        return element

    @property
    def owner(self) -> Any:
        """The provider or consumer the tree belongs to, or ``None``."""
        return self.root._owner

    def _lock(self):
        """The lock of the owner, or a stand-in if there is no owner."""
        owner = self.owner
        return owner._lock if owner is not None else _NO_LOCK

    def _changed(self, what: str, **details: Any) -> None:
        """Tell the owner that something about this element changed."""
        owner = self.owner
        if owner is not None:
            owner._element_changed(self, what, **details)

    # -- children --------------------------------------------------------

    @property
    def children(self) -> list["Element"]:
        """The children, in the order they were added."""
        return list(self._children.values())

    def child(self, key: int | str) -> "Element | None":
        """The child with this number (``int``) or identifier (``str``), or ``None``."""
        if isinstance(key, int):
            return self._children.get(key)
        for element in self._children.values():
            if element.identifier == key:
                return element
        return None

    def __getitem__(self, key: int | str) -> "Element":
        """The child with this number or identifier. Raises ``KeyError`` if there is none."""
        element = self.child(key)
        if element is None:
            raise KeyError(key)
        return element

    def __contains__(self, key: int | str) -> bool:
        """True if there is a child with this number or identifier."""
        return self.child(key) is not None

    def __iter__(self) -> Iterator["Element"]:
        """Iterate over the children."""
        return iter(self.children)

    def __len__(self) -> int:
        """The number of children."""
        return len(self._children)

    def __bool__(self) -> bool:
        """An element is always true, also one without children."""
        return True  # an element without children is still an element

    def walk(self) -> Iterator["Element"]:
        """This element and everything below it, parents before children."""
        if self.number is not None:
            yield self
        for element in self.children:
            yield from element.walk()

    def find(self, path) -> "Element | None":
        """A descendant by numbers (``"1.2"``, ``(1, 2)``) or identifiers (``"Audio/Gain"``)."""
        if isinstance(path, str):
            text = path.strip().strip("/")
            keys: list[int | str]
            if _NUMERIC_PATH.fullmatch(text):
                keys = list(parse_path(text))
            else:
                keys = [part for part in text.split("/") if part]
        else:
            keys = list(path)
        element: Element | None = self
        for key in keys:
            element = element.child(key)
            if element is None:
                return None
        return element

    def add(self, element: "Element") -> "Element":
        """Add a child. Without a number it gets the next free one, starting at 1."""
        with self._lock():
            if element.parent is not None or element._owner is not None:
                raise ValueError(f"{element.identifier!r} already belongs to a tree")
            if element is self or element is self.root:
                raise ValueError("An element cannot be added to itself")
            if element.number is None:
                element.number = max(self._children, default=0) + 1
            if not 0 <= element.number <= MAX_NUMBER:
                raise ValueError("An element number must be 0 to 2147483647")
            if element.number in self._children:
                raise ValueError(f"Number {element.number} is already used below "
                                 f"{self.identifier_path or 'the root'}")
            if self.child(element.identifier) is not None:
                raise ValueError(f"Identifier {element.identifier!r} is already used below "
                                 f"{self.identifier_path or 'the root'}")
            self._children[element.number] = element
            element.parent = self
            self._changed("children")
            return element

    def remove(self, key: "int | str | Element") -> "Element":
        """Remove a child, given as element, number or identifier."""
        with self._lock():
            element = key if isinstance(key, Element) else self.child(key)
            if element is None or self._children.get(element.number) is not element:
                raise KeyError(key)
            self._changed("removing", child=element)
            del self._children[element.number]
            element.parent = None
            self._changed("children")
            return element

    def clear(self) -> None:
        """Remove all children."""
        with self._lock():
            for element in self.children:
                self.remove(element)

    def add_node(self, identifier: str, **options: Any) -> "Node":
        """Add a :class:`Node` as a child and return it. Options as for :class:`Node`."""
        return self.add(Node(identifier, **options))  # type: ignore[return-value]

    def add_parameter(self, identifier: str, value: Any = None, **options: Any) -> "Parameter":
        """Add a :class:`Parameter` as a child and return it. Options as for :class:`Parameter`."""
        return self.add(Parameter(identifier, value, **options))  # type: ignore[return-value]

    def add_function(self, identifier: str, handler: Callable | None = None,
                     **options: Any) -> "Function":
        """Add a :class:`Function` as a child and return it. Options as for :class:`Function`."""
        return self.add(Function(identifier, handler, **options))  # type: ignore[return-value]

    def add_matrix(self, identifier: str, target_count: int, source_count: int,
                   **options: Any) -> "Matrix":
        """Add a :class:`Matrix` as a child and return it. Options as for :class:`Matrix`."""
        return self.add(Matrix(identifier, target_count, source_count, **options))  # type: ignore[return-value]

    # -- wire and JSON ---------------------------------------------------

    def contents(self) -> dict[str, Any]:
        """The properties as a Glow contents block."""
        raise NotImplementedError

    _PLAIN_FIELDS: tuple[str, ...] = ("identifier", "description")

    def _apply(self, contents: dict[str, Any]) -> set[str]:
        """Take over properties a provider reported. Returns the names that changed.

        Nothing is validated here: a consumer shows what the provider says.
        """
        changed = set()
        for name in self._PLAIN_FIELDS:
            if name in contents and getattr(self, name) != contents[name]:
                setattr(self, name, contents[name])
                changed.add(name)
        if "is_online" in contents and hasattr(self, "_is_online"):
            online = bool(contents["is_online"])
            if online != self._is_online:
                self._is_online = online
                changed.add("is_online")
        return changed

    def to_dict(self, children: bool = True) -> dict[str, Any]:
        """Plain data for JSON. :func:`element_from_dict` reads it back."""
        raise NotImplementedError

    def _base_dict(self) -> dict[str, Any]:
        """The fields every kind of element has, for :meth:`to_dict`."""
        data: dict[str, Any] = {"kind": self.kind, "number": self.number,
                                "identifier": self.identifier}
        if self.description is not None:
            data["description"] = self.description
        return data

    def _children_dict(self, data: dict[str, Any], children: bool) -> dict[str, Any]:
        """Add the children to the data of :meth:`to_dict`, if they are wanted."""
        if children and self._children:
            data["children"] = [element.to_dict() for element in self.children]
        return data

    def __repr__(self) -> str:
        """For example ``<Parameter 1.2.1 'Gain'>``."""
        return f"<{type(self).__name__} {self.path_text or '-'} {self.identifier!r}>"


class Node(Element):
    """A folder in the tree."""

    kind = "node"
    _PLAIN_FIELDS = ("identifier", "description", "is_root", "schema_identifiers", "template_reference")

    def __init__(self, identifier: str, *, description: str | None = None,
                 number: int | None = None, is_online: bool = True, is_root: bool | None = None,
                 schema_identifiers: str | None = None,
                 template_reference: tuple[int, ...] | None = None):
        """Create a node.

        Args:
            identifier: Name of the node, unique among its siblings.
            description: Text for people.
            number: Number among its siblings, given by :meth:`add` if left out.
            is_online: False marks the node, and with it everything below, as not available.
            is_root: Sent as the field ``isRoot`` if given.
            schema_identifiers: Schemas the node complies with, separated by line feeds.
            template_reference: Path of a template. It is passed on, not evaluated.
        """
        super().__init__(identifier, description=description, number=number)
        self._is_online = bool(is_online)
        self.is_root = is_root
        self.schema_identifiers = schema_identifiers
        self.template_reference = template_reference

    @property
    def is_online(self) -> bool:
        """False if the node is marked as not available."""
        return self._is_online

    @is_online.setter
    def is_online(self, online: bool) -> None:
        """Change the online state and tell the consumers."""
        with self._lock():
            online = bool(online)
            if online != self._is_online:
                self._is_online = online
                self._changed("properties", fields=("is_online",))

    def contents(self) -> dict[str, Any]:
        """The properties as a Glow contents block."""
        contents: dict[str, Any] = {"identifier": self.identifier}
        if self.description is not None:
            contents["description"] = self.description
        if self.is_root is not None:
            contents["is_root"] = self.is_root
        contents["is_online"] = self._is_online
        if self.schema_identifiers is not None:
            contents["schema_identifiers"] = self.schema_identifiers
        if self.template_reference is not None:
            contents["template_reference"] = tuple(self.template_reference)
        return contents

    def to_dict(self, children: bool = True) -> dict[str, Any]:
        """Plain data for JSON."""
        data = self._base_dict()
        if not self._is_online:
            data["is_online"] = False
        if self.schema_identifiers is not None:
            data["schema_identifiers"] = self.schema_identifiers
        return self._children_dict(data, children)


class Tree(Node):
    """The invisible top of a tree. Its children are the root elements."""

    kind = "tree"

    def __init__(self, owner: Any = None):
        """Create an empty tree.

        Args:
            owner: The provider or consumer the tree belongs to.
        """
        super().__init__("")
        self._owner = owner
        self.children_loaded = False

    def to_dict(self, children: bool = True) -> dict[str, Any]:
        """The root elements as plain data: ``{"children": [...]}``."""
        return {"children": [element.to_dict() for element in self.children]}


def _infer_type(value: Any, has_enumeration: bool) -> ParameterType:
    """The parameter type that fits a Python value.

    Raises:
        ValueError: The value is ``None`` or of a type Ember+ cannot carry.
    """
    if isinstance(value, bool):
        return ParameterType.BOOLEAN
    if isinstance(value, int):
        return ParameterType.ENUM if has_enumeration else ParameterType.INTEGER
    if isinstance(value, float):
        return ParameterType.REAL
    if isinstance(value, str):
        return ParameterType.STRING
    if isinstance(value, (bytes, bytearray)):
        return ParameterType.OCTETS
    raise ValueError("Give the parameter a value or a type")


class Parameter(Element):
    """A value in the tree.

    The type follows from the value unless ``type`` is given: ``bool`` is
    BOOLEAN, ``int`` is INTEGER (ENUM with an ``enumeration``), ``float`` is
    REAL, ``str`` is STRING and ``bytes`` is OCTETS.

    ``access`` says what consumers may do. It is ``Access.READ`` unless given,
    so consumers cannot change the value. The provider itself can always set
    ``value``.
    """

    kind = "parameter"
    _PLAIN_FIELDS = ("identifier", "description", "minimum", "maximum", "format", "factor", "formula",
                     "step", "default", "stream_identifier", "stream_descriptor", "schema_identifiers",
                     "template_reference")

    def __init__(self, identifier: str, value: Any = None, *,
                 type: ParameterType | str | int | None = None,
                 access: Access | str | int = Access.READ,
                 minimum: int | float | None = None, maximum: int | float | None = None,
                 format: str | None = None, enumeration: list[str] | str | None = None,
                 enum_map: list[tuple[str, int]] | dict[str, int] | None = None,
                 factor: int | None = None, step: int | None = None, default: Any = None,
                 formula: str | None = None, is_online: bool = True,
                 stream_identifier: int | None = None,
                 stream_descriptor: tuple[int, int] | None = None,
                 schema_identifiers: str | None = None,
                 template_reference: tuple[int, ...] | None = None,
                 description: str | None = None, number: int | None = None,
                 on_change: Callable[["Parameter"], None] | None = None):
        """Create a parameter.

        Args:
            identifier: Name of the parameter, unique among its siblings.
            value: The value to start with. Its Python type decides the parameter type.
            type: The parameter type, as :class:`ParameterType`, name or number. Needed
                if there is no value, as for a trigger.
            access: What consumers may do: ``"read"`` (the default), ``"write"``,
                ``"read_write"`` or ``"none"``, or the matching :class:`Access`.
            minimum: Smallest value of a number. Values below are refused.
            maximum: Largest value of a number. Values above are refused.
            format: How to show a number, in C notation: ``"%.1f dB"``.
            enumeration: Names of the entries of an enumeration. The value is the index.
            enum_map: Entries with their own numbers, as pairs ``(name, number)`` or as dict.
            factor: A whole number is shown divided by this, for devices without real numbers.
            step: Suggested step for user interfaces.
            default: The value a consumer may offer as reset value.
            formula: Conversion formulas, passed on to consumers and not evaluated here.
            is_online: False marks the parameter as not available.
            stream_identifier: Makes the parameter a stream: its values go only to
                consumers that subscribed, bundled a few times per second.
            stream_descriptor: ``(format, offset)`` if the stream carries a block of
                bytes that holds this value, see :class:`StreamFormat`.
            schema_identifiers: Schemas the parameter complies with, separated by line feeds.
            template_reference: Path of a template. It is passed on, not evaluated.
            description: Text for people.
            number: Number among its siblings, given by :meth:`add` if left out.
            on_change: Called with the parameter after a consumer changed its value
                or fired it as a trigger.

        Raises:
            ValueError: Neither value nor type is given, or the value does not fit.
        """
        super().__init__(identifier, description=description, number=number)
        if isinstance(enumeration, str):
            enumeration = enumeration.split("\n")
        self.enumeration: list[str] | None = list(enumeration) if enumeration is not None else None
        if isinstance(enum_map, dict):
            enum_map = list(enum_map.items())
        self.enum_map: list[tuple[str, int]] | None = (
            [(str(name), int(number_)) for name, number_ in enum_map] if enum_map is not None else None)
        has_enumeration = self.enumeration is not None or self.enum_map is not None
        if type is None:
            self.type = _infer_type(value, has_enumeration)
        else:
            self.type = _enum(ParameterType, type, "Type")
        self.access = _enum(Access, access, "Access")
        self.minimum = minimum
        self.maximum = maximum
        self.format = format
        self.factor = factor
        self.step = step
        self.formula = formula
        self._is_online = bool(is_online)
        self.stream_identifier = stream_identifier
        self.stream_descriptor = stream_descriptor
        self.schema_identifiers = schema_identifiers
        self.template_reference = template_reference
        self.on_change = on_change
        self._value_updates = 0        # consumer side: how often the provider reported a value
        self.default = self.coerce(default) if default is not None else None
        self._value = self.coerce(value)

    # -- value -----------------------------------------------------------

    @property
    def value(self) -> Any:
        """The current value. ``None`` means the parameter has no value."""
        return self._value

    @value.setter
    def value(self, new: Any) -> None:
        """On a provider: store the value and tell the consumers.
        On a consumer: ask the provider to change the value."""
        owner = self.owner
        if owner is not None and getattr(owner, "is_consumer", False):
            owner.set_value(self, new, wait=False)
            return
        with self._lock():
            new = self.coerce(new)
            if self._same(new, self._value):
                return
            self._value = new
            self._changed("value")

    @staticmethod
    def _same(first: Any, second: Any) -> bool:
        """True if two values are equal and of the same type. NaN counts as equal to NaN."""
        if type(first) is not type(second):
            return False
        if isinstance(first, float) and math.isnan(first) and math.isnan(second):
            return True
        return first == second

    @property
    def is_online(self) -> bool:
        """False if the parameter is marked as not available."""
        return self._is_online

    @is_online.setter
    def is_online(self, online: bool) -> None:
        """Change the online state and tell the consumers."""
        with self._lock():
            online = bool(online)
            if online != self._is_online:
                self._is_online = online
                self._changed("properties", fields=("is_online",))

    @property
    def readable(self) -> bool:
        """True if consumers may read the value."""
        return self.access in (Access.READ, Access.READ_WRITE)

    @property
    def writable(self) -> bool:
        """True if consumers may change the value."""
        return self.access in (Access.WRITE, Access.READ_WRITE)

    @property
    def is_stream(self) -> bool:
        """True if the parameter has a stream identifier: its values go only to subscribers."""
        return self.stream_identifier is not None

    def enum_entries(self) -> list[tuple[str, int]]:
        """The entries of an enumeration as (name, number), from ``enum_map`` or ``enumeration``."""
        if self.enum_map is not None:
            return list(self.enum_map)
        if self.enumeration is not None:
            # Entries starting with "~" are placeholders that user interfaces hide.
            return [(name, index) for index, name in enumerate(self.enumeration)]
        return []

    def coerce(self, value: Any) -> Any:
        """Return ``value`` in the Python type of this parameter.

        Raises ``ValueError`` if the value does not fit the type or lies
        outside ``minimum`` and ``maximum``. ``None`` means "no value".
        """
        if value is None:
            return None
        kind = self.type
        if kind == ParameterType.BOOLEAN:
            if isinstance(value, bool):
                return value
            if isinstance(value, int) and value in (0, 1):
                return bool(value)
            raise ValueError(f"{self.identifier}: the value must be true or false")
        if kind in (ParameterType.INTEGER, ParameterType.ENUM):
            if kind == ParameterType.ENUM and isinstance(value, str):
                for name, number in self.enum_entries():
                    if name == value:
                        return number
                raise ValueError(f"{self.identifier}: {value!r} is not one of the entries")
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{self.identifier}: the value must be a whole number")
            if not MIN_INTEGER <= value <= MAX_INTEGER:
                raise ValueError(f"{self.identifier}: the value does not fit into 64 bits")
            if kind == ParameterType.ENUM:
                entries = self.enum_entries()
                if entries and value not in {number for _, number in entries}:
                    raise ValueError(f"{self.identifier}: {value} is not one of the entries")
            self._check_range(value)
            return value
        if kind == ParameterType.REAL:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{self.identifier}: the value must be a number")
            value = float(value)
            self._check_range(value)
            return value
        if kind == ParameterType.STRING:
            if not isinstance(value, str):
                raise ValueError(f"{self.identifier}: the value must be text")
            return value
        if kind == ParameterType.OCTETS:
            if not isinstance(value, (bytes, bytearray, memoryview)):
                raise ValueError(f"{self.identifier}: the value must be bytes")
            return bytes(value)
        if kind == ParameterType.TRIGGER:
            if isinstance(value, (bool, int, float, str, bytes)):
                return value
            raise ValueError(f"{self.identifier}: this value cannot be sent")
        raise ValueError(f"{self.identifier}: a parameter of type null has no value")

    def _check_range(self, value: int | float) -> None:
        """Raise ``ValueError`` if a number lies outside ``minimum`` and ``maximum``."""
        if isinstance(value, float) and math.isnan(value):
            return
        if isinstance(self.minimum, (int, float)) and value < self.minimum:
            raise ValueError(f"{self.identifier}: {value} is below the minimum {self.minimum}")
        if isinstance(self.maximum, (int, float)) and value > self.maximum:
            raise ValueError(f"{self.identifier}: {value} is above the maximum {self.maximum}")

    def display_value(self) -> str:
        """The value as a user interface would show it: enumeration name, factor and format applied."""
        value = self._value
        if value is None:
            return ""
        if self.type == ParameterType.ENUM:
            for name, number in self.enum_entries():
                if number == value:
                    return name
            return str(value)
        if isinstance(value, bytes):
            return value.hex(" ")
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            shown: int | float = value
            if self.factor not in (None, 0, 1) and isinstance(value, int):
                shown = value / self.factor
            if self.format and format_is_safe(self.format):
                try:
                    text = self.format % shown
                except (TypeError, ValueError):
                    try:  # "%d" with a fraction, or "%f" with a whole number
                        text = self.format % (int(shown) if isinstance(shown, float) else float(shown))
                    except (TypeError, ValueError):
                        text = f"{shown} {self.format}"
                # A format may end in "°" followed by the unit, as the Lawo viewer shows it.
                return text.replace("\n", " ")
            return f"{shown:g}" if isinstance(shown, float) else str(shown)
        return str(value)

    def stream_value(self, raw: Any) -> Any:
        """Pick this parameter's value out of a stream entry.

        With a ``stream_descriptor`` the entry is a block of bytes that holds
        several values. Otherwise the entry is the value itself.
        """
        if self.stream_descriptor is None or not isinstance(raw, (bytes, bytearray)):
            return raw
        stream_format, offset = self.stream_descriptor
        try:
            layout = _STREAM_STRUCT[StreamFormat(stream_format)]
            return struct.unpack_from(layout, raw, offset)[0]
        except (ValueError, KeyError, struct.error):
            raise ValueError(f"{self.identifier}: the stream block does not hold the described value") from None

    # -- wire and JSON ---------------------------------------------------

    def _apply(self, contents: dict[str, Any]) -> set[str]:
        """Take over properties a provider reported, and work out the effective type."""
        changed = super()._apply(contents)
        if "access" in contents:
            try:
                access = Access(contents["access"])
            except ValueError:
                access = Access.READ
            if access != self.access:
                self.access = access
                changed.add("access")
        if "enumeration" in contents:
            entries = str(contents["enumeration"]).split("\n")
            if entries != self.enumeration:
                self.enumeration = entries
                changed.add("enumeration")
        if "enum_map" in contents:
            pairs = [(str(name), int(number)) for name, number in contents["enum_map"]]
            if pairs != self.enum_map:
                self.enum_map = pairs
                changed.add("enum_map")
        value = contents.get("value")
        # The rules of the Glow DTD for the effective type. A report that carries
        # only a value keeps the type that is known already.
        new_type = None
        describes = "type" in contents or "enumeration" in contents or "enum_map" in contents
        if contents.get("type") == ParameterType.TRIGGER:
            new_type = ParameterType.TRIGGER
        elif describes and (self.enumeration is not None or self.enum_map is not None):
            new_type = ParameterType.ENUM
        elif value is not None and (describes or self.type == ParameterType.NULL):
            try:
                new_type = _infer_type(value, False)
            except ValueError:
                new_type = None
        elif "type" in contents:
            try:
                new_type = ParameterType(contents["type"])
            except ValueError:
                new_type = None
        if new_type is not None and new_type != self.type:
            self.type = new_type
            changed.add("type")
        if "value" in contents:
            if self.type == ParameterType.REAL and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            self._value_updates += 1
            if not self._same(value, self._value):
                self._value = value
                changed.add("value")
        return changed

    def contents(self) -> dict[str, Any]:
        """The properties as a Glow contents block. The value is left out if consumers may not read it."""
        contents: dict[str, Any] = {"identifier": self.identifier}
        if self.description is not None:
            contents["description"] = self.description
        if self._value is not None and self.readable:
            contents["value"] = self._value
        numeric = self.type in (ParameterType.INTEGER, ParameterType.REAL)
        if self.minimum is not None and numeric:
            contents["minimum"] = float(self.minimum) if self.type == ParameterType.REAL else int(self.minimum)
        if self.maximum is not None and numeric:
            contents["maximum"] = float(self.maximum) if self.type == ParameterType.REAL else int(self.maximum)
        contents["access"] = int(self.access)
        if self.format is not None:
            contents["format"] = self.format
        if self.enumeration is not None:
            contents["enumeration"] = "\n".join(self.enumeration)
        if self.factor is not None:
            contents["factor"] = self.factor
        contents["is_online"] = self._is_online
        if self.formula is not None:
            contents["formula"] = self.formula
        if self.step is not None:
            contents["step"] = self.step
        if self.default is not None:
            contents["default"] = self.default
        contents["type"] = int(self.type)
        if self.stream_identifier is not None:
            contents["stream_identifier"] = self.stream_identifier
        if self.enum_map is not None:
            contents["enum_map"] = list(self.enum_map)
        if self.stream_descriptor is not None:
            contents["stream_descriptor"] = tuple(self.stream_descriptor)
        if self.schema_identifiers is not None:
            contents["schema_identifiers"] = self.schema_identifiers
        if self.template_reference is not None:
            contents["template_reference"] = tuple(self.template_reference)
        return contents

    def to_dict(self, children: bool = True) -> dict[str, Any]:
        """Plain data for JSON. Bytes are written as hex digits."""
        data = self._base_dict()
        data["type"] = self.type.name.lower()
        data["access"] = self.access.name.lower()
        data["value"] = _json_value(self._value)
        for name in ("minimum", "maximum", "format", "enumeration", "factor", "step", "formula",
                     "stream_identifier", "schema_identifiers"):
            item = getattr(self, name)
            if item is not None:
                data[name] = item
        if self.default is not None:
            data["default"] = _json_value(self.default)
        if self.enum_map is not None:
            data["enum_map"] = [[name, number] for name, number in self.enum_map]
        if self.stream_descriptor is not None:
            data["stream_descriptor"] = list(self.stream_descriptor)
        if not self._is_online:
            data["is_online"] = False
        return self._children_dict(data, children)


def _json_value(value: Any) -> Any:
    """A value as JSON can carry it: bytes as hex digits, infinity and NaN as null."""
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class Function(Element):
    """Something a consumer can call.

    ``arguments`` and ``result`` describe the values going in and out, each
    as ``(type, name)``. ``handler`` is called with the argument values. It
    may return nothing, one value, or a tuple or list of values. If it raises,
    the consumer is told that the call failed.
    """

    kind = "function"

    def __init__(self, identifier: str, handler: Callable[..., Any] | None = None, *,
                 arguments=(), result=(), description: str | None = None,
                 number: int | None = None,
                 template_reference: tuple[int, ...] | None = None):
        """Create a function.

        Args:
            identifier: Name of the function, unique among its siblings.
            handler: Called with the argument values when a consumer invokes the function.
            arguments: What goes in: a sequence of ``(type, name)``, or of types alone.
            result: What comes out, in the same form.
            description: Text for people.
            number: Number among its siblings, given by :meth:`add` if left out.
            template_reference: Path of a template. It is passed on, not evaluated.
        """
        super().__init__(identifier, description=description, number=number)
        self.handler = handler
        self.arguments = [self._item(item) for item in arguments]
        self.result = [self._item(item) for item in result]
        self.template_reference = template_reference

    @staticmethod
    def _item(item) -> tuple[ParameterType, str | None]:
        """One argument or result description as ``(type, name)``."""
        if isinstance(item, (tuple, list)):
            item_type, name = item[0], (item[1] if len(item) > 1 else None)
        else:
            item_type, name = item, None
        return _enum(ParameterType, item_type, "Type"), name

    def _apply(self, contents: dict[str, Any]) -> set[str]:
        """Take over properties a provider reported."""
        changed = super()._apply(contents)
        for name in ("arguments", "result"):
            if name in contents:
                items = []
                for item_type, item_name in contents[name]:
                    try:
                        items.append((ParameterType(item_type), item_name))
                    except ValueError:
                        items.append((ParameterType.NULL, item_name))
                if items != getattr(self, name):
                    setattr(self, name, items)
                    changed.add(name)
        return changed

    def contents(self) -> dict[str, Any]:
        """The properties as a Glow contents block."""
        contents: dict[str, Any] = {"identifier": self.identifier}
        if self.description is not None:
            contents["description"] = self.description
        contents["arguments"] = [(int(item_type), name) for item_type, name in self.arguments]
        contents["result"] = [(int(item_type), name) for item_type, name in self.result]
        if self.template_reference is not None:
            contents["template_reference"] = tuple(self.template_reference)
        return contents

    def to_dict(self, children: bool = True) -> dict[str, Any]:
        """Plain data for JSON."""
        data = self._base_dict()
        data["arguments"] = [[item_type.name.lower(), name] for item_type, name in self.arguments]
        data["result"] = [[item_type.name.lower(), name] for item_type, name in self.result]
        return self._children_dict(data, children)


class Matrix(Element):
    """A routing matrix: which sources are connected to which targets.

    With linear addressing the signals are numbered 0 to count - 1. With
    non-linear addressing ``targets`` and ``sources`` list the numbers.
    """

    kind = "matrix"
    _PLAIN_FIELDS = ("identifier", "description", "target_count", "source_count",
                     "maximum_total_connects", "maximum_connects_per_target", "parameters_location",
                     "gain_parameter_number", "labels", "schema_identifiers")

    def __init__(self, identifier: str, target_count: int, source_count: int, *,
                 type: MatrixType | str | int = MatrixType.ONE_TO_N,
                 addressing_mode: MatrixAddressingMode | str | int = MatrixAddressingMode.LINEAR,
                 targets: list[int] | None = None, sources: list[int] | None = None,
                 maximum_total_connects: int | None = None,
                 maximum_connects_per_target: int | None = None,
                 parameters_location: tuple[int, ...] | int | None = None,
                 gain_parameter_number: int | None = None,
                 labels: list[tuple[tuple[int, ...], str]] | None = None,
                 schema_identifiers: str | None = None,
                 description: str | None = None, number: int | None = None):
        """Create a matrix without connections.

        Args:
            identifier: Name of the matrix, unique among its siblings.
            target_count: Number of targets.
            source_count: Number of sources.
            type: ``"one_to_n"`` (the default: one source per target), ``"one_to_one"``
                (and one target per source) or ``"n_to_n"`` (any), or the matching :class:`MatrixType`.
            addressing_mode: ``"linear"`` (signals 0 to count - 1) or ``"non_linear"``.
            targets: The target numbers, for non-linear addressing.
            sources: The source numbers, for non-linear addressing.
            maximum_total_connects: Limit for all connections of an n-to-n matrix.
            maximum_connects_per_target: Limit for the sources of one target of an n-to-n matrix.
            parameters_location: Where parameters of signals and connections live:
                a path, or the number of a child node.
            gain_parameter_number: Number of the gain parameter of a connection.
            labels: Label layers as pairs ``(path of the label node, description)``.
            schema_identifiers: Schemas the matrix complies with, separated by line feeds.
            description: Text for people.
            number: Number among its siblings, given by :meth:`add` if left out.
        """
        super().__init__(identifier, description=description, number=number)
        self.type = _enum(MatrixType, type, "Matrix type")
        self.addressing_mode = _enum(MatrixAddressingMode, addressing_mode, "Addressing mode")
        self.target_count = int(target_count)
        self.source_count = int(source_count)
        self.targets = list(targets) if targets is not None else None
        self.sources = list(sources) if sources is not None else None
        self.maximum_total_connects = maximum_total_connects
        self.maximum_connects_per_target = maximum_connects_per_target
        self.parameters_location = parameters_location
        self.gain_parameter_number = gain_parameter_number
        self.labels = [(tuple(path), str(text)) for path, text in labels] if labels else None
        self.schema_identifiers = schema_identifiers
        self.connections: dict[int, tuple[int, ...]] = {}

    def _apply(self, contents: dict[str, Any]) -> set[str]:
        """Take over properties a provider reported."""
        changed = super()._apply(contents)
        for name, enum_class in (("type", MatrixType), ("addressing_mode", MatrixAddressingMode)):
            if name in contents:
                try:
                    item = enum_class(contents[name])
                except ValueError:
                    continue
                if item != getattr(self, name):
                    setattr(self, name, item)
                    changed.add(name)
        return changed

    def target_numbers(self) -> list[int]:
        """The numbers of all targets."""
        if self.addressing_mode == MatrixAddressingMode.NON_LINEAR and self.targets is not None:
            return list(self.targets)
        return list(range(self.target_count))

    def source_numbers(self) -> list[int]:
        """The numbers of all sources."""
        if self.addressing_mode == MatrixAddressingMode.NON_LINEAR and self.sources is not None:
            return list(self.sources)
        return list(range(self.source_count))

    def sources_of(self, target: int) -> tuple[int, ...]:
        """The sources connected to a target. An empty tuple if there are none."""
        return self.connections.get(target, ())

    def connect(self, target: int, sources, operation: ConnectionOperation | str | int =
                ConnectionOperation.ABSOLUTE) -> tuple[int, ...]:
        """Change the sources of one target and return the new sources.

        ``absolute`` replaces them, ``connect`` adds, ``disconnect`` removes.
        Raises ``ValueError`` if the matrix does not allow the result.
        On a consumer this asks the provider instead and returns what is
        known so far.
        """
        owner = self.owner
        if owner is not None and getattr(owner, "is_consumer", False):
            owner.set_connection(self, target, sources, operation)
            return self.sources_of(target)
        with self._lock():
            operation = _enum(ConnectionOperation, operation, "Operation")
            sources = [sources] if isinstance(sources, int) else list(sources)
            if target not in self.target_numbers():
                raise ValueError(f"{self.identifier}: there is no target {target}")
            valid = set(self.source_numbers())
            for source in sources:
                if source not in valid:
                    raise ValueError(f"{self.identifier}: there is no source {source}")
            current = list(self.connections.get(target, ()))
            if operation == ConnectionOperation.CONNECT:
                new = current + [source for source in sources if source not in current]
            elif operation == ConnectionOperation.DISCONNECT:
                new = [source for source in current if source not in sources]
            else:
                new = list(dict.fromkeys(sources))
            touched = {target}
            if self.type != MatrixType.N_TO_N:
                if operation == ConnectionOperation.CONNECT and sources:
                    new = [sources[-1]]
                elif len(new) > 1:
                    raise ValueError(f"{self.identifier}: a target of this matrix takes one source")
                if self.type == MatrixType.ONE_TO_ONE and new:
                    for other, connected in list(self.connections.items()):
                        if other != target and new[0] in connected:
                            self.connections[other] = ()
                            touched.add(other)
            else:
                limit = self.maximum_connects_per_target
                if limit is not None and len(new) > limit:
                    raise ValueError(f"{self.identifier}: a target takes at most {limit} sources")
                total = self.maximum_total_connects
                if total is not None:
                    others = sum(len(connected) for other, connected in self.connections.items()
                                 if other != target)
                    if others + len(new) > total:
                        raise ValueError(f"{self.identifier}: at most {total} connections in total")
            if tuple(new) == self.connections.get(target, ()) and len(touched) == 1:
                return tuple(new)
            self.connections[target] = tuple(new)
            self._changed("connections", targets=tuple(sorted(touched)))
            return tuple(new)

    def contents(self) -> dict[str, Any]:
        """The properties as a Glow contents block."""
        contents: dict[str, Any] = {"identifier": self.identifier}
        if self.description is not None:
            contents["description"] = self.description
        contents["type"] = int(self.type)
        contents["addressing_mode"] = int(self.addressing_mode)
        contents["target_count"] = self.target_count
        contents["source_count"] = self.source_count
        for name in ("maximum_total_connects", "maximum_connects_per_target", "parameters_location",
                     "gain_parameter_number", "labels", "schema_identifiers"):
            item = getattr(self, name)
            if item is not None:
                contents[name] = item
        return contents

    def to_dict(self, children: bool = True) -> dict[str, Any]:
        """Plain data for JSON, including the connections."""
        data = self._base_dict()
        data["type"] = self.type.name.lower()
        data["addressing_mode"] = self.addressing_mode.name.lower()
        data["target_count"] = self.target_count
        data["source_count"] = self.source_count
        if self.targets is not None:
            data["targets"] = list(self.targets)
        if self.sources is not None:
            data["sources"] = list(self.sources)
        for name in ("maximum_total_connects", "maximum_connects_per_target", "gain_parameter_number",
                     "schema_identifiers"):
            item = getattr(self, name)
            if item is not None:
                data[name] = item
        if self.labels:
            data["labels"] = [[format_path(path), text] for path, text in self.labels]
        data["connections"] = {str(target): list(sources)
                               for target, sources in sorted(self.connections.items()) if sources}
        return self._children_dict(data, children)


def _from_json_value(parameter_type: ParameterType, value: Any) -> Any:
    """Turn a JSON value back into the Python type of the parameter."""
    if parameter_type == ParameterType.OCTETS and isinstance(value, str):
        try:
            return bytes.fromhex(value)
        except ValueError:
            raise ValueError("Bytes must be written as hex digits") from None
    if parameter_type == ParameterType.REAL and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    return value


def element_from_dict(data: dict[str, Any]) -> Element:
    """Build an element, with its children, from the data of :meth:`Element.to_dict`.

    Raises ``ValueError`` if the data does not describe a valid element.
    """
    if not isinstance(data, dict):
        raise ValueError("An element must be an object")
    kind = data.get("kind", "node")
    identifier = check_identifier(data.get("identifier"))
    description = data.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError("A description must be text")
    number = data.get("number")
    if number is not None and (isinstance(number, bool) or not isinstance(number, int)):
        raise ValueError("An element number must be a whole number")
    common = {"description": description, "number": number}
    try:
        if kind == "node":
            element: Element = Node(identifier, is_online=bool(data.get("is_online", True)),
                                    schema_identifiers=data.get("schema_identifiers"), **common)
        elif kind == "parameter":
            enumeration = data.get("enumeration")
            has_enumeration = enumeration is not None or data.get("enum_map") is not None
            raw_type = data.get("type")
            parameter_type = (_enum(ParameterType, raw_type, "Type") if raw_type is not None
                              else _infer_type(data.get("value"), has_enumeration))
            descriptor = data.get("stream_descriptor")
            element = Parameter(
                identifier, _from_json_value(parameter_type, data.get("value")),
                type=parameter_type, access=data.get("access", "read"),
                minimum=data.get("minimum"), maximum=data.get("maximum"),
                format=data.get("format"), enumeration=enumeration,
                enum_map=[tuple(pair) for pair in data["enum_map"]] if data.get("enum_map") else None,
                factor=data.get("factor"), step=data.get("step"),
                default=_from_json_value(parameter_type, data.get("default")),
                formula=data.get("formula"), is_online=bool(data.get("is_online", True)),
                stream_identifier=data.get("stream_identifier"),
                stream_descriptor=tuple(descriptor) if descriptor else None,
                schema_identifiers=data.get("schema_identifiers"), **common)
        elif kind == "function":
            element = Function(identifier, arguments=data.get("arguments", ()),
                               result=data.get("result", ()), **common)
        elif kind == "matrix":
            labels = data.get("labels")
            matrix = Matrix(
                identifier, data.get("target_count", 0), data.get("source_count", 0),
                type=data.get("type", "one_to_n"),
                addressing_mode=data.get("addressing_mode", "linear"),
                targets=data.get("targets"), sources=data.get("sources"),
                maximum_total_connects=data.get("maximum_total_connects"),
                maximum_connects_per_target=data.get("maximum_connects_per_target"),
                gain_parameter_number=data.get("gain_parameter_number"),
                labels=[(parse_path(path), text) for path, text in labels] if labels else None,
                schema_identifiers=data.get("schema_identifiers"), **common)
            for target, sources in (data.get("connections") or {}).items():
                matrix.connect(int(target), sources)
            element = matrix
        else:
            raise ValueError(f"Unknown kind {kind!r}")
    except (TypeError, KeyError, IndexError) as exc:
        raise ValueError(f"{identifier}: the description of the element is not valid ({exc})") from None
    children = data.get("children") or []
    if not isinstance(children, list):
        raise ValueError("Children must be a list")
    for child in children:
        element.add(element_from_dict(child))
    return element


__all__ = [
    "Element", "Function", "Matrix", "Node", "Parameter", "Tree", "check_identifier",
    "element_from_dict", "format_path", "parse_path",
]
