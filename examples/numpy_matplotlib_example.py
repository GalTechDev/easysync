import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from easysync import SyncedObject, SyncServer, connect

try:
    import numpy as np
    import matplotlib.pyplot as plt
except ImportError:
    print("Error: install numpy and matplotlib (pip install numpy matplotlib)")
    sys.exit(1)


def main():
    is_server = len(sys.argv) > 1 and sys.argv[1] == "server"

    if is_server:
        server = SyncServer(port=5000)
        server.start_thread()
        time.sleep(0.5)

    client = connect("127.0.0.1", 5000)

    @SyncedObject(client)
    class ScientificData:
        def __init__(self):
            self.x = np.linspace(0, 4 * np.pi, 500)
            self.y = np.zeros(500)
            self.phase = 0.0
            self.timestamp = time.time()

    data = ScientificData()

    plt.ion()
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.set_title("Real-time data (NumPy)")
    line, = ax.plot(data.x, data.y, color="red")
    ax.set_ylim(-2, 2)

    while True:
        if is_server:
            data.phase += 0.2
            data.timestamp = time.time()
            data.y = np.sin(data.x + data.phase) * np.cos(data.x * 0.5) + np.random.normal(0, 0.1, 500)

        line.set_ydata(data.y)

        if not is_server:
            latency_ms = (time.time() - data.timestamp) * 1000
            ax.set_title(f"Client reception — Latency: {latency_ms:.1f} ms")

        plt.pause(0.05)


if __name__ == "__main__":
    main()
