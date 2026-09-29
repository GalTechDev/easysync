"""EasySync performance benchmarks.

Usage:  python benchmarks/perf.py [step ...]   (no argument = every step)

Every benchmark runs the real library across separate processes on localhost.
Cross-process timings use time.perf_counter_ns (CLOCK_MONOTONIC on Linux,
shared by all processes of the machine).
"""
import asyncio
import contextlib
import io
import json
import multiprocessing as mp
import os
import pickle
import platform
import socket
import statistics
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from easysync import SyncedObject, SyncServer  # noqa: E402
from easysync.syncclient import SyncClient  # noqa: E402
import easysync.syncedobject as so  # noqa: E402

RESULTS = {}


# ---------------------------------------------------------------- helpers
def quiet():
    sys.stdout = open(os.devnull, "w")


def free_port():
    while True:
        s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
        try:
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.bind(("127.0.0.1", p + 1)); u.close()
            return p
        except OSError:
            continue


def server_proc(port):
    quiet()
    asyncio.run(SyncServer(host="127.0.0.1", port=port).start())


def start_server():
    port = free_port()
    p = mp.Process(target=server_proc, args=(port,), daemon=True)
    p.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    return port, p


def wait_connected(c, timeout=5):
    t = time.time()
    while not c.connected and time.time() - t < timeout:
        time.sleep(0.01)
    assert c.connected, "client failed to connect"


def raw_client(port):
    with contextlib.redirect_stdout(io.StringIO()):
        c = SyncClient(host="127.0.0.1", port=port, sync_new_client=False)
        c.connect()
        wait_connected(c)
    return c


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q / 100 * (len(values) - 1))))]


def us(ns):
    return round(ns / 1000, 1)


# Module-level classes so their qualname matches in every process
@SyncedObject(transport="tcp")
class PingT:
    def __init__(self): self.seq = 0


@SyncedObject(transport="tcp")
class PongT:
    def __init__(self): self.seq = 0


@SyncedObject(transport="udp")
class PingU:
    def __init__(self): self.seq = 0


@SyncedObject(transport="udp")
class PongU:
    def __init__(self): self.seq = 0


@SyncedObject()
class Counter:
    def __init__(self): self.value = 0




def make_echo(ping_cls, pong_cls, client):
    """Echo side: whenever ping.seq arrives, answer with pong.seq."""
    ping, pong = ping_cls(), pong_cls()
    oid = ping_cls.__qualname__
    orig = client.callbacks[oid]

    def cb(msg):
        orig(msg)
        pong.seq = ping.seq
    client.callbacks[oid] = cb
    return ping, pong


# ---------------------------------------------------------------- 1. latency (SyncedObject echo)
def echo_proc(port, udp, ready, stop):
    quiet()
    c = so.connect("127.0.0.1", port, sync_new_client=False)
    wait_connected(c)
    make_echo(PingU if udp else PingT, PongU if udp else PongT, c)
    ready.set()
    stop.wait()


def bench_latency(udp, n=3000, warmup=300):
    port, srv = start_server()
    ready, stop = mp.Event(), mp.Event()
    echo = mp.Process(target=echo_proc, args=(port, udp, ready, stop)); echo.start()
    ready.wait(10)

    with contextlib.redirect_stdout(io.StringIO()):
        c = so.connect("127.0.0.1", port, sync_new_client=False)
        wait_connected(c)
    ping, pong = (PingU if udp else PingT)(), (PongU if udp else PongT)()
    got = threading.Event()
    oid = type(pong).__qualname__
    orig = c.callbacks[oid]
    c.callbacks[oid] = lambda msg: (orig(msg), got.set())
    time.sleep(0.2)

    rtts, lost = [], 0
    for i in range(1, n + warmup + 1):
        got.clear()
        t0 = time.perf_counter_ns()
        ping.seq = i
        if not got.wait(0.25) or pong.seq != i:
            lost += 1
            continue
        if i > warmup:
            rtts.append(time.perf_counter_ns() - t0)
    stop.set(); echo.join(3); srv.terminate()
    c.connected = False
    one_way = [r / 2 for r in rtts]
    return {"samples": len(rtts), "lost": lost,
            "rtt_median_us": us(statistics.median(rtts)), "rtt_p99_us": us(pct(rtts, 99)),
            "one_way_median_us": us(statistics.median(one_way)), "one_way_p99_us": us(pct(one_way, 99))}


# ---------------------------------------------------------------- 2. throughput
def counter_recv_proc(port, ready, out, target):
    quiet()
    c = so.connect("127.0.0.1", port, sync_new_client=False)
    wait_connected(c)
    counter = Counter()
    stamps = []
    orig = c.callbacks["Counter"]

    def cb(msg):
        orig(msg)
        stamps.append(time.perf_counter_ns())
    c.callbacks["Counter"] = cb
    ready.set()
    # Wait until the last value sent has arrived (or give up after 30 s)
    while target.value == 0 or (counter.value != target.value and time.time() < deadline_of(target)):
        time.sleep(0.05)
    out.put({"received": len(stamps), "last": counter.value,
             "span_s": (stamps[-1] - stamps[0]) / 1e9 if len(stamps) > 1 else 0})


