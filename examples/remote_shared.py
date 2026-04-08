"""
Shared Data Structure for Remote Desktop Example
=================================================
This file defines the object that will be synchronized between
the Host (screen sender) and the Viewer (input sender).
"""

from easysync import SyncedObject
import numpy as np

# Use UDP for both frame and input updates to minimize latency
@SyncedObject(transport="udp")
class RemoteDesktopState:
    def __init__(self, width=1280, height=720):
        # We start with a black frame
        self.frame = np.zeros((height, width, 3), dtype=np.uint8)
        self.width = width
        self.height = height
        
        # A list of events to playback on the host
        # Format: ("move", x, y) or ("click", button) or ("key", key_name)
        self.last_event = None
