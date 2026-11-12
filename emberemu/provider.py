"""The provider: serves a tree to any number of consumers over TCP."""

from __future__ import annotations

import queue
import socket
import struct
import threading
import time
from typing import Any, Callable

from . import glow, s101
from .glow import (CommandType, Connection, ConnectionDisposition, ConnectionOperation, FieldFlags,
                   GlowElement, InvocationResult, MatrixAddressingMode, ParameterType, Root,
                   StreamEntry, StreamFormat)
from .model import _STREAM_STRUCT, Element, Function, Matrix, Node, Parameter, Tree, format_path

DEFAULT_PORT = 9000
MAX_CLIENTS = 64
QUEUE_FRAMES = 20000       # frames waiting for a slow consumer before it is dropped
RECEIVE_BYTES = 65536
MAX_REQUEST = 1024 * 1024  # bytes of one message a consumer may send
SEND_TIMEOUT = 10.0

_MASK_FIELDS = {
    FieldFlags.IDENTIFIER: ("identifier",),
    FieldFlags.DESCRIPTION: ("description",),
    FieldFlags.VALUE: ("value",),
    FieldFlags.TREE: ("identifier", "description", "is_online", "is_root"),
    FieldFlags.CONNECTIONS: (),
}


class ProviderError(OSError):
    """The provider could not open its port."""


class Client:
    """One connected consumer, as the provider sees it."""

    def __init__(self, sock: socket.socket, address: tuple):
        """Wrap a connection that was just accepted.

        Args:
            sock: The connected socket.
            address: Host and port of the consumer.
        """
        self.host = str(address[0])
        self.port = int(address[1])
        self.address = f"{self.host}:{self.port}"
        self.connected_at = time.time()
        self.requests = 0
        self.glow_version: tuple[int, int] | None = None
        self.directories: set[tuple[int, ...]] = set()
        self.subscriptions: set[tuple[int, ...]] = set()
        self._socket = sock
        self._decoder = s101.MessageDecoder(max_message=MAX_REQUEST)
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=QUEUE_FRAMES)
        self._closed = False
        self._last_received = time.monotonic()
        self._keepalive_sent: float | None = None

    def __repr__(self) -> str:
        """For example ``<Client 192.168.1.20:51234>``."""
        return f"<Client {self.address}>"


