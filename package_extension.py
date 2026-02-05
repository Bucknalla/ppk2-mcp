#!/usr/bin/env python3
"""
Package the PPK2 MCP server as a .mcpb extension bundle.

MCPB (MCP Bundle) is a ZIP archive containing:
- manifest.json: Extension metadata and configuration
- pyproject.toml: Python dependencies (for UV runtime)
- src/: Server source files

Usage:
    python package_extension.py

See: https://github.com/modelcontextprotocol/mcpb
"""

import json
import zipfile
from pathlib import Path

# Extension metadata following MCPB manifest spec v0.4
MANIFEST = {
    "manifest_version": "0.4",
    "name": "ppk2-mcp",
    "display_name": "PPK2 MCP",
    "version": "1.0.0",
    "description": "MCP server for Nordic Semiconductor's Power Profiler Kit II (PPK2). Enables LLMs to perform power profiling and analysis of embedded devices.",
    "long_description": """The PPK2 MCP server allows AI assistants to:

- Connect to and control Nordic PPK2 power profiling hardware
- Measure power consumption of embedded devices (DUT)
- Capture high-resolution current measurements at 100kHz
- Stream real-time power data for live visualization
- Export measurements to CSV for analysis

Perfect for embedded development, IoT device optimization, and battery life analysis.""",
    "author": {
        "name": "PPK2 API Contributors",
        "url": "https://github.com/IRNAS/ppk2-api-python"
    },
    "repository": {
        "type": "git",
        "url": "https://github.com/IRNAS/ppk2-api-python.git"
    },
    "license": "GPL-2.0",
    "keywords": ["power-profiling", "embedded", "nordic", "ppk2", "iot", "hardware"],
    "server": {
        "type": "python",
        "entry_point": "src/ppk2_mcp_server.py",
        "mcp_config": {
            "command": "uvx",
            "args": [
                "--with", "pyserial",
                "--with", "fastmcp",
                "--with", "mcp",
                "fastmcp", "run", "${__dirname}/src/ppk2_mcp_server.py"
            ],
            "env": {
                "PYTHONPATH": "${__dirname}/src"
            }
        }
    },
    "compatibility": {
        "platforms": ["darwin", "win32", "linux"],
        "runtimes": {
            "python": ">=3.9"
        }
    },
    "tools": [
        {"name": "list_devices", "description": "List all connected PPK2 devices with their port and serial number"},
        {"name": "connect", "description": "Connect to a PPK2 device on the specified serial port"},
        {"name": "disconnect", "description": "Disconnect from the currently connected PPK2 device"},
        {"name": "get_status", "description": "Get current connection status, mode, and voltage settings"},
        {"name": "set_mode", "description": "Set measurement mode: 'source' (PPK2 supplies power) or 'ampere' (external supply)"},
        {"name": "set_voltage", "description": "Set source/input voltage in millivolts (800-5000mV)"},
        {"name": "toggle_power", "description": "Toggle DUT (Device Under Test) power on or off"},
        {"name": "measure", "description": "Unified measurement tool: output='stats' for statistics, 'raw' for samples, 'file' for CSV. Optional include_digital for 8-channel logic analyzer (D0-D7)."},
        {"name": "capture_trigger", "description": "Wait for current threshold and/or digital input conditions, then capture. Supports AND/OR logic for combining triggers."},
        {"name": "start_streaming", "description": "Start streaming measurements to a TCP socket, optionally including 8-channel digital logic analyzer data"},
        {"name": "stop_streaming", "description": "Stop the active measurement stream"},
        {"name": "get_streaming_status", "description": "Check if streaming is currently active and get connection details"}
    ]
}

# pyproject.toml for UV runtime
PYPROJECT_TOML = '''[project]
name = "ppk2-mcp-server"
version = "1.0.0"
description = "MCP server for Nordic PPK2 Power Profiler"
requires-python = ">=3.9"
dependencies = [
    "pyserial>=3.5",
    "mcp>=1.0.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
'''

# Files to include in the extension
FILES_TO_INCLUDE = [
    "src/ppk2_mcp_server.py",
    "src/ppk2_api/__init__.py",
    "src/ppk2_api/ppk2_api.py",
    "LICENSE.md",
    "README.md",
]

# Optional files (include if they exist)
OPTIONAL_FILES = [
    "examples/stream_consumer.py",
]


def package_extension():
    """Create the .mcpb extension package."""
    project_root = Path(__file__).parent
    output_name = f"{MANIFEST['name']}-{MANIFEST['version']}.mcpb"
    output_path = project_root / "dist" / output_name

    # Create dist directory
    output_path.parent.mkdir(exist_ok=True)

    print(f"Packaging {MANIFEST['display_name']} v{MANIFEST['version']}...")
    print(f"Using MCPB manifest version {MANIFEST['manifest_version']}\n")

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Add manifest
        manifest_json = json.dumps(MANIFEST, indent=2)
        zf.writestr("manifest.json", manifest_json)
        print("  + manifest.json")

        # Add pyproject.toml for UV runtime
        zf.writestr("pyproject.toml", PYPROJECT_TOML)
        print("  + pyproject.toml")

        # Add required files
        for file_path in FILES_TO_INCLUDE:
            full_path = project_root / file_path
            if full_path.exists():
                zf.write(full_path, file_path)
                print(f"  + {file_path}")
            else:
                print(f"  ! Missing required file: {file_path}")
                return False

        # Add optional files
        for file_path in OPTIONAL_FILES:
            full_path = project_root / file_path
            if full_path.exists():
                zf.write(full_path, file_path)
                print(f"  + {file_path} (optional)")

    # Print summary
    size_kb = output_path.stat().st_size / 1024
    print(f"\n{'='*50}")
    print(f"Created: {output_path}")
    print(f"Size: {size_kb:.1f} KB")
    print(f"\nTo install:")
    print(f"  - Double-click the .mcpb file to install in Claude Desktop")
    print(f"  - Or use: mcpb install {output_path}")
    print(f"{'='*50}")

    return True


if __name__ == "__main__":
    success = package_extension()
    exit(0 if success else 1)
