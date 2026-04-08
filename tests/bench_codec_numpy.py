"""
EasySync Benchmark: NumPy Codec vs Default Pickle
===================================================
Compares round-trip sync latency for NumPy arrays using:
  1. Default pickle serialization (no codec)
  2. Custom NumPy codec (tobytes/frombuffer)

Usage:
    python tests/bench_codec_numpy.py
"""

import sys
import os
import time
import socket
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from easysync import SyncServer
from easysync.syncclient import SyncClient
from easysync.codecs import _registry

import numpy as np


def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024**2:.1f} MB"
    else:
        return f"{size_bytes / 1024**3:.2f} GB"


def run_single_bench(label, clientA, clientB, arrays, repeats=3):
    """Run a benchmark pass and return [(array_bytes, avg_latency_s), ...]"""
    results = []
    received_event = threading.Event()

    def on_receive(msg):
        received_event.set()

    clientB.register_callback("bench", on_receive)

    for arr in arrays:
        arr_bytes = arr.nbytes
        latencies = []

        for _ in range(repeats):
            received_event.clear()
            t0 = time.perf_counter()
            clientA.send_update("bench", "data", arr)
            got_it = received_event.wait(timeout=30)
            t1 = time.perf_counter()

            if not got_it:
                latencies.append(float("inf"))
            else:
                latencies.append(t1 - t0)

        avg = sum(latencies) / len(latencies)
        results.append((arr_bytes, avg))

    return results


