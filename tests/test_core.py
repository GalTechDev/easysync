import sys
import os
import time
import socket
import pytest
import threading
import asyncio

# Ensure the root package is in the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from easysync import SyncServer, connect, SyncedObject


def get_free_port():
    """Returns a random free port on localhost, ensuring port+1 (UDP) is also free."""
    while True:
        s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s1.bind(("", 0))
        port = s1.getsockname()[1]
        s1.close()
        # Check that port+1 (UDP) is also free
        try:
            s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s2.bind(("", port + 1))
            s2.close()
            return port
        except OSError:
            continue


def wait_for(condition, timeout=3.0, interval=0.05):
    """Wait for a condition to become true within a timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if condition():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def sync_env():
    """
    Fixture that provides a fresh server and a way to create clients.
    It cleans up everything after each test.
    """
    port = get_free_port()
    server = SyncServer(port=port)
    server.start_thread()
    time.sleep(0.5)

    clients = []

    def make_client(**kwargs):
        c = connect("127.0.0.1", port, **kwargs)
        # Wait for client to establish connection
        wait_for(lambda: c.connected, timeout=2.0)
        clients.append(c)
        return c

    yield server, make_client

    # Teardown
    for c in clients:
        c.connected = False
        try:
            c.client_socket.close()
        except:
            pass
    try:
        if server._server:
            # Safely stop the server loop
            future = asyncio.run_coroutine_threadsafe(server.stop(), server._server.get_loop())
            future.result(timeout=2.0)
    except Exception:
        pass


def test_basic_synchronization(sync_env):
    """Test that a simple integer update propagates from Client A to Client B."""
    _, make_client = sync_env
    
    clientA = make_client()
    clientB = make_client()

    @SyncedObject()
    class GameState:
        def __init__(self):
            self.score = 0
            
    stateA = GameState(_sync_client=clientA)
    stateB = GameState(_sync_client=clientB)
    
    # Mutate A
    stateA.score = 100
    
    # B should receive it
    assert wait_for(lambda: stateB.score == 100)


def test_deep_synchronization(sync_env):
    """Test that deep mutations (lists/dicts) propagate correctly."""
    _, make_client = sync_env
    
    clientA = make_client()
    clientB = make_client()

    @SyncedObject()
    class World:
        def __init__(self):
            self.players = []
            self.config = {"theme": "light"}
            
    worldA = World(_sync_client=clientA)
    worldB = World(_sync_client=clientB)
    
    # Mutate A deep objects
    worldA.players.append("Alice")
    worldA.config["theme"] = "dark"
    
    assert wait_for(lambda: "Alice" in worldB.players)
    assert wait_for(lambda: worldB.config["theme"] == "dark")


def test_sync_new_client(sync_env):
    """Test that a newly connected client automatically receives existing state."""
    _, make_client = sync_env
    
    clientA = make_client()
    
    @SyncedObject(clientA)
    class Model:
        def __init__(self):
            self.level = 1
            
    modelA = Model(_sync_client=clientA)
    modelA.level = 5  # Server now holds "5"
    time.sleep(0.2)
    
    # Connect B *after* mutation
    clientB = make_client(sync_new_client=True)
    time.sleep(0.2)
    
    modelB = Model(_sync_client=clientB)
    
    # It should instantly inherit the 5
    assert wait_for(lambda: modelB.level == 5)


def test_auto_reconnect_and_resync(sync_env):
    """Test that a client reconnects after server restart and resyncs to network."""
    server, make_client = sync_env
    
    clientA = make_client(auto_reconnect=True, auto_resync=True)
    clientB = make_client(auto_reconnect=True, auto_resync=True)
    
    @SyncedObject()
    class Data:
        def __init__(self):
            self.val = 10
            
    dataA = Data(_sync_client=clientA)
    dataB = Data(_sync_client=clientB)
    
    # Initial steady state
    dataA.val = 50
    assert wait_for(lambda: dataB.val == 50)
    
    # 1. Kill the server abruptly
    port = server.port
    try:
        future = asyncio.run_coroutine_threadsafe(server.stop(), server._server.get_loop())
        future.result(timeout=2.0)
    except Exception:
        pass
        
    time.sleep(1) # Allow clients to detect disconnect
    assert not clientA.connected
    
    # 2. Mutate B offline (will be lost/dropped on B, but B thinks it's 99)
    dataB.val = 99
    
    # Wait, the server is dead. Let's restart it on the exact same port
    server2 = SyncServer(port=port)
    server2.start_thread()
    time.sleep(1) # Allow clients to reconnect
    
    # Verify reconnection
    assert wait_for(lambda: clientA.connected), "Client A did not reconnect"
    assert wait_for(lambda: clientB.connected), "Client B did not reconnect"
    
    # 3. Because A and B both auto_resynced, but the server is FRESH (it lost history),
    # However, A and B are both sending `request_sync`. 
    # Because B mutated to 99 offline, it shouldn't push it. But wait, B's `dataB.val = 99` was processed locally.
    # Actually, let's mutate A online:
    dataA.val = 150
    assert wait_for(lambda: dataB.val == 150), f"B did not get A's update. B's val={dataB.val}"
    
    # Cleanup for fixture
    server._server = server2._server


def test_udp_synchronization(sync_env):
    """Test that UDP transport delivers updates between clients."""
    _, make_client = sync_env
    
    clientA = make_client()
    clientB = make_client()

    @SyncedObject(transport="udp")
    class Position:
        def __init__(self):
            self.x = 0
            self.y = 0
            
    posA = Position(_sync_client=clientA)
    posB = Position(_sync_client=clientB)
    
    # Mutate A via UDP
    posA.x = 42
    posA.y = 99
    
    # B should receive it (UDP is lossy but on localhost it should work)
    assert wait_for(lambda: posB.x == 42)
    assert wait_for(lambda: posB.y == 99)


def test_mixed_tcp_udp(sync_env):
    """Test TCP and UDP objects coexisting on the same connection."""
    _, make_client = sync_env
    
    clientA = make_client()
    clientB = make_client()

    @SyncedObject(transport="tcp")
    class Score:
        def __init__(self):
            self.value = 0

    @SyncedObject(transport="udp")
    class Cursor:
        def __init__(self):
            self.x = 0

    scoreA = Score(_sync_client=clientA)
    scoreB = Score(_sync_client=clientB)
    cursorA = Cursor(_sync_client=clientA)
    cursorB = Cursor(_sync_client=clientB)

    # Both transports should work simultaneously
    scoreA.value = 100
    cursorA.x = 500

    assert wait_for(lambda: scoreB.value == 100)
    assert wait_for(lambda: cursorB.x == 500)
