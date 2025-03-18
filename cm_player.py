"""Audio playback module with multiprocessing integration."""
import logging
import queue
import threading
import time
from typing import Optional

import numpy as np
from openai import audio
import sounddevice as sd

from cm_settings import AudioConfig
from cm_logging import setup_logger

logger = setup_logger(__name__, level=logging.INFO)

class AtomicBuffer:
    """Thread-safe audio buffer using numpy"""
    def __init__(self, config: AudioConfig):
        """Initializes AtomicBuffer"""
        self.config = config
        self.buffer = np.zeros(
            (int(config.buffer_seconds * config.sample_rate), config.channels),
            dtype=np.float32
        )
        self.write_pos = 0  # Keep track the write and read location
        self.read_pos = 0
        self.capacity = len(self.buffer)  # Max data it can store in samples
        self.available = 0  # Amount currently available
        self.lock = threading.RLock()  # Protects all read/write operations
        self.underrun_count = 0  # Counts how many times the buffer has to stop playback

    def write(self, data: np.ndarray) -> int:
        """Writes to the buffer"""
        with self.lock:
            write_size = min(len(data), self.capacity - self.available)
            if write_size == 0:
                # Protects from errors where write process calls
                return 0  # Buffer is full

            end = self.write_pos % self.capacity
            first_part = min(write_size, self.capacity - end)
            self.buffer[self.write_pos:end + first_part] = data[:first_part]  # Write to the end
            
            if first_part < write_size:  # If wraps, continue writing from the start
                self.buffer[:write_size - first_part] = data[first_part:write_size]
            
            self.write_pos = (self.write_pos + write_size) % self.capacity  # Next location
            self.available += write_size  # Add size.
            return write_size

    def read(self, requested: int) -> Optional[np.ndarray]:
        """Read from buffer"""
        with self.lock:
            if self.available < requested:
                self.underrun_count += 1  # Count if not enough samples
                logger.warning(f"Buffer underrun #{self.underrun_count}")
                return None  # Returns none to indicate underrun
            
            end = self.read_pos % self.capacity  # Where the read will END.
            first_part = min(requested, self.capacity - end)  # Max Read in one part.
            result = np.empty((requested, self.config.channels), dtype=np.float32)
            result[:first_part] = self.buffer[self.read_pos:end + first_part]
            
            if first_part < requested:  # If wraps, continue reading from start
                result[first_part:] = self.buffer[:requested - first_part]
            
            self.read_pos = (self.read_pos + requested) % self.capacity  # Move read point
            self.available -= requested  # Decrement what was removed
            return result  # Returns the result of the read

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
        self.buffer = AtomicBuffer(config)
        self.stream: Optional[sd.OutputStream] = None
        self.stop_event = threading.Event()
        self.prefill_complete = threading.Event()
        self.loader_complete = False
        self.clips_played = 0
        self._peak_limiter = PeakLimiter(config)
        self._buffer_thread = threading.Thread(target=self._buffer_loop)

    def start(self) -> None:
        """Start audio playback system."""
        try:
        
            # Wait for the first song to be processed and ready before opening our stream
            #test = self.processed_clips_queue.get()
            #logger.debug(f"Found processed clip: {test[0]}, {len(test[1])}")
            
            with sd.OutputStream(
                samplerate=self.config.sample_rate,
                channels=self.config.channels,
                blocksize=self.config.block_size,
                latency=self.config.latency,
                callback=self._audio_callback
            ) as stream:
                self.stream = stream
                self._buffer_thread.start()
                self._wait_until_complete()

        except Exception as e:
            logger.error("Playback failed: %s", str(e))
        finally:
            self.stop()

    def _buffer_loop(self) -> None:
        """Unified buffering loop handling both prefill and continuous loading."""
        start_time = time.time()
        logging.info("Starting buffering system (target: %.1fs)", self.config.prefill_time)

        while not self.stop_event.is_set():
            # Process queue items
            try:
                index, clip = self.processed_clips_queue.get(timeout=0.1)
                if (index, clip) == (None, None):
                    self.loader_complete = True
                    logging.debug("Received loader completion signal")
                    break
                
                self._safe_buffer(clip)
                self.clips_played += 1
                logging.debug("Buffered clip %d (Total: %d)", index, self.clips_played)

            except queue.Empty:
                if self.loader_complete:
                    break  # Normal termination case
            
            # Check prefill status
            if not self.prefill_complete.is_set():
                buffer_level = self.buffer.available_seconds()
                elapsed = time.time() - start_time
                
                if buffer_level >= self.config.prefill_time:
                    self.prefill_complete.set()
                    logging.info("Prefill target reached (%.1fs)", buffer_level)
                elif elapsed > self.config.prebuffer_timeout:
                    self.prefill_complete.set()
                    logging.warning("Prefill timeout reached (%.1fs), starting with %.1fs buffer", 
                                  self.config.prebuffer_timeout, buffer_level)

        logging.info("Buffering completed, final buffer level: %.1fs", 
                    self.buffer.available_seconds())

    def _safe_buffer(self, audio: np.ndarray) -> None:
        """Safely write audio data to buffer with flow control."""
        total_written = 0
        while total_written < len(audio) and not self.stop_event.is_set():
            chunk = audio[total_written:total_written + self.config.block_size]
            written = self.buffer.write(chunk)
            
            if written > 0:
                total_written += written
            else:
                self._handle_buffer_full()

    def _handle_buffer_full(self) -> None:
        """Manage buffer full conditions with backoff."""
        buffer_level = self.buffer.available_seconds() / self.config.buffer_seconds
        time.sleep(self.config.buffer_backoff)

    def _audio_callback(self, outdata: np.ndarray, frames: int, 
                      time_info: dict, status: sd.CallbackFlags) -> None:
        """Core audio callback handling buffer reading and signal processing."""
        if status:
            logger.warning("Audio device status: %s", status)
        
        if self.is_playback_complete():
            self.stop()
            outdata.fill(0)
            return

        # Wait for minimum buffer before starting playback
        data = None
        if self.prefill_complete.is_set():
            data = self.buffer.read(frames)

        if data is None:
            outdata.fill(0)
            return

        self._peak_limiter.apply(data)
        outdata[:] = data

    def _wait_until_complete(self) -> None:
        """Maintain playback until completion or stop signal."""
        while not self.is_playback_complete() and not self.stop_event.is_set():
            time.sleep(0.1)

    def is_playback_complete(self) -> bool:
        """Determine if playback should conclude."""
        return self.loader_complete and self.buffer.available < self.config.block_size

    def stop(self) -> None:
        """Immediately stop all playback and cleanup resources."""
        self.stop_event.set()
        if self.stream:
            self.stream.abort()
        self._buffer_thread.join(timeout=2)
        logging.info("Playback stopped")

class PeakLimiter:
    """Prevents audio clipping through peak normalization."""
    def __init__(self, config: AudioConfig):
        self.threshold = config.limiter_threshold

    def apply(self, data: np.ndarray) -> None:
        """Apply gain reduction to prevent clipping."""
        peak = np.max(np.abs(data))
        if peak > self.threshold:
            data *= self.threshold / peak