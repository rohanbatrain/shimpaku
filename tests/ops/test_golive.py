"""ops/golive/run.sh: what the lab host answers is data, never shell code run on this machine."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_check_never_evaluates_the_lab_hosts_answer(tmp_path):
    tree = tmp_path / "repo"
    (tree / "ops" / "golive").mkdir(parents=True)
    shutil.copy(ROOT / "ops" / "golive" / "run.sh", tree / "ops" / "golive" / "run.sh")
    (tree / ".local").mkdir()
    (tree / ".local" / "settings.env").write_text('LAB_HOST="lab"\nVPS_HOST="vps"\nLAB_PORT="8780"\n')
    marker = tmp_path / "pwned"
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    # a lab host whose env file says SHIMPAKU_AUTH_MODE=$(touch <marker>); the VPS is unreachable
    (bin_ / "ssh").write_text(f"""#!/bin/sh
case "$*" in
  *vps*) exit 1 ;;
  *config/shimpaku/env*) printf '%s\\n' 'auth=$(touch {marker})' 'view=1' 'pw=1' 'totp=1' 'svc=active' ;;
  *) exit 0 ;;
esac
""")
    (bin_ / "ssh").chmod(0o755)
    p = subprocess.run(["bash", str(tree / "ops" / "golive" / "run.sh"), "check"], capture_output=True, text=True,
                       env={**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}"})
    assert "shimpakud active" in p.stdout, p.stdout + p.stderr
    assert not marker.exists()
    assert "auth mode is '$(touch" in p.stdout   # shown as text, not run
