import sys
import os
import time
import socket
import threading
import numpy as np

# Adjust paths
BASE_DIR = r"c:\Users\Maxence\Downloads\easysync-main\easysync-main"
SHM_DIR = r"c:\Users\Maxence\Downloads\easyshm"
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, SHM_DIR)

from easysync import SyncServer
from easysync.syncclient import SyncClient
from easyshm import EasySHM

def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port

def run_bench(name, send_func, payload_gen, repeats=3):
    print(f"\n[{name}]")
    port = get_free_port()
    srv = SyncServer(port=port)
    srv.start_thread()
    time.sleep(0.5)

    cA = SyncClient(host="127.0.0.1", port=port)
    cA.connect()
    cB = SyncClient(host="127.0.0.1", port=port)
    cB.connect()
    time.sleep(0.5)

    received_event = threading.Event()
    def on_receive(msg):
        received_event.set()
    cB.register_callback("bench", on_receive)

    latencies = []
    for i in range(repeats):
        data = payload_gen()
        received_event.clear()
        t0 = time.perf_counter()
        send_func(cA, data)
        if received_event.wait(timeout=5):
            latencies.append(time.perf_counter() - t0)
            print(".", end="", flush=True)
        else:
            print("X", end="", flush=True)
            latencies.append(float('inf'))
            
    cA.connected = False
    cB.connected = False
    print(f" Done. Avg: {np.mean(latencies)*1000:.2f}ms")
    return np.mean(latencies)

def benchmark():
    SIZE = 50 * 1024 * 1024 # 50 MB
    print(f"Starting 50MB Benchmark...")
    
    # 1. Standard
    def std_gen(): return np.random.bytes(SIZE)
    def std_send(c, d): c.send_update("bench", "data", d)
    
    # 2. Zero-Copy
    shm = EasySHM("bench_zc", size=SIZE)
    def zc_gen(): return shm
    def zc_send(c, d): c.send_update("bench", "data", d)

    avg_std = run_bench("Standard (Pickle)", std_send, std_gen)
    avg_zc = run_bench("Zero-Copy (Raw)", zc_send, zc_gen)
    
    print(f"\nSpeedup: {avg_std/avg_zc:.2f}x")
    shm.destroy()

if __name__ == "__main__":
    benchmark()
