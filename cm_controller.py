"""Controller module handling application logic and process management."""
import json
import os
import shutil
import multiprocessing as mp
from pathlib import Path
from typing import Dict, Any, Optional

from cm_gui import MainDialog
from cm_settings import AudioConfig
from cm_logging import setup_logger

logger = setup_logger()

class Controller:
    def __init__(self, root):
        self.view = MainDialog(root)
        self.view.protocol("WM_DELETE_WINDOW", self._exit)
        
        self.playback_active = False
        self.paused = False
        self.processed_clips_queue: Optional[mp.Queue] = None
        self.loader_process: Optional[mp.Process] = None
        self.player_process: Optional[mp.Process] = None
        self.command_queue: mp.Queue = mp.Queue()

        # Bind button commands
        self.view.set_command(self.view.start_btn, self._start_playback)
        self.view.set_command(self.view.pause_btn, self._toggle_pause)
        self.view.set_command(self.view.stop_btn, self._stop_playback)
        self.view.set_command(self.view.file_btn, lambda: self._select_song_list(self.view.song_list_var, self.view.song_list_lbl))
        self.view.set_command(self.view.audio_dir_btn, lambda: self._select_audio_dir(self.view.audio_dir_var, self.view.audio_dir_lbl))
        self.view.song_list_var.trace_add("write", lambda *_: self.view.update_button_states(self.playback_active, self.paused))

        config = self._load_config()
        self._update_view_from_config(config)
        self._save_config()
        self.view.update_button_states(self.playback_active, self.paused)

        self.view.mainloop()

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
                self.command_queue = mp.Queue()
                
                self.loader_process = mp.Process(
                    target=self._run_song_loader,
                    args=(songs, config.to_dict(), self.processed_clips_queue)
                )
                self.loader_process.start()
                
                self.player_process = mp.Process(
                    target=self._run_audio_player,
                    args=(config.to_dict(), self.processed_clips_queue, self.command_queue)
                )
                self.player_process.start()
                self.playback_active = True
                self.view.after(100, self._process_player_status)

            except Exception as e:
                logger.error(f"Failed to start playback: {str(e)}")
                self.view.show_error("Playback Error", f"Failed to start playback: {str(e)}")
        
        self.view.update_button_states(self.playback_active, self.paused)

    @staticmethod
    def _run_song_loader(songs: list, config: Dict[str, Any], queue: mp.Queue) -> None:
        """Runs song loading in a dedicated process."""
        from cm_loader import SongLoader
        loader = SongLoader(AudioConfig.from_dict(config), queue)
        loader.add_songs(songs)
        loader.start_processing()

    @staticmethod
    def _run_audio_player(config: Dict[str, Any], in_queue: mp.Queue, cmd_queue: mp.Queue) -> None:
        """Runs audio player in a dedicated process."""
        from cm_player import AudioPlayer
        player = AudioPlayer(AudioConfig.from_dict(config), in_queue)
        player.start(cmd_queue)

    def _process_player_status(self):
        if self.playback_active and not self.player_process.is_alive():
            logger.info("Player process has terminated")
            self._stop_playback()
        elif self.playback_active:
            self.view.after(100, self._process_player_status)

    def _toggle_pause(self):
        """Toggle pause state."""
        if self.playback_active:
            if self.paused:
                self.command_queue.put("RESUME")
            else:
                self.command_queue.put("PAUSE")
            self.paused = not self.paused
            self.view.update_button_states(self.playback_active, self.paused)
            
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
        self.view.update_button_states(self.playback_active, self.paused)
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
            sample_playlists_dir = Path("Sample Playlists")
            
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
        """Get OS-appropriate config file path."""
        return Path.home() / ".crossmuse" / "config.json"

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

    def _default_audio_dir(self) -> str:
        """Get default audio storage directory."""
        return str(Path.home() / "Music" / "CrossMuse Audio Files")
