import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from easysync import SyncedObject, SyncServer, connect

try:
    import numpy as np
    import matplotlib.pyplot as plt
    from sklearn.linear_model import SGDClassifier
    from sklearn.datasets import make_classification
except ImportError:
    print("Error: install scikit-learn, matplotlib and numpy")
    sys.exit(1)


def main():
    is_server = len(sys.argv) > 1 and sys.argv[1] == "server"

    if is_server:
        server = SyncServer(port=5000)
        server.start_thread()
        time.sleep(0.5)

    client = connect("127.0.0.1", 5000)

    @SyncedObject(client)
    class MLState:
        def __init__(self):
            self.weights = np.zeros((1, 2))
            self.bias = np.zeros(1)
            self.epoch = 0

    state = MLState()
    X, y = make_classification(n_samples=200, n_features=2, n_redundant=0, n_clusters_per_class=1, random_state=42)

    if is_server:
        model = SGDClassifier(loss="log_loss", max_iter=1, warm_start=True, learning_rate="constant", eta0=0.01)
        model.fit(X, y)

        while True:
            model.fit(X, y)
            state.weights = model.coef_.copy()
            state.bias = model.intercept_.copy()
            state.epoch += 1
            print(f"Epoch {state.epoch}")
            time.sleep(0.5)
    else:
        plt.ion()
        fig, ax = plt.subplots(figsize=(6, 6))
        scatter = ax.scatter(X[:, 0], X[:, 1], c=y, cmap="bwr", alpha=0.5)
        line, = ax.plot([], [], "k-", lw=3)
        ax.set_xlim(-3, 3)
        ax.set_ylim(-3, 3)

        while True:
            if state.epoch > 0:
                ax.set_title(f"Decision boundary — Epoch: {state.epoch}")
                w = state.weights[0]
                b = state.bias[0]
                if w[1] != 0:
                    x_vals = np.array([-3, 3])
                    y_vals = -(w[0] * x_vals + b) / w[1]
                    line.set_data(x_vals, y_vals)
            plt.pause(0.1)


if __name__ == "__main__":
    main()
