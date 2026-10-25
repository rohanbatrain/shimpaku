"""Demo data for the public routes (``routes_public.py``) under ``SHIMPAKU_DEMO=1`` only.

Canned, lab-shaped probe results passed through the real heatmap code (``analytics/heatmap.py``),
so the dashboard can be developed without a lab. Every payload carries ``demo: true`` and the page
says so; the replay and the live daemon never use this module.
"""

from __future__ import annotations

import random

import pandas as pd

from shimpaku.analytics import heatmap

# (client_id, site); the test names follow the lab's RPM owner/test naming
PATHS = [
    ("rpm:r1:shimpaku/to-r2", "campus"), ("rpm:r2:shimpaku/to-r1", "campus"), ("rpm:sw1:shimpaku/to-r1", "campus"),
    ("rpm:leaf1:shimpaku/to-leaf2", "fabric"), ("rpm:leaf2:shimpaku/to-leaf1", "fabric"),
    ("rpm:leaf1:shimpaku/to-spine1", "fabric"), ("rpm:leaf2:shimpaku/to-spine2", "fabric"),
]


def _rows(window_min: int, end: pd.Timestamp) -> pd.DataFrame:
    rng = random.Random(7)
    step = max(1, window_min // 240)  # keep the canned frame small for long windows
    out = []
    for k, (cid, _site) in enumerate(PATHS):
        base = 2.0 + k * 0.7
        for m in range(window_min, 0, -step):
            t = end - pd.Timedelta(minutes=m)
            frac = m / window_min
            # one fault on leaf1 (total loss) and a latency patch on sw1, at fixed points of the window
            down = cid.startswith("rpm:leaf1") and 0.30 < frac < 0.36
            slow = cid.startswith("rpm:sw1") and 0.62 < frac < 0.70
            loss = 100.0 if down else (rng.choice([0.0] * 30 + [10.0]) if k == 4 else 0.0)
            rtt = None if down else (base + rng.random() * 1.5 + (70.0 if slow else 0.0))
            out.append(dict(ts=t, client_id=cid, target="", kind="rpm-icmp", ok=not down, rtt_ms=rtt,
                            jitter_ms=None if down else round(0.2 + rng.random() * (8 if slow else 0.6), 3), loss_pct=loss))
    return pd.DataFrame(out, columns=heatmap.COLS)


def probe_heatmap(site: str = "campus", window_min: int = 60) -> dict:
    end = pd.Timestamp.now(tz="UTC").tz_localize(None).floor("min")
    start = end - pd.Timedelta(minutes=window_min)
    return {**heatmap.build(_rows(window_min, end), start, end, site, window_min), "demo": True,
            "source": "demo: canned probe results through the real heatmap code"}
