"""
NumPy ndarray Codec for EasySync (with Delta Sync)
====================================================
Registered automatically the first time a NumPy array is synced
(importing it explicitly is still supported):
    import easysync.contrib.numpy_codec

Full transfers send the array memory as a raw zero-copy payload next to a
small metadata dict (dtype, shape). Delta transfers send XOR + zlib.
No pickle is involved, so received arrays cannot carry executable content.
"""

import zlib
import easysync
from easysync.codecs import Codec

_MAX_DIMS = 64


def _check_dtype(dtype_str):
    import numpy as np
    dtype = np.dtype(dtype_str)
    if dtype.hasobject:
        raise ValueError("object arrays cannot be received as raw buffers")
    return dtype


@easysync.codec("numpy.ndarray")
class NumpyArrayCodec(Codec):
    """Codec for NumPy arrays with delta sync support."""

    deep_proxy = False

    def match(self, obj):
        return (type(obj).__module__ == "numpy" and type(obj).__name__ == "ndarray"
                and not obj.dtype.hasobject)

    def encode(self, obj):
        import numpy as np
        arr = np.ascontiguousarray(obj)
        meta = {"dtype": arr.dtype.str, "shape": tuple(arr.shape)}
        # (metadata, buffer): the buffer travels as a raw zero-copy payload
        return meta, memoryview(arr).cast("B") if arr.nbytes else b""

    def decode(self, meta, raw_payload=None):
        import numpy as np
        dtype = _check_dtype(meta["dtype"])
        shape = tuple(int(n) for n in meta["shape"])
        if len(shape) > _MAX_DIMS:
            raise ValueError("too many dimensions")
        raw = raw_payload if raw_payload is not None else b""
        arr = np.frombuffer(raw, dtype=dtype) if len(raw) else np.zeros(0, dtype=dtype)
        arr = arr.reshape(shape)
        # frombuffer over a bytearray is writable; over bytes it is not
        return arr if arr.flags.writeable else arr.copy()

    def snapshot(self, obj):
        import numpy as np
        return np.array(obj, copy=True, order="C")

    def encode_delta(self, old, new):
        """Vectorized XOR and fast sampling to prevent overhead on high-entropy data."""
        import numpy as np

        if old.shape != new.shape or old.dtype != new.dtype:
            return None  # Incompatible shapes → full send

        new = np.ascontiguousarray(new)

        # --- Fast Sampling Strategy ---
        # Instead of XORing 30MB, we check 100 random positions.
        # If > 15% are different, we assume it's high-entropy and skip delta.
        sample_size = min(old.size, 100)
        if sample_size > 0:
            indices = np.random.randint(0, old.size, sample_size)
            diff_count = np.count_nonzero(new.ravel()[indices] != old.ravel()[indices])
            if diff_count > sample_size * 0.15:
                return None  # Rejection: too many changes, full send is better

        # --- Efficient Vectorized XOR on the raw bytes ---
        old_view = old.view(np.uint8).reshape(-1)
        new_view = new.view(np.uint8).reshape(-1)
        delta_bytes = np.bitwise_xor(old_view, new_view).tobytes()

        # Compress — XOR of similar data produces long runs of zeros
        compressed = zlib.compress(delta_bytes, level=1)

        # Threshold: Only use delta if it saves significant bandwidth
        if len(compressed) < old.nbytes * 0.7:
            return compressed

        return None  # Not worth the processing cost

    def decode_delta(self, current, delta_bytes):
        """Decompress and XOR to reconstruct using vectorized operations."""
        import numpy as np

        # Bounded decompression: a crafted delta cannot inflate past the array size
        raw = zlib.decompressobj().decompress(delta_bytes, current.nbytes + 1)
        if len(raw) != current.nbytes:
            raise ValueError("delta size does not match the base array")

        current_view = np.ascontiguousarray(current).view(np.uint8).reshape(-1)
        delta_view = np.frombuffer(raw, dtype=np.uint8)
        new_view = np.bitwise_xor(current_view, delta_view)
        return new_view.view(current.dtype).reshape(current.shape)
