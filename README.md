<p align="center">
  <img src="docs/images/logo.png" alt="Ember Emu logo" width="140">
</p>

# emberemu

A Python library for Ember+, the control protocol of broadcast devices
published by Lawo. It does both sides:

- as a **provider** it serves a tree of nodes, parameters, functions and
  matrices that any Ember+ consumer can read and control,
- as a **consumer** it reads the tree of any Ember+ device, follows its values
  and changes them.

It needs only the Python standard library (Python 3.10 or newer).

`emberemu` is the library behind [Ember Emu](https://github.com/MarcusMa-dotcom/ember-emu),
the test program with a desktop window, a web interface and a REST API. Use the
library when you want Ember+ inside your own software.

```python
from emberemu import Provider, Consumer

provider = Provider(port=9000)
audio = provider.add_node("Audio")
gain = audio.add_parameter("Gain", -12.0, minimum=-60.0, maximum=12.0, access="read_write")
audio.add_parameter("Name", "Studio A")            # read only for consumers
provider.start()

with Consumer("127.0.0.1", 9000) as consumer:
    print(consumer.get("Audio/Gain").value)         # -12.0
    consumer.set_value("Audio/Gain", -6.0)
    print(gain.value)                               # -6.0
```

## Install

```bash
pip install "git+https://github.com/MarcusMa-dotcom/ember-emu-lib@v0.1.0"
```

The package is not on PyPI. It is installed from this repository, or from a
copy of this folder:

```bash
pip install ./ember-emu-lib
```

## Ember+ in five sentences

A device shows what it can do as a **tree**. **Nodes** are the folders,
**parameters** are the values: numbers, text, switches, enumerations. Every
element has a number that is unique among its siblings, and the numbers from
the root down form its **path**, written `1.2.3`. It also has an
**identifier**, a name that never changes, so `Device/Audio/Gain` addresses the
same parameter. The device that owns the tree is the **provider**, a program
that reads and controls it is a **consumer**, and they talk over one TCP
connection, usually on port 9000.

## Serve a tree

```python
import time
from emberemu import Access, Provider

provider = Provider(port=9000)                      # listens on all network interfaces

device = provider.add_node("Device", description="My first Ember+ device")
audio = device.add_node("Audio")

gain = audio.add_parameter("Gain", -12.0, minimum=-60.0, maximum=12.0,
                           access=Access.READ_WRITE, format="%.1f dB")
mute = audio.add_parameter("Mute", False, access="read_write")
mode = audio.add_parameter("Mode", 1, enumeration=["Off", "Mono", "Stereo"], access="read_write")
name = device.add_parameter("Name", "Studio A")     # consumers can read it, not change it

provider.on_change = lambda parameter, client: print(
    f"{client.address} set {parameter.identifier_path} to {parameter.value!r}")

with provider:                                      # start() ... stop()
    while True:
        time.sleep(1)
```

The type of a parameter follows from its value: `True` gives a boolean, `5` an
integer, `5.0` a real, `"x"` a string, `b"x"` octets. An `enumeration` makes it
an enum whose value is the index of the entry. `access` is `"read"` unless you
say otherwise.

Change a value while the provider runs, and every consumer that looked at the
parameter is told:

```python
name.value = "Studio B"
```

A consumer that sends a value which does not fit, to a parameter that is read
only, or outside minimum and maximum, gets the current value back and nothing
changes.

### Triggers, meters, functions, matrices

```python
# A trigger has no value, it only fires.
restart = device.add_parameter("Restart", type="trigger", access="write",
                               on_change=lambda parameter: print("restart!"))

# A stream: values are sent only to consumers that subscribed, bundled about 16 times a second.
level = audio.add_parameter("Level", -600, stream_identifier=1, factor=10, format="%.1f dB")
level.value = -185                                   # shown as -18.5 dB. Call this as often as you like

# A function with arguments and a result.
device.add_function("Add", lambda a, b: a + b,
                    arguments=[("integer", "a"), ("integer", "b")], result=[("integer", "sum")])

# A routing matrix: 8 targets, 8 sources, one source per target.
router = device.add_matrix("Router", 8, 8)
router.connect(0, 3)                                 # target 0 takes source 3
provider.on_connection = lambda matrix, target, client: print(
    f"target {target} now has sources {matrix.sources_of(target)}")
```

## Read a tree

```python
from emberemu import Consumer

with Consumer("192.168.1.10", 9000) as consumer:
    for element in consumer.get_directory():        # the root elements
        print(element.number, element.identifier, element.kind)

    gain = consumer.get("Device/Audio/Gain")        # loads the nodes on the way
    print(gain.value, gain.minimum, gain.maximum, gain.writable, gain.display_value())

    for element in consumer.walk():                 # load everything
        print("  " * (len(element.path) - 1) + element.identifier)
```

The tree is loaded piece by piece, because trees of real devices are large.
`get_directory()` asks for the children of one node, `get()` follows a path,
`walk()` loads everything. Paths can be written with identifiers
(`"Device/Audio/Gain"`) or with numbers (`"1.2.1"`).

### Change values

```python
value = consumer.set_value("Device/Audio/Gain", -6.0)   # returns what the provider reports back
consumer.set_value("Device/Audio/Mode", "Stereo")       # enumerations take the name or the number
consumer.trigger("Device/Restart")
print(consumer.invoke("Device/Add", 2, 3))              # [5]
consumer.set_connection("Device/Router", 0, [5])        # target 0 takes source 5
```

If the provider refuses, `set_value` returns the value the provider kept.

### Follow values

Once a node is loaded, the provider reports changes of its parameters by
itself and the elements in `consumer.tree` follow. Three ways to hear about it:

```python
# 1. One parameter, "pinned": called with the parameter whenever its value changes.
#    A stream parameter is subscribed on the way.
consumer.watch("Device/Audio/Gain", lambda parameter: print("gain is", parameter.value))
consumer.watch("Device/Audio/Level", lambda parameter: print("level", parameter.value))

# 2. Everything the provider reports.
consumer.on_change = lambda element, fields: print(element.identifier_path, fields)

# 3. Just read gain.value when you need it. It is always the latest reported value.
```

## Examples

Ready-to-run scripts are in [examples/](examples/):

| Script | What it shows |
|---|---|
| [provider_basic.py](examples/provider_basic.py) | Serve a small tree and print what consumers change |
| [provider_meter.py](examples/provider_meter.py) | A stream parameter as audio meter, a trigger and a function |
| [print_tree.py](examples/print_tree.py) | Connect to a device and print its whole tree |
| [watch_parameters.py](examples/watch_parameters.py) | Follow some parameters and print every change |
| [set_value.py](examples/set_value.py) | Set one parameter from the command line |
| [tree_to_json.py](examples/tree_to_json.py) | Save the tree of a device as JSON and serve it again as a copy |

## Reference

All classes and functions are described in [docs/reference.md](docs/reference.md).
How the protocol is put together, layer by layer, is in
[docs/protocol.md](docs/protocol.md).

## Notes

- Callbacks run in network threads. Keep them short, and do not call methods
  that wait for the provider (`get`, `get_directory`, `set_value`, `invoke`)
  from inside a consumer callback: the answer cannot arrive while the callback
  runs. Hand the work to another thread instead.
- Ember+ has no login and no encryption. Whoever reaches the port of a provider
  can read its tree and change every parameter that is writable.
- Numbers of elements may differ from one session to the next. Remember
  elements by their identifier path.
- Not every consumer reads every kind of stream. Whole numbers and blocks of
  bytes are what devices usually send, so some consumers ignore a stream of
  plain real numbers. The reference shows how to serve each kind.
- Formulas, schemas and templates are passed on as they are and not evaluated.
  The newer way of labelling matrix signals (`labelsLocation`) is not read.
- A consumer keeps at most 250,000 elements of one provider.

## Tests

```bash
pip install -e ".[dev]"
```

```bash
python -m pytest
```

The tests let a provider and consumers talk over real TCP connections on this
machine. The encoding was also checked against the Lawo reference libraries:
the REAL encoding gives the same bytes as libember, and the message captures
that ship with Lawo's `ember-plus-sharp` (94 files) are all read and written
back unchanged.

[interop/](interop/) runs the library against an independent Ember+
implementation, the one of the Sofie TV automation system, in both directions:
their consumer reads a provider of `emberemu`, the consumer of `emberemu` reads
their provider, and both encode the same values to the same bytes. It needs
Node.js and is not part of the usual tests.

## License

MIT License. Copyright (c) 2026 Marcus Mathy. Use at your own risk: the
software comes as is, without warranty of any kind. The full text is in
[LICENSE](LICENSE).

Ember+ is a protocol published by Lawo GmbH. This library is an independent
implementation written from the public Ember+ documentation. It contains no
code of the Lawo libraries and is not affiliated with Lawo.
