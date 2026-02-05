# PPK2 MCP Server

> **Note:** This project is a fork of [IRNAS/ppk2-api-python](https://github.com/IRNAS/ppk2-api-python), which provides the underlying Python API for the PPK2. This fork adds an MCP server layer to enable AI-assisted power profiling.

An MCP (Model Context Protocol) server for Nordic Semiconductor's [Power Profiler Kit II (PPK2)](https://www.nordicsemi.com/Software-and-tools/Development-Tools/Power-Profiler-Kit-2), enabling AI-assisted power profiling of embedded devices.

![Power Profiler Kit II](https://github.com/IRNAS/ppk2-api-python/blob/master/images/power-profiler-kit-II.jpg)

## Features

- **13 MCP tools** for complete PPK2 control via LLMs
- **100kHz sampling rate** for detailed power analysis
- **Real-time streaming** via TCP for live visualization
- **Source and Ampere meter modes** for flexible measurement setups
- **Cross-platform support** (macOS, Windows, Linux)

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/Bucknalla/ppk2-mcp.git
cd ppk2-mcp

# Install dependencies
pip install -r requirements.txt
```

### Using with Claude Code

The repository includes a `.mcp.json` configuration file. Simply open the project in Claude Code and the PPK2 MCP server will be available.

Alternatively, add to your Claude Code MCP settings:

```json
{
  "mcpServers": {
    "ppk2": {
      "command": "uvx",
      "args": [
        "--with", "pyserial",
        "--with", "fastmcp",
        "fastmcp", "run", "src/ppk2_mcp_server.py"
      ]
    }
  }
}
```

### Running Standalone

```bash
python src/ppk2_mcp_server.py
```

## MCP Tools

| Tool                   | Description                              | Arguments                                                                                                                   |
| ---------------------- | ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `list_devices`         | List all connected PPK2 devices          | _(none)_                                                                                                                    |
| `connect`              | Connect to a PPK2 device                 | `port` (serial port path)                                                                                                   |
| `disconnect`           | Disconnect from the PPK2                 | _(none)_                                                                                                                    |
| `get_status`           | Get connection status, mode, and voltage | _(none)_                                                                                                                    |
| `set_mode`             | Set measurement mode                     | `mode`: `source` or `ampere`                                                                                                |
| `set_voltage`          | Set voltage in millivolts                | `millivolts`: 800-5000                                                                                                      |
| `toggle_power`         | Turn DUT power on/off                    | `state`: `on` or `off`                                                                                                      |
| `measure`              | Capture power measurements               | `duration_seconds`, `output`: `stats`/`raw`/`file`, `include_digital`, `output_file`                                        |
| `capture_trigger`      | Capture when conditions met              | `current_above_uA`, `current_below_uA`, `digital_d0`-`d7`, `trigger_logic`, `timeout_seconds`, `duration_seconds`, `output` |
| `start_streaming`      | Start TCP streaming                      | `port` (default: 5555), `include_digital`                                                                                   |
| `stop_streaming`       | Stop the measurement stream              | _(none)_                                                                                                                    |
| `get_streaming_status` | Check streaming status                   | _(none)_                                                                                                                    |

## Example Workflows

### Basic Power Measurement

```
User: Measure the power consumption of my device for 5 seconds

AI: I'll connect to the PPK2 and measure power consumption.

1. list_devices() → Found PPK2 at /dev/ttyACM0
2. connect("/dev/ttyACM0") → Connected
3. set_mode("source") → Source meter mode enabled
4. set_voltage(3300) → Voltage set to 3.3V
5. toggle_power("on") → DUT powered on
6. measure(5.0) → Results:
   - Average: 15.2 mA
   - Min: 0.5 mA
   - Max: 125.8 mA
   - Samples: 500,000
```

### Real-Time Streaming

```bash
# 1. Start streaming via MCP
start_streaming(port=5555)

# 2. Run the visualization tool
python examples/stream_consumer.py --split --window 2

# 3. Stop when done
stop_streaming()
```

### Triggered Capture

```
User: Capture power data when my device wakes up (current goes above 10mA)

AI: I'll set up a triggered capture that waits for the current spike.

1. list_devices() → Found PPK2 at /dev/ttyACM0
2. connect("/dev/ttyACM0") → Connected
3. set_mode("source") → Source meter mode enabled
4. set_voltage(3300) → Voltage set to 3.3V
5. toggle_power("on") → DUT powered on
6. capture_trigger(current_above_uA=10000, duration_seconds=2.0, timeout_seconds=60) →
   - Triggered: True
   - Trigger time: 12,450 ms (waited 12.4 seconds)
   - Condition met: "current > 10000 uA"
   - Captured 2 seconds of data:
     - Average: 45.3 mA
     - Peak: 152.7 mA
```

```
User: Capture when D0 goes high (GPIO indicating task start)

AI: I'll trigger on the digital input.

1. capture_trigger(digital_d0="high", duration_seconds=1.0, output="file", output_file="task_power.csv") →
   - Triggered: True
   - Trigger time: 3,200 ms
   - Condition met: "D0 = high"
   - Saved 100,000 samples to task_power.csv
```

## Live Visualization

The included `stream_consumer.py` provides real-time power visualization:

```bash
# Basic view
python examples/stream_consumer.py

# Split view (average + peaks) with 2-second window
python examples/stream_consumer.py --split --window 2

# Log scale for high dynamic range
python examples/stream_consumer.py --log --split
```

**Options:**
- `--window N` - Display N seconds of data (default: 5)
- `--split` - Separate plots for average and min/max
- `--log` - Logarithmic Y-axis
- `--smooth N` - Smoothing window size (default: 20)

## Packaging & Distribution

Build an MCPB bundle for distribution:

```bash
python package_extension.py
# Creates: dist/ppk2-power-profiler-1.0.0.mcpb
```

Install the bundle:
```bash
# Double-click the .mcpb file, or:
mcpb install dist/ppk2-power-profiler-1.0.0.mcpb
```

## Python API

For direct Python usage without MCP, see the API documentation below.

### Source Meter Mode

```python
from ppk2_api.ppk2_api import PPK2_API

ppk2 = PPK2_API("/dev/ttyACM0", timeout=1)
ppk2.get_modifiers()
ppk2.use_source_meter()
ppk2.set_source_voltage(3300)  # 3.3V
ppk2.start_measuring()

for i in range(100):
    data = ppk2.get_data()
    if data:
        samples, _ = ppk2.get_samples(data)
        avg = sum(samples) / len(samples)
        print(f"Average: {avg:.2f} uA")
    time.sleep(0.01)

ppk2.stop_measuring()
```

### Ampere Meter Mode

```python
ppk2.use_ampere_meter()  # External power supply
ppk2.set_source_voltage(3300)  # Set expected voltage for accuracy
ppk2.start_measuring()
# ... measure current from external supply
```

### Multiprocessing Version

For continuous sampling without data loss:

```python
from ppk2_api.ppk2_api import PPK2_MP

ppk2 = PPK2_MP("/dev/ttyACM0")
ppk2.get_modifiers()
ppk2.use_source_meter()
ppk2.set_source_voltage(3300)
ppk2.start_measuring()

# Background thread handles continuous sampling
for i in range(10):
    data = ppk2.get_data()
    if data:
        samples, _ = ppk2.get_samples(data)
        print(f"Captured {len(samples)} samples")
    time.sleep(1)  # Can do other work

ppk2.stop_measuring()
```

## Requirements

- Python 3.9+
- Nordic PPK2 hardware
- Serial port access (may require permissions on Linux)

## License

Licensed under [GPL V2](https://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html).

Based on [ppk2-api-python](https://github.com/IRNAS/ppk2-api-python) by IRNAS.
