"""S101, the framing protocol that carries Ember+ data over TCP.

A frame starts with 0xFE and ends with 0xFF. Bytes of 0xF8 and above are
escaped inside, and a 16 bit checksum closes the frame. A frame holds either a
keep-alive request, a keep-alive response or a piece of EmBER data. EmBER data
longer than 1024 bytes is spread over several frames.

Sending always uses this escaping variant, which every Ember+ device must
support. Receiving also understands the second variant without escaping
(start byte 0xF8, from Glow 2.50 on).
"""

from __future__ import annotations

from dataclasses import dataclass, field

BOF = 0xFE
EOF = 0xFF
CE = 0xFD
XOR = 0x20
INVALID = 0xF8  # also the start byte of the variant without escaping

MESSAGE_EMBER = 0x0E
COMMAND_EMBER = 0x00
COMMAND_KEEPALIVE_REQUEST = 0x01
COMMAND_KEEPALIVE_RESPONSE = 0x02
VERSION = 0x01
DTD_GLOW = 0x01

FLAG_FIRST = 0x80
FLAG_LAST = 0x40
FLAG_EMPTY = 0x20
FLAG_SINGLE = FLAG_FIRST | FLAG_LAST

GLOW_VERSION = (2, 50)       # (major, minor) announced in every EmBER frame
MAX_PAYLOAD = 1024           # EmBER bytes per frame
MAX_FRAME = 65536            # a longer frame is given up on
MAX_MESSAGE = 16 * 1024 * 1024  # EmBER bytes of one message across its frames


class S101Error(ValueError):
    """A frame or message breaks the S101 rules."""


def _crc_table() -> tuple[int, ...]:
    """The 256 entries of the checksum table: CRC-16 with the reflected polynomial 0x8408."""
    table = []
    for value in range(256):
        crc = value
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
        table.append(crc)
    return tuple(table)


CRC_TABLE = _crc_table()
CRC_RESIDUE = 0xF0B8


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    """The running checksum over ``data``. Start value 0xFFFF."""
    for byte in data:
        crc = (crc >> 8) ^ CRC_TABLE[(crc ^ byte) & 0xFF]
    return crc


def encode_frame(body: bytes) -> bytes:
    """Wrap message bytes into one frame: start byte, escaping, checksum, end byte."""
    crc = ~crc16(body) & 0xFFFF
    out = bytearray([BOF])
    for byte in body + bytes([crc & 0xFF, crc >> 8]):
        if byte >= INVALID:
            out.append(CE)
            out.append(byte ^ XOR)
        else:
            out.append(byte)
    out.append(EOF)
    return bytes(out)


def keepalive_request(slot: int = 0) -> bytes:
    """The frame that asks the other side for a sign of life."""
    return encode_frame(bytes([slot, MESSAGE_EMBER, COMMAND_KEEPALIVE_REQUEST, VERSION]))


def keepalive_response(slot: int = 0) -> bytes:
    """The frame that answers a keep-alive request."""
    return encode_frame(bytes([slot, MESSAGE_EMBER, COMMAND_KEEPALIVE_RESPONSE, VERSION]))


def encode_message(payload: bytes, slot: int = 0,
                   glow_version: tuple[int, int] = GLOW_VERSION) -> list[bytes]:
    """Frames for one EmBER message. Long payloads are split at 1024 bytes."""
    major, minor = glow_version
    chunks = [payload[i:i + MAX_PAYLOAD] for i in range(0, len(payload), MAX_PAYLOAD)] or [b""]
    frames = []
    for index, chunk in enumerate(chunks):
        flags = 0
        if index == 0:
            flags |= FLAG_FIRST
        if index == len(chunks) - 1:
            flags |= FLAG_LAST
        if not chunk:
            flags |= FLAG_EMPTY
        header = bytes([slot, MESSAGE_EMBER, COMMAND_EMBER, VERSION, flags, DTD_GLOW, 2, minor, major])
        frames.append(encode_frame(header + chunk))
    return frames


