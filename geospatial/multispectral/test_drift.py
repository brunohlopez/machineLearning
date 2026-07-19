"""Unit tests for the Drift engine using a fake analyzer (no network)."""
import io
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

import numpy as np
from PIL import Image


class FakeAnalyzer:
    """Generates synthetic gradient tiles instantly via the drift_tile API."""

    def __init__(self, empty_keys=None):
        self.empty_bboxes = empty_keys or set()
        self.fetch_count = 0

    def drift_tile(self, bbox, start, end, cloud, resolution=0.001,
                   gap_fill=True, stretch=None, max_scenes=4):
        self.fetch_count += 1
        key = (round(bbox[0], 4), round(bbox[1], 4))
        if key in self.empty_bboxes:
            return None
        w, s, e, n = bbox
        nx = int(round((e - w) / resolution))
        ny = int(round((n - s) / resolution))
        # Gradient keyed to position so every tile looks different
        xx, yy = np.meshgrid(np.linspace(0, 255, nx), np.linspace(0, 255, ny))
        r = ((xx + abs(w) * 100) % 256).astype(np.uint8)
        g = ((yy + abs(s) * 100) % 256).astype(np.uint8)
        b = np.full_like(r, 120)
        a = np.full_like(r, 255)
        rgba = np.dstack([r, g, b, a])
        return {'rgba': rgba, 'bounds': [s, w, n, e],
                'stretch': (0.0, 3000.0)}


