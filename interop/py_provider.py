"""A provider of emberemu with one of everything, for the consumer of emberplus-connection to read."""
import sys, time
from emberemu import Access, Provider, StreamFormat

port = int(sys.argv[1]); seconds = float(sys.argv[2])
def log(*parts): print("PY", *parts, flush=True)
provider = Provider(port=port, bind="127.0.0.1",
                    on_request=lambda c, a, e, d: log("request", a, e.identifier_path if e is not None else None, d),
                    on_change=lambda p, c: log("changed", p.identifier_path, repr(p.value)),
                    on_connection=lambda m, t, c: log("connection", m.identifier_path, t, m.sources_of(t)),
                    on_error=lambda m: log("ERROR", m))
device = provider.add_node("Device", description="Test device")
ident = device.add_node("Identity")
ident.add_parameter("Name", "Studio A")
serial = ident.add_parameter("Serial", 123456)
audio = device.add_node("Audio")
gain = audio.add_parameter("Gain", -12.0, minimum=-60.0, maximum=12.0, access=Access.READ_WRITE, format="%.1f dB", description="Input gain")
audio.add_parameter("Mute", False, access="read_write")
audio.add_parameter("Mode", 1, enumeration=["Off", "Mono", "Stereo"], access="read_write")
audio.add_parameter("Standard", 50, enum_map=[("1080i50", 25), ("1080p50", 50)], access="read_write")
audio.add_parameter("Count", 5, minimum=0, maximum=10, step=1, access="read_write")
audio.add_parameter("Fader", -125, minimum=-1200, maximum=100, factor=10, access="read_write")
audio.add_parameter("Label", "Mic 1", access="read_write")
audio.add_parameter("Blob", b"\x01\xfe\xff", access="read_write")
audio.add_parameter("Fire", type="trigger", access="write")
level = audio.add_parameter("Level", -40.0, stream_identifier=7, format="%.1f dB")
meters = device.add_node("Meters")
level_int = meters.add_parameter("LevelInteger", -400, factor=10, stream_identifier=8)
left = meters.add_parameter("Left", -40.0, stream_identifier=9, stream_descriptor=(StreamFormat.FLOAT32_LE, 0))
right = meters.add_parameter("Right", -40.0, stream_identifier=9, stream_descriptor=(StreamFormat.FLOAT32_LE, 4))
device.add_node("Empty")
device.add_node("Offline", is_online=False)
device.add_function("Add", lambda a, b: a + b, arguments=[("integer", "a"), ("integer", "b")], result=[("integer", "sum")])
device.add_function("Boom", lambda: 1 / 0)
router = device.add_matrix("Router", 4, 4)
router.connect(0, 1)
mix = device.add_matrix("Mix", 2, 3, type="n_to_n")
big = device.add_node("Big")
for i in range(120):
    big.add_parameter(f"P{i:03d}", f"value number {i} " + "x" * 40, description="A longer description to fill frames")
provider.start()
log("serving", provider.description)
start = time.monotonic(); step = 0
while time.monotonic() - start < seconds:
    time.sleep(0.25); step += 1
    level.value = round(-40.0 + step, 1)          # stream of reals
    level_int.value = -400 + step                 # stream of whole numbers
    left.value = -40.0 + step * 0.5               # two values in one block of bytes
    right.value = -20.0 - step * 0.25
    if step == 12: serial.value = 777             # ordinary change made by the provider
    if step == 14: gain.value = 2.5
log("final", {"gain": gain.value, "mute": provider["Device/Audio/Mute"].value, "mode": provider["Device/Audio/Mode"].value,
              "label": provider["Device/Audio/Label"].value, "count": provider["Device/Audio/Count"].value,
              "blob": provider["Device/Audio/Blob"].value.hex(), "router": dict(router.connections), "mix": dict(mix.connections),
              "name": provider["Device/Identity/Name"].value, "standard": provider["Device/Audio/Standard"].value,
              "problems": provider.problem_count, "clients": len(provider.clients)})
provider.stop()
