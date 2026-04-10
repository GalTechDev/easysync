import time
import multiprocessing
import os
import sys

# Ensure we can import easysync and easyshm
root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "easyshm")) # NEW standalone path

from easysync import connect, shm_connect, SyncServer, SyncedObject

@SyncedObject()
class LargeData:
    def __init__(self):
        self.payload = b"0" * (1024 * 1024) # 1 MB

def socket_server_process(ready_event):
    server = SyncServer(port=5005)
    server.start_thread()
    ready_event.set()
    time.sleep(10) # Keep alive

def benchmark_socket(ready_event):
    ready_event.wait()
    time.sleep(0.5)
    client = connect("127.0.0.1", 5005)
    obj = LargeData()
    
    # Warmup
    obj.payload = b"1" * (1024 * 1024)
    time.sleep(0.5)
    
    print("[Socket] Measuring 10 syncs of 1MB...")
    t0 = time.perf_counter()
    for i in range(10):
        obj.payload = bytes([i % 256]) * (1024 * 1024)
        # We don't have a 'wait_sync' in client yet, but the update is sent immediately.
        # To be fair, we measure the *send* performance.
        time.sleep(0.1) # Small delay for network thread to consume
    t1 = time.perf_counter()
    print(f"[Socket] Total time for 10 syncs: {(t1-t0-1.0):.4f}s") # subtract sleep

def benchmark_shm():
    client = shm_connect("bench_sync")
    obj = LargeData()
    
    # Warmup
    obj.payload = b"1" * (1024 * 1024)
    time.sleep(0.5)
    
    print("[SHM] Measuring 10 syncs of 1MB...")
    t0 = time.perf_counter()
    for i in range(10):
        obj.payload = bytes([i % 256]) * (1024 * 1024)
        time.sleep(0.01) # Much smaller delay needed
    t1 = time.perf_counter()
    print(f"[SHM] Total time for 10 syncs: {(t1-t0-0.1):.4f}s") # subtract sleep

if __name__ == "__main__":
    # --- Part 1: Socket Benchmark ---
    ready = multiprocessing.Event()
    srv = multiprocessing.Process(target=socket_server_process, args=(ready,))
    srv.start()
    
    try:
        benchmark_socket(ready)
    finally:
        srv.terminate()
        
    print("-" * 30)
    
    # --- Part 2: SHM Benchmark ---
    benchmark_shm()