@dataclass
class Frame:
    """One decoded frame. ``problems`` is empty when the frame is sound."""

    slot: int = 0
    command: int = COMMAND_EMBER
    flags: int = 0
    dtd: int = DTD_GLOW
    app_bytes: bytes = b""
    payload: bytes = b""
    escaped: bool = True
    problems: list[str] = field(default_factory=list)

    @property
    def glow_version(self) -> tuple[int, int] | None:
        """(major, minor) as announced by the sender, if it sent the two bytes."""
        if len(self.app_bytes) >= 2:
            return self.app_bytes[1], self.app_bytes[0]
        return None


def _parse_body(body: bytes, escaped: bool) -> Frame:
    """Read the message header of a frame body. Problems are noted on the returned frame."""
    frame = Frame(escaped=escaped)
    if len(body) < 4:
        frame.problems.append(f"Frame of {len(body)} bytes is too short for a message")
        return frame
    frame.slot, message, frame.command, version = body[0], body[1], body[2], body[3]
    if message != MESSAGE_EMBER:
        frame.problems.append(f"Message type 0x{message:02X} is not EmBER (0x0E)")
        return frame
    if version != VERSION:
        frame.problems.append(f"S101 version {version} instead of 1")
    if frame.command in (COMMAND_KEEPALIVE_REQUEST, COMMAND_KEEPALIVE_RESPONSE):
        return frame
    if frame.command != COMMAND_EMBER:
        frame.problems.append(f"Unknown command 0x{frame.command:02X}")
        return frame
    if len(body) < 7:
        frame.problems.append("EmBER frame ends inside its header")
        return frame
    frame.flags, frame.dtd, count = body[4], body[5], body[6]
    if len(body) < 7 + count:
        frame.problems.append("EmBER frame ends inside its application bytes")
        return frame
    frame.app_bytes = bytes(body[7:7 + count])
    frame.payload = bytes(body[7 + count:])
    if frame.dtd != DTD_GLOW:
        frame.problems.append(f"DTD {frame.dtd} is not Glow (1)")
    return frame


class FrameDecoder:
    """Cuts a byte stream into frames. Feed it whatever arrives."""

    def __init__(self) -> None:
        """Start outside of any frame."""
        self._buffer = bytearray()
        self._in_frame = False
        self._escape = False
        self._overflow = False
        # Variant without escaping: header and payload are counted, not marked.
        self._plain_stage = 0      # 0 none, 1 length-of-length, 2 length, 3 payload
        self._plain_need = 0
        self._plain_length = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        """Take bytes as they arrive and return the frames that are complete now.

        Frames that break the rules are returned too, with ``problems`` filled in.
        """
        frames: list[Frame] = []
        for byte in data:
            if self._plain_stage:
                self._feed_plain(byte, frames)
            elif not self._in_frame:
                if byte == BOF:
                    self._start()
                elif byte == INVALID:
                    self._plain_stage = 1
                # Anything else between frames is noise and is skipped.
            elif byte == BOF:
                # A start byte inside a frame: the frame before was cut off.
                problem = Frame()
                problem.problems.append("Frame was cut off by the start of the next one")
                frames.append(problem)
                self._start()
            elif byte == EOF:
                self._in_frame = False
                frames.append(self._finish())
            elif byte == CE:
                self._escape = True
            else:
                if self._escape:
                    byte ^= XOR
                    self._escape = False
                if len(self._buffer) >= MAX_FRAME:
                    self._overflow = True
                else:
                    self._buffer.append(byte)
        return frames

    def _start(self) -> None:
        """Begin a new frame."""
        self._buffer.clear()
        self._in_frame = True
        self._escape = False
        self._overflow = False

    def _finish(self) -> Frame:
        """Close the current frame: check the checksum and read the header."""
        body = bytes(self._buffer)
        self._buffer.clear()
        if self._overflow:
            frame = Frame()
            frame.problems.append(f"Frame is longer than {MAX_FRAME} bytes")
            return frame
        if len(body) < 2:
            frame = Frame()
            frame.problems.append("Frame is too short for a checksum")
            return frame
        if crc16(body) != CRC_RESIDUE:
            frame = _parse_body(body[:-2], True)
            frame.problems.insert(0, "Checksum is wrong")
            return frame
        return _parse_body(body[:-2], True)

    def _feed_plain(self, byte: int, frames: list[Frame]) -> None:
        """Take one byte of a frame of the variant without escaping."""
        if self._plain_stage == 1:
            self._plain_need = byte & 0x07
            self._plain_length.clear()
            self._buffer.clear()
            if self._plain_need == 0:
                self._plain_stage = 0
                frames.append(_parse_body(b"", False))
            else:
                self._plain_stage = 2
        elif self._plain_stage == 2:
            self._plain_length.append(byte)
            if len(self._plain_length) == self._plain_need:
                self._plain_need = int.from_bytes(self._plain_length, "big")
                if self._plain_need > MAX_FRAME:
                    self._plain_stage = 0
                    frame = Frame(escaped=False)
                    frame.problems.append(f"Frame is longer than {MAX_FRAME} bytes")
                    frames.append(frame)
                elif self._plain_need == 0:
                    self._plain_stage = 0
                    frames.append(_parse_body(b"", False))
                else:
                    self._plain_stage = 3
        else:
            self._buffer.append(byte)
            if len(self._buffer) == self._plain_need:
                self._plain_stage = 0
                frames.append(_parse_body(bytes(self._buffer), False))
                self._buffer.clear()


