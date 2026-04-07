import inspect
import threading
import time
import weakref

from easysync.syncclient import SyncClient

_default_client = None


class SyncedProxy:
    """Proxy transparent qui intercepte les mutations et déclenche le callback réseau."""

    def __init__(self, target, callback):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_callback", callback)

    def __getattribute__(self, name):
        if name in ("_target", "_callback", "_trigger", "__class__",
                     "__array__", "__array_struct__", "__array_interface__"):
            if name in ("__array__", "__array_struct__", "__array_interface__"):
                return getattr(object.__getattribute__(self, "_target"), name)
            return object.__getattribute__(self, name)

        target = object.__getattribute__(self, "_target")
        attr = getattr(target, name)
        callback = object.__getattribute__(self, "_callback")
        return _deep_wrap(attr, callback)

    def __call__(self, *args, **kwargs):
        target = object.__getattribute__(self, "_target")
        callback = object.__getattribute__(self, "_callback")
        ret = target(*args, **kwargs)
        object.__getattribute__(self, "_trigger")()
        return _deep_wrap(ret, callback)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_target"), name, value)
        object.__getattribute__(self, "_trigger")()

    def _trigger(self):
        cb = object.__getattribute__(self, "_callback")
        if cb:
            cb()

    def __getitem__(self, key):
        res = object.__getattribute__(self, "_target")[key]
        return _deep_wrap(res, object.__getattribute__(self, "_callback"))

    def __setitem__(self, key, value):
        object.__getattribute__(self, "_target")[key] = value
        object.__getattribute__(self, "_trigger")()

    def __delitem__(self, key):
        del object.__getattribute__(self, "_target")[key]
        object.__getattribute__(self, "_trigger")()

    def __iter__(self):
        target = object.__getattribute__(self, "_target")
        callback = object.__getattribute__(self, "_callback")
        for item in target:
            yield _deep_wrap(item, callback)

    def __len__(self):
        return len(object.__getattribute__(self, "_target"))

    def __repr__(self):
        return repr(object.__getattribute__(self, "_target"))

    def __str__(self):
        return str(object.__getattribute__(self, "_target"))

    def __array__(self, *args, **kwargs):
        target = object.__getattribute__(self, "_target")
        if hasattr(target, "__array__"):
            return target.__array__(*args, **kwargs)
        return NotImplemented


def _deep_wrap(value, callback):
    """Enveloppe récursivement un objet dans un SyncedProxy.
    Les types immuables et les extensions C (numpy, pandas) sont exclus."""
    import numbers
    if value is None or isinstance(value, (numbers.Number, str, bool, tuple, bytes)):
        return value
    if type(value).__name__ in ("ndarray", "DataFrame", "Series"):
        return value
    if isinstance(value, SyncedProxy):
        object.__setattr__(value, "_callback", callback)
        return value
    return SyncedProxy(value, callback)


def _unproxy(value):
    """Retire récursivement les couches de proxy pour obtenir l'objet brut."""
    if isinstance(value, SyncedProxy):
        return _unproxy(object.__getattribute__(value, "_target"))
    if type(value) is list:
        return [_unproxy(x) for x in value]
    if type(value) is dict:
        return {k: _unproxy(v) for k, v in value.items()}
    return value


# ---------- Registre global et boucle de surveillance ----------

_master_synced_vars = []
_master_synced_objects = weakref.WeakSet()
_master_poller_thread = None


def _master_poller_loop():
    """Thread unique qui surveille les mutations locales non interceptées."""
    while True:
        time.sleep(0.05)
        for var in list(_master_synced_vars):
            try:
                if var._updating:
                    continue
                current_val = var.namespace.get(var.var_name)
                if current_val != var.last_val:
                    var.last_val = current_val
                    if var._client:
                        var._client.send_update(var.var_name, "value", current_val)
            except Exception:
                pass


def _handle_sync_request():
    """Réponse à une demande de resynchronisation d'un client."""
    for var in _master_synced_vars:
        if var._client:
            var._client.send_update(var.var_name, "value", var.last_val)

    for obj in list(_master_synced_objects):
        try:
            c = object.__getattribute__(obj, "_sync_client")
            oid = object.__getattribute__(obj, "_sync_object_id")
            if c:
                import types
                for attr_name in dir(obj):
                    if not attr_name.startswith("_"):
                        val = getattr(obj, attr_name)
                        if not isinstance(val, types.MethodType):
                            c.send_update(oid, attr_name, _unproxy(val))
        except Exception:
            pass


