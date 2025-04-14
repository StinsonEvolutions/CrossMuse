import json
import os
import queue
import random
import re
import shutil
import logging
import threading
import multiprocessing as mp
from pathlib import Path
import time
from typing import Dict, Any, Optional

import yt_dlp
from ytmusicapi import YTMusic

from cm_gui import MainDialog
from cm_settings import AudioConfig
from cm_logging import setup_logger
from cm_settings import resource_path

logger = setup_logger()

class Controller:
    def __init__(self, root):
        self.config = self._load_config()
        self.ytmusic = YTMusic()

        self.view = MainDialog(root, self.config)
        self.view.protocol("WM_DELETE_WINDOW", self._exit)

        # Initialize playback-related attributes
        self.songs_status = {}  # Dict to store songs with their statuses
        self._reset_playback()
		
        # Add search-related attributes
        self.search_query = ""
        self.current_playlist = None
        self.last_search_time = 0
        self.search_thread = None
        self.playlist_thread = None
        self.last_saved_path = None
        self.playlists_cache = None
        self.search_timer = None  # Timer for delayed search
        self.search_event = threading.Event()  # Event for new search query
        self.search_lock = threading.Lock()  # Lock for search query

        # Bind button commands
        self.view.status_lbl.bind("<Button-1>", self._open_log_file)
        self.view.set_command(self.view.save_btn, self._save_playlist)
        self.view.set_command(self.view.load_btn, self._load_playlist)
        self.view.set_command(self.view.start_btn, self._start_playback)
        self.view.set_command(self.view.pause_btn, self._toggle_pause)
        self.view.set_command(self.view.stop_btn, self._stop)
        self.view.set_command(self.view.file_btn, lambda: self._select_song_list(self.view.song_list_var, self.view.song_list_lbl))
        self.view.set_command(self.view.audio_dir_btn, lambda: self._select_audio_dir(self.view.audio_dir_var, self.view.audio_dir_lbl))
        self.view.song_list_var.trace_add("write", lambda *_: self.view.update_playback_button_states(self.playback_active, self.paused))
        
        # Bind search events
        self.view.search_var.trace_add("write", self._handle_search_input)
        self.view.results_list.bind("<<ListboxSelect>>", self._handle_playlist_select)

        # Bind mouse wheel events to the canvas
        self.view.canvas.bind("<Enter>", self._bind_mouse_wheel)
        self.view.canvas.bind("<Leave>", self._unbind_mouse_wheel)

        # Start the search thread
        self.search_thread = threading.Thread(target=self._search_thread_func, daemon=True)
        self.search_thread.start()

        self._update_view_from_config(self.config)
        self.config = self._save_config()

        self.view.update_playback_button_states(self.playback_active, self.paused)
        self.view.mainloop()

    def _bind_mouse_wheel(self, event):
        self.view.canvas.bind_all("<MouseWheel>", self._on_mouse_wheel)
        self.view.canvas.bind_all("<Shift-MouseWheel>", self._on_shift_mouse_wheel)

    def _unbind_mouse_wheel(self, event):
        self.view.canvas.unbind_all("<MouseWheel>")
        self.view.canvas.unbind_all("<Shift-MouseWheel>")

    def _on_mouse_wheel(self, event):
        """Scroll vertically with mouse wheel."""
        self.view.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_shift_mouse_wheel(self, event):
        """Scroll horizontally with shift + mouse wheel."""
        self.view.canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")
    
    def _update_status(self, message: str, message_type: str = "info", is_error: bool = False) -> bool:
        """
        Centralized status message handler with priority management.
        
        Args:
            message: The status message to display
            message_type: Type of message for priority determination 
                        ("playing", "processing", "download", "buffering", "audio", "info")
            is_error: Whether this is an error message
        
        Returns:
            bool: True if the message was displayed, False if suppressed due to priority
        """
        logger.info(f"Status update [{message_type}]: {message}")
        
        # Error messages always get displayed regardless of priority
        if is_error:
            self.view.update_status(message, is_error=True)
            return True
            
        # Define message type priorities (higher number = higher priority)
        priorities = {
            "playing": 6,
            "buffering": 5,
            "processing": 4,
            "download": 4,
            "audio": 2,
            "info": 1
        }
        
        # Get priority of current message
        current_priority = priorities.get(message_type, 1)
        
        # Get priority of last message type
        last_priority = priorities.get(self.last_status_message_type, 0)
        
        # Determine if we should update the status based on priority
        should_update = False
        
        if current_priority >= last_priority:
            # Higher or equal priority messages replace current message
            should_update = True
        elif self.last_status_message_type == "playing" and self.paused:
            # If we're paused, allow lower priority messages to show
            should_update = True
        elif self.last_status_message_type is None:
            # If no previous message, always show
            should_update = True
            
        if should_update:
            self.view.update_status(message, is_error=is_error)
            self.last_status_message_type = message_type
            return True
        
        return False

    def _start_playback(self):
        """Handle playback start/resume."""
        if not self.playback_active:
            try:
                # Clear the log file before starting playback
                self._clear_log_file()
                
                # Reset the last status message type
                self.last_status_message_type = None
                
                # Initial status message
                self._update_status("Initializing audio engine...", message_type="info")

                config = self._save_config()
                audio_path = Path(self.view.audio_dir_var.get())
                if not audio_path.is_absolute():
                    audio_path = Path(self.view.audio_dir_var.get()) / audio_path
                os.makedirs(audio_path, exist_ok=True)
                
                self._update_status("Setting up directories...", message_type="info")
            
                playlist_path = Path(self.view.playlists_dir_var.get())
                if not playlist_path.is_absolute():
                    playlist_path = Path(self.view.playlists_dir_var.get()) / playlist_path
                os.makedirs(playlist_path, exist_ok=True)
            
                # Load and potentially upgrade the playlist
                self._update_status("Loading playlist...", message_type="info")
                songs = self._load_and_upgrade_playlist(self.view.song_list_var.get())
                
                logger.debug(f"Starting with config: {config}")
            
                # Initialize songs_status list
                for song in songs:
                    self.songs_status[song['id']] = {
                        'song': song, 
                        'downloaded': False, 
                        'buffered': False, 
                        'played': False,
                        'error': False
                    }
            
                self._update_status(f"Preparing {len(songs)} songs for playback...", message_type="info")
                
                # Limit the processing queue to a reasonable size - either 4 or the number of songs, whichever is smaller
                queue_size = min(4, len(songs))
                self.processed_clips_queue = mp.Queue(queue_size)
                logger.info(f"Initialized song queue to hold up to {queue_size} songs")

                self._update_status("Starting audio processing...", message_type="info")
                self.loader_process = mp.Process(
                    target=self._run_song_loader,
                    args=(songs, config.to_dict(), self.processed_clips_queue, self.loader_queue)
                )
                self.loader_process.start()
            
                self.command_queue = mp.Queue()
                self.player_process = mp.Process(
                    target=self._run_audio_player,
                    args=(config.to_dict(), self.processed_clips_queue, self.command_queue, self.player_queue)
                )
                self.player_process.start()
                self.playback_active = True

                # Start the playback manager thread
                self.playback_manager_thread = threading.Thread(target=self._playback_manager, daemon=True)
                self.playback_manager_thread.start()
                
                self._update_status("Waiting for first song to download...", message_type="info")

            except Exception as e:
                logger.error(f"Failed to start playback: {str(e)}")
                self.view.show_error("Playback Error", f"Failed to start playback: {str(e)}")
        
        self.view.update_playback_button_states(self.playback_active, self.paused)

    @staticmethod
    def _run_song_loader(songs: list, config: Dict[str, Any], queue: mp.Queue, loader_queue: mp.Queue) -> None:
        """Runs song loading in a dedicated process."""
        from cm_loader import SongLoader
        loader = SongLoader(AudioConfig.from_dict(config), queue)
        loader.add_songs(songs)
        loader.start_processing(loader_queue)

    @staticmethod
    def _run_audio_player(config: Dict[str, Any], in_queue: mp.Queue, cmd_queue: mp.Queue, player_queue: mp.Queue) -> None:
        """Runs audio player in a dedicated process."""
        from cm_player import AudioPlayer
        player = AudioPlayer(AudioConfig.from_dict(config), in_queue)
        player.start(cmd_queue, player_queue)

    def _playback_manager(self):
        """Manage playback by processing messages from both loader_queue and player_queue."""
        while self.playback_active:
            self._process_loader_messages()
            self._process_player_messages()
            time.sleep(0.1)

    def _process_loader_messages(self):
        """Process messages from the loader queue."""
        while not self.loader_queue.empty():
            try:
                message = self.loader_queue.get_nowait()
                if message.startswith("download:"):
                    _, song_id, percent_downloaded = message.split(":")
                    song_id, percent_downloaded = str(song_id), float(percent_downloaded)
                    
                    song_title = self.songs_status[song_id]['song']['title']
                    if percent_downloaded < 1:
                        self._update_status(f"Starting download for {song_title}...", message_type="download")
                    elif percent_downloaded < 100:
                        self._update_status(f"Downloading {song_title}... {percent_downloaded:.0f}%", message_type="download")
                    else:
                        self._update_status(f"Download complete for {song_title}", message_type="download")
                    
                    if percent_downloaded > 99:
                        self.songs_status[song_id]['downloaded'] = True
                
                elif message.startswith("error:"):
                    # Handle error messages from the loader
                    _, song_id, error_message = message.split(":", 2)
                    song_title = self.songs_status[song_id]['song']['title']
                    error_text = f"Error processing {song_title}: {error_message}"
                    logger.error(error_text)
                    self._update_status(error_text, message_type="info", is_error=True)
                    
                    # Mark the song as having an error
                    self.songs_status[song_id]['error'] = True
                
                elif message == "loader:complete":
                    # All songs have been processed, force playback to start if needed
                    logger.info("Received loader completion notification, forcing playback to start if needed")
                    self.command_queue.put("FORCE_START")
                    self._update_status("All songs processed, starting playback", message_type="processing")
                
                elif message.startswith("processing:"):
                    _, song_id = message.split(":", 1)
                    song_title = self.songs_status[song_id]['song']['title']
                    self._update_status(f"Processing {song_title}...", message_type="processing")

            except queue.Empty:
                break

    def _process_player_messages(self):
        """Process messages from the player queue."""
        while not self.player_queue.empty():
            try:
                message = self.player_queue.get_nowait()

                if message == "playback:complete":
                    self._update_status("Playback complete", message_type="info")
                    if self.current_song['id'] is not None:  # Check if we have a valid current song
                        self.songs_status[self.current_song['id']]['played'] = True
                    
                    # The loader will continuously feed songs in repeat mode
                    if not self.config.repeat:
                        self._stop()
                    else:
                        # Just update the status to indicate we're continuing in repeat mode
                        self._update_status("Continuing playback (repeat mode)", message_type="info")

                elif message.startswith("buffering:"):
                    _, song_id, percent_buffered = message.split(":", 2)
                    song_id, percent_buffered = str(song_id), float(percent_buffered)
                    
                    if percent_buffered < 1:
                        self._update_status("Starting buffer fill...", message_type="buffering")
                    elif percent_buffered < 99:
                        self._update_status(f"Buffering... {percent_buffered:.0f}%", message_type="buffering")
                    else:
                        self._update_status("Buffering complete", message_type="buffering")
                    
                    if percent_buffered >= 99:
                        self.songs_status[song_id]['buffered'] = True
                        
                        # If a song was already playing (ie. buffering to catch up), show playing message again
                        if self.current_song['id'] is not None:
                            song_index = self.songs_status[self.current_song['id']]['song']['index'] + 1
                            self._update_status(f"Playing {song_index}. {self.current_song['title']}", message_type="playing")

                elif message.startswith("playing:"):
                    # Playing messages always have the highest priority
                    _, song_id, title = message.split(":", 2)
                    if self.current_song['id'] is not None:
                        self.songs_status[self.current_song['id']]['played'] = True
                    self.current_song = {'id': song_id, 'title': title}
                    song_index = self.songs_status[self.current_song['id']]['song']['index'] + 1
                    self._update_status(f"Playing {song_index}. {self.current_song['title']}", message_type="playing")
                    
                elif message.startswith("audio:"):
                    # Audio system messages have lower priority
                    _, status_msg = message.split(":", 1)
                    self._update_status(f"Audio system: {status_msg}", message_type="audio")

            except queue.Empty:
                break

    def _toggle_pause(self):
        """Toggle pause state."""
        if self.playback_active:
            if self.paused:
                self._update_status("Resuming playback...", message_type="info")
                self.command_queue.put("RESUME")
            else:
                self._update_status("Pausing playback...", message_type="info")
                self.command_queue.put("PAUSE")
            self.paused = not self.paused
            self.view.update_playback_button_states(self.playback_active, self.paused)

    def _stop(self):
        logger.info("Shutting down...")
        
        self._update_status("Stopping playback...", message_type="info")
        
        if self.playback_active:
            self.command_queue.put("STOP")
            
        if self.loader_process:
            self.loader_process.join(timeout=5)
            if self.loader_process.is_alive():
                logger.warning("Forcibly terminating loader process")
                self._update_status("Forcibly terminating loader process...", message_type="info")
                self.loader_process.terminate()
        
        if self.player_process:
            self.player_process.join(timeout=5)
            if self.player_process.is_alive():
                logger.warning("Forcibly terminating player process")
                self._update_status("Forcibly terminating player process...", message_type="info")
                self.player_process.terminate()
        
        logger.info("Application shutdown complete")
        self._update_status("Playback stopped", message_type="info")
        self._reset_playback()

    def _reset_playback(self):
        self.playback_active = False
        self.paused = False
        self.processed_clips_queue: Optional[mp.Queue] = None
        self.loader_process: Optional[mp.Process] = None
        self.player_process: Optional[mp.Process] = None
        self.command_queue: mp.Queue = mp.Queue()
        self.player_queue: mp.Queue = mp.Queue()
        self.loader_queue: mp.Queue = mp.Queue()
        self.playback_manager_thread = None
        self.current_song = {'id': None, 'title': None}
        self.last_status_message_type = None  # Track the last status message type

        self.view.update_status("")
        self.view.update_playback_button_states(self.playback_active, self.paused)
        self.view.set_settings_enabled(True)

    def _exit(self):
        self._stop()
        self.view.root.quit()
        self.view.root.destroy()

    def _select_song_list(self, file_var, file_lbl):
        """Handle JSON file selection."""
        initDir = self.view.song_list_var.get()
        if initDir == "" or not Path(initDir).exists():
            initDir = self.view.playlists_dir_var.get()
        file_path = self.view.ask_open_filename(
            title="Select Playlist (JSON)",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            initialdir=initDir
        )
        if file_path:
            file_var.set(file_path)
            file_lbl.configure(text=Path(file_path).name)

    def _select_playlists_dir(self, dir_var, dir_lbl):
        """Handle playlists directory selection."""
        file_path = self.view.ask_directory(
            title="Select Playlists Directory",
            mustexist=False,
            initialdir=self.view.playlists_dir_var.get()
        )
        if file_path:
            dir_var.set(file_path)
            dir_lbl.configure(text=file_path)

    def _select_audio_dir(self, dir_var, dir_lbl):
        """Handle audio file directory selection."""
        file_path = self.view.ask_directory(
            title="Select folder to store downloaded audio files",
            mustexist=False,
            initialdir=self.view.audio_dir_var.get()
        )
        if file_path:
            dir_var.set(file_path)
            dir_lbl.configure(text=file_path)

    def _load_config(self) -> AudioConfig:
        """Load configuration from JSON file and initialize sample playlists if needed."""
        config_path = self._get_config_path()
        
        if not config_path.exists():
            logger.info("No config file found; initializing default settings and sample playlists.")
            
            # Initialize default playlists directory and copy sample playlists
            default_playlists_dir = Path(AudioConfig.playlists_dir)
            sample_playlists_dir = resource_path("Sample Playlists")
            
            if sample_playlists_dir.exists() and sample_playlists_dir.is_dir():
                default_playlists_dir.mkdir(parents=True, exist_ok=True)
                
                for playlist_file in sample_playlists_dir.glob('*.json'):
                    target_file = default_playlists_dir / playlist_file.name
                    if not target_file.exists():
                        shutil.copy2(playlist_file, target_file)
                        logger.info(f"Copied sample playlist: {playlist_file.name}")
                    else:
                        logger.info(f"Sample playlist already exists: {playlist_file.name}")
                
                logger.info(f"Initialized default playlists in {default_playlists_dir}")
            else:
                logger.warning("Sample Playlists directory not found. No sample playlists copied.")
            
            # Return a new default configuration since no config file exists
            return AudioConfig()
        
        try:
            with open(config_path) as f:
                config_dict = json.load(f)
                return AudioConfig.from_dict(config_dict)
        except Exception as e:
            logger.error(f"Error loading config: {str(e)}")
            return AudioConfig()

    def _save_config(self) -> AudioConfig:
        """Save current configuration to JSON file."""
        config_path = self._get_config_path()
        try:
            config = self._get_config_from_view()
            config_dict = config.to_dict()
            config_dict['version'] = AudioConfig.CONFIG_VERSION
            
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w") as f:
                json.dump(config_dict, f, indent=2)

            return config
        except Exception as e:
            logger.error(f"Error saving config: {str(e)}")
            raise

    def _get_config_path(self) -> Path:
        """Just use root directory for simplicity"""
        return resource_path("config.json")

    def _update_view_from_config(self, config: AudioConfig):
        """Update view elements with config values."""
        self.view.clip_var.set(config.clip_length)
        self.view.fade_var.set(config.fade_duration)
        self.view.sample_rate_var.set(str(config.sample_rate))
        self.view.buffer_var.set(config.buffer_seconds)
        self.view.prefill_var.set(config.prefill_time)
        self.view.latency_var.set(config.latency)
        self.view.shuffle_var.set(config.shuffle)
        self.view.repeat_var.set(config.repeat)
        
        if Path(config.recent_playlist).exists():
            self.view.song_list_var.set(config.recent_playlist)
            self.view.song_list_lbl.config(text=Path(config.recent_playlist).name)
        
        if config.playlists_dir != "" and Path(config.playlists_dir).exists():
            self.view.playlists_dir_var.set(config.playlists_dir)
            self.view.playlists_dir_lbl.config(text=config.playlists_dir)
        else:
            playlists_dir = self._default_audio_dir()
            self.view.playlists_dir_var.set(playlists_dir)
            self.view.playlists_dir_lbl.config(text=playlists_dir)
        
        if config.audio_dir != "" and Path(config.audio_dir).exists():
            self.view.audio_dir_var.set(config.audio_dir)
            self.view.audio_dir_lbl.config(text=config.audio_dir)
        else:
            default_dir = self._default_audio_dir()
            self.view.audio_dir_var.set(default_dir)
            self.view.audio_dir_lbl.config(text=default_dir)

    def _get_config_from_view(self) -> AudioConfig:
        """Create AudioConfig from current view state."""
        return AudioConfig(
            recent_playlist=self.view.song_list_var.get(),
            playlists_dir=self.view.playlists_dir_var.get(),
            audio_dir=self.view.audio_dir_var.get(),
            sample_rate=int(self.view.sample_rate_var.get()),
            buffer_seconds=self.view.buffer_var.get(),
            prefill_time=self.view.prefill_var.get(),
            latency=self.view.latency_var.get(),
            clip_length=self.view.clip_var.get(),
            fade_duration=self.view.fade_var.get(),
            shuffle=self.view.shuffle_var.get(),
            repeat=self.view.repeat_var.get()
        )
        
    def _open_log_file(self, event=None):
        """Open the log file with the default text editor."""
        log_file_path = resource_path("crossmuse.log")
        if os.path.exists(log_file_path):
            os.startfile(log_file_path)
        else:
            logger.error(f"Log file not found: {log_file_path}")
            self.view.show_error("Log File Error", f"Log file not found: {log_file_path}")

    def _clear_log_file(self):
        """Clear the log file contents."""
        log_file_path = resource_path("crossmuse.log")
        try:
            # Open the file in write mode, which truncates the file
            with open(log_file_path, 'w') as f:
                pass  # Just opening in 'w' mode clears the file
            logger.info("Log file cleared before starting playback")
        except Exception as e:
            logger.error(f"Failed to clear log file: {str(e)}")

    def _default_audio_dir(self) -> str:
        """Get default audio storage directory."""
        return str(Path.home() / "Music" / "CrossMuse Audio Files")
        
    def _handle_search_input(self, *args):
        self.search_query = self.view.search_var.get().strip()

        # Cancel previous search timer if still running
        if self.search_timer:
            self.view.after_cancel(self.search_timer)

        # Start new search with proper delay
        self.search_timer = self.view.after(400, self._trigger_search_event)
    
    def _trigger_search_event(self):
        with self.search_lock:
            self.search_event.set()
        
    def _search_thread_func(self):
        current_query = ""
        while True:
            self.search_event.wait()  # Wait for a new search query event
            with self.search_lock:
                self.search_event.clear()
                new_query = self.search_query

            if new_query != current_query:
                current_query = new_query
                self._search_playlists(new_query, self.config.search_matches)
        
    def _search_playlists(self, query: str, max_results: int):
        result:list[dict] = None
        try:
            result = self.ytmusic.search(query, filter='playlists', limit=max_results)
        except Exception as e:
            error_message = f"Search failed for query '{query}': {str(e)}"
            logger.error(error_message)
            self.view.after(0, lambda: self.view.update_status(
                error_message, is_error=True
            ))
    
        playlists = []
        for i in range(len(result)):
            entry = result[i]
            if isinstance(entry.get('browseId'), str) and len(entry['browseId']) == 36 and entry['browseId'].startswith('VL'):
                playlists.append({
                    'index': i,
                    'title': self._clean(entry['title']),
                    'id': entry['browseId'][2:],
                    'count': "..."
                })
                logger.info(playlists[len(playlists) - 1])
            else:
                logger.error(f"Invalid playlist entry: {self._clean(entry['title'])} - {len(entry['browseId'])}")
        
        # Update GUI on main thread
        self.view.after(0, lambda: self._update_search_results(query, playlists))

        for i in range(len(playlists)):
            try:
                playlist = self.ytmusic.get_playlist(playlists[i]['id'], limit=None)
                playlists[i].update({
                    'count': playlist.get('trackCount', 0),
                    'songs': list(map(lambda s: {
                        "id": s.get("videoId", ""),
                        "title": self._clean(s.get("title", "")),
                        "artists": ", ".join(self._clean(a["name"]) for a in s.get("artists", [])),
                        "duration": s.get("duration_seconds", 0) # Note: often is 0 from bad/missing metadata
                    }, filter(lambda s: s.get("videoId"), playlist.get("tracks", []))))
                })

                # break if a new search has been started
                if self.search_query != query:
                    break
                self.view.after(0, lambda: self._update_search_results(query, playlists))

            except Exception as e:
                error_message = f"Unable to retrieve playlist details for {i}. {playlists[i]['title']} ({playlists[i]['id']}): {str(e)}"
                logger.error(error_message)
                self.view.after(0, lambda: self.view.update_status(
                    error_message, is_error=True
                ))

    def _clean(self, text: str):
        return text.encode("charmap", errors="ignore").decode("charmap")

    def _update_progress(self, d):
        if d['status'] == 'downloading':
            self.view.after(0, lambda: self.view.update_status(
                f"Loading {d['_percent_str']}...", 
                is_error=False
            ))

    def _update_search_results(self, search_query: str, playlists: list):
        self.playlists_cache = playlists  # Store full data

        isNewSearch = search_query != self.search_query
        self.search_query = search_query
        
        ready_count = self.view.set_results(playlists) # Display playlists and return ready count

        if isNewSearch:
            if len(playlists) > 0:
                self.view.update_status(f"Found {len(playlists)} playlists")
            else:
                self.view.update_status("No playlists found")

        # If a selected playlist is now loaded, re-select it
        if not isNewSearch and self.current_playlist is not None and self.current_playlist['index'] < ready_count:
            #self._select_playlist(self.current_playlist['index'])
            self.view.select_result_item(self.current_playlist['index'])
            
    def _handle_playlist_select(self, event):
        selection = self.view.results_list.curselection()
        if not selection or not hasattr(self, 'playlists_cache'):
            self.current_playlist = None
            self.view.clear_selection()
            self.view.update_playlist_button_states(False, False)
            return

        self._select_playlist(selection[0])
        
    def _select_playlist(self, selection: int):        
        self.current_playlist = self.playlists_cache[selection]
        self.view.display_playlist_songs(self.current_playlist.get('songs', []))
        self.view.update_playlist_button_states('songs' in self.current_playlist, False)
        
    def _clean_filename(self, filename: str) -> str:
        """Clean the filename to ensure it contains only standard characters."""
        return re.sub(r'[^a-zA-Z0-9_\-\.]', '_', filename)

    def _load_and_upgrade_playlist(self, playlist_path: str) -> list:
        """Load playlist from JSON file and upgrade if necessary."""
        PLAYLIST_VERSION = 2  # Current playlist version (using 'id' instead of 'url')

        try:
            with open(playlist_path) as f:
                songs = json.load(f)
        
            # Check if we need to upgrade the playlist format
            needs_upgrade = False
        
            # Check if this is a version 1 playlist (has 'url' but no 'id')
            if songs and 'url' in songs[0] and 'id' not in songs[0]:
                logger.info(f"Detected version 1 playlist format in {playlist_path}, upgrading to version 2")
                self.view.update_status(f"Upgrading playlist format...", is_error=False)
            
                # Upgrade each song to use 'id' instead of 'url'
                for song in songs:
                    if 'url' in song:
                        # Extract video ID from URL
                        url = song['url']
                        video_id = None
                    
                        # Try to extract ID from YouTube Music URL
                        if AudioConfig.YOUTUBE_MUSIC_VIDEO_URL_PREFIX in url:
                            video_id = url.split("v=")[-1].split("&")[0]
                    
                        # If we couldn't extract an ID, skip this song
                        if not video_id:
                            logger.warning(f"Could not extract video ID from URL: {url}")
                            continue
                    
                        # Add the ID field and remove the URL field
                        song['id'] = video_id
                        song.pop('url', None)  # Remove the 'url' field
                    
                        needs_upgrade = True
            
            # Check for duration in time format and convert to seconds
            for song in songs:
                if 'duration' in song and isinstance(song['duration'], str):
                    # Check if duration is in time format (contains colons)
                    if ':' in song['duration']:
                        try:
                            # Split by colon and convert to seconds
                            parts = song['duration'].split(':')
                            
                            # Handle different formats (H:M:S or M:S)
                            if len(parts) == 3:  # H:M:S format
                                hours, minutes, seconds = map(int, parts)
                                total_seconds = hours * 3600 + minutes * 60 + seconds
                            elif len(parts) == 2:  # M:S format
                                minutes, seconds = map(int, parts)
                                total_seconds = minutes * 60 + seconds
                            else:
                                # Unexpected format, default to 180 seconds
                                logger.warning(f"Unexpected time format '{song['duration']}' for song '{song.get('title', 'Unknown')}', defaulting to 180 seconds")
                                total_seconds = 180
                            
                            logger.info(f"Converted duration for '{song.get('title', 'Unknown')}' from {song['duration']} to {total_seconds} seconds")
                            song['duration'] = total_seconds
                            needs_upgrade = True
                        except ValueError:
                            logger.warning(f"Could not parse duration '{song['duration']}' for song '{song.get('title', 'Unknown')}', defaulting to 180 seconds")
                            song['duration'] = 180
                            needs_upgrade = True
                    else:
                        # Try to convert string to integer
                        try:
                            song['duration'] = int(song['duration'])
                            needs_upgrade = True
                        except ValueError:
                            logger.warning(f"Could not convert duration '{song['duration']}' to integer for song '{song.get('title', 'Unknown')}', defaulting to 180 seconds")
                            song['duration'] = 180
                            needs_upgrade = True
            
            # Save the upgraded playlist if needed
            if needs_upgrade:
                self._save_upgraded_playlist(playlist_path, songs)
                logger.info(f"Successfully upgraded playlist format")
                self.view.update_status(f"Playlist upgraded to new format", is_error=False)
        
            # Validate that all songs have the required fields
            for i, song in enumerate(songs):
                if 'id' not in song:
                    logger.warning(f"Song at index {i} is missing 'id' field, skipping")
                    continue
            
                # Ensure all songs have the required fields
                song['index'] = i
                if 'title' not in song:
                    song['title'] = f"Unknown Song {i+1}"
                if 'artists' not in song:
                    song['artists'] = "Unknown Artist"
                if 'duration' not in song:
                    song['duration'] = 180  # Default to 3 minutes
        
            return songs

        except Exception as e:
            logger.error(f"Failed to load playlist: {str(e)}")
            self.view.show_error("Playlist Error", f"Failed to load playlist: {str(e)}")
            return []
    
    def _save_upgraded_playlist(self, playlist_path: str, songs: list) -> None:
        """Save the upgraded playlist back to the original file."""
        try:
            # Create a backup of the original playlist
            backup_path = f"{playlist_path}.bak"
            shutil.copy2(playlist_path, backup_path)
            logger.info(f"Created backup of original playlist at {backup_path}")
        
            # Save the upgraded playlist
            with open(playlist_path, 'w') as f:
                json.dump(songs, f, indent=2)
        
            logger.info(f"Saved upgraded playlist to {playlist_path}")
        except Exception as e:
            logger.error(f"Failed to save upgraded playlist: {str(e)}")
            self.view.show_error("Playlist Error", f"Failed to save upgraded playlist: {str(e)}")

    def _save_playlist(self):
        """Save the current playlist to a JSON file."""
        if not self.current_playlist:
            return

        # Clean the playlist title
        clean_title = self._clean_filename(self.current_playlist['title'])
        default_name = f"{clean_title}.json"
        default_path = Path(self.config.playlists_dir) / default_name
        
        # Check if a file with the same name already exists
        file_exists = default_path.exists()
        save_path = default_path
        
        if file_exists:
            # Show a message about the existing file
            message = f"A playlist named '{default_name}' already exists. Do you want to overwrite it?"
            if not self.view.ask_yes_no("Playlist Exists", message):
                # User chose not to overwrite, show save dialog
                path = self.view.ask_save_filename(
                    initialdir=self.config.playlists_dir,
                    initialfile=default_name,
                    filetypes=(("JSON files", "*.json"), ("All files", "*.*"))
                )
                if not path:
                    return
                save_path = path
        
        try:
            # Ensure we're saving in the current format (version 2)
            songs_to_save = []
            for song in self.current_playlist.get('songs', []):
                # Ensure duration is an integer
                duration = song.get("duration", 180)
                if isinstance(duration, str):
                    try:
                        # Split by colon and convert to seconds if in time format
                        if ':' in duration:
                            parts = duration.split(':')
                            
                            # Handle different formats (H:M:S or M:S)
                            if len(parts) == 3:  # H:M:S format
                                hours, minutes, seconds = map(int, parts)
                                duration = hours * 3600 + minutes * 60 + seconds
                            elif len(parts) == 2:  # M:S format
                                minutes, seconds = map(int, parts)
                                duration = minutes * 60 + seconds
                            else:
                                # Unexpected format, default to 180 seconds
                                logger.warning(f"Unexpected time format '{duration}' for song '{song.get('title', 'Unknown')}', defaulting to 180 seconds")
                                duration = 180
                        else:
                            duration = int(duration)
                    except ValueError:
                        duration = 180
                
                song_data = {
                    "id": song.get("id", ""),
                    "title": song.get("title", "Unknown"),
                    "artists": song.get("artists", "Unknown"),
                    "duration": duration
                }
                songs_to_save.append(song_data)
            
            with open(save_path, 'w') as f:
                json.dump(songs_to_save, f, indent=2)

            self.last_saved_path = str(save_path)
            self.view.update_playlist_button_states(True, True)
            self.view.update_status(f"Playlist saved to {save_path}")
        except Exception as e:
            self.view.update_status(f"Save failed: {str(e)}", is_error=True)

    def _load_playlist(self):
        """Load the saved playlist into the active playlist slot."""
        if not self.last_saved_path:
            return
        
        # Set the playlist in the song_list_var (Configure tab)
        self.view.song_list_var.set(self.last_saved_path)
        self.view.song_list_lbl.configure(text=Path(self.last_saved_path).name)
        
        # Update the configuration to remember this playlist
        self.config = self._save_config()
        
        # Update the status message
        self.view.update_status(f"Loaded playlist: {Path(self.last_saved_path).name}")
        
        # Update button states
        self.view.update_playback_button_states(self.playback_active, self.paused)

