# Talk to Your Power Profiler: Using the PPK2 MCP Server with AI

If you've ever spent an afternoon squinting at power trace graphs trying to figure out why your IoT device isn't hitting its sleep current target, this one's for you.

The [PPK2 MCP Server](https://github.com/Bucknalla/ppk2-mcp) is an open-source project that bridges Nordic Semiconductor's [Power Profiler Kit II (PPK2)](https://www.nordicsemi.com/Software-and-tools/Development-Tools/Power-Profiler-Kit-2) with AI assistants through Anthropic's [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). In plain terms: it lets you control your power profiler and analyze results by having a conversation with an AI like Claude.

---

## What Is MCP, Briefly?

The Model Context Protocol is an open standard that allows AI models to use external tools — files, APIs, hardware devices — in a structured, safe way. Rather than pasting output into a chat window and hoping the AI makes sense of it, MCP lets the AI directly invoke tools, read results, and act on them within a single workflow.

An MCP *server* exposes a set of named tools. An MCP *client* (like Claude Code) connects to that server and can call those tools on the AI's behalf. The result is a tight feedback loop: the AI can query your device, interpret the data, make a decision, reconfigure the device, and measure again — all without you manually copying numbers around.

---

## What Is the PPK2?

The Power Profiler Kit II is a low-cost USB measurement instrument from Nordic Semiconductor. It samples current at **100 kHz** with microamp resolution, which makes it ideal for embedded and IoT work where power budgets are tight and events happen fast.

It has two modes:

- **Source Meter**: The PPK2 supplies power to your device under test (DUT) — useful for full control of the power rail.
- **Ampere Meter**: You supply power externally; the PPK2 sits in-line and measures current — useful when your device has its own supply or when you need to measure at a specific voltage the PPK2 can't produce.

It also has **8 digital input channels** (D0–D7) for logging GPIO signals alongside the power trace, which is invaluable for correlating firmware events with current spikes.

---

## What Does the MCP Server Expose?

The server implements **13 tools** that map directly to PPK2 operations:

| Tool | What It Does |
|------|-------------|
| `list_devices` | Scan serial ports and find connected PPK2 devices |
| `connect` | Open a serial connection to the device |
| `disconnect` | Close the connection cleanly |
| `get_status` | Query current mode, voltage, and connection state |
| `set_mode` | Switch between source and ampere meter modes |
| `set_voltage` | Set the supply voltage (800–5000 mV) |
| `toggle_power` | Turn the DUT power rail on or off |
| `measure` | Capture a fixed-duration measurement and return stats, raw data, or a CSV file |
| `capture_trigger` | Wait for a condition (current threshold or GPIO edge), then capture |
| `start_streaming` | Open a TCP server that streams live measurements as JSON |
| `stop_streaming` | Shut down the TCP stream |
| `get_streaming_status` | Check whether streaming is currently active |

These tools give an AI everything it needs to conduct a proper power measurement session: discover hardware, configure it, take measurements, and export data.

---

## How Would You Actually Use This?

Here are a few concrete workflows that become much easier when an AI can drive the instrument.

### 1. Quick Power Budget Check

You've just flashed a new firmware build and want a sanity check before diving into nRF Power Profiler or a spreadsheet.

```
You: Measure the power consumption of my device for 10 seconds and give me a summary.

Claude:
  1. list_devices()        → PPK2 found at /dev/ttyACM0
  2. connect("/dev/ttyACM0")
  3. set_mode("source")
  4. set_voltage(3300)     → 3.3 V
  5. toggle_power("on")
  6. measure(10.0, output="stats")

  Results:
    Average: 18.4 mA
    Min:      0.3 mA  (sleep state)
    Max:    142.1 mA  (peak on radio TX)
    Samples: 1,000,000 @ 100 kHz
```

The AI can then compare that against your power budget targets or previous measurements without you needing to manually extract numbers.

---

### 2. Event-Triggered Capture

Your device wakes up, does something, and goes back to sleep. You want to capture just that activity window without sitting and watching a live graph waiting for it to happen.

```
You: Wait for my device to wake up (current above 5 mA) and capture 3 seconds of data.

Claude:
  1. capture_trigger(current_above_uA=5000, duration_seconds=3.0, timeout_seconds=60)

  → Waited 8.2 seconds for trigger
  → Condition met: current > 5000 uA
  → Captured 3 seconds (300,000 samples):
       Average: 22.7 mA
       Peak:   138.4 mA
```

You could also trigger on a GPIO signal. If your firmware toggles D0 when a task starts:

```
You: Capture power data when D0 goes high, save to a CSV.

Claude:
  1. capture_trigger(digital_d0="high", duration_seconds=1.0, output="file", output_file="task_power.csv")

  → Triggered on D0 rising edge after 3.2 seconds
  → 100,000 samples saved to task_power.csv
```

This is particularly useful for profiling specific firmware tasks — BLE advertisements, sensor reads, cryptographic operations — without instrumenting the code beyond a single GPIO toggle.

---

### 3. Comparative Analysis Across Firmware Builds

One of the most tedious parts of power optimization is keeping track of numbers across builds. With an AI in the loop, you can ask it to run the same measurement sequence on multiple firmware versions and summarize the differences.

```
You: Flash build A, measure power for 5 seconds, then I'll flash build B and you measure again.
     Compare the two.

[After build A measurement]
Claude: Build A average: 14.2 mA, peak: 98.3 mA

[After build B flash and measurement]
Claude: Build B average: 11.8 mA, peak: 87.1 mA

Build B uses 17% less average current and the peak dropped by 11%.
The minimum current is identical (0.3 mA), suggesting the sleep state
is the same but the active period is shorter or less intensive.
```

---

### 4. Real-Time Monitoring with Live Visualization

For interactive debugging sessions, the server can open a TCP stream that a visualization client consumes:

```bash
# Ask Claude to start streaming
# → start_streaming(port=5555)

# Run the included visualization tool
python examples/stream_consumer.py --split --window 2 --log
```

The `stream_consumer.py` tool plots a rolling window of the live current trace. The `--split` flag gives you separate panels for average current and min/max peaks, and `--log` enables a log-scale Y axis — essential when your device swings from 50 µA sleep to 150 mA TX bursts.

The stream format is simple JSON lines, so you can pipe it into your own tooling:

```json
{"t": 1.234, "uA": 15200.5, "n": 1000, "d": [0, 0, 1, 0, 0, 0, 0, 0]}
```

---

## Getting Started

### Prerequisites

- Nordic PPK2 hardware
- Python 3.9+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) (or any MCP-compatible client)

