"""Set one parameter of an Ember+ provider from the command line.

    python set_value.py Device/Audio/Gain -6
    python set_value.py Device/Audio/Mute true
    python set_value.py --host 192.168.1.10 Device/Audio/Mode Stereo

The value is read according to the type the provider announces. The script
prints the value the provider reports back, which is the old value if the
provider refused the change.
"""

import argparse
import sys

from emberemu import Consumer, ConsumerError, Parameter, ParameterType


def read_value(parameter: Parameter, text: str):
    """Turn text from the command line into the Python type of the parameter."""
    kind = parameter.type
    if kind == ParameterType.BOOLEAN:
        return text.lower() in ("true", "1", "on", "yes")
    if kind == ParameterType.INTEGER:
        return int(text)
    if kind == ParameterType.REAL:
        return float(text)
    if kind == ParameterType.ENUM:
        return int(text) if text.lstrip("-").isdigit() else text     # number or name of the entry
    if kind == ParameterType.OCTETS:
        return bytes.fromhex(text)
    return text


def main() -> int:
    """Read the command line, set the value and print the answer."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="identifier path such as Device/Audio/Gain, or numbers such as 1.2.1")
    parser.add_argument("value")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    try:
        with Consumer(args.host, args.port) as consumer:
            parameter = consumer.get(args.path)
            if not isinstance(parameter, Parameter):
                print(f"{args.path} is a {parameter.kind}, not a parameter", file=sys.stderr)
                return 1
            before = parameter.value
            wanted = parameter.coerce(read_value(parameter, args.value))
            reported = consumer.set_value(parameter, wanted)
            print(f"{parameter.identifier_path}: {before!r} -> {reported!r}")
            if reported != wanted:
                print("The provider kept its value"
                      + ("" if parameter.writable else ": the parameter is read only") + ".")
                return 2
    except KeyError:
        print(f"The provider has no element {args.path}", file=sys.stderr)
        return 1
    except (ConsumerError, TimeoutError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
