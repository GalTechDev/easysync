"""
Remote Desktop - VIEWER Side
============================
1. Connects to the host.
2. Displays the remote screen frame in a Pygame window.
3. Captures mouse/keyboard inputs and syncs them back to the host.

Usage:
    python examples/remote_viewer.py
"""

import sys
import os
import time
import pygame
import numpy as np

# Ensure easysync is in the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from easysync import connect
from examples.remote_shared import RemoteDesktopState

def run_viewer():
    # Initialize Pygame
    pygame.init()
    
    # 1. Connect to the host (with the secret key)
    SECRET_KEY = "super-secret-password"
    client = connect("127.0.0.1", 5000, auth_payload=SECRET_KEY)
    
    # 2. Bind the shared state
    state = RemoteDesktopState(_sync_client=client)
    
    # Wait for the first frame to determine window size
    print("[Viewer] Waiting for connection and first frame...")
    start_wait = time.time()
    while state.frame is None or not client.connected:
        pygame.time.delay(100)
        
        # Check if we were rejected
        if not client.connected and time.time() - start_wait > 2.0:
            # Check terminal for auth errors
            pass 
            
        # Keep pygame alive
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                return

        # If connected but no frame yet, give it a bit
        if client.connected and time.time() - start_wait > 10.0:
            print("[Viewer] Still no frame received. Check if Host is sending data.")
            start_wait = time.time() # Reset warning
        
    screen = pygame.display.set_mode((state.width, state.height))
    pygame.display.set_caption("EasySync Remote Desktop")
    clock = pygame.time.Clock()
    
    # Font for stats
    font = pygame.font.SysFont("Consolas", 18)
    last_ping = 0
    last_bw_check = time.time()
    last_bytes_recv = 0
    kb_per_sec = 0

    print("[Viewer] Keyboard/Mouse inputs captured and sent to Host.")

    running = True
    while running:
        now = time.time()
        
        # Ping every second
        if now - last_ping > 1.0:
            client.ping()
            last_ping = now
            
        # Calc bandwidth every second
        if now - last_bw_check > 1.0:
            diff = client.stats["bytes_recv"] - last_bytes_recv
            kb_per_sec = diff / 1024.0
            last_bytes_recv = client.stats["bytes_recv"]
            last_bw_check = now

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            
            # --- Input Capture ---
            if event.type == pygame.MOUSEMOTION:
                # Send mouse position
                x, y = event.pos
                state.last_event = ("move", x, y)
                
            elif event.type == pygame.MOUSEBUTTONDOWN:
                button = "left" if event.button == 1 else "right" if event.button == 3 else "middle"
                state.last_event = ("click", 0, 0, button)
                
            elif event.type == pygame.KEYDOWN:
                # Map pygame keys to pyautogui strings
                key_name = pygame.key.name(event.key)
                state.last_event = ("key", 0, 0, key_name)

        # --- Render ---
        if state.frame is not None:
            # 1. BGR to RGB
            rgb_frame = state.frame[:, :, ::-1]
            
            # 2. NumPy to Surface
            surf_frame = pygame.surfarray.make_surface(np.transpose(rgb_frame, (1, 0, 2)))
            
            # 3. Blit
            screen.blit(surf_frame, (0, 0))
            
            # --- Stats Overlay ---
            stats_texts = [
                f"Latency: {client.stats['latency_ms']} ms",
                f"Download: {kb_per_sec:.1f} KB/s",
                f"Packets In: {client.stats['packets_recv']}",
            ]
            
            for i, text in enumerate(stats_texts):
                shadow = font.render(text, True, (0, 0, 0))
                label = font.render(text, True, (0, 255, 0))
                screen.blit(shadow, (12, 12 + i*22))
                screen.blit(label, (10, 10 + i*22))
            
        pygame.display.flip()
        clock.tick(30) # 30 FPS display

    pygame.quit()

if __name__ == "__main__":
    try:
        run_viewer()
    except KeyboardInterrupt:
        print("\n[Viewer] Stopped.")
