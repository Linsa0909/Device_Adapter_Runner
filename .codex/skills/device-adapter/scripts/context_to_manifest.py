#!/usr/bin/env python3
import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_EXCLUDES = [
    ".git",
    ".git/**",
    "**/.git",
    "**/.git/**",
    "__pycache__",
    "**/__pycache__/**",
    "*.pyc",
    "build",
    "build/**",
    "dist",
    "dist/**",
    "logs",
    "logs/**",
    "log",
    "log/**",
    "install",
    "install/**",
    "test_videos",
    "test_videos/**",
    "ops/artifacts/*.tar.gz",
    "ops/artifacts/*.tar",
    "ops/artifacts/logs/**",
]


def stage(name, status, exit_code=None):
    suffix = f" exit_code={exit_code}" if exit_code is not None else ""
    print(f"[AGENT_STAGE] stage={name} status={status}{suffix}")


def write_failure(stage_name, command, exit_code, message, next_action=None):
    out = Path("ops/artifacts/last_failure.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "stage": stage_name,
                "command": command,
                "exit_code": exit_code,
                "message": message,
                "next_action": next_action,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def split_paths(value):
    value = value.replace("、", ",").replace("，", ",")
    paths = []
    for item in re.split(r"[,;\n]", value):
        item = item.strip().strip("。.:：")
        if item:
            paths.append(item)
    return paths


def extract_after_markers(text, markers):
    found = []
    for marker in markers:
        pattern = re.compile(rf"{re.escape(marker)}(?:是|为|:|：)?\s*([^\n。]+)")
        for match in pattern.finditer(text):
            found.extend(split_paths(match.group(1)))
    return found


def clean_path(candidate):
    candidate = candidate.strip().strip("`'\" ")
    candidate = candidate.rstrip("。；;，,")
    candidate = candidate.replace("\\", "/")
    if candidate.startswith("./"):
        candidate = candidate[2:]
    return candidate


def looks_like_path(value):
    if not value or " " in value:
        return False
    if value.startswith("-"):
        return False
    return "/" in value or "." in Path(value).name or value in {"src", "configs", "config", "run.sh", "Dockerfile", "docker-compose.yml", "requirements.txt"}


def discover_ros2_workspace(excludes):
    packages = []
    package_files = []
    launch_files = []
    for package_xml in sorted(Path(".").glob("src/*/package.xml")):
        if is_excluded(package_xml, excludes):
            continue
        package_dir = package_xml.parent
        package_files.append(package_xml.as_posix())
        package_name = package_dir.name
        try:
            text = package_xml.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r"<name>\s*([^<]+)\s*</name>", text)
            if match:
                package_name = match.group(1).strip()
        except OSError:
            pass
        packages.append({"name": package_name, "path": package_dir.as_posix()})
    for launch_file in sorted(Path(".").glob("src/*/launch/*.launch.py")):
        if not is_excluded(launch_file, excludes):
            launch_files.append(launch_file.as_posix())
    return packages, package_files, launch_files


def discover_cpp_project(excludes):
    source_suffixes = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
    lib_suffixes = {".so", ".a", ".dll", ".dylib"}
    build_names = {"CMakeLists.txt", "Makefile", "makefile"}
    candidates = []
    cpp_files = []
    lib_files = []
    build_files = []
    for path in sorted(Path(".").rglob("*")):
        if not path.is_file() or is_excluded(path, excludes):
            continue
        if path.parts and path.parts[0] in {".codex", "ops"}:
            continue
        suffix = path.suffix.lower()
        if suffix in source_suffixes:
            cpp_files.append(path.as_posix())
        elif suffix in lib_suffixes:
            lib_files.append(path.as_posix())
        elif path.name in build_names or path.suffix.lower() in {".cmake", ".pc"}:
            build_files.append(path.as_posix())

    for directory in ["src", "include", "inc", "lib", "libs", "sdk", "third_party", "3rdparty", "configs", "config", "docs"]:
        if Path(directory).exists():
            candidates.append(directory)

    for file_name in build_files:
        top = file_name.split("/", 1)[0]
        candidates.append(file_name if top == file_name else top)

    if cpp_files and not candidates:
        top_dirs = []
        for file_name in cpp_files[:40]:
            top = file_name.split("/", 1)[0]
            top_dirs.append(top)
        candidates.extend(top_dirs)

    for file_name in lib_files:
        top = file_name.split("/", 1)[0]
        candidates.append(file_name if top == file_name else top)

    return unique(candidates), cpp_files, lib_files, build_files


