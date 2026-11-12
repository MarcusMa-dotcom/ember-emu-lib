"""Save the tree of an Ember+ provider as JSON, or serve such a file as a copy.

    python tree_to_json.py save 192.168.1.10 device.json
    python tree_to_json.py serve device.json --port 9001

The copy has the same structure, values and access rights as the original, so
a control system can be set up against it while the device is somewhere else.
Functions of the copy answer that the call failed, because their behaviour is
not part of the tree.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from emberemu import Consumer, ConsumerError, Provider, element_from_dict


def save(host: str, port: int, target: Path) -> int:
    """Read the whole tree of a provider and write it to a file."""
    try:
        with Consumer(host, port) as consumer:
            count = len(consumer.walk())
            with consumer.lock:                       # the provider keeps reporting changes
                data = consumer.tree.to_dict()
    except (ConsumerError, TimeoutError) as exc:
        print(exc, file=sys.stderr)
        return 1
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{count} elements written to {target}")
    return 0


def serve(source: Path, port: int) -> int:
    """Serve the tree of a file."""
    data = json.loads(source.read_text(encoding="utf-8"))
    provider = Provider(port=port, on_change=lambda parameter, client: print(
        f"{client.address} set {parameter.identifier_path} to {parameter.value!r}"))
    for item in data["children"]:
        try:
            provider.add(element_from_dict(item))
        except ValueError as exc:   # devices do not always keep to the rules for identifiers
            print(f"left out {item.get('identifier')!r}: {exc}", file=sys.stderr)
    with provider:
        count = sum(1 for _ in provider.tree.walk())
        print(f"Serving {count} elements on {provider.description}. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    return 0


def main() -> int:
    """Read the command line and save or serve."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    saving = commands.add_parser("save", help="read a provider and write a file")
    saving.add_argument("host")
    saving.add_argument("file", type=Path)
    saving.add_argument("--port", type=int, default=9000)
    serving = commands.add_parser("serve", help="serve a file")
    serving.add_argument("file", type=Path)
    serving.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    if args.command == "save":
        return save(args.host, args.port, args.file)
    return serve(args.file, args.port)


if __name__ == "__main__":
    sys.exit(main())
