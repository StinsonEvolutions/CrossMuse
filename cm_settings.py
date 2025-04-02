"""Configuration settings module for CrossMuse application."""
from dataclasses import dataclass, asdict
from typing import Dict, Any
from pathlib import Path
import platformdirs
import os
import sys

@dataclass
class AudioConfig:
    CONFIG_VERSION = 3  # New version for these changes
	
    playlist_songs: int = 20  # New config value
    search_matches: int = 5    # New config value
    search_delay: float = 0.5  # Seconds between keystroke and search
    
    version: int = CONFIG_VERSION
    pause_fade: float = 0.2
    playlists_dir: str = str(Path(platformdirs.user_data_dir("CrossMuse", "Stinson Evolutions")) / "Playlists")
    audio_dir: str = str(Path.home() / "Music" / "CrossMuse Audio Files")
    recent_playlist: str = ""
    sample_rate: int = 96000
    channels: int = 2
    block_size: int = 4096
    latency: str = "high"
    buffer_seconds: int = 60
    prefill_time: int = 12
    buffer_backoff: float = 0.05
    clip_length: float = 30.0
    fade_duration: float = 4.0
    volume_adjustment: float = -3.0
    limiter_threshold: float = 0.97

    def __post_init__(self):
        """Validate configuration values after initialization."""
        self._validate_config()

    def _validate_config(self):
        """Ensure configuration values are within acceptable ranges."""
        if self.sample_rate not in [44100, 48000, 96000, 192000]:
            raise ValueError("Sample rate must be 44100, 48000, 96000, or 192000")
        if self.channels not in [1, 2]:
            raise ValueError("Channels must be 1 or 2")
        if self.latency not in ["low", "medium", "high"]:
            raise ValueError("Latency must be 'low', 'medium', or 'high'")
        if self.buffer_seconds < 10:
            raise ValueError("Buffer seconds must be at least 10")
        if self.clip_length < 0:
            raise ValueError("Clip length must be non-negative")
        if self.fade_duration < 0 or (self.clip_length > 0 and self.fade_duration > self.clip_length / 2):
            raise ValueError("Fade duration must be non-negative and not exceed half the clip length")
        if self.limiter_threshold <= 0 or self.limiter_threshold > 1:
            raise ValueError("Limiter threshold must be between 0 and 1")
        if not Path(self.playlists_dir).is_dir():
            if not self.playlists_dir == "":
                os.makedirs(self.playlists_dir, exist_ok=True)
        if not Path(self.audio_dir).parent.is_dir():
            if not self.audio_dir == "":
                os.makedirs(self.playlists_dir, exist_ok=True)
            

    def to_dict(self) -> Dict[str, Any]:
        """Convert the config to a dictionary for easy serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> 'AudioConfig':
        version = config_dict.get('version', 1)
    
        # Migration path for v1 -> v2
        if version == 1:
            config_dict['pause_fade'] = config_dict.get('pause_fade', 0.5)  # Use existing value if present
            config_dict['version'] = cls.CONFIG_VERSION
            config_dict['playlists_dir'] = config_dict.get('playlists_dir', cls.playlists_dir)
            config_dict['recent_playlist'] = config_dict.pop('song_list', config_dict.get('recent_playlist', ''))  # Handle rename
            config_dict.pop('prebuffer_timeout', None)  # Remove if present, do nothing if not
        
        # Migration from v2 to v3
        if version == 2:
            config_dict['playlist_songs'] = 20
            config_dict['search_matches'] = 5
            config_dict['search_delay'] = 0.5
            config_dict['version'] = cls.CONFIG_VERSION

        # Set values to defaults for specified keys, if loaded value is empty
        if config_dict.get('audio_dir', '') == '':
            config_dict['audio_dir'] = cls.audio_dir
        if config_dict.get('playlists_dir', '') == '':
            config_dict['playlists_dir'] = cls.playlists_dir
    
        return cls(**config_dict)

    def update(self, **kwargs):
        """Update configuration with new values."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                raise AttributeError(f"AudioConfig has no attribute '{key}'")
        self._validate_config()

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
 
    return Path(os.path.join(base_path, relative_path))