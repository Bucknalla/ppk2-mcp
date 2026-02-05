#!/usr/bin/env python3
"""
Example consumer for PPK2 MCP streaming data with live matplotlib graph.

Connects to the TCP stream and displays a real-time power consumption graph.

Requirements:
    pip install matplotlib numpy

Usage:
    1. Start streaming via MCP: start_streaming(port=5555)
    2. Run this script: python examples/stream_consumer.py
    3. Stop with Ctrl+C or close the window
"""

import argparse
import json
import socket
import threading
import time
from collections import deque

import matplotlib.pyplot as plt
import matplotlib.animation as animation
import numpy as np


class PPK2StreamPlotter:
    def __init__(self, host: str, port: int, window_seconds: float = 5.0, 
                 downsample: int = 100, smooth_window: int = 20,
                 log_scale: bool = False, split_view: bool = False):
        """
        Initialize the stream plotter.
        
        Args:
            host: TCP host to connect to
            port: TCP port to connect to
            window_seconds: How many seconds of data to display
            downsample: Keep every Nth sample for display (100kHz -> 1kHz at downsample=100)
            smooth_window: Number of samples to average for smoothing (also tracks min/max)
            log_scale: Use logarithmic Y-axis (good for high dynamic range)
            split_view: Show average and peaks in separate subplots
        """
        self.host = host
        self.port = port
        self.window_seconds = window_seconds
        self.downsample = downsample
        self.smooth_window = smooth_window
        self.log_scale = log_scale
        self.split_view = split_view
        
        # Calculate buffer size based on downsampled rate
        # ~100kHz / downsample * window_seconds
        display_rate = 100000 / downsample
        buffer_size = int(display_rate * window_seconds)
        
        # Thread-safe data buffers (store time, avg, min, max for each chunk)
        self.times = deque(maxlen=buffer_size)
        self.values_avg = deque(maxlen=buffer_size)
        self.values_min = deque(maxlen=buffer_size)
        self.values_max = deque(maxlen=buffer_size)
        self.lock = threading.Lock()
        
        # Chunk accumulator for smoothing
        self.chunk_samples = []
        self.chunk_time = 0
        
        # Statistics
        self.stats = {"min": 0, "max": 0, "avg": 0, "count": 0, "rate": 0}
        self.stats_window = deque(maxlen=10000)  # For calculating statistics
        
        # Connection state
        self.connected = False
        self.running = True
        self.sock = None
        
        # Sample counter for downsampling
        self.sample_counter = 0
        self.last_rate_time = time.time()
        self.rate_sample_count = 0
        
    def connect(self):
        """Connect to the TCP stream."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            self.sock.setblocking(False)
            self.connected = True
            print(f"Connected to {self.host}:{self.port}")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False
    
    def receive_data(self):
        """Background thread to receive and process data."""
        buffer = ""
        
        while self.running:
            if not self.connected:
                time.sleep(0.1)
                continue
            
            try:
                data = self.sock.recv(65536).decode('utf-8')
                if not data:
                    print("Connection closed by server")
                    self.connected = False
                    continue
                
                buffer += data
                
                # Process complete lines
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue
                    
                    try:
                        record = json.loads(line)
                        timestamp = record.get("t", 0)
                        current = record.get("uA", 0)
                        
                        self.sample_counter += 1
                        self.rate_sample_count += 1
                        
                        # Update statistics window
                        self.stats_window.append(current)
                        
                        # Accumulate samples for smoothing
                        if self.sample_counter % self.downsample == 0:
                            self.chunk_samples.append(current)
                            self.chunk_time = timestamp
                            
                            # When chunk is full, compute stats and add to display buffer
                            if len(self.chunk_samples) >= self.smooth_window:
                                chunk_arr = np.array(self.chunk_samples)
                                with self.lock:
                                    self.times.append(self.chunk_time)
                                    self.values_avg.append(np.mean(chunk_arr))
                                    self.values_min.append(np.min(chunk_arr))
                                    self.values_max.append(np.max(chunk_arr))
                                self.chunk_samples = []
                        
                    except json.JSONDecodeError:
                        continue
                
                # Update rate calculation periodically
                now = time.time()
                dt = now - self.last_rate_time
                if dt >= 1.0:
                    self.stats["rate"] = self.rate_sample_count / dt
                    self.rate_sample_count = 0
                    self.last_rate_time = now
                    
            except BlockingIOError:
                time.sleep(0.001)
            except Exception as e:
                if self.running:
                    print(f"Receive error: {e}")
                time.sleep(0.1)
    
    def update_plot(self, frame):
        """Animation update function."""
        with self.lock:
            if len(self.times) > 1:
                times_arr = np.array(self.times)
                values_avg = np.array(self.values_avg)
                values_min = np.array(self.values_min)
                values_max = np.array(self.values_max)
            else:
                times_arr = np.array([0])
                values_avg = np.array([0])
                values_min = np.array([0])
                values_max = np.array([0])
        
        # Update statistics
        if self.stats_window:
            stats_arr = np.array(self.stats_window)
            self.stats["min"] = np.min(stats_arr)
            self.stats["max"] = np.max(stats_arr)
            self.stats["avg"] = np.mean(stats_arr)
            self.stats["count"] = len(self.stats_window)
        
        # Format statistics for title
        unit = "uA"
        avg, min_v, max_v = self.stats["avg"], self.stats["min"], self.stats["max"]
        if max_v >= 1000:
            unit = "mA"
            avg, min_v, max_v = avg/1000, min_v/1000, max_v/1000
        
        status = "LIVE" if self.connected else "DISCONNECTED"
        title_text = (
            f"PPK2 Power Monitor [{status}] | "
            f"Avg: {avg:.2f} {unit} | Min: {min_v:.2f} {unit} | Max: {max_v:.2f} {unit} | "
            f"Rate: {self.stats['rate']/1000:.1f} kHz"
        )
        
        if self.split_view:
            # Split view: top = average, bottom = min/max envelope
            # Update average plot (top)
            self.line_avg.set_data(times_arr, values_avg)
            
            # Update envelope plot (bottom) - show min AND max with fill
            # Remove old fill
            for coll in self.ax_peak.collections:
                coll.remove()
            
            if len(times_arr) > 1:
                # For log scale, ensure positive values
                if self.log_scale:
                    plot_min = np.maximum(values_min, 0.1)
                    plot_max = np.maximum(values_max, 0.1)
                else:
                    plot_min = values_min
                    plot_max = values_max
                
                # Fill between min and max
                self.ax_peak.fill_between(times_arr, plot_min, plot_max,
                                          alpha=0.5, color='orange', label='Min/Max Range')
                
                # Update the lines
                self.line_min.set_data(times_arr, plot_min)
                self.line_max.set_data(times_arr, plot_max)
                
                # X axis (shared)
                self.ax_avg.set_xlim(times_arr[0], times_arr[-1])
                self.ax_peak.set_xlim(times_arr[0], times_arr[-1])
                
                # Y axis for average (tighter range around the average)
                if self.log_scale:
                    avg_vals = np.maximum(values_avg, 0.1)
                    self.ax_avg.set_ylim(np.min(avg_vals) * 0.5, np.max(avg_vals) * 2)
                else:
                    avg_ymin, avg_ymax = np.min(values_avg), np.max(values_avg)
                    avg_padding = (avg_ymax - avg_ymin) * 0.1 or 10
                    self.ax_avg.set_ylim(max(0, avg_ymin - avg_padding), avg_ymax + avg_padding)
                
                # Y axis for peaks (full range)
                if self.log_scale:
                    self.ax_peak.set_ylim(np.min(plot_min) * 0.5, np.max(plot_max) * 2)
                else:
                    peak_ymin, peak_ymax = np.min(values_min), np.max(values_max)
                    peak_padding = (peak_ymax - peak_ymin) * 0.1 or 10
                    self.ax_peak.set_ylim(max(0, peak_ymin - peak_padding), peak_ymax + peak_padding)
            
            self.ax_avg.set_title(title_text, fontsize=10)
            return [self.line_avg, self.line_min, self.line_max]
        
        else:
            # Single view with envelope
            # Remove old fill and create new one
            for coll in self.ax.collections:
                coll.remove()
            
            if len(times_arr) > 1:
                # For log scale, ensure positive values
                plot_min = np.maximum(values_min, 0.1) if self.log_scale else values_min
                plot_max = np.maximum(values_max, 0.1) if self.log_scale else values_max
                plot_avg = np.maximum(values_avg, 0.1) if self.log_scale else values_avg
                
                self.ax.fill_between(times_arr, plot_min, plot_max, 
                                     alpha=0.3, color='cyan', label='Min/Max Range')
                self.line.set_data(times_arr, plot_avg)
                
                # Auto-scale axes
                self.ax.set_xlim(times_arr[0], times_arr[-1])
                
                ymin, ymax = np.min(plot_min), np.max(plot_max)
                if self.log_scale:
                    # Log scale: set reasonable bounds
                    self.ax.set_ylim(max(0.1, ymin * 0.5), ymax * 2)
                else:
                    padding = (ymax - ymin) * 0.1 or 10
                    self.ax.set_ylim(max(0, ymin - padding), ymax + padding)
            
            self.ax.set_title(title_text, fontsize=10)
            return [self.line]
    
    def run(self):
        """Start the plotter."""
        # Connect to stream
        if not self.connect():
            print("Could not connect. Make sure streaming is started via MCP.")
            return
        
        # Start receive thread
        recv_thread = threading.Thread(target=self.receive_data, daemon=True)
        recv_thread.start()
        
        # Set up the plot
        plt.style.use('dark_background')
        
        if self.split_view:
            # Two subplots: average (top) and peaks (bottom)
            self.fig, (self.ax_avg, self.ax_peak) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
            self.fig.canvas.manager.set_window_title('PPK2 Power Profiler')
            
            # Average plot (top)
            self.line_avg, = self.ax_avg.plot([], [], 'lime', linewidth=1.0)
            self.ax_avg.set_ylabel('Average (uA)')
            self.ax_avg.grid(True, alpha=0.3)
            
            # Min/Max envelope plot (bottom)
            self.line_min, = self.ax_peak.plot([], [], 'cyan', linewidth=0.5, alpha=0.7)
            self.line_max, = self.ax_peak.plot([], [], 'orange', linewidth=0.5, alpha=0.7)
            self.ax_peak.set_xlabel('Time (s)')
            self.ax_peak.set_ylabel('Min/Max (uA)')
            self.ax_peak.grid(True, alpha=0.3)
            
            if self.log_scale:
                self.ax_avg.set_yscale('log')
                self.ax_peak.set_yscale('log')
                # Set initial limits to avoid log(0) errors
                self.ax_avg.set_ylim(1, 1000)
                self.ax_peak.set_ylim(1, 1000)
            
            self.fig.subplots_adjust(hspace=0.1)
            
        else:
            # Single plot with envelope
            self.fig, self.ax = plt.subplots(figsize=(12, 6))
            self.fig.canvas.manager.set_window_title('PPK2 Power Profiler')
            
            self.line, = self.ax.plot([], [], 'w-', linewidth=1.0, label='Average')
            self.ax.set_xlabel('Time (s)')
            self.ax.set_ylabel('Current (uA)')
            self.ax.grid(True, alpha=0.3)
            
            if self.log_scale:
                self.ax.set_yscale('log')
                # Set initial limits to avoid log(0) errors
                self.ax.set_ylim(1, 1000)
        
        # Set up animation
        ani = animation.FuncAnimation(
            self.fig,
            self.update_plot,
            interval=50,  # 20 FPS
            blit=False,
            cache_frame_data=False
        )
        
        try:
            plt.show()
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            if self.sock:
                self.sock.close()
            print("\nPlotter stopped.")


def main():
    parser = argparse.ArgumentParser(
        description="PPK2 Stream Consumer - Live Graph",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python stream_consumer.py

  # Log scale (good for seeing both low baseline and high spikes)
  python stream_consumer.py --log

  # Split view (separate plots for average and peaks)
  python stream_consumer.py --split

  # Combined: log scale with split view
  python stream_consumer.py --log --split

  # Less smoothing (see individual spikes more clearly)
  python stream_consumer.py --smooth 5
        """
    )
    parser.add_argument("--host", default="localhost", help="Host to connect to (default: localhost)")
    parser.add_argument("--port", "-p", type=int, default=5555, help="Port to connect to (default: 5555)")
    parser.add_argument("--window", "-w", type=float, default=5.0, help="Seconds of data to display (default: 5.0)")
    parser.add_argument("--downsample", "-d", type=int, default=100, help="Downsample factor (default: 100, shows 1kHz from 100kHz)")
    parser.add_argument("--smooth", "-s", type=int, default=20, help="Smoothing window size (default: 20, groups samples for avg/min/max)")
    parser.add_argument("--log", "-l", action="store_true", help="Use logarithmic Y-axis (good for high dynamic range data)")
    parser.add_argument("--split", action="store_true", help="Split view: show average and peaks in separate plots")
    args = parser.parse_args()

    plotter = PPK2StreamPlotter(
        host=args.host,
        port=args.port,
        window_seconds=args.window,
        downsample=args.downsample,
        smooth_window=args.smooth,
        log_scale=args.log,
        split_view=args.split
    )
    plotter.run()


if __name__ == "__main__":
    main()
