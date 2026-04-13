import torch
import numpy as np
from typing import Any, Dict, Tuple
from easysync.codecs import Codec
from easyshm import EasySHM

class TorchGPUCodec(Codec):
    """Codec for Zero-Copy GPU synchronization using Pinned Memory bridge.
    
    This codec bypasses OS memory isolation by using Pinned Host Memory (EasySHM)
    as a DMA bridge between GPU VRAM and the Network stack.
    """

    def __init__(self, pinned_shm_name="torch_gpu_bridge", initial_size=1024*1024*10, sync_stream=True):
        """
        Args:
            pinned_shm_name: Name for the EasySHM bridge segment.
            initial_size: Initial size of the bridge in bytes (default 10MB).
            sync_stream: If True, synchronization is performed before network send.
        """
        self.bridge_name = pinned_shm_name
        self.pinned_shm = None
        self.sync_stream = sync_stream
        self._initial_size = initial_size

    def _ensure_bridge(self, size):
        if self.pinned_shm is None:
            self.pinned_shm = EasySHM(self.bridge_name, size=max(size, self._initial_size), pinned=True)
        elif self.pinned_shm.capacity < size:
            # Auto-grow the bridge if the tensor is larger than current capacity
            self.pinned_shm.resize(size)
        return self.pinned_shm

    def match(self, obj):
        return isinstance(obj, torch.Tensor) and obj.is_cuda

    def encode(self, tensor: torch.Tensor) -> Tuple[Dict, Any]:
        """Move tensor from GPU to Pinned SHM bridge and return (metadata, bridge)."""
        required_size = tensor.element_size() * tensor.nelement()
        shm = self._ensure_bridge(required_size)

        # Create a metadata dict to reconstruct the tensor on the receiver
        metadata = {
            "shape": list(tensor.shape),
            "dtype": str(tensor.dtype).split(".")[-1], # e.g. "float32"
            "size": required_size
        }

        # Perform Async Copy: GPU -> Pinned Host (DMA)
        # We use a memoryview of the underlying SHM segment
        shm_view = np.frombuffer(shm._data.buf, dtype=self._get_numpy_dtype(tensor.dtype), count=tensor.nelement())
        shm_tensor = torch.from_numpy(shm_view).reshape(tensor.shape)
        
        # Non-blocking copy triggers DMA transfer
        shm_tensor.copy_(tensor, non_blocking=True)
        
        if self.sync_stream:
            torch.cuda.current_stream().synchronize()
            
        # Return metadata and the SHM source to SyncClient
        return metadata, shm

    def decode(self, metadata: Dict, raw_payload: Any = None, target_device=None):
        """Move data from Pinned Host (raw_payload) to preferred device."""
        if raw_payload is None:
            raise ValueError("[TorchGPUCodec] decode requires raw_payload")

        shape = metadata["shape"]
        dtype_str = metadata["dtype"]
        torch_dtype = getattr(torch, dtype_str)
        
        shm_view = np.frombuffer(raw_payload, dtype=self._get_numpy_dtype(torch_dtype))
        shm_view = shm_view[:np.prod(shape)]
        
        cpu_tensor = torch.from_numpy(shm_view).reshape(shape)
        
        # Decide target device: use provided, otherwise auto-detect
        if target_device is None:
            target_device = "cuda" if torch.cuda.is_available() else "cpu"
            
        return cpu_tensor.to(target_device, non_blocking=True)

    def _get_numpy_dtype(self, torch_dtype):
        mapping = {
            torch.float32: np.float32,
            torch.float64: np.float64,
            torch.float16: np.float16,
            torch.int32: np.int32,
            torch.int64: np.int64,
            torch.uint8: np.uint8,
            "float32": np.float32,
            "float64": np.float64,
            "float16": np.float16,
            "int32": np.int32,
            "int64": np.int64,
            "uint8": np.uint8,
        }
        return mapping.get(torch_dtype, np.float32)

    deep_proxy = False
