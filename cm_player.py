"""Model: Audio playback module with multiprocessing integration."""
import logging
import queue
import threading
import time
from typing import Optional

import numpy as np
from regex import P
import sounddevice as sd
import multiprocessing as mp

from cm_settings import AudioConfig
from cm_logging import setup_logger

logger = setup_logger()

class AtomicBuffer:
    """Thread-safe audio buffer using numpy"""
    def __init__(self, config: AudioConfig):
        """Initializes AtomicBuffer"""
        self.config = config
        self.buffer = np.zeros(
            (int(config.buffer_seconds * config.sample_rate), config.channels),
            dtype=np.float32
        )
        # Keep track of the song index for each block
        self.index_buffer = np.full(int(config.buffer_seconds * config.sample_rate / config.block_size), -1, dtype=np.int32)
        self.write_pos = 0  # Keep track the write and read location
        self.read_pos = 0
        self.capacity = len(self.buffer)  # Max data it can store in samples
        self.available = 0  # Amount currently available
        self.lock = threading.RLock()  # Protects all read/write operations
        self.underrun_count = 0  # Counts how many times the buffer has to stop playback

    def write(self, data: np.ndarray, song_index: int) -> int:
        """Writes to the buffer"""
        with self.lock:
            write_size = min(len(data), self.capacity - self.available)
            if write_size == 0:
                # Protects from errors where write process calls
                return 0  # Buffer is full

            end = self.write_pos % self.capacity
            first_part = min(write_size, self.capacity - end)
            self.buffer[self.write_pos:end + first_part] = data[:first_part]  # Write to the end
            self.index_buffer[(self.write_pos // self.config.block_size) % len(self.index_buffer)] = song_index
            
            if first_part < write_size:  # If wraps, continue writing from the start
                self.buffer[:write_size - first_part] = data[first_part:write_size]
                self.index_buffer[0] = song_index
            
            self.write_pos = (self.write_pos + write_size) % self.capacity  # Next location
            self.available += write_size  # Add size.
            return write_size

    def read(self, requested: int) -> Optional[tuple[np.ndarray, int]]:
        """Read from buffer"""
        with self.lock:
            if self.available < requested:
                self.underrun_count += 1  # Avoid too many log entries
                if self.underrun_count == 1:
                    logger.warn(f"Buffer underrun - not enough processed audio")
                return None  # Returns none to indicate underrun
            
            # Identify buffer underrun resolution
            if self.underrun_count > 0:
                logger.info(f"Buffer ready ({self.underrun_count} underruns)")
                self.underrun_count = 0

            end = self.read_pos % self.capacity  # Where the read will END.
            first_part = min(requested, self.capacity - end)  # Max Read in one part.
            result = np.empty((requested, self.config.channels), dtype=np.float32)
            result[:first_part] = self.buffer[self.read_pos:end + first_part]
            song_index = self.index_buffer[(self.read_pos // self.config.block_size) % len(self.index_buffer)]
            
            if first_part < requested:  # If wraps, continue reading from start
                result[first_part:] = self.buffer[:requested - first_part]
            
            self.read_pos = (self.read_pos + requested) % self.capacity  # Move read point
            self.available -= requested  # Decrement what was removed
            return result, song_index  # Returns the result of the read

    def clear(self):
        """Clear by resetting markers"""
        with self.lock:
            self.write_pos = 0  # Reset the write position
            self.read_pos = 0  # Reset the read position
            self.available = 0  # indicate nothing was available.
            logger.info("Buffer Cleaned")

    def available_seconds(self) -> float:
        """Return the # of available seconds from data already there"""
        with self.lock:
            return self.available / self.config.sample_rate  # Returns as a float with available data

class AudioPlayer:
    """Handles audio playback with buffer management and multiprocessing integration."""
    def __init__(self, config: AudioConfig, processed_clips_queue: queue.Queue):
        """Initialize audio player with shared processing queue."""
        self.config = config
        self.processed_clips_queue = processed_clips_queue
        self.player_queue = None
        self.buffer = AtomicBuffer(config)
        self.stream: Optional[sd.OutputStream] = None
        self.stop_event = threading.Event()
        self.prefill_complete = threading.Event()
        self.loader_complete = False
        self.clips_buffered = 0
        self.paused = False
        self.current_volume = 1.0
        self.fade_step = 0.02  # 20ms per step
        self.fade_duration = config.pause_fade
        self._peak_limiter = PeakLimiter(config)
        self._buffer_thread = threading.Thread(target=self._buffer_loop)
        self.current_song_index = -1
        self.song_list = []

    def start(self, command_queue: queue.Queue, player_queue: queue.Queue) -> None:
        """Start audio playback system with command queue for IPC."""
        self.player_queue = player_queue
        try:
            logger.info("Starting playback...")
            
            # Open audio stream
            with sd.OutputStream(
                samplerate=self.config.sample_rate,
                channels=self.config.channels,
                blocksize=self.config.block_size,
                latency=self.config.latency,
                callback=self._audio_callback
            ) as stream:
                self.stream = stream
                
                # Start buffering thread
                self._buffer_thread.start()

                # Process commands from the main process
                while not self.stop_event.is_set():
                    try:
                        cmd = command_queue.get_nowait()
                        if cmd == "PAUSE":
                            logger.info("Playback paused")
                            self._handle_pause_fade()
                        elif cmd == "RESUME":
                            logger.info("Playback resumed")
                            self._handle_resume_fade()
                        elif cmd == "STOP":
                            logger.info("Stopping playback...")
                            break
                    except queue.Empty:
                        pass

                    if self.is_playback_complete():
                        logger.info("Playback complete")
                        self.player_queue.put("complete")  # Notify main process
                        break

                    time.sleep(0.1)

        except Exception as e:
            logger.error("Playback failed: %s", str(e))
        finally:
            self.stop()

    def _buffer_loop(self) -> None:
        """Unified buffering loop handling both prefill and continuous loading."""
        start_time = time.time()
        logger.info("Starting buffering system (target: %.1fs)", self.config.prefill_time)

        while not self.stop_event.is_set():
            try:
                index, title, clip = self.processed_clips_queue.get(timeout=0.1)
                self.song_list.append(title)
                if (index, title, clip) == (None, None, None):
                    self.loader_complete = True
                    logger.debug("Received loader completion signal")
                    break
                
                self._safe_buffer(clip, index)
                self.clips_buffered += 1

            except queue.Empty:
                if self.loader_complete:
                    break  # Normal termination case
            
            # Check prefill status
            if not self.prefill_complete.is_set():
                buffer_level = self.buffer.available_seconds()
                elapsed = time.time() - start_time
                
                if buffer_level >= self.config.prefill_time:
                    self.prefill_complete.set()
                    logger.info("Prefill target reached (%.1fs)", buffer_level)
                elif self.loader_complete:
                    self.prefill_complete.set()
                    logger.info("Loader complete, starting with %.1fs buffer", buffer_level)

        logger.info(
            "Buffering completed, final buffer level: %.1fs", 
            self.buffer.available_seconds()
        )

    def _safe_buffer(self, audio: np.ndarray, song_index: int) -> None:
        """Safely write audio data to buffer with flow control."""
        total_written = 0
        while total_written < len(audio) and not self.stop_event.is_set():
            chunk = audio[total_written : total_written + self.config.block_size]
            written = self.buffer.write(chunk, song_index)
            
            if written > 0:
                total_written += written
            else:
                time.sleep(self.config.buffer_backoff)

    def _audio_callback(self, outdata: np.ndarray, frames: int, 
                      time_info: dict, status: sd.CallbackFlags) -> None:
        """Core audio callback handling buffer reading and signal processing."""
        if status:
            logger.warning("Audio device status: %s", status)
            
        if self.paused or (not self.prefill_complete.is_set() and not self.loader_complete):
            outdata.fill(0)
            return
        
        result = self.buffer.read(frames)
        
        if result is None:
            outdata.fill(0)
            return

        data, song_index = result

        # Check if the song index has changed
        if song_index != self.current_song_index:
            self.current_song_index = song_index
            self.player_queue.put(f"playing:{song_index}_{self.song_list[song_index]}")

        # Apply volume adjustment (for pause fading)
        data *= self.current_volume

        # Apply peak limiting to prevent clipping
        self._peak_limiter.apply(data)
        
        outdata[:] = data
        
    def _handle_pause_fade(self):
        target_time = time.time() + self.fade_duration
        while time.time() < target_time and not self.stop_event.is_set():
            self.current_volume = max(0, (target_time - time.time()) / self.fade_duration)
            time.sleep(0.01)
        self.current_volume = 0
        self.paused = True

    def _handle_resume_fade(self):
        self.paused = False
        start_time = time.time()
        while self.current_volume < 1.0 and not self.stop_event.is_set():
            self.current_volume = min(1.0, (time.time() - start_time) / self.fade_duration)
            time.sleep(0.01)
        self.current_volume = 1.0

    def is_playback_complete(self) -> bool:
        """Determine if playback should conclude."""
        return (
            self.loader_complete 
            and self.buffer.available < self.config.block_size
        )

    def stop(self) -> None:
        """Immediately stop all playback and cleanup resources."""
        logger.info("Stopping playback...")
        
        # Signal stop event for threads and callbacks
        self.stop_event.set()
        
        # Stop and close the audio stream safely
        if self.stream:
            try:
                self.stream.abort()
            except Exception as e:
                logger.error(f"Failed to abort stream: {str(e)}")

        # Ensure the buffering thread terminates gracefully
        if self._buffer_thread.is_alive():
            self._buffer_thread.join(timeout=2)

class PeakLimiter:
    """Prevents audio clipping through peak normalization."""
    def __init__(self, config: AudioConfig):
        self.threshold = config.limiter_threshold

    def apply(self, data: np.ndarray) -> None:
        """Apply gain reduction to prevent clipping."""
        peak = np.max(np.abs(data))
        if peak > self.threshold:
            data *= self.threshold / peak

    def apply(self, data: np.ndarray) -> None:
        """Apply gain reduction to prevent clipping."""
        peak = np.max(np.abs(data))
        if peak > self.threshold:
            data *= self.threshold / peak