class Provider:
    """Serves an Ember+ tree.

    Build the tree with ``add_node``, ``add_parameter``, ``add_function`` and
    ``add_matrix``, then call :meth:`start`. Changing ``parameter.value``
    while the provider runs tells every consumer that looked at the
    parameter.

    Callbacks are called from network threads, one at a time per consumer:

    * ``on_change(parameter, client)``: a consumer changed a value, or fired a trigger.
    * ``on_connection(matrix, target, client)``: a consumer changed a matrix connection.
    * ``on_connect(client)`` and ``on_disconnect(client)``.
    * ``on_request(client, action, element, detail)``: every request, for logging.
      ``action`` is ``"directory"``, ``"set"``, ``"refused"``, ``"subscribe"``,
      ``"unsubscribe"``, ``"invoke"``, ``"connect"`` or ``"unknown"``.
    * ``on_error(message)``: bad data from a consumer and similar.
    """

    is_consumer = False

    def __init__(self, port: int = DEFAULT_PORT, bind: str = "0.0.0.0", *,
                 stream_interval: float = 0.06, keepalive: float | None = None,
                 max_clients: int = MAX_CLIENTS,
                 on_change: Callable[[Parameter, Client], None] | None = None,
                 on_connection: Callable[[Matrix, int, Client], None] | None = None,
                 on_connect: Callable[[Client], None] | None = None,
                 on_disconnect: Callable[[Client], None] | None = None,
                 on_request: Callable[[Client, str, Element | None, str], None] | None = None,
                 on_error: Callable[[str], None] | None = None):
        """Create a provider with an empty tree. The port opens with :meth:`start`.

        Args:
            port: TCP port to listen on. 9000 is usual for Ember+. 0 picks a free port.
            bind: Address to listen on. ``"0.0.0.0"`` is every network interface,
                ``"127.0.0.1"`` only this machine.
            stream_interval: Seconds between two bundles of stream values.
            keepalive: Seconds of silence after which a consumer is asked for a sign of
                life, and dropped if none comes. ``None`` switches this off.
            max_clients: Consumers accepted at the same time.
            on_change: See the class description.
            on_connection: See the class description.
            on_connect: See the class description.
            on_disconnect: See the class description.
            on_request: See the class description.
            on_error: See the class description.
        """
        self.bind = bind
        self.port = port
        self.stream_interval = stream_interval
        self.keepalive = keepalive
        self.max_clients = max_clients
        self.on_change = on_change
        self.on_connection = on_connection
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_request = on_request
        self.on_error = on_error
        self.error = ""
        self.problem_count = 0
        self._lock = threading.RLock()
        self.tree = Tree(self)
        self._clients: list[Client] = []
        self._server: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._dirty_streams: dict[Parameter, None] = {}
        self._stream_blocks: dict[int, list[Parameter]] | None = None   # filled when first needed
        self._local = threading.local()

    # -- tree shortcuts --------------------------------------------------

    def add(self, element: Element) -> Element:
        """Add a root element."""
        return self.tree.add(element)

    def add_node(self, identifier: str, **options: Any) -> Node:
        """Add a root :class:`Node` and return it. Options as for :class:`Node`."""
        return self.tree.add_node(identifier, **options)

    def add_parameter(self, identifier: str, value: Any = None, **options: Any) -> Parameter:
        """Add a root :class:`Parameter` and return it. Options as for :class:`Parameter`."""
        return self.tree.add_parameter(identifier, value, **options)

    def add_function(self, identifier: str, handler: Callable | None = None, **options: Any) -> Function:
        """Add a root :class:`Function` and return it. Options as for :class:`Function`."""
        return self.tree.add_function(identifier, handler, **options)

    def add_matrix(self, identifier: str, target_count: int, source_count: int, **options: Any) -> Matrix:
        """Add a root :class:`Matrix` and return it. Options as for :class:`Matrix`."""
        return self.tree.add_matrix(identifier, target_count, source_count, **options)

    def find(self, path) -> Element | None:
        """An element by numbers (``"1.2"``) or identifiers (``"Device/Audio/Gain"``)."""
        with self._lock:
            return self.tree.find(path)

    def __getitem__(self, path) -> Element:
        """An element by path, like :meth:`find`. Raises ``KeyError`` if there is none."""
        element = self.find(path)
        if element is None:
            raise KeyError(path)
        return element

    # -- running ---------------------------------------------------------

    @property
    def lock(self) -> threading.RLock:
        """Hold this lock (``with provider.lock:``) to change several things in one go
        or to read the tree while consumers are writing to it."""
        return self._lock

    @property
    def running(self) -> bool:
        """True while the port is open."""
        return self._server is not None

    @property
    def description(self) -> str:
        """For example ``TCP 0.0.0.0:9000``."""
        return f"TCP {self.bind}:{self.port}"

    @property
    def clients(self) -> list[Client]:
        """The consumers that are connected right now."""
        with self._lock:
            return list(self._clients)

    def start(self) -> "Provider":
        """Open the port and accept consumers. Raises :class:`ProviderError` if that fails."""
        with self._lock:
            if self._server is not None:
                return self
            family = socket.AF_INET6 if ":" in self.bind else socket.AF_INET
            try:
                server = socket.create_server((self.bind, self.port), family=family)
            except (OSError, OverflowError, ValueError) as exc:
                self.error = f"Cannot listen on {self.bind}:{self.port}: {exc}"
                raise ProviderError(self.error) from None
            server.settimeout(0.5)
            self.port = server.getsockname()[1]
            self.error = ""
            self._server = server
            self._stop = threading.Event()
            self._threads = [
                threading.Thread(target=self._accept, args=(server, self._stop),
                                 name="ember-provider-accept", daemon=True),
                threading.Thread(target=self._timer, args=(self._stop,),
                                 name="ember-provider-timer", daemon=True),
            ]
            for thread in self._threads:
                thread.start()
            return self

    def stop(self) -> None:
        """Close the port and all connections."""
        with self._lock:
            server, threads = self._server, self._threads
            if server is None:
                return
            self._server = None
            self._threads = []
            self._stop.set()
            clients = list(self._clients)
        try:
            server.close()
        except OSError:
            pass
        for client in clients:
            self._drop(client)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(timeout=2.0)

    def __enter__(self) -> "Provider":
        """Start serving."""
        return self.start()

    def __exit__(self, *exc) -> None:
        """Stop serving."""
        self.stop()

    # -- connections -----------------------------------------------------

    def _accept(self, server: socket.socket, stop: threading.Event) -> None:
        """Thread: accept consumers until the provider stops."""
        while not stop.is_set():
            try:
                sock, address = server.accept()
            except socket.timeout:
                continue
            except OSError:
                if not stop.is_set():
                    self._report("The listening port was closed unexpectedly")
                return
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(None)
            except OSError:
                pass
            client = Client(sock, address)
            with self._lock:
                if stop.is_set() or len(self._clients) >= self.max_clients:
                    refused = True
                else:
                    refused = False
                    self._clients.append(client)
            if refused:
                try:
                    sock.close()
                except OSError:
                    pass
                continue
            threading.Thread(target=self._writer, args=(client,),
                             name="ember-provider-send", daemon=True).start()
            threading.Thread(target=self._reader, args=(client,),
                             name="ember-provider-receive", daemon=True).start()
            self._call(self.on_connect, client)

    def _writer(self, client: Client) -> None:
        """Thread: send what is queued for one consumer, in order."""
        while True:
            data = client._queue.get()
            if data is None:
                return
            try:
                client._socket.sendall(data)
            except OSError:
                self._drop(client)
                return

    def _reader(self, client: Client) -> None:
        """Thread: receive from one consumer and handle its messages."""
        while True:
            try:
                data = client._socket.recv(RECEIVE_BYTES)
            except OSError:
                data = b""
            if not data:
                self._drop(client)
                return
            client._last_received = time.monotonic()
            client._keepalive_sent = None
            before = client._decoder.problem_count
            for message in client._decoder.feed(data):
                if message.is_keepalive_request:
                    self._enqueue(client, [s101.keepalive_response(message.slot)])
                elif message.is_ember:
                    client.glow_version = message.glow_version or client.glow_version
                    self._handle_message(client, message.payload)
            if client._decoder.problem_count != before:
                self.problem_count += client._decoder.problem_count - before
                self._report(f"{client.address}: {'; '.join(client._decoder.last_problems)}")

    def _drop(self, client: Client) -> None:
        """Close the connection to a consumer and forget it."""
        with self._lock:
            if client._closed:
                return
            client._closed = True
            if client in self._clients:
                self._clients.remove(client)
        try:
            client._queue.put_nowait(None)
        except queue.Full:
            pass
        try:
            client._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            client._socket.close()
        except OSError:
            pass
        self._call(self.on_disconnect, client)

    def _enqueue(self, client: Client, frames: list[bytes]) -> None:
        """Queue frames for a consumer. A consumer that does not read is dropped."""
        if client._closed:
            return
        try:
            for frame in frames:
                client._queue.put_nowait(frame)
        except queue.Full:
            # The consumer does not read. Dropping it keeps the others served.
            threading.Thread(target=self._drop, args=(client,), daemon=True).start()

    def _send(self, clients, root: Root) -> None:
        """Encode a message once and queue it for several consumers."""
        clients = [client for client in clients if not client._closed]
        if not clients:
            return
        frames = s101.encode_message(glow.encode_root(root))
        for client in clients:
            self._enqueue(client, frames)

    def _timer(self, stop: threading.Event) -> None:
        """Thread: send stream values at the stream interval and check keep-alives."""
        while not stop.wait(self.stream_interval):
            with self._lock:
                self._flush_streams()
                if self.keepalive:
                    self._check_keepalive()

    def _flush_streams(self) -> None:
        """Send the changed stream values to the consumers that subscribed to them."""
        if not self._dirty_streams:
            return
        changed = list(self._dirty_streams)
        self._dirty_streams.clear()
        values: dict[int, Any] = {}
        for parameter in changed:
            if parameter.stream_identifier is not None and parameter.stream_identifier not in values:
                values[parameter.stream_identifier] = self._stream_value(parameter)
        for client in self._clients:
            wanted = {parameter.stream_identifier for parameter in changed
                      if parameter.path in client.subscriptions and parameter.stream_identifier is not None}
            if wanted:
                self._send([client], Root(streams=[
                    StreamEntry(identifier, values[identifier]) for identifier in sorted(wanted)]))

    def _stream_value(self, parameter: Parameter) -> Any:
        """What travels for the stream of ``parameter``: its value, or a block of bytes.

        Parameters with a stream descriptor share one block per stream
        identifier. Each value sits in the block at its offset, in its format.
        """
        if parameter.stream_descriptor is None:
            return parameter.value
        if self._stream_blocks is None:
            blocks: dict[int, list[Parameter]] = {}
            for element in self.tree.walk():
                if (isinstance(element, Parameter) and element.stream_identifier is not None
                        and element.stream_descriptor is not None):
                    blocks.setdefault(element.stream_identifier, []).append(element)
            self._stream_blocks = blocks
        members = self._stream_blocks.get(parameter.stream_identifier or 0) or [parameter]
        parts = []
        for member in members:
            stream_format, offset = member.stream_descriptor or (0, 0)
            try:
                layout = _STREAM_STRUCT[StreamFormat(stream_format)]
            except (ValueError, KeyError):
                continue
            parts.append((layout, int(offset), member.value))
        block = bytearray(max((offset + struct.calcsize(layout) for layout, offset, _ in parts), default=0))
        for layout, offset, value in parts:
            number = value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
            try:
                struct.pack_into(layout, block, offset, float(number) if layout[-1] in "fd" else int(number))
            except (struct.error, OverflowError, ValueError):
                pass  # a value that does not fit its format leaves zeros
        return bytes(block)

    def _check_keepalive(self) -> None:
        """Ask idle consumers for a sign of life and drop those that stay silent."""
        now = time.monotonic()
        for client in list(self._clients):
            idle = now - client._last_received
            if client._keepalive_sent is None and idle >= self.keepalive:
                client._keepalive_sent = now
                self._enqueue(client, [s101.keepalive_request()])
            elif client._keepalive_sent is not None and now - client._keepalive_sent >= self.keepalive:
                threading.Thread(target=self._drop, args=(client,), daemon=True).start()

    # -- callbacks -------------------------------------------------------

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

    # -- changes made by the program that runs the provider -------------

    def _interested(self, element: Element) -> list[Client]:
        """The consumers that must hear about a change of ``element``."""
        path = element.path
        parent = path[:-1]
        requester = getattr(self._local, "client", None)
        return [client for client in self._clients
                if client is requester or parent in client.directories
                or path in client.directories or path in client.subscriptions]

    def _element_changed(self, element: Element, what: str, **details: Any) -> None:
        """Called by the tree, with the lock held, whenever something changes."""
        if what == "value":
            parameter: Parameter = element  # type: ignore[assignment]
            if parameter.is_stream:
                self._dirty_streams[parameter] = None
            elif parameter.readable:
                self._send(self._interested(parameter), Root(elements=[
                    GlowElement("parameter", path=parameter.path, contents={"value": parameter.value})]))
        elif what == "properties":
            contents = element.contents()
            wanted = {name: contents[name] for name in details.get("fields", ()) if name in contents}
            if wanted:
                self._send(self._interested(element), Root(elements=[
                    GlowElement(element.kind, path=element.path, contents=wanted)]))
        elif what == "connections":
            matrix: Matrix = element  # type: ignore[assignment]
            self._send(self._interested(matrix), Root(elements=[GlowElement(
                "matrix", path=matrix.path,
                connections=[Connection(target, matrix.sources_of(target),
                                        ConnectionOperation.ABSOLUTE, ConnectionDisposition.MODIFIED)
                             for target in details.get("targets", ())])]))
        elif what == "removing":
            self._stream_blocks = None
            child: Element = details["child"]
            if isinstance(child, (Node, Parameter)):
                # The documented way to say that a sub-tree is gone.
                self._send(self._interested(child), Root(elements=[
                    GlowElement(child.kind, path=child.path, contents={"is_online": False})]))
            for parameter in child.walk():
                self._dirty_streams.pop(parameter, None)  # type: ignore[arg-type]
        elif what == "children":
            self._stream_blocks = None
            path = element.path
            watching = [client for client in self._clients if path in client.directories]
            if watching:
                self._send(watching, self._listing(element, None))

    def refresh(self, element: Element) -> None:
        """Send all properties of an element again, after changing some of them directly.

        ``value`` and ``is_online`` announce themselves. Other properties,
        such as ``minimum`` or ``description``, are plain attributes: call
        this after changing them while consumers are connected.
        """
        with self._lock:
            self._stream_blocks = None
            if element.number is None:
                return
            self._send(self._interested(element), Root(elements=[
                GlowElement(element.kind, path=element.path, contents=element.contents())]))

    # -- requests from consumers ----------------------------------------

    def _handle_message(self, client: Client, payload: bytes) -> None:
        """Decode one message of a consumer and carry out what it asks for."""
        try:
            root = glow.decode_root(payload)
        except ValueError as exc:
            self.problem_count += 1
            self._report(f"{client.address}: message cannot be read: {exc}")
            return
        if root.elements is None:
            return  # consumers send neither streams nor function results
        client.requests += 1
        events: list[tuple] = []
        calls: list[tuple[Function, glow.Invocation]] = []
        with self._lock:
            self._local.client = client
            try:
                self._handle_elements(client, root.elements, (), events, calls)
            finally:
                self._local.client = None
        for function, invocation in calls:
            self._invoke(client, function, invocation)
        for callback, args in events:
            self._call(callback, *args)

    def _handle_elements(self, client: Client, elements: list[GlowElement], base: tuple[int, ...],
                         events: list, calls: list) -> None:
        """Walk the elements of a request: commands, new values and connections."""
        for item in elements:
            if item.kind == "command":
                target = self.tree.find(base)
                if target is not None:
                    self._command(client, target, item, events, calls)
                continue
            if item.kind == "template":
                continue
            path = item.path if item.path is not None else base + (item.number,)
            target = self.tree.find(path)
            if target is None:
                events.append((self.on_request, (client, "unknown", None, format_path(path))))
                continue
            if item.contents and "value" in item.contents and isinstance(target, Parameter):
                self._set_value(client, target, item.contents["value"], events)
            if item.connections and isinstance(target, Matrix):
                self._connect(client, target, item.connections, events)
            if item.children:
                self._handle_elements(client, item.children, path, events, calls)

    def _command(self, client: Client, target: Element, command: GlowElement,
                 events: list, calls: list) -> None:
        """Carry out one command on ``target``."""
        number = command.number
        path = target.path
        if number == CommandType.GET_DIRECTORY:
            client.directories.add(path)
            self._send([client], self._listing(target, command.dir_field_mask))
            events.append((self.on_request, (client, "directory", target, "")))
        elif number == CommandType.SUBSCRIBE:
            if isinstance(target, Parameter):
                client.subscriptions.add(path)
                if target.is_stream and target.stream_identifier is not None:
                    self._send([client], Root(streams=[
                        StreamEntry(target.stream_identifier, self._stream_value(target))]))
            else:
                client.directories.add(path)
            events.append((self.on_request, (client, "subscribe", target, "")))
        elif number == CommandType.UNSUBSCRIBE:
            if isinstance(target, Parameter):
                client.subscriptions.discard(path)
            else:
                client.directories.discard(path)
                depth = len(path)
                client.subscriptions = {other for other in client.subscriptions
                                        if other[:depth] != path}
            events.append((self.on_request, (client, "unsubscribe", target, "")))
        elif number == CommandType.INVOKE and isinstance(target, Function):
            calls.append((target, command.invocation or glow.Invocation()))

    def _contents(self, element: Element, mask: int | None) -> dict[str, Any]:
        """The contents of an element, reduced to what a field mask asks for."""
        contents = element.contents()
        try:
            keep = _MASK_FIELDS.get(FieldFlags(mask)) if mask is not None else None
        except ValueError:
            keep = None
        if keep is None:
            return contents
        return {name: contents[name] for name in keep if name in contents}

    def _plain(self, element: Element, mask: int | None) -> GlowElement:
        """An element as it appears in a listing: number and contents."""
        return GlowElement(element.kind, number=element.number, contents=self._contents(element, mask))

    def _listing(self, target: Element, mask: int | None) -> Root:
        """The answer to GetDirectory on ``target``."""
        if isinstance(target, Tree):
            return Root(elements=[self._plain(child, mask) for child in target.children])
        children = [self._plain(child, mask) for child in target.children] or None
        if isinstance(target, Node):
            # A node without children reports itself without any properties.
            return Root(elements=[GlowElement("node", path=target.path, children=children)])
        answer = GlowElement(target.kind, path=target.path, contents=self._contents(target, mask),
                             children=children)
        if isinstance(target, Matrix):
            if target.addressing_mode == MatrixAddressingMode.NON_LINEAR:
                answer.targets = target.target_numbers()
                answer.sources = target.source_numbers()
            answer.connections = [Connection(number, target.sources_of(number))
                                  for number in target.target_numbers()]
        return Root(elements=[answer])

    def _set_value(self, client: Client, parameter: Parameter, raw: Any, events: list) -> None:
        """A consumer asks for a new value: accept it, or answer with the current one."""
        accepted = False
        changed = False
        reason = ""
        if not parameter.writable:
            reason = "read only"
        else:
            try:
                value = parameter.coerce(raw)
            except ValueError as exc:
                reason = str(exc)
            else:
                accepted = True
                if parameter.type != ParameterType.TRIGGER and value is not None:
                    before = parameter.value
                    parameter.value = value          # tells every interested consumer
                    changed = not Parameter._same(before, parameter.value)
        answered = changed and not parameter.is_stream   # the change itself went to this consumer
        if not answered and parameter.readable and (
                parameter.value is not None or parameter.type != ParameterType.TRIGGER):
            # A provider always answers a change request, with the current value if it refuses.
            self._send([client], Root(elements=[
                GlowElement("parameter", path=parameter.path, contents={"value": parameter.value})]))
        if accepted:
            events.append((self.on_request, (client, "set", parameter, repr(parameter.value)
                                             if parameter.type != ParameterType.TRIGGER else "trigger")))
            if changed or parameter.type == ParameterType.TRIGGER:
                events.append((parameter.on_change, (parameter,)))
                events.append((self.on_change, (parameter, client)))
        else:
            events.append((self.on_request, (client, "refused", parameter, f"{raw!r}: {reason}")))

    def _connect(self, client: Client, matrix: Matrix, connections: list[Connection],
                 events: list) -> None:
        """A consumer asks for matrix connections: apply them, or report the refusal."""
        for connection in connections:
            target = connection.target
            before = matrix.sources_of(target)
            operation = connection.operation if connection.operation is not None else 0
            try:
                matrix.connect(target, connection.sources or (), operation)
                refused = ""
            except ValueError as exc:
                refused = str(exc)
            if refused or matrix.sources_of(target) == before:
                disposition = ConnectionDisposition.LOCKED if refused else ConnectionDisposition.MODIFIED
                self._send([client], Root(elements=[GlowElement(
                    "matrix", path=matrix.path,
                    connections=[Connection(target, matrix.sources_of(target),
                                            ConnectionOperation.ABSOLUTE, disposition)])]))
            sources = ", ".join(str(source) for source in matrix.sources_of(target)) or "none"
            if refused:
                events.append((self.on_request, (client, "refused", matrix, f"target {target}: {refused}")))
            else:
                events.append((self.on_request, (client, "connect", matrix,
                                                 f"target {target}: sources {sources}")))
                events.append((self.on_connection, (matrix, target, client)))

    def _invoke(self, client: Client, function: Function, invocation: glow.Invocation) -> None:
        """Run a function for a consumer and send the result."""
        success = True
        result: list = []
        try:
            if function.handler is None:
                raise RuntimeError("no handler")
            answer = function.handler(*invocation.arguments)
            if answer is None:
                result = []
            elif isinstance(answer, (tuple, list)):
                result = list(answer)
            else:
                result = [answer]
            glow.encode_root(Root(invocation_result=InvocationResult(0, True, result)))  # sendable?
        except Exception as exc:
            success = False
            result = []
            self._report(f"Function {function.identifier_path} failed: {type(exc).__name__}: {exc}")
        self._call(self.on_request, client, "invoke", function,
                   f"({', '.join(repr(item) for item in invocation.arguments)})"
                   + (f" -> {result!r}" if success else " failed"))
        if invocation.invocation_id is not None:
            with self._lock:
                self._send([client], Root(invocation_result=InvocationResult(
                    invocation.invocation_id, success, result)))
