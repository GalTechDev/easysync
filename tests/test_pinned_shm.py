import sys
import os
import ctypes

BASE_DIR = r"c:\Users\Maxence\Downloads\easysync-main\easysync-main"
SHM_DIR = r"c:\Users\Maxence\Downloads\easyshm"
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, SHM_DIR)

from easyshm import EasySHM

def test_basic():
    print("Testing Standard Memory...")
    shm = EasySHM("test_basic", size=1024)
    shm.write(b"hello")
    got = shm.read(5)
    print(f"  Read: {got}")
    assert got == b"hello"
    shm.destroy()

def test_pinning():
    print("Testing Memory Pinning (VirtualLock)...")
    SIZE = 10 * 1024 * 1024 # 10 MB
    
    try:
        # Test creation with pinning
        shm = EasySHM("test_pinned", size=SIZE, pinned=True)
        print(f"  [PASS] Created 10MB Pinned SHM segment '{shm.name}'")
        
        # Verify it works
        shm.write(b"data" * 100, 0)
        got = shm.read(4, 0)
        print(f"  Read: {got}")
        assert got == b"data"
        print("  [PASS] Read/Write verified on pinned memory")
        
        shm.destroy()
    except Exception as e:
        print(f"  [FAIL] Pinning failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

def test_fallback():
    print("\nTesting Pinning Fallback (Warning Mode)...")
    try:
        # Test with impossible size (e.g. 100 GB) to trigger failure
        # Note: on Windows SetProcessWorkingSetSize might fail if it's too big
        shm_huge = EasySHM("test_huge", size=100 * 1024 * 1024 * 1024, pinned=True, on_pin_fail="warn")
        print(f"  [PASS] Handled huge segment with 'warn' mode")
        shm_huge.destroy()
    except Exception as e:
        print(f"  [FAIL] Fallback mode errored instead of warning: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_basic()
    test_pinning()
    test_fallback()
