import sys
import os
import time
import torch
import numpy as np

# Adjust paths
BASE_DIR = r"c:\Users\Maxence\Downloads\easysync-main\easysync-main"
SHM_DIR = r"c:\Users\Maxence\Downloads\easyshm"
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, SHM_DIR)

from easysync import SyncServer
from easysync.syncclient import SyncClient
from easyshm import EasySHM
from easysync.contrib.torch_gpu import TorchGPUCodec

def benchmark_gpu():
    if not torch.cuda.is_available():
        print("CUDA not available. Skipping GPU benchmark.")
        return

    SIZE_MB = 20
    SIZE_BYTES = SIZE_MB * 1024 * 1024
    print(f"Benchmarking {SIZE_MB}MB GPU Tensor Transfer...")

    # 1. Pinned SHM Bridge
    bridge = EasySHM("gpu_bridge", size=SIZE_BYTES, pinned=True)
    gpu_codec = TorchGPUCodec(pinned_shm=bridge, sync_stream=True)

    # Setup Server and Clients
    port = 5555
    srv = SyncServer(port=port)
    srv.start_thread()
    time.sleep(0.5)

    cA = SyncClient(host="127.0.0.1", port=port)
    cA.connect()
    cB = SyncClient(host="127.0.0.1", port=port)
    cB.connect()
    time.sleep(0.5)

    # Data to send
    tensor = torch.randn(SIZE_BYTES // 4, device="cuda")
    
    # Register callback on B
    received_event = torch.cuda.Event(enable_timing=True)
    done_event = [False]
    
    def on_receive(msg):
        # In a real scenario, the receiver would use the codec to move back to GPU
        # Here we just mark completion
        done_event[0] = True

    cB.register_callback("gpu_sync", on_receive)

    # Benchmark Standard (CPU copy)
    print("\n[Method 1: Standard CPU Fallback]")
    t0 = time.perf_counter()
    for _ in range(5):
        # This will trigger tensor.cpu().numpy() (Standard PyTorch behavior)
        cA.send_update("gpu_sync", "weights", tensor)
        while not done_event[0]: time.sleep(0.001)
        done_event[0] = False
    t1 = time.perf_counter()
    print(f"  Avg Latency: {(t1-t0)/5*1000:.2f} ms")

    # Benchmark Zero-Copy (DMA via Pinned SHM)
    print("\n[Method 2: Zero-Copy DMA Bridge]")
    # Register our GPU codec
    import easysync.codecs as codecs
    codecs._registry["torch_gpu"] = gpu_codec
    
    t0 = time.perf_counter()
    for _ in range(5):
        # This will now use TorchGPUCodec.encode -> Pinned SHM
        cA.send_update("gpu_sync", "weights", tensor)
        while not done_event[0]: time.sleep(0.001)
        done_event[0] = False
    t1 = time.perf_counter()
    print(f"  Avg Latency: {(t1-t0)/5*1000:.2f} ms")

    srv.stop()
    bridge.destroy()

if __name__ == "__main__":
    benchmark_gpu()
