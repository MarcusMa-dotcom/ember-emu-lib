"""The consumer: connects to a provider, reads its tree and changes values."""

from __future__ import annotations

import socket
import threading
import time
from typing import Any, Callable

from . import glow, s101
from .glow import (CommandType, Connection, ConnectionDisposition, ConnectionOperation, FieldFlags,
                   GlowElement, Invocation, InvocationResult, ParameterType, Root)
from .model import (_NUMERIC_PATH, Element, Function, Matrix, Node, Parameter, Tree, _enum,
                    parse_path)

DEFAULT_PORT = 9000
RECEIVE_BYTES = 65536
MAX_ELEMENTS = 250_000     # a consumer keeps at most this many elements of one provider
_KINDS = {"node": Node, "parameter": Parameter, "function": Function, "matrix": Matrix}


class ConsumerError(OSError):
    """The connection failed, was lost, or is not open."""


class InvocationError(RuntimeError):
    """The provider reported that a function call failed."""


def _blank(kind: str, number: int) -> Element:
    """An element nothing is known about yet except its kind and number."""
    if kind == "parameter":
        return Parameter("", type=ParameterType.NULL, number=number)
    if kind == "function":
        return Function("", number=number)
    if kind == "matrix":
        return Matrix("", 0, 0, number=number)
    return Node("", number=number)


