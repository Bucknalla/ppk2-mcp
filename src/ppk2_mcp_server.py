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
def measure(duration_seconds: float = 1.0) -> dict:
    """Capture power measurements for a specified duration.
    
    Starts measurement, collects samples for the given duration, then stops
    and returns statistics about the captured data.
    
    Prerequisites: Must call set_mode() and set_voltage() before measuring.
    
    Args:
        duration_seconds: How long to measure in seconds (default: 1.0, max: 10.0)
    
    Returns:
        On success, a dictionary with:
        - sample_count: Number of samples captured
        - duration_seconds: Actual measurement duration
        - samples_per_second: Achieved sampling rate
        - min_uA: Minimum current in microamps
        - max_uA: Maximum current in microamps
        - avg_uA: Average current in microamps
        - unit: "microamps (uA)"
        
        On failure, a dictionary with:
        - error: Description of what went wrong (not connected, voltage/mode not set, etc.)
    """
    if _ppk2 is None:
        return {"error": "No PPK2 connected. Use connect() first."}

    if _ppk2.current_vdd is None:
        return {"error": "Voltage not set. Use set_voltage() first."}

    if _ppk2.mode is None:
        return {"error": "Mode not set. Use set_mode() first."}

    # Clamp duration
    duration_seconds = min(max(duration_seconds, 0.1), 10.0)

    all_samples = []

    try:
        _ppk2.start_measuring()
        start_time = time.time()

        while (time.time() - start_time) < duration_seconds:
            read_data = _ppk2.get_data()
            if read_data != b'':
                samples, _ = _ppk2.get_samples(read_data)
                all_samples.extend(samples)
            time.sleep(0.001)  # Small delay to avoid busy-waiting

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

    return {
        "sample_count": len(all_samples),
        "duration_seconds": round(actual_duration, 3),
        "samples_per_second": round(len(all_samples) / actual_duration, 1),
        "min_uA": round(min(all_samples), 3),
        "max_uA": round(max(all_samples), 3),
        "avg_uA": round(sum(all_samples) / len(all_samples), 3),
        "unit": "microamps (uA)",
    }


@mcp.tool
def measure_raw(duration_seconds: float = 0.1) -> dict:
    """Capture power measurements and return raw sample data for visualization.
    
    Similar to measure(), but returns the actual time-series data for plotting.
    Limited to short durations to avoid overwhelming response size.
    
    Prerequisites: Must call set_mode() and set_voltage() before measuring.
    
    Args:
        duration_seconds: How long to measure in seconds (default: 0.1, max: 1.0)
    
    Returns:
        On success, a dictionary with:
        - samples_uA: List of current measurements in microamps (time-series data)
        - sample_count: Number of samples captured
        - duration_seconds: Actual measurement duration
        - samples_per_second: Achieved sampling rate (use to calculate time axis)
        - statistics: Dict with min_uA, max_uA, avg_uA
        - unit: "microamps (uA)"
        
        On failure, a dictionary with:
        - error: Description of what went wrong
    """
    if _ppk2 is None:
        return {"error": "No PPK2 connected. Use connect() first."}

    if _ppk2.current_vdd is None:
        return {"error": "Voltage not set. Use set_voltage() first."}

    if _ppk2.mode is None:
        return {"error": "Mode not set. Use set_mode() first."}

    # Clamp duration (max 1 second to limit response size ~100k samples)
    duration_seconds = min(max(duration_seconds, 0.01), 1.0)

    all_samples = []

    try:
        _ppk2.start_measuring()
        start_time = time.time()

        while (time.time() - start_time) < duration_seconds:
            read_data = _ppk2.get_data()
            if read_data != b'':
                samples, _ = _ppk2.get_samples(read_data)
                all_samples.extend(samples)
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

    # Round samples to reduce response size
    rounded_samples = [round(s, 2) for s in all_samples]

    return {
        "samples_uA": rounded_samples,
        "sample_count": len(all_samples),
        "duration_seconds": round(actual_duration, 3),
        "samples_per_second": round(len(all_samples) / actual_duration, 1),
        "statistics": {
            "min_uA": round(min(all_samples), 3),
            "max_uA": round(max(all_samples), 3),
            "avg_uA": round(sum(all_samples) / len(all_samples), 3),
        },
        "unit": "microamps (uA)",
    }


