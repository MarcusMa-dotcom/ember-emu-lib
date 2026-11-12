# How Ember+ is put together

This page explains what travels over the wire, with the bytes of real
messages. You do not need it to use the library. It helps when you look at a
capture, or when a device behaves in a way you did not expect.

Ember+ has three layers, and the library has one module for each:

| Layer | Job | Module |
|---|---|---|
| S101 | Cuts the TCP stream into frames, protects them with a checksum | `emberemu.s101` |
| BER | Encodes single values: numbers, text, containers | `emberemu.ber` |
| Glow | Says what the values mean: nodes, parameters, commands | `emberemu.glow` |

The source of all of this is the Ember+ documentation that Lawo publishes with
its reference libraries.

## One request, byte by byte

A consumer that has just connected asks for the root elements. This is the
whole message:

```
fe 00 0e 00 01 c0 01 02 32 02 60 0b 6b 09 a0 07 62 05 a0 03 02 01 20 8c 90 ff
```

### The S101 frame

| Bytes | Meaning |
|---|---|
| `fe` | Start of a frame |
| `00` | Slot, always 0 |
| `0e` | Message type: EmBER |
| `00` | Command: 0 is EmBER data, 1 a keep-alive request, 2 a keep-alive response |
| `01` | Version of S101 |
| `c0` | Flags: this frame is the first (`80`) and the last (`40`) of its message |
| `01` | The data schema: 1 is Glow |
| `02` | Two more bytes follow before the data |
| `32 02` | Version of the Glow schema, minor then major: 2.50 |
| `60 ... 20` | The data, see below |
| `8c 90` | Checksum, low byte first |
| `ff` | End of the frame |

Inside a frame, every byte from `f8` upwards is sent as `fd` followed by the
byte XOR `20`, so the start and end bytes never appear in the middle. The
checksum is a CRC-16 (start value `ffff`, reflected polynomial `8408`,
inverted at the end) over all bytes between start byte and checksum.

At most 1024 bytes of data go into one frame. Longer messages are spread over
several frames: the first has the flag `80`, the last `40`, those in between
`00`. The Lawo C# library closes a long message with an empty frame, flags
`60`. The library reads both.

A keep-alive has no data:

```
fe 00 0e 01 01 94 e4 ff          request
fe 00 0e 02 01 fd dc ce ff       response
```

Every device must answer a keep-alive request. Asking is optional. A consumer
of this library asks after 5 seconds of silence and gives up after 15.

Since Glow 2.50 there is a second variant without escaping, which starts with
`f8` and a length. Devices rarely use it. The library reads it and always
sends the first variant, which every device must support.

### The data: BER

Every value is written as tag, length, content. Containers hold more values.

```
60 0b                          application 0: the root, 11 bytes
   6b 09                       application 11: a collection of root elements, 9 bytes
      a0 07                    [0]: one element of the collection
         62 05                 application 2: a command
            a0 03              [0]: the field "number"
               02 01 20        INTEGER, 1 byte: 32
```

Tags that start with `6` are the Glow types (see the table below). Tags that
start with `a` number the fields of a type. Tags below `20` are the plain
values:

| Tag | Value | Example |
|---|---|---|
| `01` | BOOLEAN | `01 01 ff` is true |
| `02` | INTEGER, two's complement | `02 01 20` is 32 |
| `04` | OCTET STRING | `04 02 01 ff` |
| `05` | NULL | `05 00` |
| `09` | REAL | `09 03 c0 02 03` is -6.0 |
| `0c` | UTF8String | `0c 05 41 75 64 69 6f` is "Audio" |
| `0d` | RELATIVE-OID, used for paths | `0d 02 01 02` is 1.2 |
| `30` | SEQUENCE | |
| `31` | SET | |

The Lawo libraries write containers with an open end: the length byte is `80`
and two zero bytes close the container. This library reads both forms and
writes lengths, which every device accepts.

**REAL** is special in Ember+. The first content byte holds the sign (`40`)
and the length of the exponent, then follow the exponent and the mantissa. The
mantissa is the binary number with its trailing zeros cut off, and it is read
as 1.xxx: its highest bit counts `2 ** exponent`. So `c0 02 03` is negative,
exponent 2, mantissa binary 11, read as 1.1 = 1.5, times 4 = -6.0. This is not
how the ASN.1 standard defines it, but it is how every Ember+ device does it.

### The meaning: Glow

Command 32 is GetDirectory. Sent at the root, it asks for the root elements.

| Application tag | Glow type |
|---|---|
| 0 (`60`) | Root |
| 1 (`61`) | Parameter |
| 2 (`62`) | Command |
| 3 (`63`) | Node |
| 4 (`64`) | ElementCollection: the children of an element |
| 5, 6 (`65`, `66`) | StreamEntry, StreamCollection |
| 9 (`69`) | QualifiedParameter |
| 10 (`6a`) | QualifiedNode |
| 11 (`6b`) | RootElementCollection |
| 13, 17 (`6d`, `71`) | Matrix, QualifiedMatrix |
| 19, 20 (`73`, `74`) | Function, QualifiedFunction |
| 22, 23 (`76`, `77`) | Invocation, InvocationResult |

## The answer

The provider lists its root elements. Here it has one node:

```
60 1d
   6b 1b
      a0 19
         63 17                                  a Node
            a0 03 02 01 01                      [0] number: 1
            a1 10 31 0e                         [1] contents, a SET
               a0 07 0c 05 41 75 64 69 6f       [0] identifier: "Audio"
               a3 03 01 01 ff                   [3] isOnline: true
```

## Addressing: nested and qualified

To ask for the children of node 1.2, a consumer can nest the elements, as old
devices expect:

```
Node 1
   children
      Node 2
         children
            Command 32
```

or name the whole path in one **qualified** element:

```
QualifiedNode path 1.2
   children
      Command 32
```

The library sends the qualified form unless a `Consumer` is created with
`qualified=False`, and reads both. Answers may come in either form as well.

## A value changes

A consumer sets parameter 1.2 to -6.0, and the provider confirms. Both
messages look the same:

```
60 17 6b 15 a0 13
   69 11                                 QualifiedParameter
      a0 04 0d 02 01 02                  [0] path: 1.2
      a1 09 31 07                        [1] contents
         a2 05 09 03 c0 02 03            [2] value: REAL -6.0
```

The provider sends this report to every consumer that asked for the children
of the node the parameter lives in. If it refuses the request, it answers with
the value it kept.

## The other commands

| Command | Number | Does |
|---|---|---|
| Subscribe | 30 | Asks for the values of a stream parameter |
| Unsubscribe | 31 | Ends that |
| GetDirectory | 32 | Asks for the children of a node, or the properties of an element |
| Invoke | 33 | Calls a function. The answer is an InvocationResult at the root |

Stream values do not come as parameters. They come in a StreamCollection at
the root: a list of pairs of stream identifier and value, which keeps the
messages of fast meters small.

## Looking at bytes with the library

```python
from emberemu import glow, s101

capture = bytes.fromhex("fe 00 0e 00 01 c0 01 02 32 02 60 0b 6b 09 a0 07 62 05 a0 03 02 01 20 8c 90 ff")
decoder = s101.MessageDecoder()
for message in decoder.feed(capture):
    print(message.glow_version)                 # (2, 50)
    print(glow.decode_root(message.payload))    # Root(elements=[GlowElement(kind='command', number=32, ...)])
print(decoder.problem_count)                    # 0
```
