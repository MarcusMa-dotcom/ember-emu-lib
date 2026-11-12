# emberemu reference

Everything below is imported from the package itself:

```python
from emberemu import Provider, Consumer, Node, Parameter, Function, Matrix, Access, ParameterType
```

- [The tree](#the-tree)
- [Element](#element)
- [Node](#node)
- [Parameter](#parameter)
- [Function](#function)
- [Matrix](#matrix)
- [Provider](#provider)
- [Client](#client)
- [Consumer](#consumer)
- [Trees as JSON](#trees-as-json)
- [Enumerations](#enumerations)
- [Errors](#errors)
- [Threads and callbacks](#threads-and-callbacks)
- [The lower layers](#the-lower-layers)
- [What is covered](#what-is-covered)

## The tree

Provider and consumer work with the same classes. A provider builds a tree and
serves it. A consumer receives a tree and keeps it up to date.

| Class | Is |
|---|---|
| `Node` | A folder |
| `Parameter` | A value |
| `Function` | Something a consumer can call |
| `Matrix` | A routing matrix |
| `Tree` | The invisible top. Its children are the root elements. `provider.tree`, `consumer.tree` |

Every element has a `number` that is unique among its siblings. The numbers
from the root down are its `path`: `(1, 2, 3)`, written `"1.2.3"`. Every
element also has an `identifier`. Ember+ asks that an identifier starts with a
letter or an underscore, contains no slash and never changes. The identifiers
from the root down, joined with slashes, are the `identifier_path`:
`"Device/Audio/Gain"`.

Wherever a method takes a path, it accepts

- text of identifiers: `"Device/Audio/Gain"`,
- text of numbers: `"1.2.3"`,
- a sequence of numbers and identifiers: `(1, "Audio", 3)`,
- on a consumer also the element itself.

## Element

What `Node`, `Parameter`, `Function` and `Matrix` have in common.

| Property | Content |
|---|---|
| `number` | Number among the siblings. Given by `add()` if it was left out: the highest number in use plus 1 |
| `identifier` | The name |
| `description` | Text for people, or `None` |
| `kind` | `"node"`, `"parameter"`, `"function"` or `"matrix"` |
| `path` | The numbers from the root down, a tuple |
| `path_text` | The same as text: `"1.2.3"` |
| `identifier_path` | The identifiers from the root down: `"Device/Audio/Gain"` |
| `parent` | The element above, the `Tree` for a root element |
| `children` | The children as a list, in the order they were added |
| `children_loaded` | Consumer side: True once the provider has listed the children |
| `owner` | The `Provider` or `Consumer` the tree belongs to, or `None` |

| Method | Does |
|---|---|
| `add(element)` | Adds a child and returns it. Raises `ValueError` if number or identifier are in use |
| `add_node(identifier, **options)` | Makes a `Node`, adds it and returns it |
| `add_parameter(identifier, value=None, **options)` | The same for a `Parameter` |
| `add_function(identifier, handler=None, **options)` | The same for a `Function` |
| `add_matrix(identifier, target_count, source_count, **options)` | The same for a `Matrix` |
| `remove(child)` | Removes a child, given as element, number or identifier. Raises `KeyError` if there is none |
| `clear()` | Removes all children |
| `child(key)` | The child with this number (`int`) or identifier (`str`), or `None` |
| `element[key]` | The same, but raises `KeyError` |
| `key in element` | True if there is such a child |
| `len(element)`, `for child in element` | Number of children, and a loop over them |
| `find(path)` | A descendant by path, or `None` |
| `walk()` | The element and everything below it, parents before children |
| `to_dict(children=True)` | Plain data for JSON, see [Trees as JSON](#trees-as-json) |

An element is always true in an `if`, also when it has no children.

On a running provider, `add()` and `remove()` tell the consumers that looked
at the parent: a new child is announced with a fresh listing, a removed element
is reported as offline first, as the Ember+ documentation asks.

## Node

```python
Node(identifier, *, description=None, number=None, is_online=True, is_root=None,
     schema_identifiers=None, template_reference=None)
```

| Option | Meaning | Default |
|---|---|---|
| `is_online` | `False` marks the node, and with it everything below, as not available. Assigning to it on a running provider tells the consumers | `True` |
| `is_root` | Sent as the field `isRoot` if given | `None` |
| `schema_identifiers` | Schemas the node complies with, separated by line feeds | `None` |
| `template_reference` | Path of a template. Passed on, not evaluated | `None` |

## Parameter

```python
Parameter(identifier, value=None, *, type=None, access=Access.READ, minimum=None, maximum=None,
          format=None, enumeration=None, enum_map=None, factor=None, step=None, default=None,
          formula=None, is_online=True, stream_identifier=None, stream_descriptor=None,
          schema_identifiers=None, template_reference=None, description=None, number=None,
          on_change=None)
```

### Types

The type follows from the value unless `type` is given.

| `ParameterType` | Name | Python value | Follows from |
|---|---|---|---|
| `INTEGER` | `"integer"` | `int`, 64 bits | an `int` value |
| `REAL` | `"real"` | `float` | a `float` value |
| `STRING` | `"string"` | `str` | a `str` value |
| `BOOLEAN` | `"boolean"` | `bool` | `True` or `False` |
| `ENUM` | `"enum"` | `int`, the number of the entry | an `int` value together with `enumeration` or `enum_map` |
| `OCTETS` | `"octets"` | `bytes` | a `bytes` value |
| `TRIGGER` | `"trigger"` | none | only `type="trigger"` |
| `NULL` | `"null"` | none | a consumer shows this for a parameter the provider did not describe |

`type` takes a `ParameterType`, its name or its number. A parameter without a
value needs a `type`.

### Options

| Option | Meaning | Default |
|---|---|---|
| `access` | What consumers may do: `Access.READ`, `WRITE`, `READ_WRITE`, `NONE`, or the names `"read"`, `"write"`, `"read_write"`, `"none"` | `Access.READ` |
| `minimum`, `maximum` | Range of an integer or real. Values outside are refused | `None` |
| `format` | How to show a number, in C notation: `"%.1f dB"`, `"%d ms"` | `None` |
| `enumeration` | Names of the entries of an enum, as a list. The value is the index | `None` |
| `enum_map` | Entries with their own numbers: `[("1080i50", 25), ("1080p50", 50)]` or a dict | `None` |
| `factor` | A whole number is shown divided by this: value -125 with factor 10 is shown as -12.5 | `None` |
| `step` | Suggested step for user interfaces | `None` |
| `default` | A value consumers may offer as reset value | `None` |
| `formula` | Conversion formulas. Passed on, not evaluated | `None` |
| `is_online` | `False` marks the parameter as not available | `True` |
| `stream_identifier` | Makes the parameter a stream, see [Streams](#streams) | `None` |
| `stream_descriptor` | `(format, offset)` for a stream that carries a block of bytes, see [Streams](#streams) | `None` |
| `on_change` | Function called with the parameter after a consumer changed it or fired it | `None` |

### Value

| Property or method | Content |
|---|---|
| `value` | The value. `None` means there is none |
| `value = x` | On a provider: stores the value and tells the consumers. Raises `ValueError` if it does not fit. On a consumer: asks the provider to change the value and returns at once |
| `coerce(x)` | `x` in the Python type of the parameter, checked against range and entries. Raises `ValueError` |
| `display_value()` | The value as a user interface shows it: entry name, factor and format applied |
| `enum_entries()` | The entries as `(name, number)`, from `enum_map` or `enumeration` |
| `readable`, `writable` | What `access` allows consumers |
| `is_stream` | True if there is a `stream_identifier` |

What fits:

| Type | Accepted |
|---|---|
| integer | `int`, and a `float` without fraction. Not `bool` |
| real | `int` and `float`, stored as `float` |
| string | `str` |
| boolean | `bool`, and the numbers 0 and 1 |
| enum | The number of an entry, or its name |
| octets | `bytes`, `bytearray` |
| trigger | Anything. It is not stored |

`access` limits consumers only. The program that runs the provider can always
assign to `value`.

### Streams

A parameter with a `stream_identifier` is a stream, typically an audio level.
Its values are not sent like other changes. They go only to consumers that
subscribed, collected and sent together every `stream_interval` seconds (0.06
unless the provider is told otherwise). Assign to `value` as often as you
like: only the latest value of each interval travels.

```python
level = audio.add_parameter("Level", -600, stream_identifier=1, factor=10)   # tenths of a dB
level.value = -185
```

A consumer subscribes with `consumer.subscribe(parameter)` or
`consumer.watch(parameter, callback)`.

Devices send streams in three forms, and not every consumer reads all three:

| Form | How | Read by |
|---|---|---|
| Whole numbers | An integer parameter with a `stream_identifier`. A `factor` gives it decimals | every consumer |
| A block of bytes | Several parameters share one `stream_identifier`, each with a `stream_descriptor` | every consumer that knows stream descriptors |
| Real numbers | A real parameter with a `stream_identifier` | valid, but some consumers ignore it |

A `stream_descriptor` is `(format, offset)`: how the number is stored in the
block (see `StreamFormat`) and at which byte. The provider builds the block
from all parameters of the tree that share the stream identifier and sends it
whenever one of them changes. A consumer unpacks such blocks by itself.

```python
from emberemu import StreamFormat

left = audio.add_parameter("Left", -60.0, stream_identifier=2,
                           stream_descriptor=(StreamFormat.FLOAT32_LE, 0))
right = audio.add_parameter("Right", -60.0, stream_identifier=2,
                            stream_descriptor=(StreamFormat.FLOAT32_LE, 4))
left.value = -18.5            # travels as 8 bytes: two 32 bit floats
```

A value that does not fit its format, such as 300 in `UINT8`, is sent as zero.

## Function

```python
Function(identifier, handler=None, *, arguments=(), result=(), description=None, number=None)
```

| Option | Meaning |
|---|---|
| `handler` | Called with the argument values when a consumer invokes the function |
| `arguments` | What goes in: a sequence of `(type, name)`, or of types alone |
| `result` | What comes out, in the same form |

The handler may return nothing, one value, or a tuple or list of values. If it
raises, the consumer is told that the call failed and the provider reports the
error through `on_error`. The handler runs in the network thread of the
consumer that called.

```python
def divide(a, b):
    return a / b, a % b

device.add_function("Divide", divide,
                    arguments=[("integer", "a"), ("integer", "b")],
                    result=[("real", "quotient"), ("integer", "rest")])
```

## Matrix

```python
Matrix(identifier, target_count, source_count, *, type=MatrixType.ONE_TO_N,
       addressing_mode=MatrixAddressingMode.LINEAR, targets=None, sources=None,
       maximum_total_connects=None, maximum_connects_per_target=None, parameters_location=None,
       gain_parameter_number=None, labels=None, schema_identifiers=None, description=None, number=None)
```

A matrix says which sources are connected to which targets.

| `type` | Name | Rule |
|---|---|---|
| `MatrixType.ONE_TO_N` | `"one_to_n"` | A target takes one source. A source may feed many targets |
| `MatrixType.ONE_TO_ONE` | `"one_to_one"` | A target takes one source, and a source feeds one target. Connecting a source that is in use moves it |
| `MatrixType.N_TO_N` | `"n_to_n"` | Any number of sources per target, limited by `maximum_connects_per_target` and `maximum_total_connects` |

With `addressing_mode="linear"` the signals are numbered 0 to count - 1. With
`"non_linear"` the lists `targets` and `sources` hold the numbers.

| Property or method | Content |
|---|---|
| `connections` | A dict: target number to tuple of source numbers |
| `sources_of(target)` | The sources of one target, an empty tuple for none |
| `connect(target, sources, operation="absolute")` | Changes the sources of one target and returns the new ones. `sources` is a number or a list. `operation` is `"absolute"` (replace), `"connect"` (add) or `"disconnect"` (remove). Raises `ValueError` if the matrix does not allow the result. On a consumer it asks the provider instead |
| `target_numbers()`, `source_numbers()` | All signal numbers |

Labels follow the Ember+ scheme: `labels=[(path, "Primary")]` points to a node
that holds a node number 1 with one string parameter per target and a node
number 2 with one string parameter per source. Each of these parameters has
the number of its signal.

```python
router = device.add_matrix("Router", 2, 2, labels=[((1, 9), "Primary")])
labels = device.add_node("Labels", number=9)
targets = labels.add_node("targets", number=1)
targets.add_parameter("T1", "PGM", number=0, access="read_write")
targets.add_parameter("T2", "PVW", number=1, access="read_write")
sources = labels.add_node("sources", number=2)
sources.add_parameter("S1", "CAM 1", number=0)
sources.add_parameter("S2", "CAM 2", number=1)
```

## Provider

```python
Provider(port=9000, bind="0.0.0.0", *, stream_interval=0.06, keepalive=None, max_clients=64,
         on_change=None, on_connection=None, on_connect=None, on_disconnect=None,
         on_request=None, on_error=None)
```

| Option | Meaning | Default |
|---|---|---|
| `port` | TCP port. 0 picks a free one, readable from `provider.port` after `start()` | 9000 |
| `bind` | `"0.0.0.0"` listens on all network interfaces, `"127.0.0.1"` only on this machine | `"0.0.0.0"` |
| `stream_interval` | Seconds between two bundles of stream values | 0.06 |
| `keepalive` | Seconds of silence after which a consumer is asked for a sign of life, and dropped if none comes | `None`: off |
| `max_clients` | Consumers accepted at the same time | 64 |

| Method | Does |
|---|---|
| `add(element)`, `add_node(...)`, `add_parameter(...)`, `add_function(...)`, `add_matrix(...)` | Add a root element. The same as on `provider.tree` |
| `find(path)` | An element by path, or `None` |
| `provider[path]` | The same, but raises `KeyError` |
| `start()` | Opens the port. Raises `ProviderError` if that fails. Returns the provider |
| `stop()` | Closes the port and all connections |
| `with provider:` | `start()` on the way in, `stop()` on the way out |
| `refresh(element)` | Sends all properties of an element again, after changing plain attributes such as `minimum` or `description` while consumers are connected |

| Property | Content |
|---|---|
| `tree` | The `Tree` |
| `running` | True while the port is open |
| `clients` | The connected consumers, a list of [Client](#client) |
| `description` | For example `TCP 0.0.0.0:9000` |
| `error` | The last error message |
| `problem_count` | How many frames and messages of consumers could not be read |
| `lock` | Hold it (`with provider.lock:`) to change several things in one go |

### Callbacks

| Callback | Called when |
|---|---|
| `on_change(parameter, client)` | A consumer changed a value, or fired a trigger |
| `on_connection(matrix, target, client)` | A consumer changed the sources of a matrix target |
| `on_connect(client)`, `on_disconnect(client)` | A consumer came or went |
| `on_request(client, action, element, detail)` | Every request, for logging. `action` is `"directory"`, `"set"`, `"refused"`, `"subscribe"`, `"unsubscribe"`, `"invoke"`, `"connect"` or `"unknown"` |
| `on_error(message)` | Bad data from a consumer, a failing function or callback |

`parameter.on_change(parameter)` is called before `provider.on_change`.
Changes the program itself makes by assigning to `value` call neither.

### What the provider does by itself

- It answers GetDirectory for the root, for nodes, parameters, functions and
  matrices, in the qualified form. A consumer that asked for the children of a
  node is told about value changes below that node from then on.
- It answers every request to change a value: with the new value if it
  accepted, with the current value if it refused.
- It honours the field mask of GetDirectory.
- It accepts requests in the qualified and in the nested form, definite and
  indefinite lengths, and both S101 variants.
- It answers keep-alive requests.
- It drops a consumer that does not read what is sent to it.

## Client

One connected consumer, as passed to the provider callbacks.

| Property | Content |
|---|---|
| `address` | `host:port` |
| `host`, `port` | The two parts |
| `connected_at` | Time of the connect, seconds since 1970 |
| `requests` | Number of messages received |
| `glow_version` | `(major, minor)` of the Glow schema the consumer announced, or `None` |
| `directories` | Paths of the nodes whose children the consumer asked for |
| `subscriptions` | Paths of the parameters the consumer subscribed to |

## Consumer

```python
Consumer(host, port=9000, *, timeout=5.0, keepalive=5.0, qualified=True,
         on_change=None, on_disconnect=None, on_error=None)
```

| Option | Meaning | Default |
|---|---|---|
| `timeout` | Seconds to wait for the connection and for answers | 5.0 |
| `keepalive` | Seconds of silence after which the provider is asked for a sign of life. Three times as long without any, and the connection counts as lost. `None` switches this off | 5.0 |
| `qualified` | Address elements by their whole path, as current devices expect. `False` sends the nested form of old devices | `True` |

### Connection

| Method or property | Does |
|---|---|
| `connect()` | Opens the connection and starts with an empty tree. Raises `ConsumerError`. Returns the consumer |
| `close()`, `disconnect()` | Closes the connection. The tree read so far stays in `consumer.tree` |
| `with Consumer(...) as consumer:` | `connect()` on the way in, `close()` on the way out |
| `connected` | True while the connection is open |
| `description` | For example `TCP 192.168.1.10:9000` |
| `error` | The last error message |
| `provider_glow_version` | `(major, minor)` of the Glow schema the provider announced |
| `problem_count` | How many frames and messages of the provider could not be read |

### Reading

| Method | Does |
|---|---|
| `get_directory(target=None, *, timeout=None, refresh=False)` | Asks for the children of a node and returns them. Without `target`: the root elements. What was loaded before is returned at once, unless `refresh` is true: then the provider is asked again, and children it no longer lists are dropped. On a parameter, function or matrix it asks for the properties |
| `get(path, *, timeout=None)` | The element at a path, loading the nodes on the way. Raises `KeyError` if the provider has no such element |
| `walk(target=None, *, max_elements=10000, timeout=None)` | Loads everything below `target`, or the whole tree, and returns the elements. A node that does not answer is left unloaded |
| `tree` | The `Tree` with everything read so far |
| `lock` | Hold it (`with consumer.lock:`) to read the tree in one consistent state |

`get_directory` raises `TimeoutError` if the provider does not answer, and
`ConsumerError` if there is no connection.

A consumer shows what the provider reports and checks nothing: a parameter may
hold a value outside its announced range, and identifiers may break the rules.

### Changing

| Method | Does |
|---|---|
| `set_value(target, value, *, wait=True, check=True, timeout=None)` | Asks the provider to change a parameter. With `wait` it returns the value the provider reports back: the old value if the provider refused. `check=False` sends the value as it is, without fitting it to the announced type and range, to test a provider |
| `trigger(target)` | Fires a trigger parameter |
| `invoke(target, *arguments, timeout=None)` | Calls a function and returns its result values as a list. Raises `InvocationError` if the provider reports a failure |
| `set_connection(target, matrix_target, sources, operation="absolute")` | Asks for new sources of one matrix target. The answer arrives as a change of the matrix |
| `send_raw(data)` | Sends bytes as they are, to see how a provider reacts to bad data |

### Following values

| Method | Does |
|---|---|
| `watch(target, callback)` | Calls `callback(element)` whenever the provider reports a new value, online state or connection for the element. A stream parameter is subscribed on the way. Returns the element |
| `unwatch(target, callback=None)` | Ends one callback, or all of the element |
| `subscribe(target)` | Asks for the values of a stream parameter |
| `unsubscribe(target)` | Ends that |

Watching and subscriptions end with the connection. Set them up again after
`connect()`.

### Callbacks

| Callback | Called when |
|---|---|
| `on_change(element, fields)` | The provider reported an element. `fields` is a set of what changed: `"value"`, `"is_online"`, `"children"`, `"connections"`, `"created"`, or the name of another property |
| `on_disconnect(reason)` | The connection ended by itself |
| `on_error(message)` | Bad data from the provider, or a failing callback |

## Trees as JSON

`element.to_dict()` gives plain data that `json.dumps` accepts.
`element_from_dict(data)` builds the element back, with everything below it,
and raises `ValueError` if the data is not valid. Function handlers are not
part of the data.

```python
import json
from emberemu import Provider, element_from_dict

provider = Provider()
provider.add_node("Device").add_parameter("Gain", -12.0, access="read_write")

text = json.dumps(provider.tree.to_dict(), indent=2)        # {"children": [...]}

copy = Provider(port=9001)
for data in json.loads(text)["children"]:
    copy.add(element_from_dict(data))
```

```json
{
  "kind": "parameter",
  "number": 1,
  "identifier": "Gain",
  "type": "real",
  "access": "read_write",
  "value": -12.0,
  "minimum": -60.0,
  "maximum": 12.0,
  "format": "%.1f dB"
}
```

| Key | For | Content |
|---|---|---|
| `kind` | all | `"node"` (if left out), `"parameter"`, `"function"`, `"matrix"` |
| `number`, `identifier`, `description` | all | As on the element |
| `children` | all | List of elements |
| `is_online` | node, parameter | Only written when false |
| `type`, `access`, `value` | parameter | Names as in the tables above. Octets as hex digits |
| `minimum`, `maximum`, `format`, `enumeration`, `enum_map`, `factor`, `step`, `default`, `formula`, `stream_identifier`, `stream_descriptor` | parameter | As on the element |
| `arguments`, `result` | function | Lists of `[type, name]` |
| `type`, `addressing_mode`, `target_count`, `source_count`, `targets`, `sources`, `labels`, `connections` | matrix | `connections` maps target number to list of sources |

## Enumerations

All are `IntEnum`, so the plain numbers of the protocol work as well.

| Enumeration | Members |
|---|---|
| `ParameterType` | `NULL` 0, `INTEGER` 1, `REAL` 2, `STRING` 3, `BOOLEAN` 4, `TRIGGER` 5, `ENUM` 6, `OCTETS` 7 |
| `Access` | `NONE` 0, `READ` 1, `WRITE` 2, `READ_WRITE` 3 |
| `MatrixType` | `ONE_TO_N` 0, `ONE_TO_ONE` 1, `N_TO_N` 2 |
| `MatrixAddressingMode` | `LINEAR` 0, `NON_LINEAR` 1 |
| `ConnectionOperation` | `ABSOLUTE` 0, `CONNECT` 1, `DISCONNECT` 2 |
| `ConnectionDisposition` | `TALLY` 0, `MODIFIED` 1, `PENDING` 2, `LOCKED` 3 |
| `CommandType` | `SUBSCRIBE` 30, `UNSUBSCRIBE` 31, `GET_DIRECTORY` 32, `INVOKE` 33 |
| `FieldFlags` | `SPARSE` -2, `ALL` -1, `DEFAULT` 0, `IDENTIFIER` 1, `DESCRIPTION` 2, `TREE` 3, `VALUE` 4, `CONNECTIONS` 5 |
| `StreamFormat` | `UINT8` 0, `UINT16_BE` 2, `UINT16_LE` 3, `UINT32_BE` 4, `UINT32_LE` 5, `UINT64_BE` 6, `UINT64_LE` 7, `INT8` 8, `INT16_BE` 10, `INT16_LE` 11, `INT32_BE` 12, `INT32_LE` 13, `INT64_BE` 14, `INT64_LE` 15, `FLOAT32_BE` 20, `FLOAT32_LE` 21, `FLOAT64_BE` 22, `FLOAT64_LE` 23 |

## Errors

| Error | Raised when |
|---|---|
| `ValueError` | A value does not fit a parameter, a number or identifier is in use, a matrix does not allow a connection, tree data is not valid |
| `KeyError` | There is no element at a path: `provider[path]`, `consumer.get(path)`, `element[key]` |
| `ProviderError` (an `OSError`) | The provider cannot open its port |
| `ConsumerError` (an `OSError`) | The connection cannot be opened, was lost, or is not open |
| `TimeoutError` | The provider did not answer in time |
| `InvocationError` (a `RuntimeError`) | The provider reported that a function call failed |
| `BerError`, `GlowError` (both `ValueError`) | Only from the lower layers: bytes that are not valid BER or Glow |

Nothing that arrives from the network raises. A message that cannot be read is
counted in `problem_count` and passed to `on_error` as text, and the
connection goes on.

## Threads and callbacks

A provider runs one thread that accepts connections, one timer thread, and two
threads per consumer. A consumer runs one thread that receives and one for the
keep-alive. All are daemon threads: they end with the program.

- Callbacks run in these network threads. A callback that raises does no harm:
  the error goes to `on_error`.
- The tree may be used from any thread. Assigning to `parameter.value` and
  calling `matrix.connect` are safe everywhere.
- In a **consumer** callback, do not call methods that wait for the provider
  (`get`, `get_directory`, `walk`, `set_value` with `wait`, `invoke`). The
  answer is received by the very thread the callback is running in. Use
  `set_value(..., wait=False)`, or hand the work to another thread.
- In a **provider** callback everything is allowed, including changing other
  parameters.

## The lower layers

Most programs never need them. They are there for tools that look at the bytes.

| Module | Holds |
|---|---|
| `emberemu.s101` | The framing: `encode_message(payload)` gives the frames of one message, `MessageDecoder().feed(data)` cuts received bytes into messages, `keepalive_request()`, `keepalive_response()`, `crc16(data)` |
| `emberemu.glow` | The messages: `decode_root(payload)` gives a `Root` with `elements`, `streams` or `invocation_result`, `encode_root(root)` gives the bytes back |
| `emberemu.ber` | The encoding of single values: `integer`, `real`, `utf8_string`, ... and `decode(data)` |

```python
from emberemu import glow, s101

request = glow.Root(elements=[glow.GlowElement("command", number=glow.CommandType.GET_DIRECTORY)])
frames = s101.encode_message(glow.encode_root(request))
print(frames[0].hex(" "))        # fe 00 0e 00 01 c0 01 02 32 02 60 0b 6b 09 a0 07 62 05 a0 03 02 01 20 ...

decoder = s101.MessageDecoder()
for message in decoder.feed(frames[0]):
    print(glow.decode_root(message.payload))
```

How the layers fit together is described in [protocol.md](protocol.md).

## What is covered

| Part of Ember+ | Provider | Consumer |
|---|---|---|
| S101 framing with escaping and checksum, messages over several frames | yes | yes |
| S101 framing without escaping (Glow 2.50) | reads it, answers with escaping | reads it |
| Keep-alive | answers, can ask | asks and answers |
| Nodes and parameters with all properties of Glow 2.50 | yes | yes |
| GetDirectory with field mask | yes | asks for everything |
| Qualified and nested addressing | reads both, answers qualified | reads both, sends either |
| Value changes, triggers | yes | yes |
| Streams with plain values | yes | yes |
| Streams packed into byte blocks (`streamDescriptor`) | yes | yes |
| Functions | yes | yes |
| Matrices: three types, linear and non-linear, labels | yes | yes |
| Matrix parameters (`parametersLocation`), signal contents (`labelsLocation`) | passed on | not read |
| Schemas, formulas | passed on, not evaluated | shown, not evaluated |
| Templates | no | skipped |
| Service discovery (DNS-SD) | no | no |
