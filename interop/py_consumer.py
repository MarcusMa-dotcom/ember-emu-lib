"""The consumer of emberemu against the provider of emberplus-connection."""
import sys, time
from emberemu import Consumer, MatrixType, ParameterType, Access, InvocationError

port = int(sys.argv[1]); results = []
def check(name, ok, detail=None):
    results.append(bool(ok)); print(f"PY {'ok  ' if ok else 'FAIL'} {name}" + (f" -> {detail!r}" if detail is not None else ""), flush=True)
def wait_for(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition(): return True
        time.sleep(0.02)
    return condition()

errors, changes = [], []
consumer = Consumer("127.0.0.1", port, timeout=4.0, on_error=errors.append,
                    on_change=lambda element, fields: changes.append((element.identifier_path, sorted(fields))))
consumer.connect()
check("connect", consumer.connected)
roots = consumer.get_directory()
check("root listing", [e.identifier for e in roots] == ["Root"], [(e.number, e.identifier, e.kind) for e in roots])
check("glow version announced", consumer.provider_glow_version is not None, consumer.provider_glow_version)
everything = consumer.walk()
check("walk whole tree", len(everything) == 17, [e.path_text + " " + e.identifier for e in everything])
check("node description", consumer.get("Root").description == "The root node", consumer.get("Root").description)

count = consumer.get("Root/Values/Count")
check("integer", (count.value, count.type, count.minimum, count.maximum, count.step, count.access) == (2, ParameterType.INTEGER, 0, 10, 1, Access.READ_WRITE),
      (count.value, count.type.name, count.minimum, count.maximum, count.step, count.access.name))
gain = consumer.get("Root/Values/Gain")
check("real", (gain.value, gain.type, gain.minimum, gain.maximum, gain.format, gain.description) == (-12.5, ParameterType.REAL, -60, 12, "%.1f dB", "Input gain"),
      (gain.value, gain.type.name, gain.minimum, gain.maximum, gain.format, gain.description))
check("real shown with format", gain.display_value() == "-12.5 dB", gain.display_value())
label = consumer.get("Root/Values/Label")
check("string", (label.value, label.type) == ("Grüße", ParameterType.STRING), (label.value, label.type.name))
mute = consumer.get("Root/Values/Mute")
check("boolean", (mute.value, mute.type) == (True, ParameterType.BOOLEAN), (mute.value, mute.type.name))
mode = consumer.get("Root/Values/Mode")
check("enum", (mode.value, mode.type, mode.enumeration, mode.display_value()) == (1, ParameterType.ENUM, ["Off", "Mono", "Stereo"], "Mono"),
      (mode.value, mode.type.name, mode.enumeration))
name = consumer.get("Root/Values/Name")
check("read only", (name.value, name.writable) == ("Sofie", False), (name.value, name.access.name))
whole = consumer.get("Root/Values/Whole")
check("real with a whole value stays a real", (whole.value, whole.type) == (3.0, ParameterType.REAL) and isinstance(whole.value, float), (whole.value, whole.type.name))
check("empty node", consumer.get_directory("Root/Empty") == [], None)
check("offline node", consumer.get("Root/Offline").is_online is False, consumer.get("Root/Offline").is_online)
check("by numbers", consumer.get("1.1.2") is gain)

# writing
check("set integer", consumer.set_value(count, 7) == 7, count.value)
check("set real", consumer.set_value(gain, -6.25) == -6.25, gain.value)
check("set real with whole number", consumer.set_value(gain, 3) == 3.0 and isinstance(gain.value, float), gain.value)
check("set string", consumer.set_value(label, "Gäste 1 ✓") == "Gäste 1 ✓", label.value)
check("set boolean", consumer.set_value(mute, False) is False, mute.value)
check("set enum by name", consumer.set_value(mode, "Stereo") == 2, mode.value)
try:
    kept = consumer.set_value("Root/Values/Locked", 9, timeout=2.0)
    check("refused write reports the kept value", kept == 5, kept)
except TimeoutError as exc:
    check("refused write reports the kept value", False, f"no answer: {exc}")

# function
add = consumer.get("Root/Functions/Add")
check("function signature", add.arguments == [(ParameterType.INTEGER, "a"), (ParameterType.INTEGER, "b")] and add.result == [(ParameterType.INTEGER, "sum")],
      (add.arguments, add.result, add.description))
try:
    check("invoke", consumer.invoke(add, 2, 40) == [42])
except (InvocationError, TimeoutError) as exc:
    check("invoke", False, repr(exc))

# matrix
matrix = consumer.get("Root/Matrices/Test Matrix")
consumer.get_directory(matrix)
check("matrix", (matrix.type, matrix.target_count, matrix.source_count, matrix.targets, matrix.sources) == (MatrixType.N_TO_N, 5, 5, [1, 2, 3, 4, 5], [1, 2, 3, 4, 5]),
      (matrix.type.name, matrix.addressing_mode.name, matrix.target_count, matrix.source_count, matrix.targets, matrix.sources, dict(matrix.connections)))
consumer.set_connection(matrix, 2, [3, 4])
check("matrix connect", wait_for(lambda: matrix.sources_of(2) == (3, 4)), dict(matrix.connections))
consumer.set_connection(matrix, 2, [4], "disconnect")
check("matrix disconnect", wait_for(lambda: matrix.sources_of(2) == (3,)), dict(matrix.connections))

# changes made by the provider itself
ticks = consumer.get("Root/Values/Ticks")
seen = []
consumer.watch(ticks, lambda parameter: seen.append(parameter.value))
before = ticks.value
check("provider change arrives", wait_for(lambda: ticks.value is not None and ticks.value >= (before or 0) + 3, 4.0) and len(seen) >= 3, (before, ticks.value, seen[:5]))
check("no decoding problems", consumer.problem_count == 0 and not errors, (consumer.problem_count, errors[:3]))
consumer.close()
print(f"PY done: {sum(results)} of {len(results)} checks passed", flush=True)
sys.exit(0 if all(results) else 1)