_deadlines = {}


def deadline_of(target):
    return _deadlines.setdefault(id(target), time.time() + 30)


def bench_throughput(duration=3.0):
    port, srv = start_server()
    ready, out, target = mp.Event(), mp.Queue(), mp.Value("q", 0)
    r = mp.Process(target=counter_recv_proc, args=(port, ready, out, target)); r.start()
    ready.wait(10)
    with contextlib.redirect_stdout(io.StringIO()):
        c = so.connect("127.0.0.1", port, sync_new_client=False)
        wait_connected(c)
    counter = Counter()
    time.sleep(0.2)
    i, t0 = 0, time.perf_counter()
    while time.perf_counter() - t0 < duration:
        i += 1
        counter.value = i
    sent_rate = i / (time.perf_counter() - t0)
    target.value = i
    res = out.get(timeout=60)
    r.join(); srv.terminate(); c.connected = False
    return {"sent": i, "sent_per_s": round(sent_rate), "received": res["received"],
            "received_per_s": round(res["received"] / res["span_s"]) if res["span_s"] else 0,
            "all_delivered": res["received"] == i and res["last"] == i}


# ---------------------------------------------------------------- 3. fan-out (1 sender -> N receivers)
def fan_recv_proc(port, n_clients, ready, out, n_msgs):
    quiet()
    arrivals = []
    lock = threading.Lock()
    clients = []
    for _ in range(n_clients):
        c = SyncClient(host="127.0.0.1", port=port, sync_new_client=False)
        c.connect()
        clients.append(c)
    for c in clients:
        wait_connected(c, 20)
        c.register_callback("Fan", lambda msg: (lock.acquire(), arrivals.append((msg["value"][0], time.perf_counter_ns() - msg["value"][1])), lock.release()))
    ready.release()
    deadline = time.time() + 60
    while len(arrivals) < n_clients * n_msgs and time.time() < deadline:
        time.sleep(0.05)
    out.put(arrivals)


