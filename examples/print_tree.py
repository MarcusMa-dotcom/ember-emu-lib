"""Connect to an Ember+ provider and print its whole tree.

    python print_tree.py 192.168.1.10
    python print_tree.py 127.0.0.1 --port 9000 --below Device/Audio
"""

import argparse
import sys

from emberemu import Consumer, ConsumerError, Element, Function, Matrix, Parameter


def describe(element: Element) -> str:
    """One line about an element."""
    if isinstance(element, Parameter):
        text = f"{element.type.name.lower()} {element.access.name.lower()} = {element.display_value()!r}"
        if element.minimum is not None or element.maximum is not None:
            text += f"  [{element.minimum} to {element.maximum}]"
        if element.is_stream:
            text += f"  stream {element.stream_identifier}"
        return text
    if isinstance(element, Function):
        arguments = ", ".join(f"{kind.name.lower()} {name or ''}".strip() for kind, name in element.arguments)
        result = ", ".join(f"{kind.name.lower()} {name or ''}".strip() for kind, name in element.result)
        return f"function ({arguments}) -> ({result})"
    if isinstance(element, Matrix):
        return (f"matrix {element.type.name.lower()}, {element.target_count} targets, "
                f"{element.source_count} sources, connections {dict(element.connections)}")
    return "" if getattr(element, "is_online", True) else "(offline)"


def main() -> int:
    """Read the command line, load the tree and print it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--below", default=None, help="only this part, for example Device/Audio")
    parser.add_argument("--max", type=int, default=10000, help="stop loading after this many elements")
    args = parser.parse_args()

    try:
        with Consumer(args.host, args.port) as consumer:
            elements = consumer.walk(args.below, max_elements=args.max)
            depth_of_start = len(consumer.get(args.below).path) if args.below else 1
            for element in elements:
                indent = "  " * (len(element.path) - depth_of_start)
                print(f"{indent}{element.path_text:<12} {element.identifier}  {describe(element)}".rstrip())
            print(f"\n{len(elements)} elements, Glow {consumer.provider_glow_version}")
    except (ConsumerError, TimeoutError) as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"The provider has no element {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
