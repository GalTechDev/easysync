"""
PyTorch Tensor Codec for EasySync
==================================
Auto-registers on import:
    import easysync.contrib.torch_codec

This replaces pickle serialization with torch.save/torch.load
for all torch.Tensor values, which is significantly faster
for large tensors (GPU tensors are moved to CPU automatically).
"""

import io
import easysync
from easysync.codecs import Codec


@easysync.codec("torch.Tensor")
class TorchTensorCodec(Codec):
    """Codec for PyTorch tensors using native torch serialization."""

    deep_proxy = False

    def match(self, obj):
        return type(obj).__module__ == "torch" and type(obj).__name__ == "Tensor"

    def encode(self, obj):
        import torch
        buf = io.BytesIO()
        # Move to CPU before serializing to ensure cross-device compatibility
        torch.save(obj.detach().cpu(), buf)
        return buf.getvalue()

    def decode(self, data):
        import torch
        buf = io.BytesIO(data)
        return torch.load(buf, weights_only=True)