### Installation

```bash
git clone https://github.com/Bucknalla/ppk2-mcp.git
cd ppk2-mcp
pip install -r requirements.txt
```

### Connect to Claude Code

The repo includes a `.mcp.json` that Claude Code picks up automatically — just open the project directory and the PPK2 tools will be available.

For manual configuration, add this to your Claude Code MCP settings:

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

On Linux you may need to add your user to the `dialout` group to access serial ports:

```bash
sudo usermod -aG dialout $USER
# Log out and back in for the change to take effect
```

---

## Why This Matters

Power optimization in embedded systems is iterative and measurement-heavy. The traditional workflow — configure instrument, capture, export, import into analysis tool, interpret, tweak firmware, repeat — has a lot of friction. Each step requires context-switching and manual data handling.

An AI with direct instrument access can run that loop faster, keep track of results across iterations, and surface patterns ("your average current went up but your peak went down — that suggests the activity period got longer even though peak draw decreased"). It's not replacing the engineer's judgment; it's removing the mechanical overhead so you can spend more time thinking and less time copying numbers.

The PPK2 is already one of the most accessible tools in the embedded power measurement space. This MCP server makes it conversational.

---

## Project Details

- **Source:** [github.com/Bucknalla/ppk2-mcp](https://github.com/Bucknalla/ppk2-mcp)
- **Based on:** [IRNAS/ppk2-api-python](https://github.com/IRNAS/ppk2-api-python)
- **License:** GPL v2
- **Platform:** Linux, macOS, Windows
