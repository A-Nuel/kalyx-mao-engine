import subprocess
import sys
import os

def test_cli_demo_runner_in_memory():
    cmd = [sys.executable, "scripts/run_demo.py", "--fast"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=".")
    assert res.returncode == 0
    assert "MINIMUM AUTONOMOUS ORGANISATION (MAO) - MISSION CONTROL" in res.stdout
    assert "PROPOSAL REJECTED BY POLICY ENGINE" in res.stdout
    assert "AUDIT VERIFICATION: 100% UNTAMPERED" in res.stdout
    assert "CORE INVARIANT PROVEN" in res.stdout

def test_cli_demo_runner_with_persistence(tmp_path):
    db_path = str(tmp_path / "cli_demo.db")
    cmd = [sys.executable, "scripts/run_demo.py", "--persist", db_path, "--fast"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=".")
    assert res.returncode == 0
    assert "Using persistent SQLite database" in res.stdout
    assert "AUDIT VERIFICATION: 100% UNTAMPERED" in res.stdout
    assert os.path.exists(db_path)