def bench_fanout(n_receivers, n_msgs=100, per_proc=25):
    port, srv = start_server()
    procs, out = [], mp.Queue()
    ready = mp.Semaphore(0)
    groups = [per_proc] * (n_receivers // per_proc) + ([n_receivers % per_proc] if n_receivers % per_proc else [])
    for g in groups:
        p = mp.Process(target=fan_recv_proc, args=(port, g, ready, out, n_msgs)); p.start(); procs.append(p)
    for _ in groups:
        ready.acquire(timeout=60)
    sender = raw_client(port)
    time.sleep(0.5)
    for seq in range(n_msgs):
        sender.send_update("Fan", "v", (seq, time.perf_counter_ns()))
        time.sleep(0.02)
    arrivals = []
    for _ in groups:
        arrivals += out.get(timeout=90)
    for p in procs:
        p.join(5)
    srv.terminate(); sender.connected = False
    per_seq = {}
    for seq, lat in arrivals:
        per_seq.setdefault(seq, []).append(lat)
    complete = [max(v) for v in per_seq.values() if len(v) == n_receivers]
    return {"receivers": n_receivers, "delivered": len(arrivals), "expected": n_receivers * n_msgs,
            "all_received_median_ms": round(statistics.median(complete) / 1e6, 2) if complete else None,
            "all_received_p99_ms": round(pct(complete, 99) / 1e6, 2) if complete else None,
            "per_client_median_ms": round(statistics.median([l for _, l in arrivals]) / 1e6, 2)}


# ---------------------------------------------------------------- 4. payload size
def blob_recv_proc(port, ready, out):
    quiet()
    c = SyncClient(host="127.0.0.1", port=port, sync_new_client=False)
    c.connect(); wait_connected(c)
    c.register_callback("Blob", lambda msg: out.put((time.perf_counter_ns(), len(msg["value"]), type(msg["value"]).__name__)))
    ready.set()
    time.sleep(3600)


def bench_payloads(sizes=(1_000, 100_000, 1_000_000, 10_000_000), reps=15):
    port, srv = start_server()
    ready, out = mp.Event(), mp.Queue()
    r = mp.Process(target=blob_recv_proc, args=(port, ready, out), daemon=True); r.start()
    ready.wait(10)
    s = raw_client(port)
    time.sleep(0.3)
    res = {}
    for size in sizes:
        blob = os.urandom(size)
        lats, rtype = [], None
        for i in range(reps + 2):
            t0 = time.perf_counter_ns()
            s.send_update("Blob", "data", blob)
            t1, n, rtype = out.get(timeout=30)
            assert n == size
            if i >= 2:
                lats.append(t1 - t0)
        med = statistics.median(lats)
        res[size] = {"median_ms": round(med / 1e6, 3), "MB_per_s": round(size / (med / 1e9) / 1e6, 1),
                     "received_type": rtype}
    r.terminate(); srv.terminate(); s.connected = False
    return res


# ---------------------------------------------------------------- 5. delta codec efficiency
def bench_delta():
    import numpy as np
    import easysync.contrib.numpy_codec  # noqa: F401
    from easysync.codecs import get_codec
    codec = get_codec("numpy.ndarray")
    rng = np.random.default_rng(0)
    out = {}

    def measure(name, old, new):
        t = time.perf_counter(); full = codec.encode(new); t_full = time.perf_counter() - t
        if isinstance(full, tuple):  # (metadata, raw buffer) -> bytes on the wire
            full = b"x" * (len(pickle.dumps(full[0])) + memoryview(full[1]).nbytes)
        t = time.perf_counter(); delta = codec.encode_delta(old, new); t_delta = time.perf_counter() - t
        ok = delta is not None and np.array_equal(codec.decode_delta(old, delta), new)
        out[name] = {"full_bytes": len(full), "delta_bytes": len(delta) if delta else None,
                     "saving_pct": round(100 * (1 - len(delta) / len(full)), 2) if delta else 0,
                     "encode_full_ms": round(t_full * 1e3, 2), "encode_delta_ms": round(t_delta * 1e3, 2),
                     "roundtrip_ok": ok}

    # 720p RGB frame where a 100x100 region changes (cursor / small UI update)
    frame = rng.integers(0, 256, (720, 1280, 3), dtype=np.uint8)
    f2 = frame.copy(); f2[300:400, 600:700] = rng.integers(0, 256, (100, 100, 3), dtype=np.uint8)
    measure("frame_720p_100x100_changed", frame, f2)
    # 1000x1000 float32 matrix, 1 % of elements changed
    m = rng.random((1000, 1000), dtype=np.float32)
    m2 = m.copy(); idx = rng.choice(m.size, m.size // 100, replace=False); m2.ravel()[idx] += 1
    measure("float32_1000x1000_1pct_changed", m, m2)
    # Same matrix, 10 % changed
    m3 = m.copy(); idx = rng.choice(m.size, m.size // 10, replace=False); m3.ravel()[idx] += 1
    measure("float32_1000x1000_10pct_changed", m, m3)
    return out


# ---------------------------------------------------------------- 6. SHM mode
def shm_echo_proc(cluster, ready, stop):
    quiet()
    c = so.shm_connect(cluster)
    make_echo(PingT, PongT, c)
    ready.set()
    stop.wait()
    c.close()


def bench_shm(n=3000, warmup=300):
    cluster = f"bench{os.getpid()}"
    ready, stop = mp.Event(), mp.Event()
    e = mp.Process(target=shm_echo_proc, args=(cluster, ready, stop)); e.start()
    ready.wait(10)
    with contextlib.redirect_stdout(io.StringIO()):
        c = so.shm_connect(cluster)
    ping, pong = PingT(), PongT()
    got = threading.Event()
    orig = c.callbacks["PongT"]
    c.callbacks["PongT"] = lambda msg: (orig(msg), got.set())
    time.sleep(0.2)
    rtts, lost = [], 0
    for i in range(1, n + warmup + 1):
        got.clear()
        t0 = time.perf_counter_ns()
        ping.seq = i
        if not got.wait(0.25) or pong.seq != i:
            lost += 1
            continue
        if i > warmup:
            rtts.append(time.perf_counter_ns() - t0)
    stop.set(); e.join(5); c.close()
    one_way = [r / 2 for r in rtts]
    return {"samples": len(rtts), "lost": lost, "one_way_median_us": us(statistics.median(one_way)),
            "one_way_p99_us": us(pct(one_way, 99))}


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    mp.set_start_method("spawn")
    only = sys.argv[1:] or None
    steps = [
        ("latency_tcp", lambda: bench_latency(False)),
        ("latency_udp", lambda: bench_latency(True)),
        ("latency_shm", bench_shm),
        ("throughput_tcp", bench_throughput),
        ("fanout_1", lambda: bench_fanout(1)),
        ("fanout_10", lambda: bench_fanout(10)),
        ("fanout_50", lambda: bench_fanout(50)),
        ("fanout_100", lambda: bench_fanout(100)),
        ("payloads", bench_payloads),
        ("delta_codec", bench_delta),
    ]
    env = {"python": platform.python_version(), "platform": platform.platform(), "cpus": os.cpu_count()}
    print(json.dumps({"env": env}))
    for name, fn in steps:
        if only and name not in only:
            continue
        t = time.time()
        try:
            RESULTS[name] = fn()
        except Exception as e:  # keep going, report the failure
            RESULTS[name] = {"error": repr(e)}
        print(json.dumps({name: RESULTS[name], "_s": round(time.time() - t, 1)}), flush=True)