@dataclass
class Message:
    """What a connection received: a keep-alive, or complete EmBER data."""

    command: int
    slot: int = 0
    payload: bytes = b""
    glow_version: tuple[int, int] | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def is_keepalive_request(self) -> bool:
        """True for a keep-alive request, which must be answered."""
        return self.command == COMMAND_KEEPALIVE_REQUEST

    @property
    def is_keepalive_response(self) -> bool:
        """True for the answer to a keep-alive request."""
        return self.command == COMMAND_KEEPALIVE_RESPONSE

    @property
    def is_ember(self) -> bool:
        """True if ``payload`` holds EmBER data."""
        return self.command == COMMAND_EMBER


class MessageDecoder:
    """Cuts a byte stream into messages and joins EmBER data spread over frames.

    Frames with problems are not passed on as messages. They are counted in
    ``problem_count`` and their texts are kept in ``last_problems``.
    """

    def __init__(self, max_message: int = MAX_MESSAGE) -> None:
        """Start with no message in progress.

        Args:
            max_message: Largest EmBER data of one message, in bytes. A longer
                message is dropped and counted as a problem.
        """
        self.max_message = max_message
        self._frames = FrameDecoder()
        self._parts: bytearray | None = None
        self._dropping = False   # the rest of a message that was too long is passed over
        self._version: tuple[int, int] | None = None
        self.problem_count = 0
        self.last_problems: list[str] = []

    def _problem(self, texts: list[str]) -> None:
        """Count a problem and keep its texts."""
        self.problem_count += 1
        self.last_problems = list(texts)

    def feed(self, data: bytes) -> list[Message]:
        """Take bytes as they arrive and return the messages that are complete now."""
        messages: list[Message] = []
        for frame in self._frames.feed(data):
            if frame.problems:
                self._problem(frame.problems)
                self._parts = None
                continue
            if frame.command != COMMAND_EMBER:
                messages.append(Message(frame.command, frame.slot))
                continue
            first = bool(frame.flags & FLAG_FIRST)
            last = bool(frame.flags & FLAG_LAST)
            payload = b"" if frame.flags & FLAG_EMPTY else frame.payload
            if first:
                if self._parts is not None:
                    self._problem(["A new message began before the last one was complete"])
                self._parts = bytearray()
                self._dropping = False
                self._version = frame.glow_version
            elif self._parts is None:
                if self._dropping:      # already reported once
                    self._dropping = not last
                else:
                    self._problem(["A frame continues a message whose beginning is missing"])
                continue
            self._parts.extend(payload)
            if len(self._parts) > self.max_message:
                self._problem([f"Message is longer than {self.max_message} bytes"])
                self._parts = None
                self._dropping = not last
                continue
            if last:
                if self._parts:
                    messages.append(Message(COMMAND_EMBER, frame.slot, bytes(self._parts),
                                            self._version))
                self._parts = None
        return messages
