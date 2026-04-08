# EasySync

Universal real-time state synchronization for Python.

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

Manipulate your Python objects as if the network didn't exist. EasySync intercepts attribute mutations through a transparent proxy and propagates them instantly to all machines connected to the server.

## Installation

```bash
pip install py-easysync
```

## Quick Start

**Server** (hosts the shared state):

```python
from easysync import SyncedObject, SyncServer, connect

server = SyncServer(port=5000)
server.start_thread()

client = connect("127.0.0.1", 5000)

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

## Architecture

The server uses `asyncio` for non-blocking connection handling, allowing it to support a large number of simultaneous clients without CPU overhead. The client uses a dedicated receive thread to remain compatible with standard application loops (Pygame, Matplotlib, etc.).

The wire protocol relies on binary Pickle serialization framed by a 4-byte header (payload size). This ensures measured latency under 20ms.

## Features

- **Zero configuration**: a single `@SyncedObject` decorator is all you need.
- **Transparent proxy**: automatic interception of `__setattr__`, `__setitem__`, `append`, etc.
- **Hybrid Transport (TCP/UDP)**: Use UDP for low-latency streaming and TCP for critical state.
- **Delta Sync**: Efficient differential encoding for heavy objects (NumPy, Torch) using XOR + Compression.
- **Live Telemetry**: Integrated tracking of latency (ms), bandwidth (KB/s), and packet counts.
- **Secure Auth**: Protect your SyncServer with custom authentication handlers.
- **High Scalability**: Asyncio-based server handling hundreds of concurrent clients.

## Examples

The `examples/` folder contains several demos:

| File | Description |
|---|---|
| `remote_host.py` / `viewer.py` | **NEW**: Remote Desktop with UDP & Delta Sync |
| `pygame_example.py` | Synchronized square between two Pygame windows |
| `pygame_hanoi.py` | Collaborative Tower of Hanoi |
| `numpy_matplotlib_example.py` | NumPy data streaming with Matplotlib |
| `pandas_example.py` | Collaborative Pandas spreadsheet |
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

## License

MIT
