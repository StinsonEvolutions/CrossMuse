from dataclasses import dataclass

@dataclass
class AudioConfig():
    """Audio system parameters"""
    song_list: str = ""              # Output directory for audio files
    output_dir: str = ""             # Output directory for audio files
    sample_rate: int = 96000         # Audio sample rate in Hz
    channels: int = 2                # Number of audio channels
    block_size: int = 4096           # Samples per buffer write
    latency: str = "high"            # Latency mode for audio stream
    buffer_seconds: int = 60         # Enlarged to allow more pre-buffering
    prefill_time: int = 6            # Seconds of audio to buffer before starting playback
    prebuffer_timeout: int = 25      # Max time (seconds) to wait for initial buffer fill
    buffer_backoff: float = 0.05     # Seconds to wait when buffer full
    clip_length: int = 30            # Playback duration in seconds
    fade_duration: int = 4           # Crossfade duration in seconds
    volume_adjustment: float = -3.0  # Apply gain reduction in dB to avoid clipping
    limiter_threshold: float = 0.97  # Amplitude threshold for limiter