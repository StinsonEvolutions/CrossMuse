"""GUI module for Parallel Clip Composer configuration."""
import tkinter as tk
from tkinter import ttk, filedialog
from typing import Dict, Any

from emoji import config
from openai import audio
from cm_settings import AudioConfig

class SettingsDialog(tk.Toplevel):
    """Main configuration window with basic and advanced settings."""
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Parallel Clip Composer - Configuration")
        self.geometry("600x400")
        self.resizable(False, False)
        
        self.settings = {}
        self._create_widgets()
        self._setup_validation()
        self._layout_interface()
        
        self.grab_set()  # Make dialog modal
        self.wait_window()  # Pause main thread until closed

    def _create_widgets(self):
        """Initialize all UI components."""
        # Basic Settings
        self.file_var = tk.StringVar()
        self.clip_var = tk.IntVar(value=30)
        self.fade_var = tk.IntVar(value=4)
        
        # Advanced Settings
        self.sample_rate_var = tk.StringVar(value="96000")
        self.buffer_var = tk.IntVar(value=60)
        self.prefill_var = tk.IntVar(value=6)
        self.latency_var = tk.StringVar(value="high")
        
        # Widgets
        self.notebook = ttk.Notebook(self)
        self.basic_frame = ttk.Frame(self.notebook)
        self.advanced_frame = ttk.Frame(self.notebook)
        
        # Basic Settings Widgets
        self.file_btn = ttk.Button(
            self.basic_frame, text="Select Song List", 
            command=self._select_file
        )
        self.file_lbl = ttk.Label(self.basic_frame, text="No file selected")
        self.clip_entry = ttk.Entry(
            self.basic_frame, textvariable=self.clip_var, width=10
        )
        self.fade_entry = ttk.Entry(
            self.basic_frame, textvariable=self.fade_var, width=10
        )
        
        # Advanced Settings Widgets
        self.sample_rate_cb = ttk.Combobox(
            self.advanced_frame, textvariable=self.sample_rate_var,
            values=["44100", "48000", "96000", "192000"], state="readonly"
        )
        self.buffer_entry = ttk.Entry(
            self.advanced_frame, textvariable=self.buffer_var, width=10
        )
        self.prefill_entry = ttk.Entry(
            self.advanced_frame, textvariable=self.prefill_var, width=10
        )
        self.latency_cb = ttk.Combobox(
            self.advanced_frame, textvariable=self.latency_var,
            values=["low", "medium", "high"], state="readonly"
        )
        
        # Control Buttons
        self.start_btn = ttk.Button(
            self, text="Start Playback", command=self._validate_and_start
        )
        self.cancel_btn = ttk.Button(
            self, text="Cancel", command=self.destroy
        )

    def _setup_validation(self):
        """Configure input validation rules."""
        val_int = (self.register(self._validate_int), "%P")
        
        for entry in (self.clip_entry, self.fade_entry, 
                     self.buffer_entry, self.prefill_entry):
            entry.configure(validate="key", validatecommand=val_int)
            
        self.clip_var.trace_add("write", self._adjust_fade_limit)
        self.fade_var.trace_add("write", self._adjust_fade_limit)
        self.buffer_var.trace_add("write", self._adjust_buffer_min)

    def _layout_interface(self):
        """Arrange UI components."""
        self.notebook.add(self.basic_frame, text="Basic Settings")
        self.notebook.add(self.advanced_frame, text="Advanced Settings")
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Basic Settings Layout
        ttk.Label(self.basic_frame, text="Song List (JSON):").grid(
            row=0, column=0, sticky=tk.W, padx=5, pady=5
        )
        self.file_btn.grid(row=0, column=1, padx=5, pady=5)
        self.file_lbl.grid(row=0, column=2, padx=5, pady=5)
        
        ttk.Label(self.basic_frame, text="Clip Length (seconds):").grid(
            row=1, column=0, sticky=tk.W, padx=5, pady=5
        )
        self.clip_entry.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.basic_frame, text="Crossfade Duration (seconds):").grid(
            row=2, column=0, sticky=tk.W, padx=5, pady=5
        )
        self.fade_entry.grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)
        
        # Advanced Settings Layout
        ttk.Label(self.advanced_frame, text="Sample Rate:").grid(
            row=0, column=0, sticky=tk.W, padx=5, pady=5
        )
        self.sample_rate_cb.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.advanced_frame, text="Buffer Size (seconds):").grid(
            row=1, column=0, sticky=tk.W, padx=5, pady=5
        )
        self.buffer_entry.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.advanced_frame, text="Prefill Buffer (seconds):").grid(
            row=2, column=0, sticky=tk.W, padx=5, pady=5
        )
        self.prefill_entry.grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)
        
        ttk.Label(self.advanced_frame, text="Latency Mode:").grid(
            row=3, column=0, sticky=tk.W, padx=5, pady=5
        )
        self.latency_cb.grid(row=3, column=1, sticky=tk.W, padx=5, pady=5)
        
        # Control Buttons Layout
        btn_frame = ttk.Frame(self)
        self.start_btn.pack(side=tk.RIGHT, padx=5, pady=5)
        self.cancel_btn.pack(side=tk.RIGHT, padx=5, pady=5)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

    def _select_file(self):
        """Handle JSON file selection."""
        file_path = filedialog.askopenfilename(
            title="Select Song List (JSON)",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*"))
        )
        if file_path:
            self.file_var.set(file_path)
            self.file_lbl.configure(text=file_path.split("/")[-1])

    def _validate_int(self, value: str) -> bool:
        """Validate integer input fields."""
        if value.strip() == "": return True
        try: 
            int(value)
            return True
        except ValueError: 
            return False

    def _adjust_fade_limit(self, *args):
        """Ensure fade duration doesn't exceed clip length."""
        try:
            clip = self.clip_var.get()
            fade = self.fade_var.get()
        except Exception:
            clip = 0
            fade = 0
        
        if clip > 0 and fade > clip / 2:
            self.fade_var.set(clip / 2)
            
    def _adjust_buffer_min(self, *args):
        """Ensure buffer size meets minimum requirement."""
        if self.buffer_var.get() < 10:
            self.buffer_var.set(10)

    def _validate_and_start(self):
        """Final validation before closing dialog."""
        if not self.file_var.get():
            tk.messagebox.showerror("Error", "Please select a song list file")
            return
            
        self.settings = {
            "song_list": self.file_var.get(),
            "clip_length": self.clip_var.get(),
            "fade_duration": self.fade_var.get(),
            "sample_rate": int(self.sample_rate_var.get()),
            "buffer_seconds": self.buffer_var.get(),
            "prefill_seconds": self.prefill_var.get(),
            "latency": self.latency_var.get()
        }
        self.destroy()

    def save_settings(self) -> AudioConfig:
        """Apply validated settings to config singleton."""
        try:
            config = AudioConfig()
            config.song_list = self.settings["song_list"]
            #self.config.output_dir = self.settings["output_dir"]
            config.sample_rate = self.settings["sample_rate"]
            config.buffer_seconds = self.settings["buffer_seconds"]
            config.prefill_time = self.settings["prefill_seconds"]
            config.latency = self.settings["latency"]
            config.clip_length = self.settings["clip_length"]
            config.fade_duration = self.settings["fade_duration"]
            return config
        except Exception as e:
            #User cancelled
            return None;