@mcp.tool
def measure_to_file(duration_seconds: float = 1.0, output_file: str = "power_measurement.csv") -> dict:
    """Capture power measurements and save raw samples to a CSV file.
    
    Use this for longer measurements where returning all samples would be impractical.
    The CSV file contains timestamps and current values for later analysis.
    
    Prerequisites: Must call set_mode() and set_voltage() before measuring.
    
    Args:
        duration_seconds: How long to measure in seconds (default: 1.0, max: 60.0)
        output_file: Path to save the CSV file (default: "power_measurement.csv")
    
    Returns:
        On success, a dictionary with:
        - file_path: Path to the saved CSV file
        - sample_count: Number of samples captured
        - duration_seconds: Actual measurement duration
        - samples_per_second: Achieved sampling rate
        - statistics: Dict with min_uA, max_uA, avg_uA
        - unit: "microamps (uA)"
        
        On failure, a dictionary with:
        - error: Description of what went wrong
    """
    if _ppk2 is None:
        return {"error": "No PPK2 connected. Use connect() first."}

    if _ppk2.current_vdd is None:
        return {"error": "Voltage not set. Use set_voltage() first."}

    if _ppk2.mode is None:
        return {"error": "Mode not set. Use set_mode() first."}

    # Clamp duration (max 60 seconds for file output)
    duration_seconds = min(max(duration_seconds, 0.1), 60.0)

    all_samples = []

    try:
        _ppk2.start_measuring()
        start_time = time.time()

        while (time.time() - start_time) < duration_seconds:
            read_data = _ppk2.get_data()
            if read_data != b'':
                samples, _ = _ppk2.get_samples(read_data)
                all_samples.extend(samples)
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

    # Calculate time interval between samples
    time_interval_us = (actual_duration * 1_000_000) / len(all_samples)

    # Write to CSV file
    try:
        import os
        output_path = os.path.abspath(output_file)
        with open(output_path, 'w') as f:
            f.write("timestamp_us,current_uA\n")
            for i, sample in enumerate(all_samples):
                timestamp = round(i * time_interval_us, 3)
                f.write(f"{timestamp},{round(sample, 3)}\n")
    except Exception as e:
        return {"error": f"Failed to write file: {e}"}

    return {
        "file_path": output_path,
        "sample_count": len(all_samples),
        "duration_seconds": round(actual_duration, 3),
        "samples_per_second": round(len(all_samples) / actual_duration, 1),
        "statistics": {
            "min_uA": round(min(all_samples), 3),
            "max_uA": round(max(all_samples), 3),
            "avg_uA": round(sum(all_samples) / len(all_samples), 3),
        },
        "unit": "microamps (uA)",
    }


def _streaming_worker(ppk2: PPK2_API, server_socket: socket.socket, stop_event: threading.Event):
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
                samples, _ = ppk2.get_samples(read_data)
                current_time = time.time() - start_time
                
                for sample in samples:
                    sample_count += 1
                    # Create JSON line
                    data = json.dumps({
                        "t": round(current_time, 6),
                        "uA": round(sample, 2),
                        "n": sample_count
                    }) + "\n"
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
def start_streaming(port: int = 5555) -> dict:
    """Start streaming power measurements to a TCP socket.
    
    Opens a TCP server on the specified port. Clients can connect to receive
    real-time measurements as JSON lines.
    
    Prerequisites: Must call set_mode() and set_voltage() before streaming.
    
    Connect with: nc localhost 5555
    Or use the example script: python examples/stream_consumer.py
    
    Data format (JSON lines):
        {"t": 0.001234, "uA": 125.5, "n": 1}
        {"t": 0.001244, "uA": 130.2, "n": 2}
        ...
    
    Fields:
        - t: Timestamp in seconds since stream start
        - uA: Current in microamps
        - n: Sample number
    
    Args:
        port: TCP port to listen on (default: 5555)
    
    Returns:
        On success: {"status": "streaming", "port": 5555, "host": "localhost"}
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
            args=(_ppk2, _stream_server, _stream_stop_event),
            daemon=True
        )
        _stream_thread.start()
        
        return {
            "status": "streaming",
            "port": port,
            "host": "localhost",
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


if __name__ == "__main__":
    mcp.run()
