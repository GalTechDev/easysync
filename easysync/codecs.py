"""
EasySync Codec Registry
========================
Extension system allowing users to register custom serializers
for any type without modifying the library source code.

Usage:
    # Class-based registration
    @easysync.codec("torch.Tensor")
    class TorchCodec(easysync.codecs.Codec):
        def match(self, obj): ...
        def encode(self, obj): ...
        def decode(self, data): ...
        deep_proxy = False

    # Functional registration
    easysync.register_codec(
        name="numpy.ndarray",
        match=lambda obj: type(obj).__name__ == "ndarray",
        encode=lambda obj: obj.tobytes(),
        decode=lambda data: np.frombuffer(data),
        deep_proxy=False,
    )

encode() may return bytes, or a (metadata, buffer) tuple: the buffer is then
sent as a raw zero-copy payload and decode() is called as
decode(metadata, raw_payload=buffer).
"""

import inspect


class Codec:
    """Base class for custom type codecs.

    Subclasses may optionally implement encode_delta/decode_delta
    to enable delta synchronization (sending only what changed).
    """

    def match(self, obj) -> bool:
        """Return True if this codec can handle the given object."""
        raise NotImplementedError

    def encode(self, obj) -> bytes:
        """Serialize the object to bytes (full)."""
        raise NotImplementedError

    def decode(self, data: bytes):
        """Deserialize bytes back to the object (full)."""
        raise NotImplementedError

    # --- Delta sync (optional) ---

    def encode_delta(self, old, new) -> bytes | None:
        """Encode only the difference between old and new.

        Return compressed delta bytes, or None to fall back to full encode.
        Returning None is always safe — the system will send the full value.
        """
        return None

    def decode_delta(self, current, delta: bytes):
        """Apply a delta to the current value to reconstruct the new value."""
        raise NotImplementedError

    def snapshot(self, obj):
        """Return an independent copy of obj, kept as the base for the next delta.

        Codecs of mutable types must override it (e.g. ``obj.copy()``): keeping a
        reference would make in-place changes invisible to encode_delta().
        """
        return obj

    def supports_delta(self) -> bool:
        """Returns True if this codec has a real delta implementation."""
        return hasattr(self, 'encode_delta') and hasattr(self, 'decode_delta') and \
               type(self).encode_delta is not Codec.encode_delta

    deep_proxy: bool = False


# --------------- Global Registry ---------------

_registry: dict[str, Codec] = {}

# Default type names excluded from deep proxying (legacy behavior)
_default_excluded = {"ndarray", "DataFrame", "Series"}

# Built-in codecs imported on first use: {type module: (codec name, contrib module)}
_LAZY_CODECS = {
    "numpy": ("numpy.ndarray", "easysync.contrib.numpy_codec"),
    "torch": ("torch.Tensor", "easysync.contrib.torch_codec"),
}


def register_codec(name: str, match_or_instance=None, encode=None, decode=None,
                   deep_proxy=False, *, match=None):
    """Register a codec.

    Args:
        name: Unique identifier for this codec.
        match_or_instance: A Codec instance, or a callable match(obj) -> bool.
        encode: (Optional) Callable(obj) -> bytes.
        decode: (Optional) Callable(bytes) -> obj.
        deep_proxy: If False, objects handled by this codec will NOT be proxy-wrapped.
        match: Keyword alias of match_or_instance for the functional form.
    """
    if match_or_instance is None:
        match_or_instance = match
    if isinstance(match_or_instance, Codec) or hasattr(match_or_instance, "match"):
        instance = match_or_instance
        instance.__codec_name__ = name
        _registry[name] = instance
        return instance

    if not callable(match_or_instance):
        raise TypeError("register_codec() needs a Codec instance or a match callable")
    c = Codec()
    c.match = match_or_instance
    c.encode = encode
    c.decode = decode
    c.deep_proxy = deep_proxy
    c.__codec_name__ = name
    _registry[name] = c
    return c


def codec(name: str):
    """Class decorator that registers a Codec subclass.

    Usage:
        @easysync.codec("torch.Tensor")
        class TorchCodec(easysync.codecs.Codec):
            def match(self, obj): ...
            def encode(self, obj): ...
            def decode(self, data): ...
            deep_proxy = False
    """
    def decorator(cls):
        instance = cls()
        instance.__codec_name__ = name
        _registry[name] = instance
        return cls
    return decorator


def _load_builtin(codec_name):
    for name, module in _LAZY_CODECS.values():
        if name == codec_name and name not in _registry:
            try:
                __import__(module)
            except ImportError:
                pass


def find_codec(obj):
    """Find which codec handles the given object.

    Returns:
        (name, codec) tuple if found, None otherwise.
    """
    lazy = _LAZY_CODECS.get(type(obj).__module__.split(".")[0])
    if lazy and lazy[0] not in _registry:
        _load_builtin(lazy[0])
    for name, c in list(_registry.items()):
        try:
            if c.match(obj):
                return (name, c)
        except Exception:
            continue
    return None


def get_codec(name: str):
    """Retrieve a codec by its registered name.

    Returns:
        Codec instance or None.
    """
    if name not in _registry:
        _load_builtin(name)
    return _registry.get(name)


def decode_value(c, data, raw_payload=None):
    """Call c.decode(), passing raw_payload only to codecs that accept it."""
    if raw_payload is None:
        return c.decode(data)
    try:
        params = inspect.signature(c.decode).parameters
        accepts = "raw_payload" in params or any(p.kind is p.VAR_KEYWORD for p in params.values())
    except (TypeError, ValueError):
        accepts = True
    if accepts:
        return c.decode(data, raw_payload=raw_payload)
    return c.decode(data, raw_payload)


def snapshot(c, value):
    """Independent copy of value used as a delta base (see Codec.snapshot)."""
    snap = getattr(c, "snapshot", None)
    return snap(value) if snap else value


def get_excluded_types() -> set:
    """Returns type names that should NOT be wrapped in SyncedProxy.

    Merges the hardcoded defaults with any registered codec
    that has deep_proxy=False.
    """
    excluded = set(_default_excluded)
    for name, c in _registry.items():
        if not c.deep_proxy:
            # Add the codec name itself and try to extract the class name
            parts = name.rsplit(".", 1)
            excluded.add(parts[-1])  # e.g. "Tensor" from "torch.Tensor"
    return excluded


def list_codecs() -> list[str]:
    """Returns the names of all registered codecs."""
    return list(_registry.keys())
