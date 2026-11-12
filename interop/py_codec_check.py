"""Decode what the foreign library encoded and compare the bytes with those of emberemu."""
import json
import sys
from collections import Counter

from emberemu import glow

FAIR = ("real of usual size", "integer within 32 bits", "string", "boolean")


def group_of(kind, value):
    """Sort a value into a group. emberplus-connection has limits that emberemu does not share."""
    if kind == "real":
        return "real of usual size" if value == 0 or 1e-38 < abs(value) < 1e38 else "real beyond 1e38 or below 1e-38"
    if kind == "integer":
        return "integer within 32 bits" if -2 ** 31 <= value < 2 ** 31 else "integer beyond 32 bits"
    return kind


def main() -> int:
    """Read the file of the foreign encodings, compare, and report by group."""
    items = json.load(open(sys.argv[1]))
    total, same = Counter(), Counter()
    for item in items:
        value = float(item["value"]) if item["kind"] == "real" else item["value"]
        group = group_of(item["kind"], value)
        total[group] += 1
        try:
            got = glow.decode_root(bytes.fromhex(item["hex"])).elements[0].contents["value"]
        except ValueError:
            got = None
        if got == value and item["hex"] == item["mine"]:
            same[group] += 1
    failed = False
    for group, count in total.items():
        note = ""
        if group not in FAIR:
            note = "  (emberplus-connection cannot hold these, not counted)"
        elif same[group] != count:
            failed = True
            note = "  FAIL"
        print(f"PY {group:34} {count:5} values, same bytes and same value: {same[group]}{note}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
