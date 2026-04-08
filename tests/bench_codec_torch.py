"""
EasySync Benchmark: Torch Codec vs Default Pickle
===================================================
Compares round-trip sync latency for PyTorch tensors using:
  1. Default pickle serialization (no codec)
  2. Custom Torch codec (torch.save/torch.load)

Usage:
    python tests/bench_codec_torch.py
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

import torch


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


def run_single_bench(clientA, clientB, tensors, repeats=3):
    """Run a benchmark pass and return [(tensor_bytes, avg_latency_s), ...]"""
    results = []
    received_event = threading.Event()

    def on_receive(msg):
        received_event.set()

    clientB.register_callback("bench", on_receive)

    for tensor in tensors:
        tensor_bytes = tensor.nelement() * tensor.element_size()
        latencies = []

        for _ in range(repeats):
            received_event.clear()
            t0 = time.perf_counter()
            clientA.send_update("bench", "weights", tensor)
            got_it = received_event.wait(timeout=60)
            t1 = time.perf_counter()

            if not got_it:
                latencies.append(float("inf"))
            else:
                latencies.append(t1 - t0)

        avg = sum(latencies) / len(latencies)
        results.append((tensor_bytes, avg))

    return results


def make_clients(port):
    """Create and connect a pair of clients."""
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


def cleanup_clients(cA, cB):
    cA.connected = False
    cB.connected = False
    try: cA.client_socket.close()
    except: pass
    try: cB.client_socket.close()
    except: pass


def run_benchmark():
    # Tensor shapes to test (float32 = 4 bytes per element)
    SHAPES = [
        (100,),               #    400 B
        (1_000,),             #   ~4 KB
        (10_000,),            #  ~40 KB
        (100_000,),           # ~400 KB
        (1_000_000,),         #  ~4 MB
        (4_000_000,),         # ~16 MB
        (16_000_000,),        # ~64 MB
        (32_000_000,),        # ~128 MB
    ]
    REPEATS = 10

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tensors = [torch.randn(*s).to(device) for s in SHAPES]

    print("=" * 70)
    print("  EasySync Benchmark: Torch Codec vs Default Pickle")
    print(f"  PyTorch {torch.__version__} | {device.upper()}")
    print("=" * 70)
    print()

    # ---- Pass 1: Default Pickle ----
    print("[Pass 1/2] Default Pickle serialization...")
    _registry.pop("torch.Tensor", None)

    port1 = get_free_port()
    srv1, cA1, cB1 = make_clients(port1)
    pickle_results = run_single_bench(cA1, cB1, tensors, REPEATS)
    cleanup_clients(cA1, cB1)
    print("  Done.\n")

    # ---- Pass 2: Torch Codec ----
    print("[Pass 2/2] Torch Codec (torch.save/torch.load)...")
    import easysync.contrib.torch_codec  # noqa — auto-registers

    port2 = get_free_port()
    srv2, cA2, cB2 = make_clients(port2)
    codec_results = run_single_bench(cA2, cB2, tensors, REPEATS)
    cleanup_clients(cA2, cB2)
    print("  Done.\n")

    # ---- Print Results Table ----
    print(f"{'Tensor Size':>12}  {'Pickle':>12}  {'Codec':>12}  {'Speedup':>10}")
    print("-" * 52)

    for (size, lat_p), (_, lat_c) in zip(pickle_results, codec_results):
        speedup = lat_p / lat_c if lat_c > 0 else 0
        marker = "▲" if speedup > 1.0 else "▼"
        print(
            f"{format_size(size):>12}  "
            f"{lat_p*1000:>9.2f} ms  "
            f"{lat_c*1000:>9.2f} ms  "
            f"{marker} {speedup:.2f}x"
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
    ax1.plot(range(len(sizes)), codec_ms, "s-", color="#da70d6", linewidth=2.5,
             markersize=8, label="Torch Codec", zorder=5)
    ax1.set_yscale("log")
    ax1.set_xticks(range(len(sizes)))
    ax1.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=9)
    ax1.set_xlabel("Tensor Size (float32)", fontsize=12, fontweight="bold")
    ax1.set_ylabel("Latency (ms, log scale)", fontsize=12, fontweight="bold")
    ax1.set_title("Sync Latency: Pickle vs Torch Codec", fontsize=14, fontweight="bold", pad=15)
    ax1.legend(facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9", fontsize=11)
    ax1.grid(True, alpha=0.15, color="#c9d1d9")

    # ---- Right: Speedup bars ----
    colors = ["#3fb950" if s > 1 else "#f85149" for s in speedups]
    bars = ax2.bar(range(len(sizes)), speedups, color=colors, edgecolor="#30363d",
                   linewidth=1.5, width=0.6, zorder=5)
    ax2.axhline(y=1.0, color="#8b949e", linestyle="--", linewidth=1, alpha=0.7)
    ax2.set_xticks(range(len(sizes)))
    ax2.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=9)
    ax2.set_xlabel("Tensor Size (float32)", fontsize=12, fontweight="bold")
    ax2.set_ylabel("Speedup (Codec / Pickle)", fontsize=12, fontweight="bold")
    ax2.set_title("Torch Codec Speedup Factor", fontsize=14, fontweight="bold", pad=15)
    ax2.grid(True, axis="y", alpha=0.15, color="#c9d1d9")

    for bar, sp in zip(bars, speedups):
        label = f"{sp:.2f}x"
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(speedups) * 0.02,
                 label, ha="center", va="bottom", fontsize=9, color="#c9d1d9", fontweight="bold")

    fig.suptitle(f"EasySync — Torch Codec vs Pickle Benchmark (PyTorch {torch.__version__})",
                 fontsize=16, fontweight="bold", color="#e6edf3", y=0.98)
    plt.tight_layout(rect=[0, 0, 1, 0.93])

    output_path = os.path.join(os.path.dirname(__file__), "..", "benchmark_codec_torch.png")
    output_path = os.path.abspath(output_path)
    fig.savefig(output_path, dpi=150, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)

    print(f"\n[OK] Graph saved to: {output_path}")


if __name__ == "__main__":
    run_benchmark()
