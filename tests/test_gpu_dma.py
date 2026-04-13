import sys
import os
import time
import torch
import numpy as np
import threading

# Adjust paths
BASE_DIR = r"c:\Users\Maxence\Downloads\easysync-main\easysync-main"
SHM_DIR = r"c:\Users\Maxence\Downloads\easyshm"
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, SHM_DIR)

from easysync import SyncServer, SyncedObject, connect
from easysync.contrib.torch_gpu import TorchGPUCodec

def test_gpu_sync():
    if not torch.cuda.is_available():
        print("CUDA not available, skipping GPU test.")
        return

    print("Starting GPU DMA Sync Test...")
    port = 5005
    srv = SyncServer(port=port)
    srv.start_thread()
    time.sleep(1)

    # Register the codec
    # Note: In a real app, this would be done via easysync.register_codec or similar
    # But since we are testing the contrib, we'll manually ensure it's used.
    from easysync.codecs import _registry
    codec = TorchGPUCodec()
    _registry["torch.Tensor"] = codec

    c1 = connect("127.0.0.1", port)
    c2 = connect("127.0.0.1", port)
    time.sleep(1)

    @SyncedObject()
    class SyncedModel:
        def __init__(self):
            self.weights = None

    # We manually set the client for each instance since we defined the class once
    m1 = SyncedModel(_sync_client=c1)
    m2 = SyncedModel(_sync_client=c2)
    time.sleep(1)

    print(f"m1 client: {m1._sync_client}")
    print(f"m2 client: {m2._sync_client}")
    print(f"m1 ID: {m1._sync_object_id}")
    print(f"m2 ID: {m2._sync_object_id}")

    print("Sending GPU Tensor (4MB)...")
    # Fill with random data
    data = torch.randn((1024, 1024), device="cuda")
    t0 = time.perf_counter()
    m1.weights = data
    
    # Wait for sync
    success = False
    print("Waiting for sync...")
    for i in range(50):
        if m2.weights is not None:
            print(f"Attempt {i}: m2.weights is not None, shape={m2.weights.shape}")
            if torch.allclose(m2.weights.cpu(), data.cpu()):
                success = True
                break
        else:
            if i % 10 == 0:
                print(f"Attempt {i}: m2.weights is still None")
        time.sleep(0.1)

    t1 = time.perf_counter()
    if success:
        print(f"SUCCESS: GPU Tensor synchronized in {(t1-t0)*1000:.2f}ms")
        print(f"Device of received tensor: {m2.weights.device}")
    else:
        print("FAILURE: GPU Tensor sync failed or timed out.")
        if m2.weights is not None:
            print(f"Difference: {torch.norm(m2.weights - data)}")

    c1.connected = False
    c2.connected = False
    srv.stop()

if __name__ == "__main__":
    test_gpu_sync()