def run_benchmark():
    # Array sizes to test
    SHAPES = [
        (100,),             #    800 B   (100 floats)
        (1_000,),           #   ~8 KB
        (10_000,),          #  ~80 KB
        (100_000,),         # ~800 KB
        (1_000_000,),       #  ~8 MB
        (4_000_000,),       # ~32 MB
        (16_000_000,),      # ~128 MB
    ]
    REPEATS = 3

    arrays = [np.random.randn(*s).astype(np.float64) for s in SHAPES]

    print("=" * 70)
    print("  EasySync Benchmark: NumPy Codec vs Default Pickle")
    print("=" * 70)
    print()

    # ---- Pass 1: Default Pickle (no codec) ----
    print("[Pass 1/2] Default Pickle serialization...")

    # Make sure no numpy codec is registered
    _registry.pop("numpy.ndarray", None)

    port1 = get_free_port()
    srv1 = SyncServer(port=port1)
    srv1.start_thread()
    time.sleep(0.3)

    cA1 = SyncClient(host="127.0.0.1", port=port1)
    cA1.connect()
    cB1 = SyncClient(host="127.0.0.1", port=port1)
    cB1.connect()
    deadline = time.time() + 3
    while (not cA1.connected or not cB1.connected) and time.time() < deadline:
        time.sleep(0.05)

    pickle_results = run_single_bench("Pickle", cA1, cB1, arrays, REPEATS)

    cA1.connected = False
    cB1.connected = False
    try: cA1.client_socket.close()
    except: pass
    try: cB1.client_socket.close()
    except: pass

    print("  Done.\n")

    # ---- Pass 2: NumPy Codec ----
    print("[Pass 2/2] NumPy Codec (tobytes/frombuffer)...")

    # Register the numpy codec
    import easysync.contrib.numpy_codec  # noqa

    port2 = get_free_port()
    srv2 = SyncServer(port=port2)
    srv2.start_thread()
    time.sleep(0.3)

    cA2 = SyncClient(host="127.0.0.1", port=port2)
    cA2.connect()
    cB2 = SyncClient(host="127.0.0.1", port=port2)
    cB2.connect()
    deadline = time.time() + 3
    while (not cA2.connected or not cB2.connected) and time.time() < deadline:
        time.sleep(0.05)

    codec_results = run_single_bench("Codec", cA2, cB2, arrays, REPEATS)

    cA2.connected = False
    cB2.connected = False
    try: cA2.client_socket.close()
    except: pass
    try: cB2.client_socket.close()
    except: pass

    # ---- Print Results ----
    print()
    print(f"{'Array Size':>12}  {'Pickle':>12}  {'Codec':>12}  {'Speedup':>10}")
    print("-" * 52)

    for (size, lat_p), (_, lat_c) in zip(pickle_results, codec_results):
        speedup = lat_p / lat_c if lat_c > 0 else 0
        color_marker = "▲" if speedup > 1.0 else "▼"
        print(
            f"{format_size(size):>12}  "
            f"{lat_p*1000:>9.2f} ms  "
            f"{lat_c*1000:>9.2f} ms  "
            f"{color_marker} {speedup:.2f}x"
        )

    print()
    print("=" * 52)

    # ---- Generate Graph ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker
    except ImportError:
        print("[!] matplotlib not installed, skipping graph.")
        return

    sizes = [r[0] for r in pickle_results]
    pickle_ms = [r[1] * 1000 for r in pickle_results]
    codec_ms = [r[1] * 1000 for r in codec_results]
    speedups = [p / c if c > 0 else 1 for p, c in zip(pickle_ms, codec_ms)]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))
    fig.patch.set_facecolor("#0d1117")

    for ax in (ax1, ax2):
        ax.set_facecolor("#161b22")
        ax.tick_params(colors="#c9d1d9")
        ax.xaxis.label.set_color("#c9d1d9")
        ax.yaxis.label.set_color("#c9d1d9")
        ax.title.set_color("#e6edf3")
        for spine in ax.spines.values():
            spine.set_color("#30363d")

    x_labels = [format_size(s) for s in sizes]

    # ---- Left: Latency comparison ----
    ax1.plot(range(len(sizes)), pickle_ms, "o-", color="#f85149", linewidth=2.5,
             markersize=8, label="Pickle (default)", zorder=5)
    ax1.plot(range(len(sizes)), codec_ms, "s-", color="#58a6ff", linewidth=2.5,
             markersize=8, label="NumPy Codec", zorder=5)
    ax1.set_yscale("log")
    ax1.set_xticks(range(len(sizes)))
    ax1.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=9)
    ax1.set_xlabel("NumPy Array Size", fontsize=12, fontweight="bold")
    ax1.set_ylabel("Latency (ms, log scale)", fontsize=12, fontweight="bold")
    ax1.set_title("Sync Latency: Pickle vs NumPy Codec", fontsize=14, fontweight="bold", pad=15)
    ax1.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9", fontsize=11)
    ax1.grid(True, alpha=0.15, color="#c9d1d9")

    # ---- Right: Speedup bars ----
    colors = ["#3fb950" if s > 1 else "#f85149" for s in speedups]
    bars = ax2.bar(range(len(sizes)), speedups, color=colors, edgecolor="#30363d",
                   linewidth=1.5, width=0.6, zorder=5)
    ax2.axhline(y=1.0, color="#8b949e", linestyle="--", linewidth=1, alpha=0.7)
    ax2.set_xticks(range(len(sizes)))
    ax2.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=9)
    ax2.set_xlabel("NumPy Array Size", fontsize=12, fontweight="bold")
    ax2.set_ylabel("Speedup (Codec / Pickle)", fontsize=12, fontweight="bold")
    ax2.set_title("Codec Speedup Factor", fontsize=14, fontweight="bold", pad=15)
    ax2.grid(True, axis="y", alpha=0.15, color="#c9d1d9")

    for bar, sp in zip(bars, speedups):
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(speedups) * 0.02,
                 f"{sp:.2f}x", ha="center", va="bottom", fontsize=9, color="#c9d1d9", fontweight="bold")

    fig.suptitle("EasySync — NumPy Codec vs Pickle Benchmark",
                 fontsize=16, fontweight="bold", color="#e6edf3", y=0.98)
    plt.tight_layout(rect=[0, 0, 1, 0.93])

    output_path = os.path.join(os.path.dirname(__file__), "..", "benchmark_codec_numpy.png")
    output_path = os.path.abspath(output_path)
    fig.savefig(output_path, dpi=150, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)

    print(f"\n[OK] Graph saved to: {output_path}")


if __name__ == "__main__":
    run_benchmark()
