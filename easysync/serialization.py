"""
Safe (de)serialization for EasySync messages
=============================================

Messages are pickled, but received data is never trusted: ``loads`` uses a
restricted unpickler that only rebuilds plain data (numbers, strings, bytes,
lists, tuples, dicts, sets...) plus a small allow-list of harmless types.
Anything else (functions, arbitrary classes, ``os.system``...) is rejected,
so a peer cannot execute code on your machine by sending a crafted packet.

Extend the allow-list for your own data classes::

    easysync.allow_types(MyDataclass, "mypackage.module.OtherClass")

Or, on a network where every peer is trusted, accept any picklable object::

    easysync.trust_all_types()
"""

import io
import pickle

PROTOCOL = pickle.HIGHEST_PROTOCOL

# (module, qualname) pairs that are safe to rebuild.
_SAFE_GLOBALS = {
    ("builtins", "complex"), ("builtins", "range"), ("builtins", "slice"),
    ("builtins", "set"), ("builtins", "frozenset"), ("builtins", "bytearray"),
    ("datetime", "datetime"), ("datetime", "date"), ("datetime", "time"),
    ("datetime", "timedelta"), ("datetime", "timezone"),
    ("collections", "OrderedDict"), ("collections", "deque"),
    ("decimal", "Decimal"), ("fractions", "Fraction"), ("uuid", "UUID"),
    # NumPy arrays and scalars nested inside other values
    ("numpy", "dtype"), ("numpy", "ndarray"),
    ("numpy._core.numeric", "_frombuffer"), ("numpy.core.numeric", "_frombuffer"),
    ("numpy._core.multiarray", "_reconstruct"), ("numpy.core.multiarray", "_reconstruct"),
    ("numpy._core.multiarray", "scalar"), ("numpy.core.multiarray", "scalar"),
}
_user_globals = set()
_trust_all = False


def allow_types(*types):
    """Allow extra classes to be received, given as classes or "module.QualName" strings.

    Only allow classes whose construction has no side effects: a class is rebuilt
    with the arguments chosen by the sender.
    """
    for t in types:
        if isinstance(t, str):
            module, _, name = t.rpartition(".")
            if not module:
                raise ValueError(f"expected 'module.ClassName', got {t!r}")
        else:
            module, name = t.__module__, t.__qualname__
        _user_globals.add((module, name))


def trust_all_types(enabled=True):
    """Accept any picklable object from peers.

    Only for networks where every peer is trusted: a malicious peer can then
    execute arbitrary code on this machine.
    """
    global _trust_all
    _trust_all = bool(enabled)


class UnsafeTypeError(pickle.UnpicklingError):
    """Raised when a received message contains a type that is not allowed."""


class _RestrictedUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) in _SAFE_GLOBALS or (module, name) in _user_globals:
            return super().find_class(module, name)
        raise UnsafeTypeError(
            f"refused to deserialize {module}.{name}: call "
            f"easysync.allow_types('{module}.{name}') if this type is safe"
        )


def dumps(obj):
    return pickle.dumps(obj, protocol=PROTOCOL)


def loads(data):
    """Deserialize data received from a peer (restricted unless trust_all_types())."""
    if _trust_all:
        return pickle.loads(data)
    return _RestrictedUnpickler(io.BytesIO(data)).load()


# ---------- Server side: read routing fields without rebuilding anything ----------

class _Opaque:
    """Inert stand-in for any object the server does not need to understand."""

    def __init__(self, *args, **kwargs):
        pass

    def __setstate__(self, state):
        pass

    def __call__(self, *args, **kwargs):
        return _Opaque()


class _OpaqueUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        return _Opaque

    def persistent_load(self, pid):
        return _Opaque()


def peek(data):
    """Parse a message only to read its routing fields.

    No constructor or function from the message is ever called: every global
    is replaced by an inert placeholder. The server relays the original bytes.
    """
    return _OpaqueUnpickler(io.BytesIO(data)).load()
