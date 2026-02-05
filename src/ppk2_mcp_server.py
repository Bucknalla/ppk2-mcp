"""
MCP Server for Nordic Semiconductor's Power Profiler Kit II (PPK2).
Exposes power profiling functionality to LLMs via the Model Context Protocol.
"""

import json
import socket
import threading
import time
from typing import Optional
from fastmcp import FastMCP

from ppk2_api.ppk2_api import PPK2_API, PPK2_Modes

mcp = FastMCP("PPK2 Power Profiler")

# Global state for the connected PPK2
_ppk2: Optional[PPK2_API] = None

# Global state for streaming
_stream_thread: Optional[threading.Thread] = None
_stream_stop_event: Optional[threading.Event] = None
_stream_server: Optional[socket.socket] = None
_stream_port: Optional[int] = None


@mcp.tool
def list_devices() -> list[dict]:
    """List all connected PPK2 devices.

    Returns a list of devices with their port and serial number.
    """
    devices = PPK2_API.list_devices()
    return [{"port": port, "serial_number": serial} for port, serial in devices]


@mcp.tool
def connect(port: str) -> str:
    """Connect to a PPK2 device on the specified port.

    Args:
        port: The serial port where the PPK2 is connected (e.g., '/dev/ttyACM0' or 'COM3')

    Returns:
        Status message indicating success or failure.
    """
    global _ppk2

    if _ppk2 is not None:
        return "Already connected to a PPK2. Disconnect first."

    try:
        _ppk2 = PPK2_API(port, timeout=1, write_timeout=1, exclusive=True)
        result = _ppk2.get_modifiers()
        if result is None:
            _ppk2 = None
            return f"Failed to connect to PPK2 on port {port}. Could not read calibration data."
        return f"Successfully connected to PPK2 on port {port}"
    except Exception as e:
        _ppk2 = None
        return f"Failed to connect to PPK2 on port {port}: {e}"


@mcp.tool
def disconnect() -> str:
    """Disconnect from the currently connected PPK2 device.

    Returns:
        Status message indicating success or failure.
    """
    global _ppk2

    if _ppk2 is None:
        return "No PPK2 connected."

    try:
        _ppk2.stop_measuring()
    except Exception:
        pass

    _ppk2 = None
    return "Disconnected from PPK2."


@mcp.tool
def get_status() -> dict:
    """Get the current status of the PPK2 connection and configuration.

    Returns:
        Dictionary with connection status, mode, and voltage settings.
    """
    if _ppk2 is None:
        return {"connected": False}

    return {
        "connected": True,
        "mode": _ppk2.mode,
        "voltage_mV": _ppk2.current_vdd,
    }


@mcp.tool
def set_mode(mode: str) -> str:
    """Set the PPK2 measurement mode.

    Args:
        mode: Either 'source' (PPK2 supplies power) or 'ampere' (external power supply)

    Returns:
        Status message indicating success or failure.
    """
    if _ppk2 is None:
        return "No PPK2 connected. Use connect() first."

    mode_lower = mode.lower()
    if mode_lower == "source":
        _ppk2.use_source_meter()
        return "Set to source meter mode. PPK2 will supply power to DUT."
    elif mode_lower == "ampere":
        _ppk2.use_ampere_meter()
        return "Set to ampere meter mode. External power supply required."
    else:
        return f"Invalid mode '{mode}'. Use 'source' or 'ampere'."


@mcp.tool
def set_voltage(voltage_mV: int) -> str:
    """Set the source/input voltage in millivolts.

    In source mode, this is the voltage supplied to the DUT.
    In ampere mode, this is the voltage of the external supply (for accurate measurements).

    Args:
        voltage_mV: Voltage in millivolts (800-5000)

    Returns:
        Status message indicating success or failure.
    """
    if _ppk2 is None:
        return "No PPK2 connected. Use connect() first."

    if voltage_mV < 800 or voltage_mV > 5000:
        return f"Voltage {voltage_mV}mV out of range. Must be between 800-5000mV."

    _ppk2.set_source_voltage(voltage_mV)
    return f"Voltage set to {voltage_mV}mV"


