"""
EasySync Stress Test & Latency Benchmark
=========================================
Measures round-trip synchronization latency across different payload sizes.
Generates a graph showing latency vs data size.

Usage:
    python tests/test_stress.py
"""

import sys
import os
import time
import socket
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from easysync import SyncServer, SyncedObject
from easysync.syncclient import SyncClient
from easysync.syncedobject import _unproxy


def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def format_size(size_bytes):
    """Human-readable size string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024**2:.1f} MB"
    else:
        return f"{size_bytes / 1024**3:.2f} GB"


def run_benchmark():
    # ------------------------------------------------------------------
    # Data size tiers to benchmark (in bytes)
    # ------------------------------------------------------------------
    SIZE_TIERS = [
        64,              # 64 B   - tiny (score increment)
        512,             # 512 B  - small dict
        4 * 1024,        # 4 KB   - medium object
        64 * 1024,       # 64 KB  - large object
        256 * 1024,      # 256 KB
        1 * 1024**2,     # 1 MB
        4 * 1024**2,     # 4 MB
        16 * 1024**2,    # 16 MB
        64 * 1024**2,    # 64 MB
        128 * 1024**2,   # 128 MB
        256 * 1024**2,   # 256 MB
    ]

    REPEATS = 3  # Number of repetitions per tier

    print("=" * 60)
    print("  EasySync Stress Test & Latency Benchmark")
    print("=" * 60)
    print()

    # ------------------------------------------------------------------
    # 1. Start server
    # ------------------------------------------------------------------
    port = get_free_port()
    server = SyncServer(port=port)
    server.start_thread()
    time.sleep(0.5)

    # ------------------------------------------------------------------
    # 2. Connect two clients (A = sender, B = receiver)
    # ------------------------------------------------------------------
    clientA = SyncClient(host="127.0.0.1", port=port)
    clientA.connect()

    clientB = SyncClient(host="127.0.0.1", port=port)
    clientB.connect()

    # Wait for both to connect
    deadline = time.time() + 5
    while (not clientA.connected or not clientB.connected) and time.time() < deadline:
        time.sleep(0.05)

    if not clientA.connected or not clientB.connected:
        print("ERROR: Could not connect both clients!")
        return

    print(f"[OK] Both clients connected on port {port}")
    print()

    # ------------------------------------------------------------------
    # 3. Direct low-level benchmark (bypass proxy, use raw callbacks)
    #    This measures the true network round-trip without proxy overhead.
    # ------------------------------------------------------------------
    results = []  # [(size_bytes, latency_seconds), ...]
    received_event = threading.Event()
    received_size = [0]

    def on_receive(msg):
        val = msg.get("value")
        if isinstance(val, bytes):
            received_size[0] = len(val)
        received_event.set()

    clientB.register_callback("bench", on_receive)

    print(f"{'Size':>12}  {'Latency':>12}  {'Throughput':>14}  {'Status'}")
    print("-" * 58)

    for target_size in SIZE_TIERS:
        payload = b"X" * target_size
        latencies = []
        failed = False

        for attempt in range(REPEATS):
            received_event.clear()
            received_size[0] = 0

            # Time the send -> receive round trip
            t0 = time.perf_counter()
            clientA.send_update("bench", "data", payload)
            got_it = received_event.wait(timeout=120)  # 2 min max
            t1 = time.perf_counter()

            if not got_it:
                failed = True
                break

            latencies.append(t1 - t0)

        if failed:
            print(f"{format_size(target_size):>12}  {'TIMEOUT':>12}  {'N/A':>14}  FAILED")
            break
        else:
            avg_lat = sum(latencies) / len(latencies)
            throughput = target_size / avg_lat if avg_lat > 0 else 0
            results.append((target_size, avg_lat))
            print(f"{format_size(target_size):>12}  {avg_lat*1000:>9.2f} ms  {format_size(int(throughput))+'/s':>14}  OK")

    print()
    print("=" * 58)

    # ------------------------------------------------------------------
    # 4. Cleanup
    # ------------------------------------------------------------------
    clientA.connected = False
    clientB.connected = False
    try:
        clientA.client_socket.close()
    except:
        pass
    try:
        clientB.client_socket.close()
    except:
        pass

    # ------------------------------------------------------------------
    # 5. Generate graph
    # ------------------------------------------------------------------
    if not results:
        print("No results to plot.")
        return

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker
    except ImportError:
        print("\n[!] matplotlib not installed. Skipping graph generation.")
        print("    Install with: pip install matplotlib")
        return

    sizes = [r[0] for r in results]
    latencies_ms = [r[1] * 1000 for r in results]
    throughputs = [r[0] / r[1] / (1024**2) for r in results]  # MB/s

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

    # --- Left plot: Latency ---
    ax1.plot(sizes, latencies_ms, "o-", color="#58a6ff", linewidth=2.5,
             markersize=8, markerfacecolor="#1f6feb", markeredgecolor="white",
             markeredgewidth=1.5, zorder=5)
    ax1.fill_between(sizes, latencies_ms, alpha=0.15, color="#58a6ff")
    ax1.set_xscale("log")
    ax1.set_yscale("log")
    ax1.set_xlabel("Payload Size", fontsize=12, fontweight="bold")
    ax1.set_ylabel("Latency (ms)", fontsize=12, fontweight="bold")
    ax1.set_title("Sync Latency vs Payload Size", fontsize=14, fontweight="bold", pad=15)
    ax1.grid(True, alpha=0.15, color="#c9d1d9")

    # Custom tick labels
    ax1.set_xticks(sizes)
    ax1.set_xticklabels([format_size(s) for s in sizes], rotation=45, ha="right", fontsize=9)
    ax1.xaxis.set_minor_locator(ticker.NullLocator())

    # --- Right plot: Throughput ---
    colors = ["#3fb950" if tp > 10 else "#f0883e" if tp > 1 else "#f85149" for tp in throughputs]
    bars = ax2.bar(range(len(sizes)), throughputs, color=colors, edgecolor="#30363d",
                   linewidth=1.5, width=0.7, zorder=5)
    ax2.set_xticks(range(len(sizes)))
    ax2.set_xticklabels([format_size(s) for s in sizes], rotation=45, ha="right", fontsize=9)
    ax2.set_xlabel("Payload Size", fontsize=12, fontweight="bold")
    ax2.set_ylabel("Throughput (MB/s)", fontsize=12, fontweight="bold")
    ax2.set_title("Effective Throughput", fontsize=14, fontweight="bold", pad=15)
    ax2.grid(True, axis="y", alpha=0.15, color="#c9d1d9")

    # Value labels on bars
    for bar, tp in zip(bars, throughputs):
        label = f"{tp:.1f}" if tp >= 1 else f"{tp:.2f}"
        ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(throughputs) * 0.02,
                 label, ha="center", va="bottom", fontsize=8, color="#c9d1d9", fontweight="bold")

    fig.suptitle("EasySync — Local Stress Test Benchmark",
                 fontsize=16, fontweight="bold", color="#e6edf3", y=0.98)

    plt.tight_layout(rect=[0, 0, 1, 0.93])

    output_path = os.path.join(os.path.dirname(__file__), "..", "benchmark_results.png")
    output_path = os.path.abspath(output_path)
    fig.savefig(output_path, dpi=150, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)

    print(f"\n[OK] Graph saved to: {output_path}")


if __name__ == "__main__":
    run_benchmark()