# ---------- API publique ----------

def connect(host="localhost", port=5000):
    """Se connecte à un SyncServer distant et retourne le client."""
    global _default_client
    _default_client = SyncClient(host=host, port=port)
    _default_client.on_sync_request_callback = _handle_sync_request
    _default_client.connect()
    return _default_client


def get_client():
    """Retourne le client par défaut."""
    return _default_client


def SyncedObject(client=None):
    """Décorateur de classe : synchronise les attributs publics sur le réseau."""
    def decorator(cls):
        _object_id = cls.__qualname__
        original_init = cls.__init__ if hasattr(cls, "__init__") else None

        def new_init(self, *args, _sync_client=None, **kwargs):
            resolved_client = _sync_client or client or _default_client
            object.__setattr__(self, "_sync_client", resolved_client)
            object.__setattr__(self, "_sync_object_id", _object_id)
            object.__setattr__(self, "_sync_updating", False)

            if resolved_client:
                resolved_client.register_callback(
                    _object_id, lambda msg, obj=self: _apply_update(obj, msg)
                )

            if original_init:
                original_init(self, *args, **kwargs)

            _master_synced_objects.add(self)

        def new_setattr(self, name, value):
            if name.startswith("_"):
                object.__setattr__(self, name, value)
                return

            def trigger_sync():
                try:
                    if object.__getattribute__(self, "_sync_updating"):
                        return
                except AttributeError:
                    pass
                try:
                    c = object.__getattribute__(self, "_sync_client")
                    oid = object.__getattribute__(self, "_sync_object_id")
                    if c:
                        unproxied = _unproxy(object.__getattribute__(self, name))
                        c.send_update(oid, name, unproxied)
                except AttributeError:
                    pass

            wrapped_value = _deep_wrap(value, trigger_sync)
            object.__setattr__(self, name, wrapped_value)
            trigger_sync()

        cls.__init__ = new_init
        cls.__setattr__ = new_setattr
        return cls
    return decorator


def _apply_update(obj, message):
    """Applique une mise à jour reçue du réseau sur l'objet local."""
    attr_name = message.get("attr_name")
    value = message.get("value")

    if attr_name:
        if attr_name.startswith("_"):
            return
        if not hasattr(obj, attr_name):
            return

        object.__setattr__(obj, "_sync_updating", True)
        try:
            setattr(obj, attr_name, value)
        finally:
            object.__setattr__(obj, "_sync_updating", False)


class SyncedVar:
    """Variable simple synchronisée sur le réseau."""

    def __init__(self, value=None, client=None):
        global _master_poller_thread

        self._client = client or _default_client
        self.frame = inspect.currentframe().f_back
        self.namespace = self.frame.f_globals
        self.var_name = None

        try:
            import dis
            instructions = list(dis.get_instructions(self.frame.f_code))
            for i, inst in enumerate(instructions):
                if inst.offset == self.frame.f_lasti:
                    for j in range(i - 1, -1, -1):
                        prev_inst = instructions[j]
                        if prev_inst.opname in ("LOAD_FAST", "LOAD_NAME", "LOAD_GLOBAL", "LOAD_DEREF"):
                            self.var_name = prev_inst.argval
                            break
                        elif prev_inst.opname.startswith("CALL") or prev_inst.opname.startswith("PRECALL"):
                            break
                    break
        except Exception:
            pass

        if not self.var_name:
            raise ValueError("Impossible de résoudre le nom de variable.")

        self.last_val = self.namespace.get(self.var_name, value)
        self._updating = False

        if self._client:
            self._client.register_callback(self.var_name, self._apply_net_update)

        _master_synced_vars.append(self)

        if _master_poller_thread is None:
            _master_poller_thread = threading.Thread(target=_master_poller_loop, daemon=True)
            _master_poller_thread.start()

    def _apply_net_update(self, message):
        val = message.get("value")
        if val is not None:
            self._updating = True
            try:
                self.namespace[self.var_name] = val
                self.last_val = val
            finally:
                self._updating = False

    def get(self):
        return self.last_val

    def set(self, value):
        self.namespace[self.var_name] = value

    def set_client(self, client):
        self._client = client
        if self._client:
            self._client.register_callback(self.var_name, self._apply_net_update)

    def __repr__(self):
        return f"SyncedVar({self.var_name!r}, {self.last_val!r})"

    def __str__(self):
        return str(self.last_val)