def main():
    import drift
    from drift import DriftEngine, build_gif_bytes, build_mp4_bytes, DRIFT_SITES

    ok = lambda name: print(f"PASS  {name}")

    # 1. Engine boots, fetches first tile, mosaic appears
    fake = FakeAnalyzer()
    eng = DriftEngine(0.15, 0.001, "2023-06-01", "2023-09-30", 25,
                      site=("Test Site", 10.05, 20.05), analyzer=fake)
    deadline = time.time() + 10
    while eng.mosaic_b64() is None and time.time() < deadline:
        eng.poll()
        time.sleep(0.05)
    assert eng.mosaic_b64() is not None, "mosaic never appeared"
    b64, bounds = eng.mosaic_b64()
    assert len(b64) > 100 and len(bounds) == 4
    ok("boot + first tile + mosaic")

    # 2. Viewport frame renders
    jpg = eng.viewport_jpeg((512, 384))
    assert jpg is not None
    img = Image.open(io.BytesIO(jpg))
    assert img.size == (512, 384)
    ok("viewport frame 512x384")

    # 3. Ticking advances position, captures frames, crosses tiles, prefetches
    eng.heading = 90.0  # due east
    lat0, lon0 = eng.lat, eng.lon
    for _ in range(30):
        eng.tick(1.0, speed=0.02, random_walk=False, capture_fps=8, capture=True)
        eng.poll()
        time.sleep(0.02)
    assert eng.lon > lon0, "did not move east"
    assert abs(eng.lat - lat0) < 1e-6, "latitude changed on fixed heading"
    assert len(eng.frames) > 0, "no frames captured"
    assert eng.tiles_crossed >= 1, f"no tile crossing (moved {eng.lon-lon0:.3f} deg)"
    assert fake.fetch_count >= 2, "prefetch never fired"
    ok(f"motion + capture ({len(eng.frames)} frames, "
       f"{eng.tiles_crossed} tiles crossed, {fake.fetch_count} fetches)")

    # Snapshot varied frames now (before test 4 pollutes the buffer with dupes)
    varied_frames = list(eng.frames)[:40]

    # 4. Frame cap enforced
    from drift import FRAME_CAP
    for _ in range(400):
        eng.frames.append(jpg)
        if len(eng.frames) > FRAME_CAP:
            eng.frames.pop(0)
            eng.frame_cap_hit = True
    assert len(eng.frames) <= FRAME_CAP and eng.frame_cap_hit
    ok("frame cap")

    # 5. Random jump: corridor exhausted -> new site prefetched -> clean cut
    eng.tiles_crossed = 99
    assert eng.need_jump(5)
    eng.start_jump()
    assert eng.jump_target is not None
    target_name = eng.jump_target[0]
    deadline = time.time() + 10
    jumped = False
    while time.time() < deadline:
        eng.poll()
        if eng.try_complete_jump():
            jumped = True
            break
        time.sleep(0.05)
    assert jumped, "jump never completed"
    assert eng.site_name == target_name and eng.tiles_crossed == 0
    assert eng.mosaic_b64() is not None, "blank frame after jump"
    ok(f"random jump -> {eng.site_name}")

    # 6. Empty tile triggers need_jump; bounce reverses heading
    fake2 = FakeAnalyzer()
    eng2 = DriftEngine(0.15, 0.001, "2023-06-01", "2023-09-30", 25,
                       site=("T2", 0.05, 0.05), analyzer=fake2)
    time.sleep(0.3); eng2.poll()
    eng2.tiles[eng2.key_for(eng2.lat, eng2.lon)] = {'empty': True}
    assert eng2.current_tile_empty() and eng2.need_jump(999)
    h0 = eng2.heading
    eng2.bounce()
    assert abs(((eng2.heading - h0) % 360) - 180) < 1e-6
    ok("empty tile detection + bounce")

    # 7. GIF export is a valid animated GIF
    frames = varied_frames
    gif = build_gif_bytes(frames, fps=10)
    g = Image.open(io.BytesIO(gif))
    assert g.format == 'GIF' and getattr(g, 'n_frames', 1) > 1
    ok(f"GIF export ({len(gif)//1024} KB, {g.n_frames} frames)")

    # 8. MP4 export is a valid readable video
    mp4 = build_mp4_bytes(frames, fps=10)
    assert len(mp4) > 1000
    import imageio.v2 as imageio, tempfile, os
    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as t:
        t.write(mp4); path = t.name
    reader = imageio.get_reader(path)
    meta = reader.get_meta_data()
    n_read = sum(1 for _ in reader)
    reader.close(); os.unlink(path)
    assert abs(meta['fps'] - 10) < 0.5, f"fps={meta['fps']}"
    assert n_read >= len(frames) - 2, f"read {n_read}/{len(frames)} frames"
    ok(f"MP4 export ({len(mp4)//1024} KB, {n_read} frames @ {meta['fps']} fps)")

    # 9. LRU never exceeds MAX_TILES; current tile stays hot
    assert len(eng.tiles) <= DriftEngine.MAX_TILES
    ok("LRU cap")

    # 10. Component HTML sane
    from drift import drift_component_html
    html = drift_component_html(b64, bounds, eng.lat, eng.lon, 0.02, 90.0,
                                eng.tile_size, True, eng.site_name)
    assert 'requestAnimationFrame' in html and 'drawImage' in html
    assert f"{eng.lat}" in html
    ok("component HTML")

    eng.shutdown(); eng2.shutdown()

    # 11. Stalled fetch times out with a clear error instead of hanging forever
    import drift as drift_mod

    class HangingAnalyzer(FakeAnalyzer):
        def drift_tile(self, *a, **k):
            # Simulate a stalled/black-holed connection, but bounded so this
            # test's worker thread doesn't block process exit via the
            # ThreadPoolExecutor atexit join hook.
            time.sleep(4)
            return None

    orig_timeout = drift_mod.FETCH_TIMEOUT_S
    drift_mod.FETCH_TIMEOUT_S = 1  # shrink for the test
    fake3 = HangingAnalyzer()
    eng3 = DriftEngine(0.15, 0.001, "2023-06-01", "2023-09-30", 25,
                       site=("Hang Site", 5.05, 5.05), analyzer=fake3)
    assert eng3.mosaic_b64() is None
    assert eng3.current_tile_error() is None, "should still be in-flight"
    w = eng3.current_wait_seconds()
    assert w is not None and w >= 0
    time.sleep(1.3)
    eng3.poll()
    err = eng3.current_tile_error()
    assert err and 'Timed out' in err, f"expected timeout error, got {err!r}"
    assert eng3.mosaic_b64() is None
    ok(f"stalled fetch times out cleanly ({err})")

    # retry() while the original stalled thread is *still occupying a worker*
    # (we gave up watching it, but a real thread can't be force-killed) must
    # still get its own worker immediately, not queue behind it forever —
    # this is why the urgent pool has 2 workers, not 1.
    eng3.analyzer = FakeAnalyzer()
    t_retry = time.time()
    eng3.retry(eng3.current_key())
    deadline = time.time() + 3
    while eng3.mosaic_b64() is None and time.time() < deadline:
        eng3.poll()
        time.sleep(0.02)
    recovered_in = time.time() - t_retry
    assert eng3.mosaic_b64() is not None, "retry never recovered"
    assert recovered_in < 2.0, (
        f"retry took {recovered_in:.2f}s — looks like it queued behind the "
        "still-running stalled thread instead of getting its own worker"
    )
    ok(f"retry() recovers promptly ({recovered_in:.2f}s) even while the "
       "original stalled thread is still running")

    drift_mod.FETCH_TIMEOUT_S = orig_timeout
    eng3.shutdown()

    # 12. Hold-at-edge: with a slow analyzer ahead, the drift waits instead of
    # panning into unloaded territory
    class SlowAnalyzer(FakeAnalyzer):
        def __init__(self):
            super().__init__()
            self.first = True

        def drift_tile(self, bbox, *a, **k):
            if not self.first:
                time.sleep(1.0)  # neighbors are slow
            self.first = False
            return super().drift_tile(bbox, *a, **k)

    eng4 = DriftEngine(0.15, 0.001, "2023-06-01", "2023-09-30", 25,
                       site=("Edge Site", 30.05, 30.05), analyzer=FakeAnalyzer())
    deadline = time.time() + 5
    while eng4.mosaic_b64() is None and time.time() < deadline:
        eng4.poll(); time.sleep(0.05)
    # Replace analyzer with one whose neighbor fetches are slow, clear
    # everything except the current tile, then drive east hard.
    cur = eng4.current_key()
    eng4.analyzer = SlowAnalyzer()
    eng4.analyzer.first = False
    with eng4.lock:
        for k in [k for k in eng4.tiles if k != cur]:
            del eng4.tiles[k]
        eng4.futures.clear()
        eng4.fetch_started.clear()
    eng4.heading = 90.0
    lon_before = eng4.lon
    # Position near the east edge of the current tile
    tx, ty = cur
    eng4.lon = (tx + 1) * eng4.tile_size - 0.02
    eng4.tick(1.0, speed=0.05, random_walk=False, capture_fps=8, capture=True)
    assert eng4.waiting, "should be holding at the edge while neighbor loads"
    east_edge = (tx + 1) * eng4.tile_size
    assert eng4.lon < east_edge, "advanced into the unloaded tile"
    ok("hold-at-edge while imagery ahead is loading")
    eng4.shutdown()

    # 13. Preload 3x3 fetches the full neighborhood and reports progress
    eng5 = DriftEngine(0.15, 0.001, "2023-06-01", "2023-09-30", 25,
                       site=("Pre Site", 40.05, 40.05), analyzer=FakeAnalyzer())
    eng5.preload()
    assert len(eng5.preload_keys) == 9
    assert eng5.max_tiles >= 12
    deadline = time.time() + 8
    while eng5.preload_progress() is not None and time.time() < deadline:
        eng5.poll(); time.sleep(0.05)
    assert eng5.preload_progress() is None, "preload never completed"
    assert len(eng5.tiles) == 9, f"expected 9 cached tiles, got {len(eng5.tiles)}"
    ok("preload 3x3 completes with 9 tiles cached")
    eng5.shutdown()

    # 14. Instant-source mercator helpers are sane
    x, y = DriftEngine._merc_frac(0.0, 0.0, 2)
    assert abs(x - 2.0) < 1e-6 and abs(y - 2.0) < 1e-6, (x, y)
    x, y = DriftEngine._merc_frac(85.05, -180.0, 1)
    assert abs(x - 0.0) < 1e-6 and y < 0.01, (x, y)
    ok("web-mercator tile math")

    print("\nALL TESTS PASSED")


if __name__ == '__main__':
    main()
