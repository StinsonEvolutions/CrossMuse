"""Model: Song loading and processing module with multiprocessing support."""
import json
import os
from pathlib import Path
import random
import threading
import time
import unicodedata
import re
import multiprocessing as mp
import queue
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Deque
from collections import deque

import numpy as np
import yt_dlp
from pydub import AudioSegment

from cm_settings import AudioConfig
from cm_logging import setup_logger

logger = setup_logger()

class SongLoader:
    """Handles song download, processing, and clip generation in background threads."""
    def __init__(self, config: AudioConfig, processed_clips_queue: mp.Queue):
        """Initialize song loader with output directory and results queue."""
        self.config = config
        self.processed_clips = processed_clips_queue
        self.loader_queue = None
        self.songs: List[Dict] = []
        self.max_workers = min(4, processed_clips_queue._maxsize)
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self.previous_tail: Optional[np.ndarray] = None
        self.complete_event = threading.Event()
        self.repeat_mode = config.repeat
        self.shuffle_mode = config.shuffle
        
        # Processing queue and related variables
        self.processing_queue = deque()  # Queue of songs to process
        self.queue_lock = threading.Lock()  # Lock for thread-safe queue access
        self.queue_not_empty = threading.Condition(self.queue_lock)  # Condition for queue not empty
        self.current_buffer_seconds = 0  # Current audio content length in seconds
        self.processed_songs = set()  # Set of processed song IDs
        self.last_song = None   # Last song processed
        self.last_cycle_songs = []  # Track the last N songs from previous cycle
        self.history_buffer_size = 0  # Will be set in start_processing
        self.worker_threads = []  # List of worker threads
        self.current_cycle = 0  # Current playlist cycle
        self.ready_events: Dict[str, threading.Event] = {}
    
        # Track clip lengths for processed_clips queue
        self.clip_lengths = deque()  # Store lengths of clips in the processed_clips queue
        self.clip_lengths_lock = threading.Lock()  # Lock for thread-safe access to clip_lengths

    def add_songs(self, songs: List[Dict]) -> None:
        """Add songs to the processing queue with tracking indices."""
        # Store original songs list
        for i, song in enumerate(songs):
            self.ready_events[song['id']] = threading.Event()
            self.songs.append(song)

    def start_processing(self, loader_queue: mp.Queue) -> None:
        """Start continuous processing of songs with smart queue management."""
        self.loader_queue = loader_queue
    
        # Set history buffer size based on playlist length
        self.history_buffer_size = len(self.songs) // 3
        logger.info(f"Set history buffer size to {self.history_buffer_size}")
    
        logger.info(f"Starting song processing with repeat={self.repeat_mode}, shuffle={self.shuffle_mode}")
    
        try:
            # Start worker threads
            for i in range(self.max_workers):
                thread = threading.Thread(
                    target=self._worker_thread_func,
                    args=(i,),
                    daemon=True
                )
                self.worker_threads.append(thread)
                thread.start()
            
            # Initial population of the queue and ongoing monitoring
            self._queue_monitor_thread()
            
        finally:
            # Wait for worker threads to finish
            for thread in self.worker_threads:
                thread.join(timeout=2)
            
            # Send termination sentinel
            self.processed_clips.put((None, None, None))
            logger.info("Song processing completed")

    def _queue_monitor_thread(self):
        """Monitor the processing queue and add more songs as needed."""
        while not self.complete_event.is_set():               
            with self.queue_lock:
                # Check the total length of processing + processed clips
                queue_length_seconds = self._queue_clips_length()
                
                # Add songs to the queue iff processed + processing < buffer size
                if (queue_length_seconds  < self.config.buffer_seconds):
                    
                    if len(self.processed_songs) >= len(self.songs):
                        # If repeat enabled, reset cycle
                        if self.repeat_mode:
                            self._prepare_next_cycle()
                    
                    # Add more songs if available and queue not full
                    self._add_songs_to_queue()
            
                    # Notify worker threads that the queue is not empty
                    self.queue_not_empty.notify_all()
    
            # Sleep to avoid busy waiting
            time.sleep(0.5)

    def _prepare_next_cycle(self):
        """Prepare for the next playlist cycle."""
        logger.info(f"Preparing for cycle {self.current_cycle + 1}")
        
        # Get the most recent songs from both the processing queue and processed songs
        recent_songs = []
    
        # First, get songs from the processing queue (these are the most recent)
        queue_songs = [item[0]['id'] for item in self.processing_queue]
        recent_songs.extend(queue_songs)
    
        # If we need more songs to reach history_buffer_size, get them from processed_songs
        if len(recent_songs) < self.history_buffer_size:
            # Convert processed_songs to a list and get the most recent ones
            processed_list = list(self.processed_songs)
            # Get the remaining number of songs needed from the end of processed_songs
            remaining_needed = self.history_buffer_size - len(recent_songs)
            recent_from_processed = processed_list[-remaining_needed:]
            recent_songs.extend(recent_from_processed)
    
        # Trim to history_buffer_size if we have more
        self.last_cycle_songs = recent_songs[-self.history_buffer_size:]
        
        # Reset processed songs for the new cycle
        self.processed_songs.clear()
        
        # Increment cycle counter
        self.current_cycle += 1
        
        logger.info(f"Starting cycle {self.current_cycle}")

    def _add_songs_to_queue(self):
        """Add more songs to the processing queue using smart selection."""
        # Get songs that haven't been processed in this cycle
        unprocessed_songs = [
            song.copy() for song in self.songs 
            if song['id'] not in self.processed_songs and all(item[0]['id'] != song['id'] for item in self.processing_queue)
        ]
    
        if not unprocessed_songs:
            return
        
        # Apply smart shuffle if enabled
        if self.shuffle_mode:
            # Split into recent and other songs
            recent_songs = [
                song for song in unprocessed_songs 
                if song['id'] in self.last_cycle_songs
            ]
            other_songs = [
                song for song in unprocessed_songs 
                if song['id'] not in self.last_cycle_songs
            ]
        
            # Shuffle both parts
            random.shuffle(recent_songs)
            random.shuffle(other_songs)
        
            # Combine with recent songs at the end to avoid back-to-back repeats
            unprocessed_songs = other_songs + recent_songs
    
        # Add songs to the queue, up to a reasonable limit
        songs_to_add = min(self.max_workers * 2, len(unprocessed_songs))

        for i in range(songs_to_add):
            logger.info(f"Adding song {unprocessed_songs[i]['id']} to queue")
            self._add_song_to_queue(unprocessed_songs[i], i == len(unprocessed_songs) - 1)
            
        logger.info(f"Added {songs_to_add} songs to processing queue")

    def _add_song_to_queue(self, song: Dict, is_final_song: bool = False):
        """Add a song to the processing queue, updating tracking values accordingly"""
        # Set the previous song ID and update the last song
        logger.info(f"prevId: {song.get('prevId', 'none')}, last_song: {self.last_song}, id: {song['id']}")
        song['prevId'] = self.last_song
        self.last_song = song['id']

        # Add the song to the queue with the final song flag
        self.processing_queue.append((song, is_final_song))
    
        # Update the current buffer seconds
        song_duration = song.get('duration', self.config.clip_length)
        self.current_buffer_seconds += min(
            song_duration, 
            self.config.clip_length if self.config.clip_length > 0 else song_duration
        )

    def _queue_clips_length(self) -> float:
        """Return the total length of audio clips in the processed_clips queue in seconds."""
        with self.clip_lengths_lock:
            # Check if we need to sync our tracking with the actual queue size
            queue_size = len(self.processing_queue) + self.processed_clips.qsize()
        
            # If our tracking has more items than the queue, remove items from the start
            # This happens when the player process has consumed items from the queue
            while len(self.clip_lengths) > queue_size:
                self.clip_lengths.popleft()
            
            # Return the sum of all tracked clip lengths
            return sum(self.clip_lengths)
    
    def _worker_thread_func(self, worker_id):
        """Worker thread function to process songs from the queue."""
        logger.info(f"Worker {worker_id} started")
    
        while not self.complete_event.is_set() or len(self.processing_queue) > 0:
            # Get the next song from the queue
            song = None
            is_final_song = False

            with self.queue_lock:
                
                if len(self.processing_queue) == 0:
                    # Wait for the queue to have items
                    self.queue_not_empty.wait(timeout=1.0)

                if len(self.processing_queue) > 0:
                    song, is_final_song = self.processing_queue.popleft()
                    self.processed_songs.add(song['id'])  # Mark the song as processed
        
            if song:
                try:
                    # Process the song
                    success = self._process_song(song, is_final_song)
                
                    if success:
                        self.ready_events[song['id']].set()  # Indicate that this song is ready
                        logger.info(f"Worker {worker_id} processed song {song['title']}")
                
                except Exception as e:
                    logger.error(f"Worker {worker_id} error processing song: {str(e)}")

                finally:
                    if is_final_song and not self.repeat_mode:
                        self.complete_event.set()
        
            # Small sleep to prevent CPU thrashing
            time.sleep(0.1)
    
        logger.info(f"Worker {worker_id} stopped")
    
    def _process_song(self, song: Dict, is_final_song: bool) -> bool:
        """Process a single song - download, process, and send to player."""
        song_id = song['id']
        
        logger.info(f"Processing song {song_id}. {song['title']} (id: {song_id})")

        try:
            # Calculate clip timing parameters first
            song_duration = song.get('duration', 0)
            start_time, clip_length = self._calculate_clip_timing(song_duration)

            # Track the clip length (in seconds)
            with self.clip_lengths_lock:
                self.clip_lengths.append(clip_length or 30) # Default to 30 seconds for bad/missing metadata
            
            # Download the song
            download_full = (clip_length > song_duration * 0.5) if song_duration > 0 else True
            file_path = self._download_song(song, start_time, clip_length, download_full)
            logger.info(f"Downloaded {song['title']} to path {file_path}")
            
            # Load and process the audio
            self.loader_queue.put(f"processing:{song_id}")
            audio = self._load_and_process(file_path, start_time, clip_length, download_full)
        
            # Adjust clip length if needed based on actual audio length
            if clip_length == 0 or clip_length > len(audio) / self.config.sample_rate:
                clip_length = len(audio) / self.config.sample_rate
            logger.info(f"Processed {song['title']} with clip length {clip_length}")

            fade_samples = int(min(self.config.fade_duration, clip_length / 2) * self.config.sample_rate)
        
            # Apply fades to the clip
            self._apply_fades(audio, fade_samples)
            logger.info(f"Applied fades to {song['title']}")
            
            # Wait until previous song has been processed
            if song['prevId'] is not None:
                self.ready_events[song['prevId']].wait()
                self.ready_events[song['prevId']].clear()

            processed_clip = self._apply_crossfade(song_id, audio, fade_samples, is_final_song)
            logger.info(f"Applied crossfades to {song['title']}")
            # Store the tail for the next song
            self.previous_tail = audio[-fade_samples:]
        
            # Send to player
            self.processed_clips.put((song_id, song['title'], processed_clip))
            logger.info(f"Sent processed clip for {song['title']} with length {len(processed_clip)}")
        
            # Notify when the final song has been processed
            if is_final_song and not self.repeat_mode:
                self.loader_queue.put("loader:complete")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to process {song['title']}: {str(e)}")
            return False

    def _download_song(self, song: Dict, start_time: int, duration: int, download_full: bool) -> str:
        """Downloads the song from YouTube using yt_dlp"""
        song_id = song['id']
        song_url = f"{AudioConfig.YOUTUBE_MUSIC_VIDEO_URL_PREFIX}{song_id}"

        def progress_hook(progress):
            if progress['status'] in ['downloading', 'finished']:
                percent = progress['_percent']
                self.loader_queue.put(f"download:{song['id']}:{percent:.0f}")


        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': str(int(self.config.sample_rate / 1000))
            }],
            'outtmpl': os.path.join(self.config.audio_dir, '%(title)s.%(ext)s'),
            'quiet': True,
            'progress_hooks': [progress_hook],
            'force_download': True,
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 3,
            'skip_unavailable_fragments': True,
        }
        
        # Only set download range if we're not downloading the full song
        if not download_full and self.config.clip_length > 0:
            logger.info(f"Downloading clip from {start_time}s to {start_time + duration}s")
            ydl_opts['download_ranges'] = yt_dlp.utils.download_range_func([], [[start_time, start_time + duration]])
        else:
            logger.info(f"Downloading full song (clip length > 50% of song)")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.info(f"Downloading {song['title']}...")
                info = ydl.extract_info(song_url, download=False)
                base_filepath = ydl.prepare_filename(info).rsplit('.', 1)[0]
                base_filename = os.path.basename(base_filepath)
                base_filepath += ".mp3"
                desired_filepath = os.path.join(self.config.audio_dir, f"{self.sanitize_filename(base_filename)}_{song_id}.mp3")

                # Note: only downloading clip, so download and overwrite every time
                self.loader_queue.put(f"download:{song['id']}:0")
                
                # Set a timeout for the download
                download_success = False
                download_attempts = 0
                max_attempts = 3
                
                while not download_success and download_attempts < max_attempts:
                    try:
                        download_attempts += 1
                        ydl.download([song_url])
                        download_success = True
                    except yt_dlp.utils.DownloadError as e:
                        logger.error(f"Download attempt {download_attempts} failed: {str(e)}")
                        if download_attempts >= max_attempts:
                            raise
                        time.sleep(2)  # Wait before retrying
                
                logger.info(f"Downloaded {song['title']}")
                
                # Check if the file exists before trying to move it
                if os.path.exists(base_filepath):
                    os.replace(base_filepath, desired_filepath)
                else:
                    raise FileNotFoundError(f"Expected file not found: {base_filepath}")

                return desired_filepath
        except Exception as e:
            logger.error(f"Failed to download {song['title']}: {str(e)}")
            # Return a fallback or raise the exception
            raise

    def _load_and_process(self, filepath: str, start_time: int, clip_length: int, downloaded_full: bool) -> np.ndarray:
        """Load and normalize audio file to numpy array."""
        audio = AudioSegment.from_file(filepath).set_channels(
            self.config.channels
        ).set_frame_rate(
            self.config.sample_rate
        ).apply_gain(self.config.volume_adjustment)

        # Identify actual song length (handles case where bad metadata with missing duration)
        actual_length = len(audio) / 1000  # AudioSegment length is in milliseconds
        
        # If start_time >= clip_length, recalculate using actual audio length
        if start_time >= clip_length:
            logger.info(f"Invalid clip timing detected. Recalculating based on actual audio length of {actual_length}s")
            start_time, clip_length = self._calculate_clip_timing(actual_length)
        
        # If we downloaded the full song but only want a clip, extract it here
        if downloaded_full and self.config.clip_length > 0:
            start_ms = start_time * 1000  # Convert to milliseconds
            end_ms = (start_time + clip_length) * 1000  # Convert to milliseconds
            
            # Ensure we don't exceed the audio length
            if end_ms > len(audio):
                end_ms = len(audio)
            
            logger.info(f"Extracting clip from {start_ms}ms to {end_ms}ms from full audio")
            audio = audio[start_ms:end_ms]
        
        # Ensure the audio segment is limited to the clip length
        elif self.config.clip_length > 0 and not downloaded_full:
            clip_length_ms = clip_length * 1000  # Convert to milliseconds
            audio = audio[:clip_length_ms]
        
        samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
        return (samples / np.iinfo(np.int16).max).reshape(-1, self.config.channels)
    
    def _calculate_clip_timing(self, song_duration, clip_length=None):
        """
        Calculate clip timing parameters based on song duration and desired clip length.
        
        Args:
            song_duration: Duration of the song in seconds
            clip_length: Desired clip length (defaults to config.clip_length if None)
        
        Returns:
            tuple: (start_time, clip_length) in seconds
        """
        if clip_length is None:
            clip_length = self.config.clip_length
        
        # Default to full song if clip_length is 0 or greater than song duration
        if clip_length <= 0 or clip_length > song_duration:
            start_time = 0
            clip_length = song_duration
        else:
            # Calculate weighted random start time towards the center of the song
            max_start_time = max(0, song_duration - clip_length)
            if max_start_time > 0:
                center = max_start_time / 2
                deviation = max_start_time / 4  # Adjust deviation as needed
                start_time = int(random.gauss(center, deviation))
                start_time = max(0, min(start_time, max_start_time))  # Clamp to valid range
            else:
                start_time = 0
                
        return start_time, clip_length

    def _apply_fades(self, clip: np.ndarray, fade_samples: int) -> None:
        """Apply fade in and fade out to the clip"""
        fade_in = np.linspace(0, 1, fade_samples)[:, np.newaxis]
        fade_out = np.linspace(1, 0, fade_samples)[:, np.newaxis]
        clip[:fade_samples] *= fade_in
        clip[-fade_samples:] *= fade_out

    def _apply_crossfade(self, song_id: str, clip: np.ndarray, fade_samples: int, is_final_song: bool = False) -> np.ndarray:
        """Apply appropriate fade-in/crossfade based on song position."""
        # If this is the first song ever processed and we have no previous tail
        if self.previous_tail is None:
            # For the first song, we should include the full clip if it's also the final song
            if is_final_song:
                processed_clip = clip
            else:
                processed_clip = clip[:-fade_samples]
        # Otherwise, apply crossfade with previous tail
        else:
            crossfade = self.previous_tail + clip[:fade_samples]
            # For the final song, include the fade out portion
            if is_final_song and not self.repeat_mode:
                processed_clip = np.concatenate([crossfade, clip[fade_samples:]])
            else:
                processed_clip = np.concatenate([crossfade, clip[fade_samples:-fade_samples]])
            
        logger.debug(f"Song {song_id} processed - {len(processed_clip)} samples")
        return processed_clip

    def sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to be compatible with most file systems."""
        filename = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii')
        invalid_chars = r'[<>:"/\\|?*.]'
        return re.sub(invalid_chars, "_", filename)