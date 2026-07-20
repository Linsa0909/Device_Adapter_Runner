#!/usr/bin/env python3
"""Run commands in an isolated process group with deterministic cleanup."""
from __future__ import annotations
import os, signal, subprocess
from pathlib import Path
from typing import Any

def run_process(command: list[str], *, cwd: Path | None = None, timeout: int = 300,
                log_path: Path | None = None) -> dict[str, Any]:
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    timed_out = False
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGTERM)
        try: output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); output, _ = process.communicate()
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(output, encoding="utf-8")
    return {"command":command,"exit_code":process.returncode,"timed_out":timed_out,
            "log_path":str(log_path) if log_path else "","log_tail":output.splitlines()[-80:]}
