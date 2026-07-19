"""Headless smoke test of the full Streamlit app via AppTest.

Network calls are stubbed out: STAC search returns [], drift's fetch returns a
synthetic tile — so this validates the app wiring, all 4 tabs, and that the
original tabs still render without exceptions.
"""
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

import numpy as np

# ── Stub network-touching pieces BEFORE the app imports them ─────────────────
import sentinel_analysis as sa


class StubAnalyzer:
    def __init__(self):
        pass

    def search(self, bbox, start, end, cloud_pct=30):
        return []  # -> "No scenes found" path, no network

    def load_stack(self, *a, **k):
        raise RuntimeError("should not be called with empty search")

    cloud_mask = median_composite = load_stack
    render_rgb = render_index = render_timelapse = load_stack
    get_spectra = burn_scar_analysis = load_stack


sa.SentinelAnalyzer = StubAnalyzer
sa.fetch_weather = lambda lat, lon: None

import drift as drift_mod


def fake_fetch(self, key):
    bbox = self.bbox_for(key)
    w, s, e, n = bbox
    res = self.resolution
    nx, ny = int(round((e - w) / res)), int(round((n - s) / res))
    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)
    rgba[..., 0] = 90
    rgba[..., 1] = 140
    rgba[..., 2] = 90
    rgba[..., 3] = 255
    return {'empty': False, 'rgba': rgba, 'bounds': [s, w, n, e]}


drift_mod.DriftEngine._fetch = fake_fetch

from streamlit.testing.v1 import AppTest

at = AppTest.from_file(
    str(__import__("pathlib").Path(__file__).resolve().parent / "app.py"),
    default_timeout=15,
)
at.run()

assert not at.exception, f"App raised: {at.exception}"
print("PASS  app runs with no exceptions")

assert len(at.tabs) == 4, f"expected 4 tabs, got {len(at.tabs)}"
print("PASS  4 tabs render")

# Original tabs still show their expected widgets
labels = [b.label for b in at.button]
assert any("Load Data" in l for l in labels)
assert any("Create Timelapse" in l for l in labels)
assert any("Analyze Burn Scar" in l for l in labels)
print("PASS  original 3 tabs' primary buttons present")

# Drift widgets present
assert any("Start" in l or "Pause" in l for l in labels)
assert any("Record" in l for l in labels)
assert any("Export GIF" in l for l in labels)
assert any("Reset" in l for l in labels)
print("PASS  Drift controls present (Start / Record / Export GIF / Reset)")

# Press Start — drift begins; app should rerun-loop without exception.
start_btn = next(b for b in at.button if "Start" in b.label)
start_btn.click()
try:
    at.run()
except Exception as e:  # rerun-loop may hit AppTest's timeout by design
    print(f"note: rerun loop interrupted as expected ({type(e).__name__})")
assert not at.exception, f"Drift start raised: {at.exception}"
print("PASS  Drift Start clicked, no exceptions")

print("\nAPP SMOKE TEST PASSED")