class Consumer:
    """Reads the tree of an Ember+ provider.

    ::

        with Consumer("192.168.1.10") as consumer:
            gain = consumer.get("Device/Audio/Gain")
            print(gain.value)
            consumer.set_value(gain, -6.0)

    The tree is loaded piece by piece: :meth:`get_directory` asks for the
    children of one node, :meth:`get` follows a path and loads what it needs,
    :meth:`walk` loads everything. The provider then reports changes of what
    was loaded by itself, and the elements in ``consumer.tree`` follow.

    Callbacks are called from the network thread:

    * ``on_change(element, fields)``: the provider reported an element.
      ``fields`` names what changed, for example ``{"value"}``.
    * ``on_disconnect(reason)``: the connection ended.
    * ``on_error(message)``: bad data from the provider and similar.
    """

    is_consumer = True

    def __init__(self, host: str, port: int = DEFAULT_PORT, *, timeout: float = 5.0,
                 keepalive: float | None = 5.0, qualified: bool = True,
                 on_change: Callable[[Element, set], None] | None = None,
                 on_disconnect: Callable[[str], None] | None = None,
                 on_error: Callable[[str], None] | None = None):
        """Create a consumer. The connection opens with :meth:`connect`.

        Args:
            host: Name or address of the provider.
            port: TCP port of the provider. 9000 is usual for Ember+.
            timeout: Seconds to wait for the connection and for answers, unless a
                method is given its own ``timeout``.
            keepalive: Seconds of silence after which the provider is asked for a sign
                of life. After three times as long without any, the connection counts
                as lost. ``None`` switches this off.
            qualified: Address elements by their full path in one step, as current
                devices expect. ``False`` sends the nested form of old devices.
            on_change: See the class description.
            on_disconnect: See the class description.
            on_error: See the class description.
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.keepalive = keepalive
        self.qualified = qualified
        self.on_change = on_change
        self.on_disconnect = on_disconnect
        self.on_error = on_error
        self.error = ""
        self.problem_count = 0
        self.provider_glow_version: tuple[int, int] | None = None
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._send_lock = threading.Lock()
        self.tree = Tree(self)
        self._socket: socket.socket | None = None
        self._connected = False
        self._pending: set[tuple[int, ...]] = set()
        self._refreshing: set[tuple[int, ...]] = set()
        self._streams: dict[int, list[Parameter]] = {}
        self._subscribed: set[tuple[int, ...]] = set()
        self._invocation_id = 0
        self._results: dict[int, InvocationResult | None] = {}
        self._last_received = 0.0
        self._last_keepalive = 0.0
        self._element_count = 0
        self._watchers: dict[int, tuple[Element, list[Callable[[Element], None]]]] = {}

    # -- connection ------------------------------------------------------

    @property
    def lock(self) -> threading.RLock:
        """Hold this lock (``with consumer.lock:``) to read the tree in one consistent state
        while the provider keeps reporting changes."""
        return self._lock

    @property
    def connected(self) -> bool:
        """True while the connection is open."""
        return self._connected

    @property
    def description(self) -> str:
        """For example ``TCP 192.168.1.10:9000``."""
        return f"TCP {self.host}:{self.port}"

    def connect(self) -> "Consumer":
        """Open the connection. Raises :class:`ConsumerError` if that fails."""
        with self._lock:
            if self._connected:
                return self
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, OverflowError, ValueError) as exc:
            self.error = f"Cannot connect to {self.host}:{self.port}: {exc}"
            raise ConsumerError(self.error) from None
        with self._lock:
            self._socket = sock
            self._connected = True
            self.error = ""
            self.tree = Tree(self)
            self._pending.clear()
            self._refreshing.clear()
            self._streams.clear()
            self._subscribed.clear()
            self._results.clear()
            self._element_count = 0
            self._watchers.clear()
            self._last_received = self._last_keepalive = time.monotonic()
        threading.Thread(target=self._reader, args=(sock,), name="ember-consumer-receive",
                         daemon=True).start()
        if self.keepalive:
            threading.Thread(target=self._keepalive, args=(sock,), name="ember-consumer-keepalive",
                             daemon=True).start()
        return self

    def close(self) -> None:
        """Close the connection. The tree read so far stays in ``consumer.tree``."""
        self._lost(None, "")

    disconnect = close

    def __enter__(self) -> "Consumer":
        """Connect."""
        return self.connect()

    def __exit__(self, *exc) -> None:
        """Close the connection."""
        self.close()

    def _lost(self, sock: socket.socket | None, reason: str) -> None:
        """End the connection, wake everything that waits, and report the reason if there is one."""
        with self._condition:
            if not self._connected or (sock is not None and sock is not self._socket):
                return
            current = self._socket
            self._socket = None
            self._connected = False
            if reason:
                self.error = reason
            self._condition.notify_all()
        if current is not None:
            try:
                current.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                current.close()
            except OSError:
                pass
        if reason:
            self._call(self.on_disconnect, reason)

    def _reader(self, sock: socket.socket) -> None:
        """Thread: receive from the provider and handle its messages."""
        decoder = s101.MessageDecoder()
        while True:
            try:
                data = sock.recv(RECEIVE_BYTES)
            except OSError:
                data = b""
            if not data:
                self._lost(sock, "The provider closed the connection")
                return
            self._last_received = time.monotonic()
            before = decoder.problem_count
            for message in decoder.feed(data):
                if message.is_keepalive_request:
                    self._write(sock, [s101.keepalive_response(message.slot)])
                elif message.is_ember:
                    self.provider_glow_version = message.glow_version or self.provider_glow_version
                    self._handle_message(message.payload)
            if decoder.problem_count != before:
                self.problem_count += decoder.problem_count - before
                self._report("; ".join(decoder.last_problems))

    def _keepalive(self, sock: socket.socket) -> None:
        """Thread: ask a silent provider for a sign of life, and give up if none comes."""
        while True:
            time.sleep(0.5)
            if sock is not self._socket or not self.keepalive:
                return
            now = time.monotonic()
            idle = now - self._last_received
            if idle >= self.keepalive * 3:
                self._lost(sock, "The provider does not answer any more")
                return
            if idle >= self.keepalive and now - self._last_keepalive >= self.keepalive:
                self._last_keepalive = now
                self._write(sock, [s101.keepalive_request()])

    def _write(self, sock: socket.socket | None, frames: list[bytes]) -> None:
        """Send frames. Raises :class:`ConsumerError` if there is no connection."""
        if sock is None:
            raise ConsumerError("Not connected")
        try:
            with self._send_lock:
                sock.sendall(b"".join(frames))
        except OSError as exc:
            self._lost(sock, f"Connection lost while sending: {exc}")
            raise ConsumerError("Connection lost while sending") from None

    def _send(self, root: Root) -> None:
        """Encode and send one Glow message."""
        self._write(self._socket, s101.encode_message(glow.encode_root(root)))

    def send_raw(self, data: bytes) -> None:
        """Send bytes as they are, for example to see how a provider reacts to bad data."""
        self._write(self._socket, [bytes(data)])

    def _call(self, callback: Callable | None, *args: Any) -> None:
        """Call a callback of the user. An exception in it is reported, not raised."""
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:  # a failing callback must not end the connection
            self._report(f"A callback failed: {type(exc).__name__}: {exc}")

    def _report(self, message: str) -> None:
        """Remember an error and pass it to ``on_error``."""
        self.error = message
        if self.on_error is not None:
            try:
                self.on_error(message)
            except Exception:
                pass

    def _element_changed(self, element: Element, what: str, **details: Any) -> None:
        """Changes to the local copy of the tree are not sent anywhere."""

    # -- what the provider sends ----------------------------------------

    def _handle_message(self, payload: bytes) -> None:
        """Decode one message of the provider and bring the tree up to date."""
        try:
            root = glow.decode_root(payload)
        except ValueError as exc:
            self.problem_count += 1
            self._report(f"Message cannot be read: {exc}")
            return
        events: list[tuple[Element, set]] = []
        with self._condition:
            if root.elements is not None:
                try:
                    self._apply_elements(root.elements, self.tree, events)
                except glow.GlowError as exc:
                    self._report(str(exc))
                if not root.elements and () in self._pending:
                    self.tree.children_loaded = True  # an empty provider
            elif root.streams is not None:
                for entry in root.streams:
                    for parameter in self._streams.get(entry.stream_identifier, ()):
                        try:
                            value = parameter.stream_value(entry.value)
                        except ValueError as exc:
                            self._report(str(exc))
                            continue
                        parameter._value_updates += 1
                        if not Parameter._same(value, parameter._value):
                            parameter._value = value
                            events.append((parameter, {"value"}))
            elif root.invocation_result is not None:
                result = root.invocation_result
                if result.invocation_id in self._results:
                    self._results[result.invocation_id] = result
            self._condition.notify_all()
            watched = [(callback, element) for element, fields in events
                       if "value" in fields or "connections" in fields or "is_online" in fields
                       for callback in self._watchers.get(id(element), (None, ()))[1]]
        for element, fields in events:
            self._call(self.on_change, element, fields)
        for callback, element in watched:
            self._call(callback, element)

    def _ensure(self, path: tuple[int, ...]) -> Element:
        """The element at ``path``, creating placeholder nodes where nothing is known yet."""
        element: Element = self.tree
        for number in path:
            child = element.child(number)
            if child is None:
                self._count_new()
                child = Node("", number=number)
                child.parent = element
                element._children[number] = child
            element = child
        return element

    def _count_new(self) -> None:
        """Count a new element and refuse to grow beyond the limit."""
        self._element_count += 1
        if self._element_count > MAX_ELEMENTS:
            raise glow.GlowError(f"The provider reports more than {MAX_ELEMENTS} elements")

    def _apply_elements(self, items: list[GlowElement], base: Element, events: list) -> None:
        """Merge reported elements into the tree and collect what changed."""
        for item in items:
            if item.kind not in _KINDS:
                continue
            if item.path is not None:
                parent = self._ensure(item.path[:-1])
                number = item.path[-1]
            else:
                parent = base
                number = item.number
            if number is None:
                continue
            element = parent.child(number)
            created = False
            if element is None or element.kind != item.kind:
                # New, or a placeholder that turns out to be something else.
                if element is None:
                    self._count_new()
                fresh = _blank(item.kind, number)
                fresh.parent = parent
                if element is not None:
                    fresh._children = element._children
                    for child in fresh._children.values():
                        child.parent = fresh
                parent._children[number] = fresh
                element = fresh
                created = True
            fields: set[str] = set()
            if item.contents is not None:
                fields |= element._apply(item.contents)
                if isinstance(element, Parameter) and element.stream_identifier is not None:
                    known = self._streams.setdefault(element.stream_identifier, [])
                    if element not in known:
                        known.append(element)
            if isinstance(element, Matrix):
                if item.targets is not None and item.targets != element.targets:
                    element.targets = list(item.targets)
                    fields.add("targets")
                if item.sources is not None and item.sources != element.sources:
                    element.sources = list(item.sources)
                    fields.add("sources")
                for connection in item.connections or ():
                    if connection.disposition == ConnectionDisposition.PENDING:
                        continue  # describes a future state
                    sources = tuple(connection.sources or ())
                    if element.connections.get(connection.target, ()) != sources:
                        element.connections[connection.target] = sources
                        fields.add("connections")
            path = element.path
            if item.children is not None:
                real = [child for child in item.children if child.kind in _KINDS]
                # A listing names its children. A bare chain of numbers only leads to
                # something further down, and a report of values names nothing either.
                listing = any(child.contents and "identifier" in child.contents for child in real)
                self._apply_elements(item.children, element, events)
                if listing or (not real and path in self._pending):
                    if path in self._refreshing:
                        reported = {child.number for child in real if child.number is not None}
                        for stale in [n for n in element._children if n not in reported]:
                            element._children.pop(stale).parent = None
                    if not element.children_loaded or path in self._refreshing:
                        fields.add("children")
                    element.children_loaded = True
                    self._pending.discard(path)
                    self._refreshing.discard(path)
            elif item.contents is None and path in self._pending and not item.connections:
                # A node that reports itself without anything has no children.
                if path in self._refreshing:
                    for stale in list(element._children):
                        element._children.pop(stale).parent = None
                element.children_loaded = True
                self._pending.discard(path)
                self._refreshing.discard(path)
                fields.add("children")
            element._updates += 1
            if created:
                fields.add("created")
            if fields:
                events.append((element, fields))
        if base is self.tree and any(
                item.kind in _KINDS and item.contents and "identifier" in item.contents
                and (item.path is None or len(item.path) == 1) for item in items):
            if () in self._refreshing:
                reported = {item.number if item.path is None else item.path[0] for item in items}
                for stale in [n for n in self.tree._children if n not in reported]:
                    self.tree._children.pop(stale).parent = None
            self.tree.children_loaded = True
            self._pending.discard(())
            self._refreshing.discard(())

    # -- asking ----------------------------------------------------------

    def _resolve(self, target: Any) -> Element:
        """Turn ``None``, an element or a path into an element."""
        if target is None:
            return self.tree
        if isinstance(target, Element):
            return target
        return self.get(target)

    def _wrap(self, target: Element, inner: GlowElement) -> Root:
        """A request that reaches ``inner`` below ``target``."""
        if target is self.tree:
            return Root(elements=[inner])
        if self.qualified:
            return Root(elements=[GlowElement(target.kind, path=target.path, children=[inner])])
        item = GlowElement(target.kind, number=target.number, children=[inner])
        element = target.parent
        while element is not None and element.number is not None:
            item = GlowElement(element.kind, number=element.number, children=[item])
            element = element.parent
        return Root(elements=[item])

    def _address(self, target: Element, **fields: Any) -> Root:
        """A request that carries ``fields`` on ``target`` itself."""
        if self.qualified:
            return Root(elements=[GlowElement(target.kind, path=target.path, **fields)])
        item = GlowElement(target.kind, number=target.number, **fields)
        element = target.parent
        while element is not None and element.number is not None:
            item = GlowElement(element.kind, number=element.number, children=[item])
            element = element.parent
        return Root(elements=[item])

    def _wait(self, done: Callable[[], bool], timeout: float | None, what: str) -> None:
        """Wait, with the lock held, until ``done()``. Raises on timeout or lost connection."""
        limit = self.timeout if timeout is None else timeout
        self._condition.wait_for(lambda: done() or not self._connected, limit)
        if done():
            return
        if not self._connected:
            raise ConsumerError(self.error or "Not connected")
        raise TimeoutError(f"No answer from the provider: {what}")

    def get_directory(self, target: Any = None, *, timeout: float | None = None,
                      refresh: bool = False) -> list[Element]:
        """Ask for the children of a node and return them. Without ``target``: the root elements.

        What was loaded before is returned at once, unless ``refresh`` is
        true: then the provider is asked again and children it no longer
        lists are dropped. On a parameter, function or matrix this asks for
        its properties.
        """
        element = self._resolve(target)
        container = isinstance(element, (Tree, Node))
        path = element.path
        with self._condition:
            if container and element.children_loaded and not refresh:
                return element.children
            if not self._connected:
                raise ConsumerError(self.error or "Not connected")
            self._pending.add(path)
            if refresh:
                self._refreshing.add(path)
                element.children_loaded = False
            marker = element._updates
        command = GlowElement("command", number=CommandType.GET_DIRECTORY,
                              dir_field_mask=FieldFlags.ALL)
        self._send(self._wrap(element, command))
        with self._condition:
            try:
                self._wait(lambda: element.children_loaded if container else element._updates != marker,
                           timeout, f"GetDirectory on {element.identifier_path or 'the root'}")
            except TimeoutError:
                if not element.children:
                    raise
                # Small devices send their whole tree once and never answer GetDirectory.
                element.children_loaded = True
            finally:
                self._pending.discard(path)
                self._refreshing.discard(path)
            return element.children

    def get(self, path: Any, *, timeout: float | None = None) -> Element:
        """The element at a path, loading the nodes on the way.

        ``path`` is a string of identifiers (``"Device/Audio/Gain"``), a
        string of numbers (``"1.2.3"``) or a sequence of numbers and
        identifiers. Raises ``KeyError`` if the provider has no such element.
        """
        if isinstance(path, Element):
            return path
        if isinstance(path, str):
            text = path.strip().strip("/")
            keys: list = (list(parse_path(text)) if _NUMERIC_PATH.fullmatch(text)
                          else [part for part in text.split("/") if part])
        else:
            keys = list(path)
        element: Element = self.tree
        for key in keys:
            fresh = not element.children_loaded
            if fresh:
                self.get_directory(element, timeout=timeout)
            child = element.child(key)
            if child is None and not fresh and self._connected:
                # The provider may have gained elements since the node was loaded.
                self.get_directory(element, timeout=timeout, refresh=True)
                child = element.child(key)
            if child is None:
                raise KeyError(path)
            element = child
        return element

    def walk(self, target: Any = None, *, max_elements: int = 10000,
             timeout: float | None = None) -> list[Element]:
        """Load everything below ``target`` (or the whole tree) and return the elements.

        Stops loading once ``max_elements`` are known. A node that does not
        answer is left unloaded, the rest is still read.
        """
        start = self._resolve(target)
        waiting: list[Element] = [start]
        count = 0
        while waiting:
            element = waiting.pop(0)
            if isinstance(element, (Tree, Node)):
                try:
                    children = self.get_directory(element, timeout=timeout)
                except TimeoutError:
                    continue
            else:
                if isinstance(element, Matrix):
                    try:
                        self.get_directory(element, timeout=timeout)  # targets and connections
                    except TimeoutError:
                        pass
                children = element.children
            count += len(children)
            if count >= max_elements:
                break
            waiting.extend(children)
        return list(start.walk())

    def _parameter(self, target: Any) -> Parameter:
        """Like :meth:`_resolve`, but the element must be a parameter."""
        element = self._resolve(target)
        if not isinstance(element, Parameter):
            raise ValueError(f"{element.identifier_path} is not a parameter")
        return element

    def set_value(self, target: Any, value: Any, *, wait: bool = True, check: bool = True,
                  timeout: float | None = None) -> Any:
        """Ask the provider to change a parameter.

        With ``wait`` (the default) this returns the value the provider
        reports back, which is the old value if the provider refused.
        ``check=False`` sends the value as it is, without fitting it to the
        type and range the provider announced: useful to test a provider.
        """
        parameter = self._parameter(target)
        if check:
            value = parameter.coerce(value)
        with self._condition:
            marker = parameter._value_updates
        self._send(self._address(parameter, contents={"value": value}))
        if not wait:
            return None
        with self._condition:
            self._wait(lambda: parameter._value_updates != marker, timeout,
                       f"setting {parameter.identifier_path}")
            return parameter.value

    def trigger(self, target: Any) -> None:
        """Fire a trigger parameter."""
        parameter = self._parameter(target)
        value = parameter.value if parameter.value is not None else 0
        self._send(self._address(parameter, contents={"value": value}))

    def subscribe(self, target: Any) -> None:
        """Ask for the values of a stream parameter, such as an audio level."""
        parameter = self._parameter(target)
        self._send(self._wrap(parameter, GlowElement("command", number=CommandType.SUBSCRIBE)))
        with self._lock:
            self._subscribed.add(parameter.path)

    def unsubscribe(self, target: Any) -> None:
        """Stop the values of a stream parameter, or the reports about a node."""
        element = self._resolve(target)
        self._send(self._wrap(element, GlowElement("command", number=CommandType.UNSUBSCRIBE)))
        with self._lock:
            self._subscribed.discard(element.path)

    def watch(self, target: Any, callback: Callable[[Element], None]) -> Element:
        """Call ``callback(element)`` whenever the provider reports a new value for it.

        Works for parameters (value and online state) and matrices
        (connections). A stream parameter is subscribed on the way. Returns
        the element. Watching ends with the connection.
        """
        element = self._resolve(target)
        with self._lock:
            self._watchers.setdefault(id(element), (element, []))[1].append(callback)
        if isinstance(element, Parameter) and element.is_stream:
            self.subscribe(element)
        return element

    def unwatch(self, target: Any, callback: Callable[[Element], None] | None = None) -> None:
        """Stop calling ``callback`` for an element, or all its callbacks without one."""
        element = self._resolve(target)
        with self._lock:
            entry = self._watchers.get(id(element))
            if entry is None:
                return
            if callback is None:
                entry[1].clear()
            elif callback in entry[1]:
                entry[1].remove(callback)
            if not entry[1]:
                del self._watchers[id(element)]

    def invoke(self, target: Any, *arguments: Any, timeout: float | None = None) -> list:
        """Call a function and return its result values.

        Raises :class:`InvocationError` if the provider reports a failure.
        """
        function = self._resolve(target)
        if not isinstance(function, Function):
            raise ValueError(f"{function.identifier_path} is not a function")
        values = list(arguments)
        if len(values) == len(function.arguments):
            for index, (item_type, _name) in enumerate(function.arguments):
                item = values[index]
                if item_type == ParameterType.REAL and isinstance(item, int) and not isinstance(item, bool):
                    values[index] = float(item)
                elif (item_type == ParameterType.INTEGER and isinstance(item, float)
                      and item.is_integer()):
                    values[index] = int(item)
        with self._condition:
            self._invocation_id += 1
            invocation_id = self._invocation_id
            self._results[invocation_id] = None
        command = GlowElement("command", number=CommandType.INVOKE,
                              invocation=Invocation(invocation_id, values))
        try:
            self._send(self._wrap(function, command))
            with self._condition:
                self._wait(lambda: self._results.get(invocation_id) is not None, timeout,
                           f"calling {function.identifier_path}")
                result = self._results[invocation_id]
        finally:
            with self._lock:
                self._results.pop(invocation_id, None)
        assert result is not None
        if not result.success:
            raise InvocationError(f"{function.identifier_path} failed")
        return result.result

    def set_connection(self, target: Any, matrix_target: int, sources,
                       operation: ConnectionOperation | str | int = ConnectionOperation.ABSOLUTE) -> None:
        """Ask the provider to change the sources of one matrix target.

        The answer arrives like any other change: the matrix in the tree is
        updated and ``on_change`` is called with ``{"connections"}``.
        """
        matrix = self._resolve(target)
        if not isinstance(matrix, Matrix):
            raise ValueError(f"{matrix.identifier_path} is not a matrix")
        operation = _enum(ConnectionOperation, operation, "Operation")
        sources = (sources,) if isinstance(sources, int) else tuple(sources)
        self._send(self._address(matrix, connections=[
            Connection(int(matrix_target), sources, int(operation))]))
