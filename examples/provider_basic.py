"""Serve a small Ember+ tree and print what consumers change.

    python provider_basic.py
    python provider_basic.py --port 9000 --bind 127.0.0.1

Connect with any Ember+ consumer, for example ``print_tree.py`` from this
folder, the Consumer tab of Ember Emu or the Lawo Ember+ Viewer.
"""

import argparse
import time

from emberemu import Access, Client, Parameter, Provider


def on_change(parameter: Parameter, client: Client) -> None:
    """Called whenever a consumer changed a value."""
    print(f"{time.strftime('%H:%M:%S')}  {client.address} set "
          f"{parameter.identifier_path} to {parameter.value!r}")


def main() -> None:
    """Build the tree, serve it and count the uptime until Ctrl+C."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--bind", default="0.0.0.0", help="127.0.0.1 serves this machine only")
    args = parser.parse_args()

    provider = Provider(port=args.port, bind=args.bind, on_change=on_change,
                        on_connect=lambda client: print(f"{client.address} connected"),
                        on_disconnect=lambda client: print(f"{client.address} disconnected"))

    device = provider.add_node("Device", description="Example device")
    device.add_parameter("Name", "Studio A")                             # read only
    uptime = device.add_parameter("Uptime", 0, format="%d s")            # read only, changes by itself

    audio = device.add_node("Audio")
    audio.add_parameter("Gain", -12.0, minimum=-60.0, maximum=12.0,
                        access=Access.READ_WRITE, format="%.1f dB")
    audio.add_parameter("Mute", False, access="read_write")
    audio.add_parameter("Mode", 1, enumeration=["Off", "Mono", "Stereo"], access="read_write")
    audio.add_parameter("Label", "Mic 1", access="read_write")

    with provider:
        print(f"Serving on {provider.description}. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
                uptime.value += 1           # every consumer that looked at Device is told
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