def unique(items):
    result = []
    seen = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def is_excluded(path, excludes):
    p = path.as_posix()
    for pattern in excludes:
        if fnmatch.fnmatch(p, pattern) or fnmatch.fnmatch(path.name, pattern):
            return True
    return False


def expand_includes(includes, excludes):
    files = []
    missing = []
    for include in includes:
        path = Path(include)
        if not path.exists():
            missing.append(include)
            continue
        if path.is_file() or path.is_symlink():
            if not is_excluded(path, excludes):
                files.append(path.as_posix())
            continue
        for child in sorted(path.rglob("*")):
            if (child.is_file() or child.is_symlink()) and not is_excluded(child, excludes):
                files.append(child.as_posix())
    return unique(files), missing


def first_context_value(text, labels):
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*(?:是|为|=|:|：)\s*([^\n，,。]+)", text, re.I)
        if match:
            return match.group(1).strip().strip("`'\"")
    return ""


def write_plugin_contract_draft(context_id, text):
    """Persist unknown plugin facts instead of silently inventing defaults."""
    path = Path(f"ops/contexts/{context_id}.plugin_contract.json")
    if path.exists():
        return path
    adapter_type = first_context_value(text, ["adapter_type", "adapter type"])
    adapter_type = adapter_type or re.sub(r"[^a-z0-9_]+", "_", context_id.lower()).strip("_")
    arch = first_context_value(text, ["target_arch", "目标架构"])
    if not arch and re.search(r"\b(?:arm64|aarch64)\b", text, re.I):
        arch = "aarch64"
    platform_name = first_context_value(text, ["target_platform", "目标平台", "目标板"])
    if not platform_name and re.search(r"rk3588", text, re.I):
        platform_name = "RK3588"
    refs_text = first_context_value(text, ["capability_group_refs", "能力组引用"])
    refs = [item.strip() for item in re.split(r"[\s,，]+", refs_text) if item.strip()]
    payload = {
        "schema_version": "1.0",
        "generated_by": "context_to_manifest.py",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_hashes": {"context_md": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()},
        "context_id": context_id,
        "delivery_mode": "runtime_plugin",
        "adapter_type": adapter_type,
        "vendor": first_context_value(text, ["vendor", "厂商"]),
        "plugin_version": first_context_value(text, ["plugin_version", "插件版本"]),
        "sdk_root": first_context_value(text, ["hal_adapter_sdk_root", "sdk_root", "HAL Adapter SDK 路径"]),
        "platform_source_root": first_context_value(text, ["hal_platform_source_root", "平台源码根目录"]),
        "platform_install_prefix": first_context_value(text, ["hal_platform_install_prefix", "平台安装前缀"]),
        "sdk_output_dir": first_context_value(text, ["hal_adapter_sdk_output_dir", "SDK 输出目录"]),
        "sdk_version": first_context_value(text, ["hal_adapter_sdk_version", "SDK 版本"]),
        "sdk_abi": first_context_value(text, ["hal_adapter_sdk_abi", "SDK ABI"]),
        "plugin_abi": first_context_value(text, ["hal_adapter_plugin_abi", "Plugin ABI", "插件 ABI"]),
        "target_arch": arch,
        "target_platform": platform_name,
        "target_os": first_context_value(text, ["target_os", "目标操作系统", "基础系统"]),
        "compiler_triplet": first_context_value(text, ["compiler_triplet", "编译器三元组", "目标编译器"]),
        "runtime_image": first_context_value(text, ["runtime_image", "运行镜像"]),
        "capability_group_refs": refs,
        "supports_multi_instance": True,
        "private_config": {"path": f"config/{adapter_type}.json", "schema_version": "1.0"},
        "target_build": {
            "build_in_runtime_container": True,
            "sdk_build_image": "registry.ghostcloud.cn/integration/hal_dev:v1.0",
            "plugin_build_image": "registry.ghostcloud.cn/integration/hal_dev:v1.0",
        },
        "plugin_source_dir": f"adapter_plugins/{adapter_type}",
        "package_dir": f"build/{adapter_type}-package",
    }
    payload["unknown_fields"] = [key for key, value in payload.items() if value == "" or value == []]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("context_id")
    parser.add_argument("--context-file")
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--docs-dir", default="docs")
    parser.add_argument("--skip-docs-extract", action="store_true")
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.context_id):
        parser.error("context_id may contain only letters, digits, dot, underscore, and hyphen")

    command = "context_to_manifest.py " + args.context_id
    stage("stage0_env_check", "start")
    Path("ops/contexts").mkdir(parents=True, exist_ok=True)
    Path("ops/artifacts").mkdir(parents=True, exist_ok=True)
    stage("stage0_env_check", "success")

    context_path = Path(f"ops/contexts/{args.context_id}.context.md")
    manifest_path = Path(f"ops/contexts/{args.context_id}.manifest.json")
    package_list_path = Path(f"ops/artifacts/{args.context_id}.package_files.txt")

    stage("stage1_context_load", "start")
    if args.stdin:
        text = sys.stdin.read()
    elif args.context_file:
        text = Path(args.context_file).read_text(encoding="utf-8")
    elif context_path.exists():
        text = context_path.read_text(encoding="utf-8")
    else:
        text = ""

    if not text.strip():
        stage("stage1_context_load", "fail", 2)
        write_failure("stage1_context_load", command, 2, "No natural language context was provided.", "Create context.md or pass --context-file.")
        return 2

    context_path.write_text(text.rstrip() + "\n", encoding="utf-8")
    plugin_contract_path = write_plugin_contract_draft(args.context_id, text)
    stage("stage1_context_load", "success")

    docs_inventory_path = Path(f"ops/contexts/{args.context_id}.docs_inventory.json")
    docs_extraction_report = Path(f"ops/artifacts/docs/{args.context_id}/extraction_report.json")
    if not args.skip_docs_extract:
        extract_command = [
            sys.executable,
            str(Path(__file__).resolve().parent / "extract_docs.py"),
            args.context_id,
            "--docs-dir",
            args.docs_dir,
        ]
        result = subprocess.run(extract_command, check=False)
        if result.returncode != 0:
            return result.returncode

    stage("stage2_context_validate", "start")
    includes = []
    excludes = list(DEFAULT_EXCLUDES)

    explicit_package = extract_after_markers(text, ["需要打包", "打包"])
    if explicit_package:
        includes.extend(explicit_package)

    for marker_group in (
        ["入口脚本", "入口", "entrypoint"],
        ["Dockerfile"],
        ["docker-compose"],
        ["主要实现代码", "主要代码", "源码", "源代码"],
        ["运行配置", "配置"],
        ["Python 依赖", "依赖"],
    ):
        includes.extend(extract_after_markers(text, marker_group))

    ros_packages, ros_package_files, ros_launch_files = discover_ros2_workspace(excludes)
    cpp_discovered, cpp_files, lib_files, build_files = discover_cpp_project(excludes)
    is_ros2_workspace = bool(ros_packages)

    if is_ros2_workspace and "src" not in includes and Path("src").exists():
        includes.append("src")
    for doc in sorted(Path(".").glob("*.md")):
        if not is_excluded(doc, excludes):
            includes.append(doc.as_posix())

    if not includes:
        for default in ["run.sh", "Dockerfile", "docker-compose.yml", "requirements.txt", "src", "configs"]:
            if Path(default).exists():
                includes.append(default)

    for item in cpp_discovered:
        if item not in includes:
            includes.append(item)

    for raw in extract_after_markers(text, ["不要打包", "排除", "不打包"]):
        excludes.append(clean_path(raw))
        excludes.append(clean_path(raw).rstrip("/") + "/**")

    includes = [clean_path(x) for x in includes]
    includes = [x for x in includes if looks_like_path(x)]
    includes = unique(includes)

    device_paths = unique(re.findall(r"/dev/[A-Za-z0-9_.-]+", text))
    host_match = re.search(r"(?:host|服务器|板子|远端).*?((?:\d{1,3}\.){3}\d{1,3})", text)
    user_match = re.search(r"(?:用户|user)\s*(?:是|为|:|：)?\s*([A-Za-z0-9_.-]+)", text)
    arch = "arm64" if re.search(r"arm64|aarch64|-arm", text, re.I) else "amd64" if re.search(r"x86|amd64", text, re.I) else "arm64"
    wants_closed_loop_delivery = bool(
        re.search(r"交付|完整.*包|直接运行|上板运行|板子.*运行|install\.sh|status\.sh|view\.sh|DEPLOY\.md|config\.env", text, re.I)
    )
    mino17_pusher_required = bool(re.search(r"mino17|infrared_push_50fps", text, re.I))

    runtime_generated = []
    has_cpp = bool(cpp_files)
    for generated_name in ["Dockerfile", "docker-compose.yml", ".dockerignore", "run.sh"]:
        if not Path(generated_name).exists():
            runtime_generated.append(generated_name)
            if generated_name not in includes:
                includes.append(generated_name)
    if wants_closed_loop_delivery:
        for generated_name in ["config.env", "install.sh", "status.sh", "view.sh", "DEPLOY.md"]:
            if not Path(generated_name).exists():
                runtime_generated.append(generated_name)
            if generated_name not in includes:
                includes.append(generated_name)
    if has_cpp and not is_ros2_workspace and not any(Path(name).exists() for name in ["CMakeLists.txt", "Makefile", "makefile"]):
        runtime_generated.append("CMakeLists.txt")
        if "CMakeLists.txt" not in includes:
            includes.append("CMakeLists.txt")

    package_files, missing = expand_includes(includes, excludes)
    blocking_missing = [item for item in missing if item not in runtime_generated]

    manifest = {
        "schema_version": 1,
        "context_id": args.context_id,
        "summary": text.strip().splitlines()[0][:200],
        "context_file": context_path.as_posix(),
        "docs": {
            "root": args.docs_dir,
            "inventory": docs_inventory_path.as_posix() if docs_inventory_path.exists() else None,
            "extraction_report": docs_extraction_report.as_posix() if docs_extraction_report.exists() else None,
        },
        "include": includes,
        "exclude": unique(excludes),
        "package_files_file": package_list_path.as_posix(),
        "package_artifact": f"ops/artifacts/{args.context_id}_package.tar.gz",
        "docker": {
            "dockerfile": "Dockerfile" if Path("Dockerfile").exists() else None,
            "compose_file": "docker-compose.yml" if Path("docker-compose.yml").exists() else None,
            "image": args.context_id.replace("_", "-"),
            "tag": "latest",
            "default_arch": arch,
            "saved_image": f"ops/artifacts/{args.context_id}_{arch}_image.tar",
        },
        "remote": {
            "device_paths": device_paths,
            "default_user": user_match.group(1) if user_match else "root",
            "default_host": host_match.group(1) if host_match else None,
            "remote_dir": f"/opt/device_adapter/{args.context_id}",
        },
        "run": {
            "entrypoint": "run.sh" if "run.sh" in includes or Path("run.sh").exists() else None,
            "test_timeout_seconds": 60,
            "smoke_timeout_seconds": 20,
            "success_markers": [],
            "ros_launch": "hardware_abstraction_layer manager_node.launch.py" if any(p["name"] == "hardware_abstraction_layer" for p in ros_packages) else None,
        },
        "delivery": {
            "closed_loop_package": wants_closed_loop_delivery,
            "required_root_files": ["config.env", "install.sh", "run.sh", "status.sh", "view.sh", "DEPLOY.md"] if wants_closed_loop_delivery else [],
            "required_runtime_files": [],
        },
        "build": {
            "language": "cpp" if has_cpp else "unknown",
            "build_system": "ros2_colcon" if is_ros2_workspace else "cmake" if has_cpp else "unknown",
            "ros_distro": "humble" if is_ros2_workspace else None,
            "ros_packages": ros_packages,
            "ros_launch_files": ros_launch_files,
            "source_files": cpp_files[:200],
            "library_files": lib_files[:200],
            "build_files": unique(build_files + ros_package_files)[:100],
            "generated_runtime_files": runtime_generated,
            "binary_name": args.context_id.replace("-", "_"),
            "required_ros_executables": ["hardware_abstraction_layer/infrared_push_50fps"] if mino17_pusher_required else [],
            "required_elf_executables": ["hardware_abstraction_layer/infrared_push_50fps"] if mino17_pusher_required else [],
        },
        "generated": {
            "missing_paths": blocking_missing,
            "runtime_generated_files": runtime_generated,
            "package_file_count": len(package_files),
        },
    }

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    package_list_path.write_text("\n".join(package_files) + ("\n" if package_files else ""), encoding="utf-8")

    if missing:
        print("Missing include paths:")
        for item in missing:
            print(f"- {item}")
    print(f"context: {context_path}")
    print(f"plugin_contract: {plugin_contract_path}")
    print(f"manifest: {manifest_path}")
    print(f"package_files: {package_list_path}")
    print(f"file_count: {len(package_files)}")
    stage("stage2_context_validate", "success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
