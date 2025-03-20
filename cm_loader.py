"""Model: Song loading and processing module with multiprocessing support."""
import logging
import os
import random
import threading
import unicodedata
import re
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

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
        self.songs: List[Dict] = []
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.ready_events: Dict[int, threading.Event] = {}
        self.previous_tail: Optional[np.ndarray] = None
        self.completed_count = 0
        self._lock = threading.Lock()
        self.stop_event = threading.Event()

    def add_songs(self, songs: List[Dict]) -> None:
        """Add songs to the processing queue with tracking indices."""
        with self._lock:
            for song in songs:
                song['index'] = len(self.songs)
                self.songs.append(song)
                self.ready_events[song['index']] = threading.Event()

    def start_processing(self) -> None:
        """Start parallel processing of all songs in added order."""
        try:
            for song in self.songs:
                if self.stop_event.is_set():
                    break
                self.executor.submit(self._process_song, song)
            self.executor.shutdown(wait=True)
        finally:
            self.processed_clips.put((None, None))  # Termination sentinel

    def stop(self) -> None:
        """Signal all threads to stop processing."""
        self.stop_event.set()
        self.executor.shutdown(wait=False)

    def _process_song(self, song: Dict) -> None:
        """Process individual song through download, processing, and clip generation."""
        if self.stop_event.is_set():
            return

        song_index = song['index']
        logger.info("Processing song: %s", song['title'])
        
        try:
            file_path = self._download_song(song)
            audio = self._load_and_process(file_path)
            
            # Calculate timing parameters
            clip_length = self.config.clip_length
            if self.config.clip_length == 0 or self.config.clip_length > len(audio) / self.config.sample_rate:
                clip_length = len(audio) / self.config.sample_rate

            clip_samples = int(clip_length * self.config.sample_rate)
            fade_samples = int(min(self.config.fade_duration, clip_length / 2) * self.config.sample_rate)
            
            # Generate random clip with fades
            clip = self._generate_clip(audio, song_index, clip_samples)
            self._apply_fades(clip, fade_samples)
            
            # Ensure previous song in order has been processed before continuing
            if song_index > 0:
                self.ready_events[song_index-1].wait()
                
            # Handle crossfade composition + start fade in / end fade out
            processed_clip = self._apply_crossfade(song_index, clip, fade_samples)
            
            # Update tracking state
            with self._lock:
                self.previous_tail = clip[-fade_samples:]
                self.processed_clips.put((song_index, processed_clip))
                self.completed_count += 1
                
        except Exception as e:
            logger.error("Failed to process %s: %s", song.get('title'), str(e))
        finally:
            self.ready_events[song_index].set()

    def _generate_clip(self, audio: np.ndarray, song_index: int, clip_samples: int) -> np.ndarray:
        """Generate random audio clip"""
        max_start = len(audio) - clip_samples
        clip_buffer = int(0.2 * max_start)
        start = random.randint(clip_buffer, max_start - clip_buffer)
        return audio[start:start + clip_samples]

    def _apply_fades(self, clip: np.ndarray, fade_samples: int) -> None:
        """Apply fade in and fade out to the clip"""
        fade_in = np.linspace(0, 1, fade_samples)[:, np.newaxis]
        fade_out = np.linspace(1, 0, fade_samples)[:, np.newaxis]
        clip[:fade_samples] *= fade_in
        clip[-fade_samples:] *= fade_out

    def _apply_crossfade(self, song_index: int, clip: np.ndarray, fade_samples: int) -> np.ndarray:
        """Apply appropriate fade-in/crossfade based on song position."""
        if song_index == 0:
            processed_clip = clip[:-fade_samples]  # Take Clip before fade out
        else:
            if self.previous_tail is None:
                raise ValueError("Missing previous song tail for crossfade")
            crossfade = self.previous_tail + clip[:fade_samples]
            processed_clip = np.concatenate([crossfade, clip[fade_samples:-fade_samples]])
        
        if song_index == len(self.songs) - 1:  # If end Song, concatenate current tail for fadeout
            processed_clip = np.concatenate([processed_clip, clip[-fade_samples:]])
        logger.debug(f"Song {song_index} processed - {len(processed_clip)} samples")
        return processed_clip

    def _download_song(self, song: Dict) -> str:
        """Downloads the song from YouTube using yt_dlp"""
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': os.path.join(self.config.audio_dir, '%(title)s.%(ext)s'),
            'quiet': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(song['url'], download=False)
            base_filepath = ydl.prepare_filename(info).rsplit('.', 1)[0]
            base_filename = os.path.basename(base_filepath)
            base_filepath += ".mp3"
    
            desired_filepath = os.path.join(self.config.audio_dir, f"{self.sanitize_filename(base_filename)}.mp3")

            if not os.path.exists(desired_filepath):
                logger.info(f"Downloading: {song['title']}")
                ydl.download([song['url']])
                os.rename(base_filepath, desired_filepath)
                
            return desired_filepath

    def sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to be compatible with most file systems."""
        filename = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii')
        invalid_chars = r'[<>:"/\\|?*.]'
        return re.sub(invalid_chars, "_", filename)

    def _load_and_process(self, filepath: str) -> np.ndarray:
        """Load and normalize audio file to numpy array."""
        audio = AudioSegment.from_file(filepath).set_channels(
            self.config.channels
        ).set_frame_rate(
            self.config.sample_rate
        ).apply_gain(self.config.volume_adjustment)
        
        samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
        return (samples / np.iinfo(np.int16).max).reshape(-1, self.config.channels)
