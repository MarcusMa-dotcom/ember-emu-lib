"""Tests of the emberemu library: encoding, framing, provider and consumer.

The provider and consumer tests talk to each other over real TCP connections
on this machine.
"""

import math
import random
import struct
import time

import pytest

from emberemu import (Access, Consumer, ConsumerError, InvocationError, MatrixType, Node, Parameter,
                      ParameterType, Provider, ProviderError, StreamFormat, ber, element_from_dict, glow,
                      s101)
from emberemu.glow import Connection, GlowElement, Invocation, InvocationResult, Root, StreamEntry


def wait_for(condition, timeout=2.0):
    """Wait until ``condition()`` is true. Fails the test if it never is."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return
        time.sleep(0.01)
    assert condition()


# -- BER --------------------------------------------------------------------

@pytest.mark.parametrize("value, encoded", [
    (0, "020100"), (1, "020101"), (127, "02017f"), (128, "02020080"), (255, "020200ff"),
    (256, "02020100"), (-1, "0201ff"), (-128, "020180"), (-129, "0202ff7f"),
    (2 ** 31 - 1, "02047fffffff"), (-2 ** 63, "02088000000000000000"),
])
def test_integer(value, encoded):
    assert ber.integer(value).hex() == encoded
    assert ber.to_integer(ber.decode(bytes.fromhex(encoded))) == value


@pytest.mark.parametrize("value, encoded", [
    (0.0, "0900"),
    (1.0, "0903800001"),          # exponent 0, mantissa 1
    (-60.0, "0903c0050f"),        # sign, exponent 5, mantissa 1111b = 1.875 * 32
    (0.5, "090380ff01"),
    (math.inf, "090140"), (-math.inf, "090141"),
])
def test_real_known_bytes(value, encoded):
    assert ber.real(value).hex() == encoded
    assert ber.to_real(ber.decode(bytes.fromhex(encoded))) == value


def test_real_round_trip():
    random.seed(7)
    for _ in range(5000):
        value = struct.unpack(">d", struct.pack(">Q", random.getrandbits(64)))[0]
        if math.isnan(value):
            continue
        assert ber.to_real(ber.decode(ber.real(value))) == value
    assert math.isnan(ber.to_real(ber.decode(ber.real(math.nan))))
    assert math.copysign(1, ber.to_real(ber.decode(ber.real(-0.0)))) == -1


def test_real_mantissa_with_leading_zero_and_shift_is_read():
    # 0x80 exponent 0, mantissa 00 80: the leading zero byte must not matter.
    assert ber.to_real(ber.decode(bytes.fromhex("090480000080"))) == 1.0


def test_other_values():
    assert ber.to_value(ber.decode(ber.value(True))) is True
    assert ber.to_value(ber.decode(ber.value("Gäste"))) == "Gäste"
    assert ber.to_value(ber.decode(ber.value(b"\x00\xff"))) == b"\x00\xff"
    assert ber.to_value(ber.decode(ber.value(None))) is None
    assert ber.to_relative_oid(ber.decode(ber.relative_oid([1, 127, 128, 300000, 0]))) == (1, 127, 128, 300000, 0)
    assert ber.relative_oid([1, 128]).hex() == "0d03018100"


def test_indefinite_length_is_read():
    # Root, collection and field with indefinite length, as the Lawo libraries write them.
    data = bytes.fromhex("6080 6b80 a080 6280 a003020120 0000 0000 0000 0000")
    root = glow.decode_root(data)
    assert root.elements == [GlowElement("command", number=32)]


@pytest.mark.parametrize("data", [
    b"", b"\x60", b"\x60\x05\x01", b"\x02\x81", b"\x60\x80", b"\x60\x80\xa0\x80" * 500,
    bytes.fromhex("02 89 01 01 01 01 01 01 01 01 01"),
])
def test_bad_ber_raises_value_error(data):
    with pytest.raises(ValueError):
        glow.decode_root(data)


# -- S101 -------------------------------------------------------------------

def test_frame_example_of_the_ember_documentation():
    frame = s101.encode_frame(bytes([0xFF, 0x00, 0xF9, 0x01]))
    assert frame.hex(" ") == "fe fd df 00 fd d9 01 95 83 ff"


def test_crc_is_the_x25_checksum():
    assert ~s101.crc16(b"123456789") & 0xFFFF == 0x906E
    assert s101.CRC_TABLE[:4] == (0x0000, 0x1189, 0x2312, 0x329B)


def test_keepalive_frames():
    assert s101.keepalive_request().hex(" ") == "fe 00 0e 01 01 94 e4 ff"
    decoder = s101.MessageDecoder()
    messages = decoder.feed(s101.keepalive_request() + s101.keepalive_response())
    assert [m.command for m in messages] == [1, 2]


def test_long_message_is_split_and_joined():
    random.seed(3)
    payload = bytes(random.randrange(256) for _ in range(5000))
    frames = s101.encode_message(payload)
    assert len(frames) == 5
    decoder = s101.MessageDecoder()
    stream = b"noise" + b"".join(frames)
    messages = []
    for index in range(0, len(stream), 11):       # arrives in arbitrary pieces
        messages += decoder.feed(stream[index:index + 11])
    assert len(messages) == 1 and messages[0].payload == payload
    assert messages[0].glow_version == (2, 50)
    assert decoder.problem_count == 0


def test_empty_last_frame_closes_a_message():
    # The Lawo C# library ends long messages with an empty frame, flags 0x60.
    header = bytes([0, 0x0E, 0, 1])
    first = s101.encode_frame(header + bytes([0x80, 1, 2, 31, 2]) + b"abc")
    last = s101.encode_frame(header + bytes([0x60, 1, 2, 31, 2]))
    messages = s101.MessageDecoder().feed(first + last)
    assert [m.payload for m in messages] == [b"abc"]
    assert messages[0].glow_version == (2, 31)


def test_message_that_is_too_long_counts_as_one_problem():
    decoder = s101.MessageDecoder(max_message=3000)
    stream = b"".join(s101.encode_message(b"x" * 10000)) + b"".join(s101.encode_message(b"fine"))
    messages = decoder.feed(stream)
    assert [message.payload for message in messages] == [b"fine"]
    assert decoder.problem_count == 1 and "longer than 3000" in decoder.last_problems[0]


def test_wrong_checksum_is_a_problem_not_a_message():
    frame = bytearray(s101.encode_message(b"hello")[0])
    frame[12] ^= 0x01
    decoder = s101.MessageDecoder()
    assert decoder.feed(bytes(frame)) == []
    assert decoder.problem_count == 1 and "Checksum" in decoder.last_problems[0]
    assert len(decoder.feed(s101.encode_message(b"again")[0])) == 1   # and it recovers


def test_frames_without_escaping_are_read():
    body = bytes([0, 0x0E, 0, 1, 0xC0, 1, 2, 50, 2]) + b"\xfe\xff payload"
    plain = bytes([0xF8, 0x01, len(body)]) + body
    messages = s101.MessageDecoder().feed(plain + s101.keepalive_request())
    assert messages[0].payload == b"\xfe\xff payload" and messages[1].is_keepalive_request


# -- Glow -------------------------------------------------------------------

def test_get_directory_bytes():
    root = Root(elements=[GlowElement("command", number=32)])
    assert glow.encode_root(root).hex() == "600b6b09a0076205a003020120"


def test_qualified_parameter_value_bytes():
    root = Root(elements=[GlowElement("parameter", path=(1, 2), contents={"value": 5})])
    expected = ("60 15"                 # Root
                "6b 13"                 # RootElementCollection
                "a0 11"                 # [0] one element
                "69 0f"                 # QualifiedParameter
                "a0 04 0d 02 01 02"     # [0] path 1.2
                "a1 07 31 05"           # [1] contents, a set
                "a2 03 02 01 05")       # [2] value: INTEGER 5
    assert glow.encode_root(root) == bytes.fromhex(expected)
    assert glow.decode_root(glow.encode_root(root)) == root


def test_every_kind_round_trips():
    parameter = GlowElement("parameter", number=3, contents={
        "identifier": "Gain", "description": "Input gain", "value": -12.5, "minimum": -60.0,
        "maximum": 12.0, "access": 3, "format": "%.1f dB", "enumeration": "a\nb", "factor": 10,
        "is_online": True, "formula": "x*2\nx/2", "step": 1, "default": 0.0, "type": 2,
        "stream_identifier": 9, "enum_map": [("on", 1), ("off", 0)], "stream_descriptor": (20, 4),
        "schema_identifiers": "de.test", "template_reference": (1, 2)})
    function = GlowElement("function", number=4, contents={
        "identifier": "Add", "arguments": [(1, "a"), (1, None)], "result": [(1, "sum")]})
    matrix = GlowElement("matrix", path=(1, 5), contents={
        "identifier": "Router", "type": 2, "addressing_mode": 1, "target_count": 2, "source_count": 3,
        "maximum_total_connects": 4, "maximum_connects_per_target": 2, "parameters_location": (1, 9),
        "gain_parameter_number": 1, "labels": [((1, 8), "Primary")]},
        targets=[10, 20], sources=[1, 2, 3],
        connections=[Connection(10, (1, 3), 0, 1), Connection(20, ())])
    node = GlowElement("node", path=(1,), contents={"identifier": "Device", "is_root": True, "is_online": False},
                       children=[parameter, function,
                                 GlowElement("command", number=33, invocation=Invocation(7, [1, 2.5, "x", True, b"\x01", None])),
                                 GlowElement("command", number=32, dir_field_mask=-1)])
    for root in (Root(elements=[node, matrix]),
                 Root(streams=[StreamEntry(1, -20.5), StreamEntry(2, b"\x00\x01")]),
                 Root(invocation_result=InvocationResult(7, False, [])),
                 Root(invocation_result=InvocationResult(8, True, [3, "ok"])),
                 Root(elements=[])):
        assert glow.decode_root(glow.encode_root(root)) == root


def test_unknown_fields_are_skipped():
    # contents with tag 30, which no Glow version defines
    contents = ber.set_of(ber.context(0, ber.utf8_string("X")) + ber.context(30, ber.integer(1)))
    node = ber.application(3, ber.context(0, ber.integer(1)) + ber.context(1, contents))
    data = ber.application(0, ber.application(11, ber.context(0, node)))
    assert glow.decode_root(data).elements[0].contents == {"identifier": "X"}


def test_damaged_messages_never_raise_anything_but_value_error():
    random.seed(5)
    samples = [
        glow.encode_root(Root(elements=[GlowElement("node", path=(1, 2), children=[
            GlowElement("parameter", number=1, contents={"identifier": "Gain", "value": -6.5, "enum_map": [("a", 1)]}),
            GlowElement("command", number=33, invocation=Invocation(1, [1, "x", b"y"]))])])),
        glow.encode_root(Root(elements=[GlowElement("matrix", path=(1,), contents={"identifier": "M"},
                                                    targets=[0, 1], connections=[Connection(0, (1,), 0, 0)])])),
        glow.encode_root(Root(streams=[StreamEntry(1, 2.5)])),
        glow.encode_root(Root(invocation_result=InvocationResult(3, True, [1]))),
    ]
    for _ in range(4000):
        data = bytearray(random.choice(samples))
        for _ in range(random.randint(1, 4)):
            position = random.randrange(len(data))
            if random.random() < 0.6:
                data[position] = random.randrange(256)
            else:
                del data[position:position + random.randint(1, 3)]
            if not data:
                break
        try:
            glow.decode_root(bytes(data))
        except ValueError:
            pass


def test_path_of_absurd_length_is_refused():
    data = glow.encode_root(Root(elements=[GlowElement("node", path=tuple(range(1, 500)))]))
    with pytest.raises(glow.GlowError, match="more than 100"):
        glow.decode_root(data)


# -- tree -------------------------------------------------------------------

def test_tree_numbers_paths_and_lookup():
    root = Node("Device", number=1)
    audio = root.add_node("Audio")
    gain = audio.add_parameter("Gain", 1.5)
    audio.add_parameter("Mute", False, number=7)
    assert (audio.number, gain.number) == (1, 1)
    assert gain.path == (1, 1, 1) and gain.path_text == "1.1.1"
    assert gain.identifier_path == "Device/Audio/Gain"
    assert root.find("Audio/Mute").number == 7 and root.find("1.7").identifier == "Mute"
    assert root["Audio"]["Gain"] is gain and "Gain" in audio and len(audio) == 2
    assert bool(gain)                       # no children, still true
    assert [e.identifier for e in root.walk()] == ["Device", "Audio", "Gain", "Mute"]
    with pytest.raises(ValueError):
        audio.add_parameter("Gain", 2.0)    # identifier used twice
    with pytest.raises(ValueError):
        audio.add_parameter("Other", 2.0, number=7)
    audio.remove("Mute")
    assert audio.child("Mute") is None


def test_parameter_types_and_checks():
    assert Parameter("a", True).type == ParameterType.BOOLEAN
    assert Parameter("a", 1).type == ParameterType.INTEGER
    assert Parameter("a", 1.0).type == ParameterType.REAL
    assert Parameter("a", "x").type == ParameterType.STRING
    assert Parameter("a", b"x").type == ParameterType.OCTETS
    assert Parameter("a", 0, enumeration=["x", "y"]).type == ParameterType.ENUM
    assert Parameter("a", type="trigger").type == ParameterType.TRIGGER
    assert Parameter("a", 1).access == Access.READ
    with pytest.raises(ValueError):
        Parameter("a")
    gain = Parameter("Gain", 0.0, minimum=-60, maximum=12)
    gain.value = 5                               # whole numbers are fine for a real
    assert gain.value == 5.0 and isinstance(gain.value, float)
    for bad in (13.0, -61, "loud", True):
        with pytest.raises(ValueError):
            gain.value = bad
    mode = Parameter("Mode", 0, enumeration=["Off", "On"])
    mode.value = "On"
    assert mode.value == 1 and mode.display_value() == "On"
    with pytest.raises(ValueError):
        mode.value = 2
    assert Parameter("L", -125, factor=10, format="%.1f dB").display_value() == "-12.5 dB"


@pytest.mark.parametrize("bad", ["%999999999d", "%.999999f", "%*d", "%0100000d dB", "x" * 500 + "%d"])
def test_format_from_a_provider_cannot_blow_up_the_text(bad):
    # A format arrives from the device. A consumer must not build a huge text from it.
    shown = Parameter("L", 5, format=bad).display_value()
    assert len(shown) < 50 and shown.startswith("5")
    assert Parameter("L", 5, format="%40d").display_value().strip() == "5"


def test_tree_to_dict_and_back():
    root = Node("Device", number=1, description="Test")
    audio = root.add_node("Audio")
    audio.add_parameter("Gain", -3.0, minimum=-60.0, maximum=12.0, access="read_write", format="%.1f")
    audio.add_parameter("Mode", 1, enumeration=["a", "b"])
    audio.add_parameter("Blob", b"\x01\x02")
    audio.add_parameter("Fire", type="trigger", access="write")
    audio.add_function("Add", arguments=[("integer", "a")], result=[("real", "r")])
    matrix = audio.add_matrix("Router", 2, 2, type="one_to_one")
    matrix.connect(0, 1)
    data = root.to_dict()
    copy = element_from_dict(data)
    assert copy.to_dict() == data
    assert copy.find("Audio/Blob").value == b"\x01\x02"
    assert copy.find("Audio/Router").sources_of(0) == (1,)
    with pytest.raises(ValueError):
        element_from_dict({"kind": "parameter", "identifier": "x"})          # no value, no type
    with pytest.raises(ValueError):
        element_from_dict({"kind": "node", "identifier": "a/b"})


def test_matrix_rules():
    one_to_n = Node("r", number=1).add_matrix("m", 3, 3)
    one_to_n.connect(0, 1)
    one_to_n.connect(1, 1)                       # one source to several targets
    assert one_to_n.connections == {0: (1,), 1: (1,)}
    with pytest.raises(ValueError):
        one_to_n.connect(0, [1, 2])              # a target takes one source
    with pytest.raises(ValueError):
        one_to_n.connect(5, 1)
    one_to_one = Node("r", number=1).add_matrix("m", 3, 3, type=MatrixType.ONE_TO_ONE)
    one_to_one.connect(0, 1)
    one_to_one.connect(1, 1)                     # moves the source
    assert one_to_one.sources_of(0) == () and one_to_one.sources_of(1) == (1,)
    n_to_n = Node("r", number=1).add_matrix("m", 2, 4, type="n_to_n", maximum_connects_per_target=2)
    n_to_n.connect(0, [0, 1])
    n_to_n.connect(0, 1, "disconnect")
    n_to_n.connect(0, 3, "connect")
    assert n_to_n.sources_of(0) == (0, 3)
    with pytest.raises(ValueError):
        n_to_n.connect(0, 2, "connect")          # more than two sources


# -- provider and consumer -----------------------------------------------------

@pytest.fixture
def provider():
    log = []
    provider = Provider(port=0, bind="127.0.0.1",
                        on_request=lambda client, action, element, detail: log.append((action, detail)))
    provider.log = log
    device = provider.add_node("Device", description="Test device")
    identity = device.add_node("Identity")
    identity.add_parameter("Name", "Studio A")
    audio = device.add_node("Audio")
    audio.add_parameter("Gain", -12.0, minimum=-60.0, maximum=12.0, access=Access.READ_WRITE,
                        format="%.1f dB")
    audio.add_parameter("Mute", False, access="read_write")
    audio.add_parameter("Mode", 1, enumeration=["Off", "Mono", "Stereo"], access="read_write")
    audio.add_parameter("Count", 5, minimum=0, maximum=10, access="read_write")
    audio.add_parameter("Label", "Mic 1", access="read_write")
    audio.add_parameter("Blob", b"\x01\xff", access="read_write")
    audio.add_parameter("Fire", type="trigger", access="write")
    audio.add_parameter("Level", -40.0, stream_identifier=7)
    device.add_node("Empty")
    device.add_function("Add", lambda a, b: a + b, arguments=[("integer", "a"), ("integer", "b")],
                        result=[("integer", "sum")])
    device.add_function("Boom", lambda: 1 / 0)
    device.add_matrix("Router", 4, 4)
    device.add_matrix("Mix", 2, 3, type="n_to_n")
    provider.start()
    yield provider
    provider.stop()


@pytest.fixture
def consumer(provider):
    consumer = Consumer("127.0.0.1", provider.port, timeout=3.0)
    consumer.changes = []
    consumer.on_change = lambda element, fields: consumer.changes.append((element.identifier_path, fields))
    consumer.connect()
    yield consumer
    consumer.close()


def test_reading_the_tree(provider, consumer):
    assert [e.identifier for e in consumer.get_directory()] == ["Device"]
    gain = consumer.get("Device/Audio/Gain")
    assert (gain.value, gain.type, gain.minimum, gain.maximum) == (-12.0, ParameterType.REAL, -60.0, 12.0)
    assert gain.writable and gain.display_value() == "-12.0 dB"
    name = consumer.get("1.1.1")
    assert name.value == "Studio A" and not name.writable and name.identifier_path == "Device/Identity/Name"
    mode = consumer.get("Device/Audio/Mode")
    assert mode.type == ParameterType.ENUM and mode.display_value() == "Mono"
    assert consumer.get("Device/Audio/Blob").value == b"\x01\xff"
    assert consumer.get("Device/Audio/Fire").type == ParameterType.TRIGGER
    assert consumer.get_directory("Device/Empty") == []
    assert consumer.get("Device").description == "Test device"
    with pytest.raises(KeyError):
        consumer.get("Device/Nothing")
    everything = consumer.walk()
    assert len(everything) == len(list(provider.tree.walk()))
    assert consumer.provider_glow_version == (2, 50)


def test_writing_values(provider, consumer):
    assert consumer.set_value("Device/Audio/Gain", -6) == -6.0
    assert provider["Device/Audio/Gain"].value == -6.0
    assert consumer.set_value("Device/Audio/Mute", True) is True
    assert consumer.set_value("Device/Audio/Mode", "Stereo") == 2
    assert consumer.set_value("Device/Audio/Label", "Gäste") == "Gäste"
    assert consumer.set_value("Device/Audio/Blob", b"\xfe\xff\xf8") == b"\xfe\xff\xf8"
    assert consumer.set_value("Device/Audio/Gain", -6.0) == -6.0      # same value: still answered
    assert ("Device/Audio/Gain", {"value"}) in consumer.changes


def test_refused_writes_report_the_current_value(provider, consumer):
    assert consumer.set_value("Device/Identity/Name", "Hacked") == "Studio A"     # read only
    assert provider["Device/Identity/Name"].value == "Studio A"
    assert consumer.set_value("Device/Audio/Gain", 99.0, check=False) == -12.0    # out of range
    assert consumer.set_value("Device/Audio/Count", "seven", check=False) == 5    # wrong type
    assert provider["Device/Audio/Gain"].value == -12.0
    with pytest.raises(ValueError):
        consumer.set_value("Device/Audio/Gain", 99.0)        # the consumer checks first
    assert [action for action, _ in provider.log].count("refused") == 3


def test_changes_at_the_provider_reach_consumers(provider, consumer):
    gain = consumer.get("Device/Audio/Gain")
    provider["Device/Audio/Gain"].value = 3.5
    wait_for(lambda: gain.value == 3.5)
    identity = consumer.get("Device/Identity")
    provider["Device/Identity"].is_online = False
    wait_for(lambda: identity.is_online is False)


def test_two_consumers_see_each_other(provider, consumer):
    gain = consumer.get("Device/Audio/Gain")
    with Consumer("127.0.0.1", provider.port, qualified=False) as second:
        assert second.get("Device/Audio/Gain").value == -12.0
        assert second.set_value("Device/Audio/Gain", 1.0) == 1.0
        assert second.invoke("Device/Add", 20, 22) == [42]
        wait_for(lambda: gain.value == 1.0)
        assert len(provider.clients) == 2
    wait_for(lambda: len(provider.clients) == 1)


def test_trigger_and_callbacks(provider, consumer):
    fired = []
    provider.on_change = lambda parameter, client: fired.append((parameter.identifier, client.host))
    own = []
    provider["Device/Audio/Mute"].on_change = lambda parameter: own.append(parameter.value)
    consumer.trigger("Device/Audio/Fire")
    consumer.set_value("Device/Audio/Mute", True)
    wait_for(lambda: len(fired) == 2)
    assert fired == [("Fire", "127.0.0.1"), ("Mute", "127.0.0.1")] and own == [True]


def test_stream_values_need_a_subscription(provider, consumer):
    level = consumer.get("Device/Audio/Level")
    source = provider["Device/Audio/Level"]
    source.value = -30.0
    time.sleep(0.25)
    assert level.value == -40.0
    consumer.subscribe(level)
    wait_for(lambda: level.value == -30.0)
    source.value = -20.0
    wait_for(lambda: level.value == -20.0)
    consumer.unsubscribe(level)
    time.sleep(0.1)
    source.value = -10.0
    time.sleep(0.25)
    assert level.value == -20.0


def test_streams_packed_into_a_block_of_bytes(provider, consumer):
    # Two meters share stream 9: 32 bit floats, little endian, at byte 0 and byte 4.
    meters = provider["Device"].add_node("Meters")
    left = meters.add_parameter("Left", -60.0, stream_identifier=9,
                                stream_descriptor=(StreamFormat.FLOAT32_LE, 0))
    right = meters.add_parameter("Right", -60.0, stream_identifier=9,
                                 stream_descriptor=(StreamFormat.FLOAT32_LE, 4))
    peak = meters.add_parameter("Peak", 0, stream_identifier=10,
                                stream_descriptor=(StreamFormat.INT16_BE, 2))
    assert provider._stream_value(left) == struct.pack("<ff", -60.0, -60.0)
    seen_left = consumer.get("Device/Meters/Left")
    seen_right = consumer.get("Device/Meters/Right")
    seen_peak = consumer.get("Device/Meters/Peak")
    assert seen_left.stream_descriptor == (StreamFormat.FLOAT32_LE, 0)
    consumer.subscribe(seen_left)
    consumer.subscribe(seen_right)
    consumer.subscribe(seen_peak)
    left.value = -18.5
    wait_for(lambda: seen_left.value == -18.5)
    assert seen_right.value == -60.0                       # same block, its own place
    right.value = -3.25
    peak.value = -1234
    wait_for(lambda: seen_right.value == -3.25 and seen_peak.value == -1234)
    assert seen_left.value == -18.5
    extra = meters.add_parameter("Centre", 1.5, stream_identifier=9,
                                 stream_descriptor=(StreamFormat.FLOAT32_LE, 8))
    assert len(provider._stream_value(extra)) == 12        # the block grew with the tree


def test_watch(provider, consumer):
    seen = []
    gain = consumer.watch("Device/Audio/Gain", lambda parameter: seen.append(parameter.value))
    level = consumer.watch("Device/Audio/Level", lambda parameter: seen.append(parameter.value))
    wait_for(lambda: len(provider.clients[0].subscriptions) == 1)   # the stream was subscribed
    provider["Device/Audio/Gain"].value = 2.0
    provider["Device/Audio/Level"].value = -18.0
    wait_for(lambda: sorted(seen) == [-18.0, 2.0])
    consumer.unwatch(gain)
    provider["Device/Audio/Gain"].value = 4.0
    wait_for(lambda: gain.value == 4.0)
    assert sorted(seen) == [-18.0, 2.0] and level.value == -18.0


def test_functions(provider, consumer):
    assert consumer.invoke("Device/Add", 2, 3) == [5]
    with pytest.raises(InvocationError):
        consumer.invoke("Device/Boom")
    add = consumer.get("Device/Add")
    assert add.arguments == [(ParameterType.INTEGER, "a"), (ParameterType.INTEGER, "b")]


def test_matrix(provider, consumer):
    router = consumer.get("Device/Router")
    consumer.get_directory(router)
    assert (router.target_count, router.source_count, router.type) == (4, 4, MatrixType.ONE_TO_N)
    consumer.set_connection(router, 1, [2])
    wait_for(lambda: router.sources_of(1) == (2,))
    assert provider["Device/Router"].sources_of(1) == (2,)
    provider["Device/Router"].connect(0, 3)
    wait_for(lambda: router.sources_of(0) == (3,))
    consumer.set_connection(router, 1, [9])                    # no such source
    time.sleep(0.2)
    assert provider["Device/Router"].sources_of(1) == (2,)
    mix = consumer.get("Device/Mix")
    consumer.set_connection(mix, 0, [0, 1])
    consumer.set_connection(mix, 0, [2], "connect")
    consumer.set_connection(mix, 0, [0], "disconnect")
    wait_for(lambda: mix.sources_of(0) == (1, 2))


def test_tree_changes_while_connected(provider, consumer):
    audio = consumer.get("Device/Audio")
    consumer.get_directory(audio)
    extra = provider["Device/Audio"].add_parameter("Extra", 1)
    wait_for(lambda: audio.child("Extra") is not None)
    provider["Device/Audio"].remove(extra)
    wait_for(lambda: audio.child("Extra").is_online is False)
    names = [e.identifier for e in consumer.get_directory(audio, refresh=True)]
    assert "Extra" not in names and "Gain" in names
    provider["Device"].add_node("Later").add_parameter("X", 1)
    assert consumer.get("Device/Later/X").value == 1           # found by asking again


def test_large_listing_uses_several_frames(provider, consumer):
    big = provider["Device"].add_node("Big")
    for index in range(300):
        big.add_parameter(f"P{index:03d}", f"value {index} " + "x" * 40)
    children = consumer.get_directory("Device/Big")
    assert len(children) == 300 and children[299].value.startswith("value 299")


def test_lost_connection(provider, consumer):
    reasons = []
    consumer.on_disconnect = reasons.append
    consumer.get_directory()
    provider.stop()
    wait_for(lambda: not consumer.connected)
    assert reasons == ["The provider closed the connection"]
    with pytest.raises(ConsumerError):
        consumer.get_directory("Device", refresh=True)


def test_bad_data_does_not_end_the_provider(provider, consumer):
    errors = []
    provider.on_error = errors.append
    consumer.send_raw(b"\xfe\x00\x0e\x00\x01garbage\xff")                 # wrong checksum
    consumer.send_raw(s101.encode_message(b"\x60\x03\x01\x01\x01")[0])     # valid frame, bad Glow
    assert consumer.get("Device/Audio/Gain").value == -12.0
    wait_for(lambda: len(errors) == 2)
    assert provider.problem_count == 2


def test_oversized_request_is_dropped_and_the_connection_goes_on(provider, consumer):
    errors = []
    provider.on_error = errors.append
    for frame in s101.encode_message(b"\x00" * (1024 * 1024 + 2048)):     # more than the provider takes
        consumer.send_raw(frame)
    assert consumer.get("Device/Audio/Gain").value == -12.0
    wait_for(lambda: any("longer than" in message for message in errors))


def test_keepalive_is_answered(provider):
    import socket
    with socket.create_connection(("127.0.0.1", provider.port), timeout=2.0) as sock:
        sock.sendall(s101.keepalive_request())
        assert sock.recv(64) == s101.keepalive_response()


def test_connection_errors():
    with pytest.raises(ConsumerError):
        Consumer("127.0.0.1", 1, timeout=1.0).connect()
    first = Provider(port=0, bind="127.0.0.1").start()
    try:
        with pytest.raises(ProviderError):
            Provider(port=first.port, bind="127.0.0.1").start()
    finally:
        first.stop()
    with pytest.raises(ConsumerError):
        Consumer("127.0.0.1", 1).get_directory()               # not connected
