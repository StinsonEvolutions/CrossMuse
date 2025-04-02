import json
import os
import queue
import re
import shutil
import logging
import threading
import multiprocessing as mp
from pathlib import Path
from typing import Dict, Any, Optional

import yt_dlp
from ytmusicapi import YTMusic

from cm_gui import MainDialog
from cm_settings import AudioConfig
from cm_logging import setup_logger
from cm_settings import resource_path

logger = setup_logger(level=logging.DEBUG)

class Controller:
    def __init__(self, root):
        self.config = self._load_config()
        self.ytmusic = YTMusic()

        self.view = MainDialog(root, self.config)
        self.view.protocol("WM_DELETE_WINDOW", self._exit)
        
        self.playback_active = False
        self.paused = False
        self.processed_clips_queue: Optional[mp.Queue] = None
        self.loader_process: Optional[mp.Process] = None
        self.player_process: Optional[mp.Process] = None
        self.command_queue: mp.Queue = mp.Queue()
        self.player_queue: mp.Queue = mp.Queue()
		
        # Add new search-related attributes
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
        self.view.set_command(self.view.stop_btn, self._stop_playback)
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

    def _start_playback(self):
        """Handle playback start/resume."""
        if not self.playback_active:
            try:
                config = self._save_config()
                audio_path = Path(self.view.audio_dir_var.get())
                if not audio_path.is_absolute():
                    audio_path = Path(self.view.audio_dir_var.get()) / audio_path
                os.makedirs(audio_path, exist_ok=True)
                
                playlist_path = Path(self.view.playlists_dir_var.get())
                if not playlist_path.is_absolute():
                    playlist_path = Path(self.view.playlists_dir_var.get()) / playlist_path
                os.makedirs(playlist_path, exist_ok=True)
                
                with open(self.view.song_list_var.get()) as f:
                    songs = json.load(f)
    
                logger.debug(f"Starting with config: {config}")
                
                self.processed_clips_queue = mp.Queue()
                self.loader_process = mp.Process(
                    target=self._run_song_loader,
                    args=(songs, config.to_dict(), self.processed_clips_queue)
                )
                self.loader_process.start()
                
                self.command_queue = mp.Queue()
                self.player_process = mp.Process(
                    target=self._run_audio_player,
                    args=(config.to_dict(), self.processed_clips_queue, self.command_queue, self.player_queue)
                )
                self.player_process.start()
                self.playback_active = True
                self.view.after(100, self._process_player_status)

            except Exception as e:
                logger.error(f"Failed to start playback: {str(e)}")
                self.view.show_error("Playback Error", f"Failed to start playback: {str(e)}")
        
        self.view.update_playback_button_states(self.playback_active, self.paused)

    @staticmethod
    def _run_song_loader(songs: list, config: Dict[str, Any], queue: mp.Queue) -> None:
        """Runs song loading in a dedicated process."""
        from cm_loader import SongLoader
        loader = SongLoader(AudioConfig.from_dict(config), queue)
        loader.add_songs(songs)
        loader.start_processing()

    @staticmethod
    def _run_audio_player(config: Dict[str, Any], in_queue: mp.Queue, cmd_queue: mp.Queue, player_queue: mp.Queue) -> None:
        """Runs audio player in a dedicated process."""
        from cm_player import AudioPlayer
        player = AudioPlayer(AudioConfig.from_dict(config), in_queue)
        player.start(cmd_queue, player_queue)

    def _process_player_status(self):
        if self.playback_active and not self.player_process.is_alive():
            logger.info("Player process has terminated")
            self._stop_playback()
        elif self.playback_active:
            self._process_player_messages()  # Call the new method to process player messages
            self.view.after(100, self._process_player_status)

    def _process_player_messages(self):
        """Process messages from the player queue."""
        while not self.player_queue.empty():
            try:
                message = self.player_queue.get_nowait()
                if message == "complete":
                    self.view.update_status("Playback complete")
                    self._stop_playback()
                elif message.startswith("playing:"):
                    index, title = message[len("playing:"):].split("_", 1)
                    self.current_song_title = title
                    self.view.update_status(f"Playing {int(index)+1}. {self.current_song_title}")
            except queue.Empty:
                break

    def _toggle_pause(self):
        """Toggle pause state."""
        if self.playback_active:
            if self.paused:
                self.command_queue.put("RESUME")
            else:
                self.command_queue.put("PAUSE")
            self.paused = not self.paused
            self.view.update_playback_button_states(self.playback_active, self.paused)
            
    def _stop_playback(self):
        logger.info("Shutting down...")
        if self.playback_active:
            self.command_queue.put("STOP")
            
        if self.loader_process:
            self.loader_process.join(timeout=5)
            if self.loader_process.is_alive():
                logger.warning("Forcibly terminating loader process")
                self.loader_process.terminate()
        
        if self.player_process:
            self.player_process.join(timeout=5)
            if self.player_process.is_alive():
                logger.warning("Forcibly terminating player process")
                self.player_process.terminate()
        
        logger.info("Application shutdown complete")
        self.playback_active = False
        self.paused = False
        self.loader_process = None
        self.player_process = None
        self.view.update_playback_button_states(self.playback_active, self.paused)
        self.view.set_settings_enabled(True)

    def _exit(self):
        self._stop_playback()
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
            fade_duration=self.view.fade_var.get()
        )
        
    def _open_log_file(self, event=None):
        """Open the log file with the default text editor."""
        log_file_path = resource_path("crossmuse.log")
        if os.path.exists(log_file_path):
            os.startfile(log_file_path)
        else:
            logger.error(f"Log file not found: {log_file_path}")
            self.view.show_error("Log File Error", f"Log file not found: {log_file_path}")

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
        try:
            result = self.ytmusic.search(query, filter='playlists', limit=max_results)
            
            playlists = []
            for i in range(len(result)):
                entry = result[i]
                if isinstance(entry.get('browseId'), str) and len(entry['browseId']) > 2:
                    playlists.append({
                        'index': i,
				        'title': self._clean(entry['title']),
				        'id': entry['browseId'][2:],
                        'count': "..."
			        })
                    logger.info(playlists[len(playlists) - 1])
                else:
                    logger.error(f"Invalid playlist entry: {entry}")
            
            # Update GUI on main thread
            self.view.after(0, lambda: self._update_search_results(query, playlists))

            for i in range(len(playlists)):
                playlist = self.ytmusic.get_playlist(playlists[i]['id'], limit=None)
                playlists[i].update({
                    'count': playlist.get('trackCount', 0),
                    'songs': list(map(lambda s: {
                        "url": "https://music.youtube.com/watch?v=" + s.get("videoId", ""),
                        "title": self._clean(s.get("title", "")),
                        "artists": ", ".join(self._clean(a["name"]) for a in s.get("artists", [])),
                        "duration": s.get("duration_seconds", 0)
                    }, filter(lambda s: s.get("videoId"), playlist.get("tracks", []))))
			    })

                # break if a new search has been started
                if self.search_query != query:
                    break
                self.view.after(0, lambda: self._update_search_results(query, playlists))
            
        except Exception as e:
            error_message = f"Search failed: {str(e)}"
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

    def _save_playlist(self):
        if not self.current_playlist:
            return

        # Clean the playlist title
        clean_title = self._clean_filename(self.current_playlist['title'])
        default_name = f"{clean_title}.json"
        default_path = Path(self.config.playlists_dir) / default_name

        if default_path.exists():
            if not self.view.ask_yes_no("Overwrite File", f"The file '{default_name}' already exists. Do you want to overwrite it?"):
                return
            path = str(default_path)
        else:
            path = self.view.ask_save_filename(
                initialdir=self.config.playlists_dir,
                initialfile=default_name,
                filetypes=(("JSON files", "*.json"), ("All files", "*.*"))
            )
            if not path:
                return

        try:
            with open(path, 'w') as f:
                json.dump(self.current_playlist['songs'], f, indent=2)

            self.last_saved_path = path
            self.view.update_playlist_button_states(True, True)
            self.view.update_status(f"Playlist saved to {path}")
        except Exception as e:
            self.view.update_status(f"Save failed: {str(e)}", is_error=True)
            
    def _load_playlist(self):
        if not self.last_saved_path:
            return
            
        self.view.song_list_var.set(self.last_saved_path)
        self.view.update_status(f"Loaded playlist {self.last_saved_path}")

