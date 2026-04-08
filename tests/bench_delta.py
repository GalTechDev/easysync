"""
EasySync Delta Sync Benchmark
===============================
Tests delta vs full sync across:
  - Multiple array sizes
  - Multiple modification percentages (0% to 100%)

Usage:
    python tests/bench_delta.py
"""

import sys
import os
import time
import socket
import threading
import pickle

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np

from easysync import SyncServer
from easysync.syncclient import SyncClient
from easysync.codecs import _registry

# Register numpy codec with delta support
import easysync.contrib.numpy_codec  # noqa


def get_free_port():
    while True:
        s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s1.bind(("", 0))
        port = s1.getsockname()[1]
        s1.close()
        try:
            s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s2.bind(("", port + 1))
            s2.close()
            return port
        except OSError:
            continue


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024**2:.1f} MB"
    else:
        return f"{size_bytes / 1024**3:.2f} GB"


def make_env():
    port = get_free_port()
    srv = SyncServer(port=port)
    srv.start_thread()
    time.sleep(0.3)

    cA = SyncClient(host="127.0.0.1", port=port)
    cA.connect()
    cB = SyncClient(host="127.0.0.1", port=port)
    cB.connect()

    deadline = time.time() + 3
    while (not cA.connected or not cB.connected) and time.time() < deadline:
        time.sleep(0.05)
    return srv, cA, cB


def cleanup(cA, cB):
    cA.connected = False
    cB.connected = False
    try: cA.client_socket.close()
    except: pass
    try: cB.client_socket.close()
    except: pass


def bench_single(cA, cB, arr_base, pct_modified, repeats=5):
    """Measure avg latency for sending an array with X% modified elements."""
    received_event = threading.Event()

    def on_recv(msg):
        received_event.set()

    cB.register_callback("delta_bench", on_recv)

    n = arr_base.size
    n_modified = max(1, int(n * pct_modified / 100))

    # Send the base first (primes the delta cache)
    received_event.clear()
    cA.send_update("delta_bench", "data", arr_base)
    received_event.wait(timeout=10)

    latencies = []
    for _ in range(repeats):
        # Create modified version
        modified = arr_base.copy()
        indices = np.random.choice(n, size=n_modified, replace=False)
        modified[indices] = np.random.randn(n_modified).astype(arr_base.dtype)

        received_event.clear()
        t0 = time.perf_counter()
        cA.send_update("delta_bench", "data", modified)
        got_it = received_event.wait(timeout=30)
        t1 = time.perf_counter()

        if got_it:
            latencies.append(t1 - t0)

        # Update base for next delta
        arr_base = modified

    return sum(latencies) / len(latencies) if latencies else float("inf")


