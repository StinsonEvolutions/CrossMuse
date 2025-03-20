"""View: GUI module for CrossMuse application."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Callable, Dict, Any
from pathlib import Path

from cm_logging import setup_logger

logger = setup_logger()

class MainDialog(tk.Toplevel):
    """Main configuration window with basic and advanced settings."""
    def __init__(self, parent):
        super().__init__(parent)
        self.root = parent
        self.tk.call('wm', 'iconphoto', self._w, tk.PhotoImage(file='logo.png'))
        self.title("CrossMuse")
        self.geometry("800x500")
        
        # Configuration variables
        self.song_list_var = tk.StringVar()
        self.clip_var = tk.IntVar(value=30)
        self.fade_var = tk.DoubleVar(value=4.0)
        self.playlists_dir_var = tk.StringVar()
        self.audio_dir_var = tk.StringVar()
        self.sample_rate_var = tk.StringVar(value="96000")
        self.buffer_var = tk.IntVar(value=60)
        self.prefill_var = tk.IntVar(value=6)
        self.latency_var = tk.StringVar(value="high")

        self._create_widgets()
        self._setup_validation()
        self._layout_interface()

    def set_command(self, widget: tk.Widget, command: Callable):
        """Access method to set command for a specified button."""
        widget.config(command=command)

    def update_button_states(self, playback_active: bool, paused: bool):
        """Update control button states based on playback status."""
        has_file = bool(self.song_list_var.get())
        self.start_btn.config(state=tk.NORMAL if has_file and not playback_active else tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL if playback_active else tk.DISABLED)
        self.pause_btn.config(text="Resume" if paused else "Pause")
        self.stop_btn.config(state=tk.NORMAL if playback_active else tk.DISABLED)
        self.set_settings_enabled(False if playback_active else True)

    def set_settings_enabled(self, enabled: bool):
        """Enable/disable configuration controls."""
        state = tk.NORMAL if enabled else tk.DISABLED
        for frame in [self.basic_frame, self.advanced_frame]:
            for child in frame.winfo_children():
                if isinstance(child, (ttk.Entry, ttk.Combobox, ttk.Button)):
                    child.config(state=state)

    def _create_widgets(self):
        """Initialize UI components."""
        self.notebook = ttk.Notebook(self)
        self.basic_frame = ttk.Frame(self.notebook)
        self.advanced_frame = ttk.Frame(self.notebook)

        # Basic Settings
        self.file_btn = ttk.Button(
            self.basic_frame, text="Select Playlist"
        )
        self.song_list_lbl = ttk.Label(self.basic_frame, text="No file selected")
        self.clip_entry = ttk.Entry(self.basic_frame, textvariable=self.clip_var, width=10)
        self.fade_entry = ttk.Entry(self.basic_frame, textvariable=self.fade_var, width=10)

        # Advanced Settings
        self.playlists_dir_btn = ttk.Button(
            self.advanced_frame, text="Browse..."
        )
        self.playlists_dir_lbl = ttk.Label(self.advanced_frame, textvariable=self.playlists_dir_var)
        self.audio_dir_btn = ttk.Button(
            self.advanced_frame, text="Browse..."
        )
        self.audio_dir_lbl = ttk.Label(self.advanced_frame, textvariable=self.audio_dir_var)
        self.sample_rate_cb = ttk.Combobox(
            self.advanced_frame, textvariable=self.sample_rate_var,
            values=["44100", "48000", "96000", "192000"], state="readonly"
        )
        self.buffer_entry = ttk.Entry(self.advanced_frame, textvariable=self.buffer_var, width=10)
        self.prefill_entry = ttk.Entry(self.advanced_frame, textvariable=self.prefill_var, width=10)
        self.latency_cb = ttk.Combobox(
            self.advanced_frame, textvariable=self.latency_var,
            values=["low", "medium", "high"], state="readonly"
        )

        # Control Buttons
        self.btn_frame = ttk.Frame(self)
        self.start_btn = ttk.Button(self.btn_frame, text="Start")
        self.pause_btn = ttk.Button(self.btn_frame, text="Pause")
        self.stop_btn = ttk.Button(self.btn_frame, text="Stop")

    def _setup_validation(self):
        """Configure input validation rules."""
        val_int = (self.register(self._validate_int), '%P')
        val_float = (self.register(self._validate_float), "%P")
        self.clip_entry.configure(validate="key", validatecommand=val_int)
        self.fade_entry.configure(validate="key", validatecommand=val_float)
        
        self.clip_var.trace_add("write", self._adjust_fade_limit)
        self.fade_var.trace_add("write", self._adjust_fade_limit)
        self.buffer_var.trace_add("write", self._adjust_buffer_min)

    def _layout_interface(self):
        """Arrange UI components."""
        self.notebook.add(self.basic_frame, text="Basic Settings")
        self.notebook.add(self.advanced_frame, text="Advanced Settings")
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Basic Settings Layout
        ttk.Label(self.basic_frame, text="Playlist (JSON):").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.file_btn.grid(row=0, column=1, padx=5, pady=5)
        self.song_list_lbl.grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)
        
        ttk.Label(self.basic_frame, text="Clip Length (seconds):").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.clip_entry.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.basic_frame, text="Crossfade Duration (seconds):").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        self.fade_entry.grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)

        # Advanced Settings Layout
        ttk.Label(self.advanced_frame, text="Playlists Directory:").grid(row=5, column=0, sticky=tk.W, padx=5, pady=5)
        self.playlists_dir_btn.grid(row=5, column=1, padx=5, pady=5)
        self.playlists_dir_lbl.grid(row=5, column=2, padx=5, pady=5, sticky=tk.W)

        ttk.Label(self.advanced_frame, text="Songs Directory:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.audio_dir_btn.grid(row=0, column=1, padx=5, pady=5)
        self.audio_dir_lbl.grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)
        
        ttk.Label(self.advanced_frame, text="Sample Rate:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.sample_rate_cb.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.advanced_frame, text="Buffer Size (seconds):").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        self.buffer_entry.grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.advanced_frame, text="Prefill Buffer (seconds):").grid(row=3, column=0, sticky=tk.W, padx=5, pady=5)
        self.prefill_entry.grid(row=3, column=1, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.advanced_frame, text="Latency Mode:").grid(row=4, column=0, sticky=tk.W, padx=5, pady=5)
        self.latency_cb.grid(row=4, column=1, sticky=tk.W, padx=5, pady=5)

        # Control Buttons
        self.btn_frame.pack(fill=tk.X, padx=10, pady=10)
        self.stop_btn.pack(side=tk.RIGHT, padx=5)
        self.pause_btn.pack(side=tk.RIGHT, padx=5)
        self.start_btn.pack(side=tk.RIGHT, padx=5)

    def _validate_int(self, value: str) -> bool:
        """Validate int input fields."""
        if value.strip() == "": return True
        try: 
            int(value)
            return True
        except ValueError: 
            return False

    def _validate_float(self, value: str) -> bool:
        """Validate float input fields."""
        if value.strip() == "": return True
        try: 
            float(value)
            return True
        except ValueError: 
            return False

    def _adjust_fade_limit(self, *args):
        """Ensure fade duration doesn't exceed clip length."""
        try:
            clip = self.clip_var.get()
            fade = self.fade_var.get()
        except tk.TclError:
            return
        
        if clip > 0 and fade > clip / 2:
            self.fade_var.set(clip / 2)
            
    def _adjust_buffer_min(self, *args):
        """Ensure buffer size meets minimum requirement."""
        try:
            buffer = self.buffer_var.get()
        except tk.TclError:
            return
        
        if buffer < 10:
            self.buffer_var.set(10)

    def show_error(self, title: str, message: str):
        """Display an error message to the user."""
        messagebox.showerror(title, message)

    def ask_open_filename(self, **options) -> str:
        """Wrapper for filedialog.askopenfilename."""
        return filedialog.askopenfilename(**options)

    def ask_directory(self, **options) -> str:
        """Wrapper for filedialog.askdirectory."""
        return filedialog.askdirectory(**options)

    def get_config(self) -> Dict[str, Any]:
        """Get current configuration from GUI."""
        return {
            "playlists_dir": self.playlists_dir_var.get(),
            "song_list": self.song_list_var.get(),
            "clip_length": self.clip_var.get(),
            "fade_duration": self.fade_var.get(),
            "audio_dir": self.audio_dir_var.get(),
            "sample_rate": int(self.sample_rate_var.get()),
            "buffer_seconds": self.buffer_var.get(),
            "prefill_time": self.prefill_var.get(),
            "latency": self.latency_var.get()
        }

    def set_config(self, config: Dict[str, Any]):
        """Set configuration in GUI."""
        self.song_list_var.set(config.get("song_list", ""))
        self.clip_var.set(config.get("clip_length", 30.0))
        self.fade_var.set(config.get("fade_duration", 4.0))
        self.playlists_dir_var.set(config.get("playlists_dir", ""))
        self.audio_dir_var.set(config.get("audio_dir", ""))
        self.sample_rate_var.set(str(config.get("sample_rate", 96000)))
        self.buffer_var.set(config.get("buffer_seconds", 60))
        self.prefill_var.set(config.get("prefill_time", 6))
        self.latency_var.set(config.get("latency", "high"))

        if self.playlists_dir_var.get():
            self.playlists_dir_lbl.config(text=self.playlists_dir_var.get())
        if self.song_list_var.get():
            self.song_list_lbl.config(text=self.song_list_var.get())
        if self.audio_dir_var.get():
            self.audio_dir_lbl.config(text=self.audio_dir_var.get())
