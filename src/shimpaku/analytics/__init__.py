"""Shimpaku analytics: SLE engine, blast radius, detectors, incidents, L1/L2 rule tree.

Entry point for the daemon: :func:`shimpaku.analytics.loop.run_analytics_loop`.
"""

from shimpaku.analytics.loop import run_analytics_loop, run_once

__all__ = ["run_analytics_loop", "run_once"]