def run_benchmark():
    SIZES = [
        (10_000, "80 KB"),       # 80 KB
        (100_000, "781 KB"),     # 781 KB
        (1_000_000, "7.6 MB"),   # 7.6 MB
        (4_000_000, "30.5 MB"),   # 30.5 MB
    ]
    PERCENTAGES = [0, 1, 5, 10, 25, 50, 100]
    REPEATS = 5

    print("=" * 75)
    print("  EasySync Delta Sync Benchmark")
    print("  NumPy float64 arrays — XOR + zlib delta")
    print("=" * 75)

    # ---- Run with delta sync (numpy codec registered) ----
    all_results = {}  # {(size, pct): latency_ms}

    for n_elements, size_label in SIZES:
        print(f"\n--- Array: {n_elements:,} elements ({size_label}) ---")

        srv, cA, cB = make_env()
        base = np.random.randn(n_elements).astype(np.float64)

        for pct in PERCENTAGES:
            lat = bench_single(cA, cB, base.copy(), pct, REPEATS)
            all_results[(n_elements, pct)] = lat * 1000

            # Also measure the full size for reference
            full_bytes = base.nbytes
            delta_approx = lat * 1000  # ms

            print(f"  {pct:>3}% modified: {lat*1000:>9.2f} ms")

            # Clear delta cache for next percentage test
            cA._delta_sent.clear()
            cB._delta_received.clear()

        cleanup(cA, cB)

    # ---- Also measure NO-delta baseline for comparison ----
    print("\n" + "=" * 75)
    print("  Baseline (delta disabled — full pickle every time)")
    print("=" * 75)

    # Temporarily remove numpy codec
    saved = _registry.pop("numpy.ndarray", None)

    baseline_results = {}

    for n_elements, size_label in SIZES:
        print(f"\n--- Array: {n_elements:,} elements ({size_label}) ---")

        srv, cA, cB = make_env()
        base = np.random.randn(n_elements).astype(np.float64)

        for pct in PERCENTAGES:
            lat = bench_single(cA, cB, base.copy(), pct, REPEATS)
            baseline_results[(n_elements, pct)] = lat * 1000
            print(f"  {pct:>3}% modified: {lat*1000:>9.2f} ms")

        cleanup(cA, cB)

    # Restore codec
    if saved:
        _registry["numpy.ndarray"] = saved

    # ---- Summary Table ----
    print("\n" + "=" * 75)
    print("  SUMMARY: Speedup (Delta / Baseline)")
    print("=" * 75)

    header = f"{'Size':>10} |"
    for pct in PERCENTAGES:
        header += f" {pct:>5}% |"
    print(header)
    print("-" * len(header))

    for n_elements, size_label in SIZES:
        row = f"{size_label:>10} |"
        for pct in PERCENTAGES:
            d = all_results.get((n_elements, pct), 0)
            b = baseline_results.get((n_elements, pct), 0)
            speedup = b / d if d > 0 else 0
            row += f" {speedup:>5.1f}x |"
        print(row)

    # ---- Generate Graph ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n[!] matplotlib not installed, skipping graph.")
        return

    fig, axes = plt.subplots(1, len(SIZES), figsize=(5 * len(SIZES), 6), sharey=False)
    fig.patch.set_facecolor("#0d1117")

    if len(SIZES) == 1:
        axes = [axes]

    for idx, (n_elements, size_label) in enumerate(SIZES):
        ax = axes[idx]
        ax.set_facecolor("#161b22")
        ax.tick_params(colors="#c9d1d9")
        ax.xaxis.label.set_color("#c9d1d9")
        ax.yaxis.label.set_color("#c9d1d9")
        ax.title.set_color("#e6edf3")
        for spine in ax.spines.values():
            spine.set_color("#30363d")

        delta_vals = [all_results.get((n_elements, p), 0) for p in PERCENTAGES]
        base_vals = [baseline_results.get((n_elements, p), 0) for p in PERCENTAGES]

        x = range(len(PERCENTAGES))
        ax.bar([i - 0.2 for i in x], base_vals, width=0.35, color="#f85149",
               edgecolor="#30363d", label="Full (pickle)", zorder=5)
        ax.bar([i + 0.2 for i in x], delta_vals, width=0.35, color="#3fb950",
               edgecolor="#30363d", label="Delta (XOR+zlib)", zorder=5)

        ax.set_xticks(list(x))
        ax.set_xticklabels([f"{p}%" for p in PERCENTAGES], fontsize=8)
        ax.set_xlabel("% Modified", fontsize=10, fontweight="bold")
        if idx == 0:
            ax.set_ylabel("Latency (ms)", fontsize=10, fontweight="bold")
        ax.set_title(f"{size_label}", fontsize=12, fontweight="bold", pad=10)
        ax.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9", fontsize=8)
        ax.grid(True, axis="y", alpha=0.15, color="#c9d1d9")

    fig.suptitle("EasySync — Delta Sync vs Full Sync by Modification %",
                 fontsize=15, fontweight="bold", color="#e6edf3", y=0.98)
    plt.tight_layout(rect=[0, 0, 1, 0.93])

    out = os.path.join(os.path.dirname(__file__), "..", "benchmark_delta.png")
    out = os.path.abspath(out)
    fig.savefig(out, dpi=150, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)

    print(f"\n[OK] Graph saved to: {out}")


if __name__ == "__main__":
    run_benchmark()
