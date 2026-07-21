#!/usr/bin/env python3
"""Deterministic static verification for a formal HAL Adapter plugin package."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

from plugin_common import ARTIFACTS, load_contract, load_json, package_dir, readme_contract_errors, resolve_project_path, sdk_arch_dir, write_json


ABI_SYMBOLS = {"hal_get_adapter_sdk_abi_v1", "hal_get_adapter_plugin_v1"}
PLATFORM_LIBS = {"libhal_model.so", "libhal_media.so", "libhal_utils.so"}
SYSTEM_LIB_PREFIXES = (
    "linux-vdso", "ld-linux", "ld-musl", "libc.so", "libm.so", "libdl.so", "libpthread.so",
    "librt.so", "libstdc++.so", "libgcc_s.so", "libatomic.so",
)


def command(*args: str, env: dict[str, str] | None = None) -> tuple[int, str]:
    result = subprocess.run(args, text=True, capture_output=True, check=False, env=env)
    return result.returncode, (result.stdout + result.stderr).strip()


def expected_arch_marker(target_arch: str) -> str:
    normalized = target_arch.lower().replace("-", "_")
    return "aarch64" if normalized in {"aarch64", "arm64", "linux_arm64"} else "x86-64"


def needed_libraries(dynamic_output: str) -> list[str]:
    return re.findall(r"\(NEEDED\).*?\[([^]]+)\]", dynamic_output)


def provided_dependency_names(deps: Path) -> set[str]:
    names: set[str] = set()
    if not deps.is_dir():
        return names
    for path in deps.iterdir():
        if path.is_file() or path.is_symlink():
            names.add(path.name)
            rc, output = command("readelf", "-d", str(path))
            if rc == 0:
                match = re.search(r"\(SONAME\).*?\[([^]]+)\]", output)
                if match:
                    names.add(match.group(1))
    return names


def private_dependency_graph(deps: Path) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    if not deps.is_dir():
        return graph
    for path in sorted(deps.iterdir()):
        if not (path.is_file() or path.is_symlink()):
            continue
        rc, output = command("readelf", "-d", str(path))
        if rc == 0:
            graph[path.name] = needed_libraries(output)
    return graph


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    args = parser.parse_args()
    contract = load_contract(args.context_id)
    adapter = str(contract["adapter_type"])
    package = package_dir(contract)
    binary = package / f"adapters/libhal_adapter_{adapter}.so"
    model = package / f"model/devices/{adapter}.device.yaml"
    config_path = package / f"config/{adapter}.json"
    sdk = resolve_project_path(str(contract["sdk_root"]))

    private_config_contract = contract.get("private_config") or {}
    config_required = private_config_contract.get("required") is True
    config_errors: list[str] = []
    try:
        if config_path.is_file():
            private_config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(private_config, dict):
                config_errors.append("top-level config must be an object")
            else:
                if not private_config.get("schema_version"):
                    config_errors.append("schema_version is required")
                if not isinstance(private_config.get("instances"), list):
                    config_errors.append("instances must be an array")
        elif config_required:
            config_errors.append("required private config is missing")
    except (OSError, json.JSONDecodeError) as exc:
        config_errors.append(str(exc))
    config_pass = not config_errors
    write_json(ARTIFACTS / f"{args.context_id}.config_validation.json", {
        "status": "PASS" if config_pass else "FAIL", "path": str(config_path),
        "errors": config_errors,
    })
    readme_errors = readme_contract_errors(package / "README.md", contract)
    readme_pass = not readme_errors
    write_json(ARTIFACTS / f"{args.context_id}.readme_validation.json", {
        "status": "PASS" if readme_pass else "FAIL",
        "sdk_version": contract.get("sdk_version"),
        "sdk_abi": contract.get("sdk_abi"),
        "errors": readme_errors,
    })

    file_rc, file_output = command("file", str(binary)) if binary.is_file() else (1, "plugin binary missing")
    marker = expected_arch_marker(str(contract["target_arch"]))
    arch_pass = file_rc == 0 and marker.lower() in file_output.lower()
    write_json(ARTIFACTS / f"{args.context_id}.elf_arch_validation.json", {
        "status": "PASS" if arch_pass else "FAIL",
        "target_arch": contract["target_arch"],
        "file_output": file_output,
    })

    nm_rc, nm_output = command("nm", "-D", "--defined-only", str(binary)) if binary.is_file() else (1, "")
    exported = sorted(symbol for symbol in ABI_SYMBOLS if re.search(rf"\b{re.escape(symbol)}$", nm_output, re.M))
    dyn_rc, dyn_output = command("readelf", "-d", str(binary)) if binary.is_file() else (1, "")
    rpath_match = re.findall(r"\((?:RPATH|RUNPATH)\).*?\[([^]]+)\]", dyn_output)
    rpath_pass = any("$ORIGIN/../deps" in item for item in rpath_match)
    abi_pass = nm_rc == 0 and set(exported) == ABI_SYMBOLS and dyn_rc == 0 and rpath_pass
    write_json(ARTIFACTS / f"{args.context_id}.abi_validation.json", {
        "status": "PASS" if abi_pass else "FAIL",
        "exported_symbols": exported,
        "required_symbols": sorted(ABI_SYMBOLS),
        "rpath": rpath_match,
        "expected_plugin_abi": contract["plugin_abi"],
        "expected_sdk_abi": contract["sdk_abi"],
    })

    provided = provided_dependency_names(package / "deps")
    dependency_graph = private_dependency_graph(package / "deps")
    direct_needed = needed_libraries(dyn_output)
    needed = sorted(set(direct_needed + [name for values in dependency_graph.values() for name in values]))
    declared_system = set(str(item) for item in contract.get("system_libraries", []))
    unresolved = [
        name for name in needed
        if name not in provided
        and name not in declared_system
        and not any(name.startswith(item) for item in PLATFORM_LIBS)
        and not name.startswith(SYSTEM_LIB_PREFIXES)
    ]
    host_matches = (marker == "x86-64" and platform.machine().lower() in {"x86_64", "amd64"}) or (
        marker == "aarch64" and platform.machine().lower() in {"aarch64", "arm64"}
    )
    ldd_output = "NOT_RUN_CROSS_ARCH"
    ldd_missing: list[str] = []
    if host_matches and binary.is_file():
        inventory_path = Path("ops/contexts") / f"{args.context_id}.sdk_inventory.json"
        sdk_inventory = load_json(inventory_path) if inventory_path.is_file() else {}
        sdk_platform = str(
            sdk_inventory.get("platform_lib_dir")
            or sdk / "platform/lib" / sdk_arch_dir(str(contract["target_arch"]))
        )
        paths = [str(package / "deps")]
        if sdk_platform:
            paths.append(sdk_platform)
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = ":".join(paths + ([env["LD_LIBRARY_PATH"]] if env.get("LD_LIBRARY_PATH") else []))
        _, ldd_output = command("ldd", str(binary), env=env)
        ldd_missing = []
        for line in ldd_output.splitlines():
            if "not found" not in line:
                continue
            library = line.strip().split()[0] if line.strip() else ""
            if library in declared_system or any(library.startswith(name) for name in PLATFORM_LIBS):
                continue
            ldd_missing.append(line.strip())
    closure_pass = not unresolved and not ldd_missing
    write_json(ARTIFACTS / f"{args.context_id}.dependency_closure.json", {
        "status": "PASS" if closure_pass else "FAIL",
        "direct_needed": direct_needed,
        "dependency_graph": dependency_graph,
        "needed_closure": needed,
        "provided_private_dependencies": sorted(provided),
        "declared_system_libraries": sorted(declared_system),
        "unresolved": unresolved,
        "ldd": ldd_output,
        "cross_arch_static_check": not host_matches,
    })

    declaration: dict[str, object] = {}
    declaration_pass = False
    if host_matches and binary.is_file() and closure_pass:
        helper = Path(__file__).resolve().parent / "inspect_plugin_runtime.py"
        runtime_env = dict(os.environ)
        runtime_env["LD_LIBRARY_PATH"] = ":".join([
            str(package / "deps"), str(sdk / "platform/lib" / sdk_arch_dir(str(contract["target_arch"]))),
            runtime_env.get("LD_LIBRARY_PATH", ""),
        ]).rstrip(":")
        rc, output = command(sys.executable, str(helper), str(binary), env=runtime_env)
        if rc == 0:
            try:
                declaration = json.loads(output)
            except json.JSONDecodeError:
                declaration = {"error": output}
        else:
            declaration = {"error": output}
        declaration_pass = (
            rc == 0
            and declaration.get("sdk_abi") == contract["sdk_abi"]
            and declaration.get("plugin_abi") == contract["plugin_abi"]
            and declaration.get("plugin_id") == f"{contract['vendor']}.{adapter}"
            and declaration.get("vendor") == contract["vendor"]
            and declaration.get("plugin_version") == contract["plugin_version"]
            and declaration.get("adapter_types") == [adapter]
            and declaration.get("supports", {}).get(adapter) is True
        )
    else:
        plugin_source = resolve_project_path(str(contract.get("plugin_source_dir") or f"adapter_plugins/{adapter}")) / f"src/{adapter}_plugin.cpp"
        source_text = plugin_source.read_text(encoding="utf-8", errors="ignore") if plugin_source.is_file() else ""
        declaration = {"mode": "cross_arch_source_and_runtime_deferred", "source": str(plugin_source)}
        declaration_pass = (
            f'"{adapter}"' in source_text
            and f'"{contract["vendor"]}.{adapter}"' in source_text
            and f'"{contract["plugin_version"]}"' in source_text
            and re.search(
                rf"AdapterPluginApiV1\s+\w+\s*\{{\s*{int(contract['plugin_abi'])}\s*,",
                source_text,
            ) is not None
            and "hal_get_adapter_sdk_abi_v1" in source_text
            and "hal_get_adapter_plugin_v1" in source_text
        )
    write_json(ARTIFACTS / f"{args.context_id}.plugin_declaration_validation.json", {
        "status": "PASS" if declaration_pass else "FAIL", **declaration,
    })

    lint = sdk / "tools/hal_adapter_model_lint.py"
    lint_args = (
        str(lint), "--device-yaml", str(model),
        "--capability-dir", str(sdk / "model_reference/capability_groups"),
        "--package-root", str(package), "--adapter-type", adapter,
    )
    lint_rc, lint_output = command("python3", *lint_args) if lint.is_file() and model.is_file() else (1, "model or linter missing")
    write_json(ARTIFACTS / f"{args.context_id}.model_lint.json", {
        "status": "PASS" if lint_rc == 0 else "FAIL", "exit_code": lint_rc, "output": lint_output,
    })

    forbidden: list[str] = []
    if package.exists():
        forbidden.extend(path.relative_to(package).as_posix() for path in package.rglob("*.capability.yaml"))
        forbidden.extend(
            path.relative_to(package).as_posix() for path in package.rglob("*")
            if path.is_file() and any(path.name.startswith(name) for name in PLATFORM_LIBS)
        )
        adapter_root = package / "adapters"
        if adapter_root.is_dir():
            forbidden.extend(path.relative_to(package).as_posix() for path in adapter_root.rglob("*") if path.is_dir())
        allowed_exact = {
            "README.md", f"adapters/libhal_adapter_{adapter}.so",
            f"model/devices/{adapter}.device.yaml",
        }
        if config_path.is_file():
            allowed_exact.add(f"config/{adapter}.json")
        for path in package.rglob("*"):
            if not (path.is_file() or path.is_symlink()):
                continue
            rel = path.relative_to(package).as_posix()
            if rel in allowed_exact or (rel.startswith("deps/") and fnmatch.fnmatch(path.name, "*.so*")):
                continue
            forbidden.append(rel)
    write_json(ARTIFACTS / f"{args.context_id}.forbidden_files.json", {
        "status": "PASS" if not forbidden else "FAIL", "forbidden": sorted(set(forbidden)),
    })

    multi_path = ARTIFACTS / f"{args.context_id}.multi_instance_test.json"
    multi = load_json(multi_path) if multi_path.is_file() else {"status": "NOT_RUN"}
    multi_pass = (
        multi.get("status") == "PASS"
        and int(multi.get("simultaneous_instances", 0)) >= 2
        and multi.get("independent_destroy_verified") is True
    )
    write_json(ARTIFACTS / f"{args.context_id}.multi_instance_validation.json", {
        **multi, "status": "PASS" if multi_pass else "FAIL",
    })

    source_verifier = Path(__file__).resolve().parent / "verify_plugin_source.py"
    source_rc, source_output = command(sys.executable, str(source_verifier), args.context_id)
    passed = all((arch_pass, abi_pass, closure_pass, declaration_pass, lint_rc == 0, not forbidden, multi_pass, config_pass, readme_pass, source_rc == 0))
    if source_output:
        print(source_output)
    print(f"[AGENT_STAGE] stage=stage11_plugin_verify status={'success' if passed else 'fail'}")
    return 0 if passed else 12


if __name__ == "__main__":
    raise SystemExit(main())
