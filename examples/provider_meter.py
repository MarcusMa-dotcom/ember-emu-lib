"""Serve an audio meter as a stream, with a trigger and a function.

    python provider_meter.py

A stream parameter sends its values only to consumers that subscribed, bundled
about 16 times a second. The meters here are whole numbers in tenths of a dB,
shown divided by their factor 10: that is the form every consumer reads. Watch
them with ``watch_parameters.py``:

    python watch_parameters.py Mixer/Level Mixer/PeakHold
"""

import argparse
import math
import time

from emberemu import Provider


def main() -> None:
    """Build the tree and move the meter until Ctrl+C."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--bind", default="0.0.0.0")
    args = parser.parse_args()

    provider = Provider(port=args.port, bind=args.bind,
                        on_error=lambda message: print("error:", message))
    mixer = provider.add_node("Mixer")

    # stream_identifier makes these two parameters streams.
    level = mixer.add_parameter("Level", -600, minimum=-600, maximum=0, factor=10,
                                format="%.1f dB", stream_identifier=1)
    peak = mixer.add_parameter("PeakHold", -600, factor=10, format="%.1f dB", stream_identifier=2)

    def reset(_parameter) -> None:
        """A consumer fired the trigger."""
        peak.value = -600
        print("peak hold reset")

    mixer.add_parameter("ResetPeak", type="trigger", access="write", on_change=reset)

    def to_linear(decibel: float) -> float:
        """Called when a consumer invokes the function. The return value is the result."""
        return round(10 ** (decibel / 20), 6)

    mixer.add_function("ToLinear", to_linear, arguments=[("real", "decibel")],
                       result=[("real", "factor")])

    with provider:
        print(f"Serving on {provider.description}. Press Ctrl+C to stop.")
        start = time.monotonic()
        try:
            while True:
                seconds = time.monotonic() - start
                level.value = round(-300 + 300 * math.sin(seconds * 2))    # as often as you like
                peak.value = max(peak.value, level.value)
                time.sleep(0.02)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
