"""
Remote Desktop - HOST Side
==========================
1. Captures the physical screen using MSS.
2. Synchronizes the frame to the network via EasySync.
3. Receives input events and executes them via PyAutoGUI.

Usage:
    python examples/remote_host.py
"""

import sys
import os
import time
import cv2
import mss
import numpy as np
import pyautogui

# Ensure easysync is in the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from easysync import SyncServer, connect
from examples.remote_shared import RemoteDesktopState

# Performance tuning for PyAutoGUI
pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True  # Move mouse to top-left to abort

# Security Key
SECRET_KEY = "super-secret-password"

def run_host():
    # 1. Start the Relay Server
    print("[Host] Starting SyncServer on port 5000...")
    server = SyncServer(port=5000)
    
    # Register authentication handler
    @server.on_auth
    def check_auth(addr, payload):
        print(f"[Host] Auth attempt from {addr}")
        print(f"       Expected: '{SECRET_KEY}'")
        print(f"       Received: '{payload}'")
        is_ok = (payload == SECRET_KEY)
        print(f"       Result: {'SUCCESS' if is_ok else 'FAILED'}")
        return is_ok

    server.start_thread()
    time.sleep(1)

    # 2. Connect as a client (pass the key)
    client = connect("127.0.0.1", 5000, auth_payload=SECRET_KEY)
    
    # 3. Create the shared state
    # We use a downscaled resolution for better performance over the network
    # (Delta Sync works better with less data)
    TARGET_WIDTH = 1280
    TARGET_HEIGHT = 720
    state = RemoteDesktopState(width=TARGET_WIDTH, height=TARGET_HEIGHT, _sync_client=client)

    print(f"[Host] Streaming screen ({TARGET_WIDTH}x{TARGET_HEIGHT}) and listening for inputs...")

    with mss.mss() as sct:
        # Get the primary monitor
        monitor = sct.monitors[1]
        orig_w, orig_h = monitor["width"], monitor["height"]

        def handle_input(msg):
            # Check if it's an input update
            if msg.get("attr_name") == "last_event":
                event = msg.get("value")
                if not event: return
                
                # event format: (type, x, y, button/key)
                etype = event[0]
                if etype == "move":
                    # Scale coordinates from viewer resolution back to physical resolution
                    vx, vy = event[1], event[2]
                    px = int(vx * orig_w / TARGET_WIDTH)
                    py = int(vy * orig_h / TARGET_HEIGHT)
                    pyautogui.moveTo(px, py)
                elif etype == "click":
                    btn = event[3]
                    pyautogui.click(button=btn)
                elif etype == "key":
                    key = event[3]
                    try:
                        pyautogui.press(key)
                    except:
                        pass

        # Listen for updates to last_event
        client.register_callback(RemoteDesktopState.__name__, handle_input)

        while True:
            t0 = time.time()
            
            # --- 1. Screen Capture ---
            # Capture the screen (BGRA)
            img = np.array(sct.grab(monitor))
            
            # --- 2. Post-Processing ---
            # Convert BGRA to BGR
            frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            # Resize
            frame = cv2.resize(frame, (TARGET_WIDTH, TARGET_HEIGHT), interpolation=cv2.INTER_AREA)
            
            # --- 3. Sync ---
            # This triggers the Delta Sync + UDP broadcast
            state.frame = frame
            
            # Limit to ~20 FPS to keep CPU/Network usage reasonable
            elapsed = time.time() - t0
            sleep_time = max(0, (1.0 / 20.0) - elapsed)
            time.sleep(sleep_time)

if __name__ == "__main__":
    try:
        run_host()
    except KeyboardInterrupt:
        print("\n[Host] Stopped.")
