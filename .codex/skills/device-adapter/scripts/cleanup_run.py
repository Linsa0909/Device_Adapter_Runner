#!/usr/bin/env python3
"""Safely clean one workflow run while preserving diagnostic evidence."""
from __future__ import annotations
import argparse, json, shutil, subprocess
from pathlib import Path
from typing import Any

def allowed(path: Path, run_root: Path, temp_roots: list[Path]) -> bool:
    resolved=path.resolve(); roots=[run_root.resolve(),*(p.resolve() for p in temp_roots)]
    return any(resolved != root and resolved.is_relative_to(root) for root in roots)

def cleanup(manifest: dict[str,Any], workspace: Path) -> dict[str,Any]:
    run_root=(workspace/manifest["run_root"]).resolve()
    temp_roots=[Path(p) for p in manifest.get("allowed_temp_roots",[])]
    removed=[]; refused=[]; errors=[]
    for raw in manifest.get("cleanup_paths",[]):
        path=Path(raw); path=path if path.is_absolute() else workspace/path
        if not allowed(path,run_root,temp_roots): refused.append(str(path)); continue
        try:
            if path.is_dir() and not path.is_symlink(): shutil.rmtree(path)
            elif path.exists() or path.is_symlink(): path.unlink()
            removed.append(str(path))
        except OSError as exc: errors.append(f"{path}: {exc}")
    containers=[]
    for name in manifest.get("containers",[]):
        if not str(name).startswith(f"device-adapter-{manifest['context_id']}-"):
            refused.append(f"container:{name}"); continue
        result=subprocess.run(["docker","rm","-f",str(name)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,check=False)
        containers.append({"name":name,"exit_code":result.returncode})
    status="FAIL" if errors else ("PARTIAL" if refused else "PASS")
    return {"schema_version":"1.0","context_id":manifest["context_id"],"run_id":manifest["run_id"],
            "status":status,"removed":removed,"refused":refused,"errors":errors,"containers":containers}

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("manifest"); args=parser.parse_args()
    path=Path(args.manifest); manifest=json.loads(path.read_text(encoding="utf-8")); report=cleanup(manifest,Path.cwd())
    output=Path("ops/artifacts")/f"{manifest['context_id']}.cleanup_report.json"; output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(output)
    return 0 if report["status"]=="PASS" else 8
if __name__ == "__main__": raise SystemExit(main())