@mcp.tool
def toggle_power(state: str) -> str:
    """Toggle the DUT (Device Under Test) power on or off.

    Only applicable in source meter mode.

    Args:
        state: Either 'on' or 'off'

    Returns:
        Status message indicating success or failure.
    """
    if _ppk2 is None:
        return "No PPK2 connected. Use connect() first."

    state_upper = state.upper()
    if state_upper not in ("ON", "OFF"):
        return f"Invalid state '{state}'. Use 'on' or 'off'."

    _ppk2.toggle_DUT_power(state_upper)
    return f"DUT power turned {state_upper}"


@mcp.tool
def measure(
    duration_seconds: float = 1.0,
    output: str = "stats",
    include_digital: bool = False,
    output_file: str = "power_measurement.csv"
) -> dict:
    """Capture power measurements with flexible output options.

    A unified measurement tool that can return statistics, raw samples, or save to file.
    Optionally includes 8-channel digital logic analyzer data (D0-D7).

    Prerequisites: Must call set_mode() and set_voltage() before measuring.

    Args:
        duration_seconds: How long to measure in seconds (default: 1.0)
            - For "stats": max 60s
            - For "raw": max 1s (limited to avoid large responses)
            - For "file": max 60s
        output: Output format (default: "stats")
            - "stats": Return min/max/avg statistics only
            - "raw": Return raw sample arrays (for visualization)
            - "file": Save to CSV file
        include_digital: Include 8-channel digital logic analyzer data (default: False)
            - Adds D0-D7 channel statistics or raw data depending on output mode
        output_file: Path for CSV output when output="file" (default: "power_measurement.csv")

    Returns:
        On success with output="stats":
            - sample_count, duration_seconds, samples_per_second
            - min_uA, max_uA, avg_uA
            - digital_channels (if include_digital): D0-D7 with duty_cycle, transitions, frequency

        On success with output="raw":
            - samples_uA: List of current measurements
            - statistics: min/max/avg
            - digital_channels (if include_digital): D0-D7 raw state arrays

        On success with output="file":
            - file_path: Path to saved CSV
            - statistics: min/max/avg
            - CSV includes digital columns if include_digital

        On failure:
            - error: Description of what went wrong
    """
    import os

    if _ppk2 is None:
        return {"error": "No PPK2 connected. Use connect() first."}

    if _ppk2.current_vdd is None:
        return {"error": "Voltage not set. Use set_voltage() first."}

    if _ppk2.mode is None:
        return {"error": "Mode not set. Use set_mode() first."}

    # Validate output mode
    output = output.lower()
    if output not in ("stats", "raw", "file"):
        return {"error": f"Invalid output mode '{output}'. Use 'stats', 'raw', or 'file'."}

    # Clamp duration based on output mode
    if output == "raw":
        duration_seconds = min(max(duration_seconds, 0.01), 1.0)
    else:
        duration_seconds = min(max(duration_seconds, 0.1), 60.0)

    all_samples = []
    all_digital = []

    try:
        _ppk2.start_measuring()
        start_time = time.time()

        while (time.time() - start_time) < duration_seconds:
            read_data = _ppk2.get_data()
            if read_data != b'':
                samples, raw_digital = _ppk2.get_samples(read_data)
                all_samples.extend(samples)
                if include_digital:
                    all_digital.extend(raw_digital)
            time.sleep(0.001)

        _ppk2.stop_measuring()
        actual_duration = time.time() - start_time

    except Exception as e:
        try:
            _ppk2.stop_measuring()
        except Exception:
            pass
        return {"error": f"Measurement failed: {e}"}

    if not all_samples:
        return {
            "error": "No samples collected. Check connection and configuration.",
            "duration_seconds": actual_duration,
        }

    # Calculate statistics
    stats = {
        "min_uA": round(min(all_samples), 3),
        "max_uA": round(max(all_samples), 3),
        "avg_uA": round(sum(all_samples) / len(all_samples), 3),
    }

    # Process digital channels if requested
    digital_data = None
    if include_digital and all_digital:
        digital_channels = _ppk2.digital_channels(all_digital)

        if output == "raw":
            # Return raw digital data (downsampled if needed)
            max_points = 5000
            if len(all_samples) > max_points:
                step = len(all_samples) // max_points
                digital_data = {
                    f"D{i}": [digital_channels[i][j] for j in range(0, len(digital_channels[i]), step)]
                    for i in range(8)
                }
            else:
                digital_data = {f"D{i}": digital_channels[i] for i in range(8)}
        else:
            # Return digital statistics
            digital_data = {}
            for i, channel_data in enumerate(digital_channels):
                if not channel_data:
                    continue
                high_count = sum(channel_data)
                transitions = sum(1 for j in range(1, len(channel_data)) if channel_data[j] != channel_data[j-1])
                digital_data[f"D{i}"] = {
                    "high_count": high_count,
                    "low_count": len(channel_data) - high_count,
                    "duty_cycle_percent": round(100 * high_count / len(channel_data), 2),
                    "transitions": transitions,
                    "frequency_hz": round(transitions / (2 * actual_duration), 1) if transitions > 0 else 0,
                }

    # Build response based on output mode
    base_response = {
        "sample_count": len(all_samples),
        "duration_seconds": round(actual_duration, 3),
        "samples_per_second": round(len(all_samples) / actual_duration, 1),
    }

    if output == "stats":
        result = {**base_response, **stats, "unit": "microamps (uA)"}
        if digital_data:
            result["digital_channels"] = digital_data
        return result

    elif output == "raw":
        # Downsample if needed
        max_points = 5000
        if len(all_samples) > max_points:
            step = len(all_samples) // max_points
            samples_out = [round(all_samples[i], 2) for i in range(0, len(all_samples), step)]
        else:
            samples_out = [round(s, 2) for s in all_samples]

        result = {
            **base_response,
            "samples_uA": samples_out,
            "statistics": stats,
            "unit": "microamps (uA)",
        }
        if digital_data:
            result["digital_channels"] = digital_data
        return result

    elif output == "file":
        time_interval_us = (actual_duration * 1_000_000) / len(all_samples)

        try:
            output_path = os.path.abspath(output_file)
            with open(output_path, 'w') as f:
                # Write header
                if include_digital and all_digital:
                    f.write("timestamp_us,current_uA,D0,D1,D2,D3,D4,D5,D6,D7\n")
                    digital_channels = _ppk2.digital_channels(all_digital)
                    for i, sample in enumerate(all_samples):
                        timestamp = round(i * time_interval_us, 3)
                        digital_vals = ",".join(str(digital_channels[ch][i]) for ch in range(8))
                        f.write(f"{timestamp},{round(sample, 3)},{digital_vals}\n")
                else:
                    f.write("timestamp_us,current_uA\n")
                    for i, sample in enumerate(all_samples):
                        timestamp = round(i * time_interval_us, 3)
                        f.write(f"{timestamp},{round(sample, 3)}\n")
        except Exception as e:
            return {"error": f"Failed to write file: {e}"}

        result = {
            **base_response,
            "file_path": output_path,
            "statistics": stats,
            "unit": "microamps (uA)",
        }
        if digital_data:
            result["digital_channels"] = digital_data
        return result


