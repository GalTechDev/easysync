# EasySync

Universal real-time state synchronization for Python.

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

Manipulate your Python objects as if the network didn't exist. EasySync intercepts attribute mutations through a transparent proxy and propagates them instantly to all machines connected to the server.

## Installation

```bash
pip install easysync
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
- **Transparent proxy**: automatic interception of `__setattr__`, `__setitem__`, `append`, `pop`, etc.
- **Asyncio server**: built on `asyncio.start_server` for maximum scalability.
- **Binary serialization**: Pickle + TCP framing protocol, latency < 20ms.
- **Zero dependencies**: only uses the Python standard library.
- **Data Science ready**: optimized handling of NumPy, Pandas and Scikit-Learn objects via the copy-and-reassign pattern.

## Examples

The `examples/` folder contains several demos:

| File | Description |
|---|---|
| `pygame_example.py` | Synchronized square between two Pygame windows |
| `pygame_hanoi.py` | Collaborative Tower of Hanoi |
| `numpy_matplotlib_example.py` | NumPy data streaming with Matplotlib chart |
| `pandas_example.py` | Collaborative Pandas spreadsheet |
| `sklearn_live_training.py` | Live Scikit-Learn training visualization |
| `federated_learning_example.py` | Distributed federated learning |
| `genetic_island_example.py` | Distributed genetic algorithm (island model) |
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
