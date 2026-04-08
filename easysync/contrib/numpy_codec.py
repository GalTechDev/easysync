"""
NumPy ndarray Codec for EasySync (with Delta Sync)
====================================================
Auto-registers on import:
    import easysync.contrib.numpy_codec

Uses numpy's native buffer serialization for full transfers,
and XOR + zlib compression for delta transfers.
"""

import pickle
import zlib
import easysync
from easysync.codecs import Codec


@easysync.codec("numpy.ndarray")
class NumpyArrayCodec(Codec):
    """Codec for NumPy arrays with delta sync support."""

    deep_proxy = False

    def match(self, obj):
        return type(obj).__module__ == "numpy" and type(obj).__name__ == "ndarray"

    def encode(self, obj):
        import numpy as np
        meta = {"dtype": str(obj.dtype), "shape": obj.shape}
        raw = obj.tobytes()
        return pickle.dumps((meta, raw))

    def decode(self, data):
        import numpy as np
        meta, raw = pickle.loads(data)
        return np.frombuffer(raw, dtype=np.dtype(meta["dtype"])).reshape(meta["shape"]).copy()

    def encode_delta(self, old, new):
        """Vectorized XOR and fast sampling to prevent overhead on high-entropy data."""
        import numpy as np

        if old is new:
            # Identity check: absolutely no change
            return zlib.compress(b"", level=1)

        if old.shape != new.shape or old.dtype != new.dtype:
            return None  # Incompatible shapes → full send

        # --- Fast Sampling Strategy ---
        # Instead of XORing 30MB, we check 100 random positions.
        # If > 15% are different, we assume it's high-entropy and skip delta.
        sample_size = min(old.size, 100)
        if sample_size > 0:
            indices = np.random.randint(0, old.size, sample_size)
            # Flatten for sampling
            diff_count = np.count_nonzero(new.ravel()[indices] != old.ravel()[indices])
            if diff_count > sample_size * 0.15:
                return None  # Rejection: too many changes, full send is better

        # --- Efficient Vectorized XOR ---
        # We view the arrays as flat byte arrays (uint8)
        # We use .view() to avoid data copying where possible
        old_view = old.view(np.uint8).reshape(-1)
        new_view = new.view(np.uint8).reshape(-1)
        
        # bitwise_xor on views is extremely fast (C-level)
        delta_bytes = np.bitwise_xor(old_view, new_view).tobytes()

        # Compress — XOR of similar data produces long runs of zeros
        # zlib level 1 is very fast and efficient for zero-heavy buffers
        compressed = zlib.compress(delta_bytes, level=1)

        # Threshold: Only use delta if it saves significant bandwidth
        if len(compressed) < old.nbytes * 0.7:
            return compressed

        return None  # Not worth the processing cost

    def decode_delta(self, current, delta_bytes):
        """Decompress and XOR to reconstruct using vectorized operations."""
        import numpy as np

        raw = zlib.decompress(delta_bytes)
        if not raw:
            return current.copy()

        current_view = current.view(np.uint8).reshape(-1)
        delta_view = np.frombuffer(raw, dtype=np.uint8)
        
        # Vectorized reconstruction
        new_view = np.bitwise_xor(current_view, delta_view)
        # Restore original shape and dtype
        return new_view.view(current.dtype).reshape(current.shape).copy()