def _streaming_worker(ppk2: PPK2_API, server_socket: socket.socket, stop_event: threading.Event, include_digital: bool = False):
    """Background worker that streams measurements to connected TCP clients."""
    clients = []
    server_socket.setblocking(False)
    sample_count = 0
    start_time = time.time()

    try:
        ppk2.start_measuring()

        while not stop_event.is_set():
            # Accept new connections (non-blocking)
            try:
                client, addr = server_socket.accept()
                client.setblocking(False)
                clients.append(client)
            except BlockingIOError:
                pass

            # Read and stream data
            read_data = ppk2.get_data()
            if read_data != b'':
                samples, raw_digital = ppk2.get_samples(read_data)
                current_time = time.time() - start_time

                # Process digital channels if requested
                if include_digital and raw_digital:
                    digital_channels = ppk2.digital_channels(raw_digital)

                for i, sample in enumerate(samples):
                    sample_count += 1
                    # Create JSON line
                    record = {
                        "t": round(current_time, 6),
                        "uA": round(sample, 2),
                        "n": sample_count
                    }

                    # Add digital channel data if requested
                    if include_digital and raw_digital and i < len(digital_channels[0]):
                        record["d"] = [digital_channels[ch][i] for ch in range(8)]

                    data = json.dumps(record) + "\n"
                    data_bytes = data.encode('utf-8')

                    # Send to all connected clients
                    dead_clients = []
                    for client in clients:
                        try:
                            client.sendall(data_bytes)
                        except (BrokenPipeError, ConnectionResetError, BlockingIOError):
                            dead_clients.append(client)

                    # Remove disconnected clients
                    for client in dead_clients:
                        clients.remove(client)
                        try:
                            client.close()
                        except Exception:
                            pass

                    # Update time for next sample (approximate)
                    current_time += 0.00001  # ~100kHz sample rate

            time.sleep(0.001)

        ppk2.stop_measuring()

    except Exception as e:
        try:
            ppk2.stop_measuring()
        except Exception:
            pass
    finally:
        # Clean up clients
        for client in clients:
            try:
                client.close()
            except Exception:
                pass


