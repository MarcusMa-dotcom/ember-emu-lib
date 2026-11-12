"""Follow some parameters of an Ember+ provider and print every change.

    python watch_parameters.py Device/Audio/Gain Device/Audio/Mute
    python watch_parameters.py --host 192.168.1.10 Mixer/Level

This is "pinning" in a few lines: the parameters are named by their identifier
path, looked up once, and from then on the provider reports their changes by
itself. Stream parameters (meters) are subscribed on the way.
"""

import argparse
import sys
import time

from emberemu import Consumer, ConsumerError, Parameter


def on_value(parameter: Parameter) -> None:
    """Called in the network thread whenever the provider reports a new value."""
    print(f"{time.strftime('%H:%M:%S')}  {parameter.identifier_path} = {parameter.display_value()}")


def main() -> int:
    """Read the command line and watch until Ctrl+C or until the connection ends."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="identifier paths such as Device/Audio/Gain")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    consumer = Consumer(args.host, args.port,
                        on_disconnect=lambda reason: print("disconnected:", reason))
    try:
        consumer.connect()
        for path in args.paths:
            try:
                parameter = consumer.watch(path, on_value)
            except KeyError:
                print(f"{path}: not found at this provider")
                continue
            print(f"watching {parameter.identifier_path} = {parameter.display_value()}")
        while consumer.connected:
            time.sleep(0.2)
    except (ConsumerError, TimeoutError) as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        consumer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
