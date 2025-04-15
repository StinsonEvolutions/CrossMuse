# cm_player.py

import gc
import logging
import queue
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd
import multiprocessing as mp
import psutil

from cm_settings import AudioConfig
from cm_logging import setup_logger

logger = setup_logger()

class AtomicBuffer:
    """Thread-safe audio buffer using numpy"""
    def __init__(self, config: AudioConfig):
        """Initializes AtomicBuffer"""
        self.config = config
        available_memory = psutil.virtual_memory().available
        buffer_size = min(int(config.buffer_seconds * config.sample_rate), available_memory // 2)
        buffer_size = (buffer_size // config.block_size) * config.block_size  # Ensure buffer size is a multiple of block size
        logger.info(f"Allocating {buffer_size // config.sample_rate} second buffer")
        self.buffer = np.zeros(
            (buffer_size, config.channels),
            dtype=np.float32
        )
        # Keep track of song id hashes for each block
        self.id_buffer = np.full(int(config.buffer_seconds * config.sample_rate / config.block_size), -1, dtype=np.int32)
        self.write_pos = 0  # Keep track the write and read location
        self.read_pos = 0
        self.capacity = len(self.buffer)  # Max data it can store in samples
        self.available = 0  # Amount currently available
        self.lock = threading.RLock()  # Protects all read/write operations
        self.underrun_count = 0  # Counts how many times the buffer has to stop playback
        self.loader_complete = False # Flag to indicate no more data will be written

    def write(self, data: np.ndarray, id_hash: int) -> int:
        """Writes to the buffer"""
        with self.lock:
            write_size = min(len(data), self.capacity - self.available)
            if write_size == 0:
                return 0  # Buffer is full

            end = self.write_pos % self.capacity
            first_part = min(write_size, self.capacity - end)
            self.buffer[self.write_pos:end + first_part] = data[:first_part]
            self.id_buffer[(self.write_pos // self.config.block_size) % len(self.id_buffer)] = id_hash

            if first_part < write_size:
                self.buffer[:write_size - first_part] = data[first_part:write_size]
                self.id_buffer[0] = id_hash

            self.write_pos = (self.write_pos + write_size) % self.capacity
            self.available += write_size
            return write_size

    def read(self, requested: int) -> Optional[tuple[np.ndarray, int, bool]]:
        """Read from buffer"""
        with self.lock:
            if self.available == 0:
                return None, None, self.loader_complete  # Return None and loader_complete status

            read_size = min(requested, self.available)
            end = self.read_pos % self.capacity
            first_part = min(read_size, self.capacity - end)
            result = np.empty((read_size, self.config.channels), dtype=np.float32)
            result[:first_part] = self.buffer[self.read_pos:end + first_part]
            id_hash = self.id_buffer[(self.read_pos // self.config.block_size) % len(self.id_buffer)]

            remaining = read_size - first_part
            if remaining > 0:
                result[first_part:] = self.buffer[:remaining]

            self.read_pos = (self.read_pos + read_size) % self.capacity
            self.available -= read_size

            # Determine if this is the final read (loader is done and no more data)
            is_final = self.loader_complete and self.available == 0

            # Return the actual data we have, even if it's less than requested
            # The audio callback will handle padding with zeros if needed
            return result, id_hash, is_final


    def clear(self):
        """Clear by resetting markers"""
        with self.lock:
            self.write_pos = 0  # Reset the write position
            self.read_pos = 0  # Reset the read position
            self.available = 0  # indicate nothing was available.
            self.loader_complete = False # Reset loader_complete on clear
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
        self.buffer_underrun = False
        self.paused = False
        self.current_volume = 1.0
        self.fade_step = 0.02  # 20ms per step
        self.fade_duration = config.pause_fade
        self._peak_limiter = PeakLimiter(config)
        self._buffer_thread = threading.Thread(target=self._buffer_loop)
        self.current_song_id = -1
        self.song_list = {}
        self.song_hashes = {}  # Dictionary to map hash values to song IDs

    def start(self, command_queue: queue.Queue, player_queue: queue.Queue) -> None:
        """Start audio playback system with command queue for IPC."""
        self.player_queue = player_queue
        try:
            logger.info("Starting playback...")
            self.player_queue.put("audio:initializing sound device")

            # Open audio stream
            logger.info(f"Opening audio stream with sample_rate={self.config.sample_rate}, channels={self.config.channels}")
            with sd.OutputStream(
                samplerate=self.config.sample_rate,
                channels=self.config.channels,
                blocksize=self.config.block_size,
                latency=self.config.latency,
                callback=self._audio_callback
            ) as stream:
                self.stream = stream
                logger.info("Audio stream opened successfully")

                # Start buffering thread
                self._buffer_thread.start()
                logger.info("Buffer thread started")

                # Process commands from the main process
                while not self.stop_event.is_set():
                    try:
                        cmd = command_queue.get_nowait()
                        logger.info(f"Received command: {cmd}")
                        if cmd == "PAUSE":
                            logger.info("Playback paused")
                            self.player_queue.put("audio:pausing playback")
                            self._handle_pause_fade()
                            self.player_queue.put("audio:playback paused")
                        elif cmd == "RESUME":
                            logger.info("Playback resumed")
                            self.player_queue.put("audio:resuming playback")
                            self._handle_resume_fade()
                            self.player_queue.put("audio:playback resumed")
                        elif cmd == "FORCE_START" and not self.prefill_complete.is_set():
                            logger.info("Forcing playback to start despite buffer not being fully prefilled")
                            self.player_queue.put("audio:forcing playback to start")
                            self.prefill_complete.set()
                        elif cmd == "STOP":
                            logger.info("Stopping playback...")
                            self.player_queue.put("audio:stopping playback")
                            break
                    except queue.Empty:
                        pass

                    time.sleep(0.1)

        except Exception as e:
            logger.error("Playback failed: %s", str(e))
            self.player_queue.put(f"audio:playback failed: {str(e)}")
        finally:
            self.stop()

    def _buffer_loop(self) -> None:
        """Unified buffering loop handling both prefill and continuous loading."""
        start_time = time.time()
        logger.info("Starting buffering system (target: %.1fs)", self.config.prefill_time)
        self.player_queue.put(f"audio:starting buffer fill (target: {self.config.prefill_time}s)")

        while not self.stop_event.is_set():
            try:
                song_id, title, clip = self.processed_clips_queue.get(timeout=0.1)
                if (song_id, title, clip) == (None, None, None):
                    self.buffer.loader_complete = True  # Signal that no more data is coming
                    logger.debug("Received loader completion signal")
                    self.player_queue.put("audio:all songs processed")
                    break

                logger.info(f"Player received processed clip {song_id}. {title} with {len(clip)}")
                self.player_queue.put(f"audio:received clip for {title}")
                self.song_list[song_id] = title
                
                self._safe_buffer(clip, song_id)

                # Trigger garbage collection if memory usage is high
                if psutil.virtual_memory().percent > 80:
                    gc.collect()
                    self.player_queue.put("audio:performed memory cleanup")

            except queue.Empty:
                # Might just be slow processing - wait for more clips
                logger.debug("No clips available, waiting...")
                time.sleep(0.1)

        logger.info(
            "Buffering completed, final buffer level: %.1fs",
            self.buffer.available_seconds()
        )
        self.player_queue.put(f"audio:buffer fill complete ({self.buffer.available_seconds():.1f}s)")


    def _safe_buffer(self, audio: np.ndarray, song_id: str) -> None:
        """Safely write audio data to buffer with flow control."""
        audio_written = 0
        prefill_target = self.config.prefill_time * self.config.sample_rate
        
        # Create hash for the song_id and store in the mapping dictionary
        id_hash = self._hash_int32(song_id)
        self.song_hashes[id_hash] = song_id

        # Initial buffering status
        last_percent = -1
        
        while audio_written < len(audio) and not self.stop_event.is_set():
            chunk = audio[audio_written : audio_written + self.config.block_size]
            written = self.buffer.write(chunk, id_hash)

            if written > 0:
                audio_written += written

                # Update prefill status
                if not self.prefill_complete.is_set():
                    buffer_level = self.buffer.available
                    percent_buffered = (buffer_level / prefill_target) * 100
                    
                    # Only send updates when the percentage changes significantly (every 5%)
                    current_percent = int(percent_buffered / 5) * 5
                    if current_percent != last_percent:
                        self.player_queue.put(f"buffering:{song_id}:{percent_buffered}")
                        last_percent = current_percent

                    if buffer_level >= prefill_target:
                        logger.info("Prefill target reached (%.1fs)", buffer_level / self.config.sample_rate)
                        self.player_queue.put(f"audio:prefill target reached ({buffer_level / self.config.sample_rate:.1f}s)")
                        self.prefill_complete.set()
            else:
                time.sleep(self.config.buffer_backoff)


    def _audio_callback(self, outdata: np.ndarray, frames: int,
        time_info: dict, status: sd.CallbackFlags) -> None:
        """Core audio callback handling buffer reading and signal processing."""
        # Fill with silence and return early if we're paused or prefill isn't complete
        if self.paused or not self.prefill_complete.is_set():
            outdata.fill(0)
            return

        # Should be valid data now - if not, log any status issues
        if status:
            logger.warning("Audio device status: %s", status)
            self.player_queue.put(f"audio:device status issue: {status}")

        data, id_hash, is_final_read = self.buffer.read(frames)

        # Get the song_id from the hash
        song_id = self.song_hashes.get(id_hash) if id_hash is not None else None

        if data is None:
            outdata.fill(0)
            self.prefill_complete.clear()
            self.player_queue.put(f"buffering:{self.current_song_id}:0")
            return
        
        # Copy available data to output buffer
        if len(data) <= frames:
            outdata[:len(data)] = data
            if len(data) < frames:
                outdata[len(data):].fill(0)  # Pad the rest with zeros
        else:
            outdata[:] = data[:frames]

        if is_final_read:
            logger.info("Playback finished, signaling complete.")
            self.player_queue.put("audio:reached end of playlist")
            self.player_queue.put("playback:complete")
            raise sd.CallbackStop()

        # Check if the song index has changed - modified to handle index 0
        if song_id is not None and song_id != self.current_song_id:
            self.current_song_id = song_id
            title = self.song_list.get(song_id, f"[Unknown:{song_id}]")
            self.player_queue.put(f"playing:{song_id}:{title}")

        # Apply volume adjustment (for pause fading)
        outdata *= self.current_volume

        # Apply peak limiting to prevent clipping
        self._peak_limiter.apply(outdata)

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

    def stop(self) -> None:
        """Immediately stop all playback and cleanup resources."""
        logger.info("Stopping playback...")

        # Signal stop event for threads and callbacks
        self.stop_event.set()

        # Stop and close the audio stream safely
        if self.stream and self.stream.active:
            try:
                self.stream.abort()
            except Exception as e:
                logger.error(f"Failed to abort stream: {str(e)}")

        # Ensure the buffering thread terminates gracefully
        if self._buffer_thread.is_alive():
            self._buffer_thread.join(timeout=2)

    def _hash_int32(self, id_string: str) -> int:
        hashed_value = hash(id_string)
        # Ensure the value is within the 32-bit signed integer range
        int32_max = 2**31 - 1
        int32_min = -2**31
        return hashed_value % (int32_max - int32_min + 1) + int32_min

class PeakLimiter:
    """Prevents audio clipping through peak normalization."""
    def __init__(self, config: AudioConfig):
        self.threshold = config.limiter_threshold

    def apply(self, data: np.ndarray) -> None:
        """Apply gain reduction to prevent clipping."""
        peak = np.max(np.abs(data))
        if peak > self.threshold:
            data *= self.threshold / peak