@mcp.tool
def start_streaming(port: int = 5555, include_digital: bool = False) -> dict:
    """Start streaming power measurements to a TCP socket.

    Opens a TCP server on the specified port. Clients can connect to receive
    real-time measurements as JSON lines.

    Prerequisites: Must call set_mode() and set_voltage() before streaming.

    Connect with: nc localhost 5555
    Or use the example script: python examples/stream_consumer.py

    Data format (JSON lines):
        Without digital: {"t": 0.001234, "uA": 125.5, "n": 1}
        With digital:    {"t": 0.001234, "uA": 125.5, "n": 1, "d": [0,0,1,0,0,0,0,0]}

    Fields:
        - t: Timestamp in seconds since stream start
        - uA: Current in microamps
        - n: Sample number
        - d: (optional) Digital channel states [D0,D1,D2,D3,D4,D5,D6,D7] as 0 or 1

    Args:
        port: TCP port to listen on (default: 5555)
        include_digital: Include 8-channel digital logic analyzer data (default: False)

    Returns:
        On success: {"status": "streaming", "port": 5555, "host": "localhost", "include_digital": true/false}
        On failure: {"error": "..."}
    """
    global _stream_thread, _stream_stop_event, _stream_server, _stream_port

    if _ppk2 is None:
        return {"error": "No PPK2 connected. Use connect() first."}

    if _ppk2.current_vdd is None:
        return {"error": "Voltage not set. Use set_voltage() first."}

    if _ppk2.mode is None:
        return {"error": "Mode not set. Use set_mode() first."}

    if _stream_thread is not None and _stream_thread.is_alive():
        return {"error": f"Already streaming on port {_stream_port}. Call stop_streaming() first."}

    try:
        # Create TCP server
        _stream_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _stream_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _stream_server.bind(('localhost', port))
        _stream_server.listen(5)
        _stream_port = port

        # Start streaming thread
        _stream_stop_event = threading.Event()
        _stream_thread = threading.Thread(
            target=_streaming_worker,
            args=(_ppk2, _stream_server, _stream_stop_event, include_digital),
            daemon=True
        )
        _stream_thread.start()

        return {
            "status": "streaming",
            "port": port,
            "host": "localhost",
            "include_digital": include_digital,
            "connect_with": f"nc localhost {port}",
        }

    except Exception as e:
        if _stream_server:
            try:
                _stream_server.close()
            except Exception:
                pass
            _stream_server = None
        return {"error": f"Failed to start streaming: {e}"}


