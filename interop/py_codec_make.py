"""Write values and how emberemu encodes them, for emberplus-connection to decode."""
import json, random, struct, sys
from emberemu import glow
random.seed(11)
reals = [0.0, 1.0, -1.0, 0.5, -0.5, 0.1, -12.5, 3.14159265358979, 1e-7, 1e12, -6.02e23, 2.0 ** -40, 123456789.125, 1e300, 1e-300]
reals += [random.uniform(-1000, 1000) for _ in range(400)]
reals += [random.uniform(-1, 1) * 10 ** random.randint(-30, 30) for _ in range(400)]
reals += [float(random.randint(-10 ** 6, 10 ** 6)) for _ in range(200)]
ints = [0, 1, -1, 127, 128, 255, 256, -128, -129, 32767, 32768, -32768, -32769, 2 ** 31 - 1, -2 ** 31, 2 ** 40, -2 ** 40]
ints += [random.randint(-2 ** 47, 2 ** 47) for _ in range(300)]
strings = ["", "plain", "Grüße", "日本語", "a\nb", "✓ ok"]
out = []
for kind, values in (("real", reals), ("integer", ints), ("string", strings), ("boolean", [True, False])):
    for value in values:
        root = glow.Root(elements=[glow.GlowElement("parameter", path=(1, 2, 3), contents={"value": value})])
        out.append({"kind": kind, "value": value, "hex": glow.encode_root(root).hex()})
json.dump(out, open(sys.argv[1], "w"))
print("PY wrote", len(out), "encodings")
