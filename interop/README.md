# Running against another Ember+ implementation

This folder lets `emberemu` talk to
[emberplus-connection](https://github.com/Sofie-Automation/sofie-emberplus-connection),
the Ember+ library of the Sofie TV automation system. It was written
independently of this library, in TypeScript, and is used with Lawo devices.
If the two understand each other, both read the protocol the same way.

It needs Node.js. The package is fetched from the npm registry into this
folder. Nothing of it becomes part of `emberemu`.

```bash
npm install --ignore-scripts
```

```bash
./run.sh
```

`run.sh` uses `python3`. Name another interpreter with `PYTHON=../../.venv/bin/python ./run.sh`.
Ports 9481 to 9483 on this machine are used while it runs.

## What is run

| Part | Provider | Consumer | Checks |
|---|---|---|---|
| 1 | `py_provider.py` (emberemu) | `node_consumer.js` (emberplus-connection) | 35 |
| 2 | `node_provider.js` (emberplus-connection) | `py_consumer.py` (emberemu) | 30 |
| 3 | `py_codec_make.py`, `node_codec.js`, `py_codec_check.py` | | 1340 values encoded by both |

Parts 1 and 2 read the whole tree, read parameters of every type with their
properties, set values, try values that must be refused, call functions,
change matrix connections, and wait for changes the provider makes by itself.
Part 1 also covers streams, a listing that spans several S101 frames, and a
function that fails.

Part 3 encodes the same values with both libraries and compares the bytes.

## Result of 11 November 2026

With emberplus-connection 0.3.2-0, Node.js 26 and Python 3.15: all checks of
parts 1 and 2 pass. In part 3 the bytes are identical for every real number of
usual size, every integer within 32 bits, and all text and boolean values.

## What the run taught

- **Streams.** The consumer of emberplus-connection reads stream values only
  as whole numbers or as blocks of bytes that hold 32 bit floats, because that
  is what its authors saw devices send. A stream of plain real numbers is
  valid Ember+, but this consumer ignores it. `emberemu` can serve all three
  forms, see `stream_descriptor` in the reference.
- **Answers to a change.** emberplus-connection asks for the properties of a
  parameter without waiting for the answer, and takes the next report about
  the parameter as the answer to a later request. Right after
  `getElementByPath`, the promise of `setValue` can therefore resolve with the
  old value. The tree of the client is right a moment later.
- **Bytes.** emberplus-connection hands out a view into its receive buffer for
  octet values. The value changes when later messages arrive. Copy it if you
  need it.
- **Large numbers.** emberplus-connection keeps integers in 32 bits, Ember+
  allows 64. And it writes the exponent of real numbers beyond about 1e38 in a
  way its own decoder does not read back. `emberemu` writes both as the Lawo
  libraries do. Part 3 leaves these values out of the count.