@mcp.tool
def stop_streaming() -> dict:
    """Stop the active measurement stream.

    Stops the TCP server and measurement. Connected clients will be disconnected.

    Returns:
        Status message indicating success or that no stream was active.
    """
    global _stream_thread, _stream_stop_event, _stream_server, _stream_port

    if _stream_thread is None or not _stream_thread.is_alive():
        return {"status": "No active stream to stop."}

    # Signal thread to stop
    _stream_stop_event.set()

    # Wait for thread to finish (with timeout)
    _stream_thread.join(timeout=2.0)

    # Close server socket
    if _stream_server:
        try:
            _stream_server.close()
        except Exception:
            pass

    stopped_port = _stream_port
    _stream_thread = None
    _stream_stop_event = None
    _stream_server = None
    _stream_port = None

    return {"status": "stopped", "port": stopped_port}


@mcp.tool
def get_streaming_status() -> dict:
    """Check if streaming is currently active.

    Returns:
        Dictionary with streaming status and port if active.
    """
    if _stream_thread is not None and _stream_thread.is_alive():
        return {
            "streaming": True,
            "port": _stream_port,
            "host": "localhost",
        }
    return {"streaming": False}


@mcp.tool
def capture_trigger(
    duration_seconds: float = 1.0,
    timeout_seconds: float = 30.0,
    current_above_uA: float = None,
    current_below_uA: float = None,
    digital_d0: str = None,
    digital_d1: str = None,
    digital_d2: str = None,
    digital_d3: str = None,
    digital_d4: str = None,
    digital_d5: str = None,
    digital_d6: str = None,
    digital_d7: str = None,
    trigger_logic: str = "or",
    output: str = "stats",
    output_file: str = "triggered_capture.csv"
) -> dict:
    """Capture power measurements when trigger conditions are met.

    Waits for current threshold and/or digital input conditions, then captures
    data for the specified duration. Useful for capturing transient events.

    Prerequisites: Must call set_mode() and set_voltage() before capturing.

    Args:
        duration_seconds: How long to capture after trigger (default: 1.0, max: 10.0)
        timeout_seconds: How long to wait for trigger (default: 30.0, max: 120.0)
        current_above_uA: Trigger when current exceeds this value in microamps
        current_below_uA: Trigger when current drops below this value in microamps
        digital_d0-d7: Trigger state for each digital channel: "high", "low", or None (ignore)
        trigger_logic: How to combine conditions - "or" (any condition) or "and" (all conditions)
        output: Output format - "stats", "raw", or "file" (same as measure tool)
        output_file: Path for CSV output when output="file"

    Returns:
        On success:
            - triggered: True
            - trigger_time_ms: Time until trigger was detected
            - trigger_conditions_met: Which conditions triggered the capture
            - (measurement data based on output format, same as measure tool)

        On timeout:
            - triggered: False
            - error: "Timeout waiting for trigger"
            - waited_seconds: How long we waited

        On failure:
            - error: Description of what went wrong
    """
    import os

    if _ppk2 is None:
        return {"error": "No PPK2 connected. Use connect() first."}

    if _ppk2.current_vdd is None:
        return {"error": "Voltage not set. Use set_voltage() first."}

    if _ppk2.mode is None:
        return {"error": "Mode not set. Use set_mode() first."}

    # Validate output mode
    output = output.lower()
    if output not in ("stats", "raw", "file"):
        return {"error": f"Invalid output mode '{output}'. Use 'stats', 'raw', or 'file'."}

    # Validate trigger_logic
    trigger_logic = trigger_logic.lower()
    if trigger_logic not in ("and", "or"):
        return {"error": f"Invalid trigger_logic '{trigger_logic}'. Use 'and' or 'or'."}

    # Build trigger conditions
    digital_triggers = {}
    for i, state in enumerate([digital_d0, digital_d1, digital_d2, digital_d3,
                                digital_d4, digital_d5, digital_d6, digital_d7]):
        if state is not None:
            state = state.lower()
            if state not in ("high", "low"):
                return {"error": f"Invalid digital_d{i} state '{state}'. Use 'high' or 'low'."}
            digital_triggers[i] = 1 if state == "high" else 0

    # Check that at least one trigger condition is set
    has_current_trigger = current_above_uA is not None or current_below_uA is not None
    has_digital_trigger = len(digital_triggers) > 0

    if not has_current_trigger and not has_digital_trigger:
        return {"error": "No trigger conditions set. Specify current_above_uA, current_below_uA, or digital_d0-d7."}

    # Clamp durations
    duration_seconds = min(max(duration_seconds, 0.1), 10.0)
    timeout_seconds = min(max(timeout_seconds, 1.0), 120.0)

    def check_trigger(samples, digital_channels):
        """Check if trigger conditions are met. Returns (triggered, conditions_met)."""
        conditions_met = []

        if not samples:
            return False, []

        current_val = samples[-1]  # Check most recent sample

        # Check current conditions
        current_triggered = []
        if current_above_uA is not None and current_val > current_above_uA:
            current_triggered.append(f"current > {current_above_uA} uA")
        if current_below_uA is not None and current_val < current_below_uA:
            current_triggered.append(f"current < {current_below_uA} uA")

        # Check digital conditions
        digital_triggered = []
        if digital_channels and len(digital_channels) == 8:
            for ch, expected in digital_triggers.items():
                if digital_channels[ch] and digital_channels[ch][-1] == expected:
                    state_name = "high" if expected == 1 else "low"
                    digital_triggered.append(f"D{ch} = {state_name}")

        conditions_met = current_triggered + digital_triggered

        if trigger_logic == "or":
            # Any condition triggers
            triggered = len(conditions_met) > 0
        else:
            # All conditions must be met
            total_conditions = 0
            if current_above_uA is not None:
                total_conditions += 1
            if current_below_uA is not None:
                total_conditions += 1
            total_conditions += len(digital_triggers)
            triggered = len(conditions_met) == total_conditions

        return triggered, conditions_met

    # Start measuring and wait for trigger
    try:
        _ppk2.start_measuring()
        start_time = time.time()
        triggered = False
        trigger_time_ms = 0
        conditions_met = []

        # Phase 1: Wait for trigger
        while (time.time() - start_time) < timeout_seconds:
            read_data = _ppk2.get_data()
            if read_data != b'':
                samples, raw_digital = _ppk2.get_samples(read_data)
                digital_channels = _ppk2.digital_channels(raw_digital) if raw_digital else None

                triggered, conditions_met = check_trigger(samples, digital_channels)
                if triggered:
                    trigger_time_ms = round((time.time() - start_time) * 1000, 1)
                    break

            time.sleep(0.001)

        if not triggered:
            _ppk2.stop_measuring()
            return {
                "triggered": False,
                "error": "Timeout waiting for trigger",
                "waited_seconds": round(time.time() - start_time, 2),
            }

        # Phase 2: Capture data after trigger
        all_samples = []
        all_digital = []
        capture_start = time.time()

        while (time.time() - capture_start) < duration_seconds:
            read_data = _ppk2.get_data()
            if read_data != b'':
                samples, raw_digital = _ppk2.get_samples(read_data)
                all_samples.extend(samples)
                all_digital.extend(raw_digital)
            time.sleep(0.001)

        _ppk2.stop_measuring()
        actual_duration = time.time() - capture_start

    except Exception as e:
        try:
            _ppk2.stop_measuring()
        except Exception:
            pass
        return {"error": f"Capture failed: {e}"}

    if not all_samples:
        return {
            "triggered": True,
            "trigger_time_ms": trigger_time_ms,
            "trigger_conditions_met": conditions_met,
            "error": "No samples collected after trigger.",
        }

    # Calculate statistics
    stats = {
        "min_uA": round(min(all_samples), 3),
        "max_uA": round(max(all_samples), 3),
        "avg_uA": round(sum(all_samples) / len(all_samples), 3),
    }

    # Process digital channels
    digital_data = None
    include_digital = has_digital_trigger or len(all_digital) > 0
    if include_digital and all_digital:
        digital_channels = _ppk2.digital_channels(all_digital)

        if output == "raw":
            max_points = 5000
            if len(all_samples) > max_points:
                step = len(all_samples) // max_points
                digital_data = {
                    f"D{i}": [digital_channels[i][j] for j in range(0, len(digital_channels[i]), step)]
                    for i in range(8)
                }
            else:
                digital_data = {f"D{i}": digital_channels[i] for i in range(8)}
        else:
            digital_data = {}
            for i, channel_data in enumerate(digital_channels):
                if not channel_data:
                    continue
                high_count = sum(channel_data)
                transitions = sum(1 for j in range(1, len(channel_data)) if channel_data[j] != channel_data[j-1])
                digital_data[f"D{i}"] = {
                    "high_count": high_count,
                    "low_count": len(channel_data) - high_count,
                    "duty_cycle_percent": round(100 * high_count / len(channel_data), 2),
                    "transitions": transitions,
                    "frequency_hz": round(transitions / (2 * actual_duration), 1) if transitions > 0 else 0,
                }

    # Build response
    base_response = {
        "triggered": True,
        "trigger_time_ms": trigger_time_ms,
        "trigger_conditions_met": conditions_met,
        "sample_count": len(all_samples),
        "duration_seconds": round(actual_duration, 3),
        "samples_per_second": round(len(all_samples) / actual_duration, 1),
    }

    if output == "stats":
        result = {**base_response, **stats, "unit": "microamps (uA)"}
        if digital_data:
            result["digital_channels"] = digital_data
        return result

    elif output == "raw":
        max_points = 5000
        if len(all_samples) > max_points:
            step = len(all_samples) // max_points
            samples_out = [round(all_samples[i], 2) for i in range(0, len(all_samples), step)]
        else:
            samples_out = [round(s, 2) for s in all_samples]

        result = {
            **base_response,
            "samples_uA": samples_out,
            "statistics": stats,
            "unit": "microamps (uA)",
        }
        if digital_data:
            result["digital_channels"] = digital_data
        return result

    elif output == "file":
        time_interval_us = (actual_duration * 1_000_000) / len(all_samples)

        try:
            output_path = os.path.abspath(output_file)
            with open(output_path, 'w') as f:
                if include_digital and all_digital:
                    f.write("timestamp_us,current_uA,D0,D1,D2,D3,D4,D5,D6,D7\n")
                    digital_channels = _ppk2.digital_channels(all_digital)
                    for i, sample in enumerate(all_samples):
                        timestamp = round(i * time_interval_us, 3)
                        digital_vals = ",".join(str(digital_channels[ch][i]) for ch in range(8))
                        f.write(f"{timestamp},{round(sample, 3)},{digital_vals}\n")
                else:
                    f.write("timestamp_us,current_uA\n")
                    for i, sample in enumerate(all_samples):
                        timestamp = round(i * time_interval_us, 3)
                        f.write(f"{timestamp},{round(sample, 3)}\n")
        except Exception as e:
            return {"error": f"Failed to write file: {e}"}

        result = {
            **base_response,
            "file_path": output_path,
            "statistics": stats,
            "unit": "microamps (uA)",
        }
        if digital_data:
            result["digital_channels"] = digital_data
        return result


if __name__ == "__main__":
    mcp.run()
