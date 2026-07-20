#!/usr/bin/env python3
"""Inspect the stable C ABI of a same-architecture HAL Adapter plugin."""

from __future__ import annotations

import argparse
import ctypes
import json


COUNT = ctypes.CFUNCTYPE(ctypes.c_size_t)
TYPE_AT = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_size_t)
SUPPORTS = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_char_p)
CREATE = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p)
DESTROY = ctypes.CFUNCTYPE(None, ctypes.c_void_p)


class PluginApi(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("plugin_id", ctypes.c_char_p),
        ("vendor", ctypes.c_char_p),
        ("plugin_version", ctypes.c_char_p),
        ("adapter_type_count", COUNT),
        ("adapter_type_at", TYPE_AT),
        ("supports", SUPPORTS),
        ("create", CREATE),
        ("destroy", DESTROY),
    ]


def text(value: bytes | None) -> str:
    return value.decode("utf-8") if value else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plugin")
    args = parser.parse_args()
    library = ctypes.CDLL(args.plugin, mode=ctypes.RTLD_LOCAL)
    sdk_abi = library.hal_get_adapter_sdk_abi_v1
    sdk_abi.restype = ctypes.c_uint32
    get_api = library.hal_get_adapter_plugin_v1
    get_api.restype = ctypes.POINTER(PluginApi)
    api_ptr = get_api()
    if not api_ptr:
        raise RuntimeError("hal_get_adapter_plugin_v1 returned null")
    api = api_ptr.contents
    count = api.adapter_type_count()
    if count < 1 or count > 64:
        raise RuntimeError(f"invalid adapter_type_count: {count}")
    adapter_types = [text(api.adapter_type_at(index)) for index in range(count)]
    print(json.dumps({
        "sdk_abi": sdk_abi(), "plugin_abi": api.abi_version,
        "plugin_id": text(api.plugin_id), "vendor": text(api.vendor),
        "plugin_version": text(api.plugin_version), "adapter_types": adapter_types,
        "supports": {item: bool(api.supports(item.encode("utf-8"))) for item in adapter_types},
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
