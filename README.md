# EasySync

Universal real-time state synchronization for Python.

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Version](https://img.shields.io/badge/version-0.2.0-green.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

Manipulate your Python objects as if the network didn't exist. EasySync intercepts attribute mutations through a transparent proxy and propagates them to every process connected to the same server — or, on a single machine, through shared memory.

Documentation: <https://galtechdev.github.io/easysync/>

## Installation

```bash
pip install py-easysync
```

Optional extras:

```bash
pip install "py-easysync[shm]"     # shared-memory mode (shm_connect), via EasySHM
pip install "py-easysync[numpy]"   # NumPy arrays with Delta Sync
pip install "py-easysync[torch]"   # PyTorch tensors
```

## Quick Start

**Server** (hosts the shared state):

```python
from easysync import SyncedObject, SyncServer, connect

server = SyncServer(port=5000)
server.start_thread()               # returns once the server accepts connections

client = connect("127.0.0.1", 5000) # waits until connected (timeout=5.0 by default)

@SyncedObject(client)
class GameState:
    def __init__(self):
        self.score = 0
        self.players = []

state = GameState()
state.score = 42           # propagated to all clients
state.players.append("A")  # propagated too
```

**Client** (joins the server):

```python
from easysync import SyncedObject, connect

client = connect("192.168.1.10", 5000)

@SyncedObject(client)
class GameState:
    def __init__(self):
        self.score = 0
        self.players = []

state = GameState()
print(state.score)    # 42, updated in real time
print(state.players)  # ['A']
```

## Features

- **Zero configuration**: a single `@SyncedObject` decorator is all you need.
- **Transparent proxy**: automatic interception of `__setattr__`, `__setitem__`, `append`, etc., including nested lists and dicts.
- **Hybrid transport (TCP/UDP)**: TCP for critical state, UDP for streams where the next value replaces a lost one.
- **Shared-memory mode**: `shm_connect()` syncs processes of one machine through RAM, without server or port.
- **NumPy & Delta Sync**: arrays travel as raw zero-copy buffers; small changes are sent as XOR + zlib deltas.
- **Safe by default**: received data is never allowed to run code (see [Security](#security)).
- **Authentication**: protect a server with your own handler.
- **Live telemetry**: latency, bytes and packet counters on every client.

## Usage notes

### Several instances of one class

Each instance is identified by its class name and its creation order on its client: the first `Player()` is `"Player"`, the next ones `"Player#2"`, `"Player#3"`… so the n-th instance of one process syncs with the n-th instance of the others. Choose the identifier yourself when creation order differs:

```python
alice = Player(_sync_id="alice")
```

### Transports

```python
@SyncedObject(client, transport="udp")   # positions, cursors, video frames...
class Cursor:
    def __init__(self):
        self.x = 0
```

UDP updates carry a sequence number, so a late datagram never overwrites a newer value. Deltas and values too large for a datagram (> 60 KB) automatically go over TCP.

### Shared memory

```python
from easysync import shm_connect, SyncedObject

client = shm_connect("my_app")   # every process using "my_app" syncs together
```

Updates go through a shared ring buffer (16 MB by default, `shm_connect("my_app", buffer_size=...)`). A single update must fit in half of it.

### NumPy arrays and Delta Sync

NumPy arrays keep their dtype and shape. After the first full transfer, a small change is sent as a compressed XOR delta. In-place changes (`state.frame[0] = 1`) are not intercepted: reassign the attribute to send them.

```python
state.frame[100:200, 100:200] = 255
state.frame = state.frame        # sends a delta of the changed pixels
```

### Custom codecs

```python
import easysync
from easysync.codecs import Codec

@easysync.codec("mylib.Point")
class PointCodec(Codec):
    def match(self, obj):
        return isinstance(obj, Point)

    def encode(self, obj):
        return f"{obj.x},{obj.y}".encode()

    def decode(self, data):
        return Point(*map(int, data.split(b",")))
```

The functional form works too: `easysync.register_codec("name", match=..., encode=..., decode=...)`.

### Telemetry

```python
client.ping()                      # the pong fills stats["latency_ms"] (round trip)
print(client.stats)                # bytes_sent, bytes_recv, packets_sent, packets_recv, latency_ms
```

## Security

Messages are pickled, but **received data is deserialized with a restricted unpickler**: only plain data is rebuilt (numbers, strings, bytes, lists, tuples, dicts, sets, `datetime`, `Decimal`, `UUID`, NumPy arrays and scalars…). Anything else is refused and logged, so a peer cannot execute code on your machine with a crafted packet. The server does not deserialize updates at all: it only reads their routing fields and relays the original bytes.

To receive your own classes:

```python
easysync.allow_types(MyDataclass, "mypackage.module.OtherClass")
```

On a network where **every peer is trusted**, you can accept any picklable object (e.g. pandas DataFrames):

```python
easysync.trust_all_types()   # a malicious peer could then run code on this machine
```

Server side:

```python
server = SyncServer(port=5000)

@server.on_auth
def check(addr, payload):
    return payload == "secret-token"

client = connect("192.168.1.10", 5000, auth_payload="secret-token")
```

- The first message of every connection must be the handshake (64 KB max); nothing is relayed before it is accepted.
- UDP datagrams are only relayed from authenticated clients, and clients only accept datagrams coming from the server.
- `SyncServer(max_message_size=512 MB)` bounds every message; `send_queue_size` and `slow_client_timeout` keep a stuck client from freezing the others (it is disconnected after 10 s of full queue by default).
- The payload of `on_auth` travels in clear text: use a VPN or an SSH tunnel on untrusted networks.

## Performance

Measured on localhost (Linux WSL2, Python 3.12, 12 CPU), separate processes, a real `SyncServer`:

| Measure | Result |
|---|---|
| Update latency, one-way, TCP (median / p99) | **100 µs** / 290 µs |
| Same, UDP | 290 µs / 1.1 ms |
| Same, shared memory (`shm_connect`) | 230 µs / 680 µs |
| Sustained updates, one sender → one receiver | **~100,000 updates/s**, 100 % delivered |
| Broadcast until every client has it, 10 / 50 / 100 clients (median) | 1.1 / 3.0 / 5.2 ms |
| Binary payload, 1 MB / 10 MB | 1.0 ms / 9.5 ms (~1 GB/s) |
| Delta Sync, 720p frame with a 100×100 px change | 2.76 MB → 45 KB on the wire (−98 %) |

Latency is half of a ping/pong round trip through `@SyncedObject`; the delta figure is the size of the update actually sent after `state.frame = state.frame`. Run the benchmarks on your machine:

```bash
python benchmarks/perf.py
```

## Examples

The `examples/` folder contains several demos:

| File | Description |
|---|---|
| `remote_host.py` / `remote_viewer.py` | Remote desktop with UDP, Delta Sync and authentication |
| `pygame_example.py` | Synchronized square between two Pygame windows |
| `pygame_hanoi.py` | Collaborative Tower of Hanoi |
| `numpy_matplotlib_example.py` | NumPy data streaming with Matplotlib |
| `pandas_example.py` | Collaborative Pandas spreadsheet (uses `trust_all_types()`) |
| `federated_learning_example.py` | Distributed federated learning |
| `genetic_island_example.py` | Distributed genetic algorithm |
| `tetris_ai_example.py` | Distributed Tetris AI via genetic algorithm |

To run the examples, install the additional dependencies:

```bash
pip install -r requirements_examples.txt
```

Then launch a server and one or more clients:

```bash
python examples/pygame_example.py server    # Terminal 1
python examples/pygame_example.py           # Terminal 2
```

## Tests

```bash
pip install pytest numpy py-easyshm
python -m pytest tests
```

## Changelog

### 0.2.0

The wire protocol changed: 0.2.0 peers cannot talk to 0.1.x peers.

- **Security**: received data can no longer execute code (restricted deserialization, server relays without deserializing, handshake required before anything else, UDP accepted from authenticated peers only, message size limits). Custom classes now need `allow_types()` or `trust_all_types()`.
- **NumPy**: arrays no longer corrupt the connection and keep their dtype and shape; the NumPy codec and Delta Sync are now actually used (zero-copy, no pickle).
- **Delta Sync**: bases tracked per sender, in-place changes detected, late joiners get the full value, no deltas over lossy UDP.
- **Shared memory**: bursts of updates are no longer lost (ring buffer instead of a single slot); new clients request the current state.
- **Threads**: sending from several threads no longer interleaves frames.
- **Instances**: several instances of one class no longer share the same identifier (`_sync_id` added).
- **Codecs**: `decode(self, data)` works as documented; `register_codec(match=...)` accepted.
- **Telemetry**: `latency_ms` and `packets_recv` are now updated.
- **Server**: one slow client no longer delays the others; `start_thread()` returns once the server listens; `connect()` waits for the connection, so updates made right after it are not lost.
- `bytes` values arrive as `bytes`; a failing update callback no longer disconnects the client; UDP datagrams arriving late are dropped.

## License

MIT
