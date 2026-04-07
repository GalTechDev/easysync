import sys
import os
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from easysync import SyncedObject, SyncServer, connect

try:
    import numpy as np
except ImportError:
    print("Error: install numpy (pip install numpy)")
    sys.exit(1)


def main():
    is_server = len(sys.argv) > 1 and sys.argv[1] == "server"

    if is_server:
        server = SyncServer(port=5000)
        server.start_thread()
        time.sleep(0.5)

    client = connect("127.0.0.1", 5000)
    worker_id = f"worker_{uuid.uuid4().hex[:4]}" if not is_server else "orchestrator"

    @SyncedObject(client)
    class FederatedBrain:
        def __init__(self):
            self.global_weights = np.array([0.0, 0.0])
            self.epoch = 0
            self.client_gradients = {}

    brain = FederatedBrain()

    if is_server:
        lr = 0.05
        while True:
            if len(brain.client_gradients) > 0:
                grads = list(brain.client_gradients.values())
                avg = np.mean(grads, axis=0)
                new_w = brain.global_weights - lr * avg
                brain.global_weights = new_w
                print(f"Epoch {brain.epoch} — m={new_w[0]:.3f}, c={new_w[1]:.3f}")
                brain.client_gradients.clear()
                brain.epoch += 1
            time.sleep(2)
    else:
        np.random.seed()
        my_x = np.random.uniform(-10, 10, 1000)
        my_y = 3.0 * my_x + 5.0 + np.random.normal(0, 5, 1000)
        last_epoch = -1

        while True:
            if brain.epoch > last_epoch:
                m, c = brain.global_weights
                pred = m * my_x + c
                err = pred - my_y
                dm = np.mean(2 * err * my_x)
                dc = np.mean(2 * err)
                time.sleep(np.random.uniform(0.5, 2.0))
                brain.client_gradients[worker_id] = np.array([dm, dc])
                last_epoch = brain.epoch
            time.sleep(0.1)


if __name__ == "__main__":
    main()
