"""View: GUI module for CrossMuse application."""
import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Callable, Dict, Any

from cm_logging import setup_logger
from cm_settings import AudioConfig, resource_path

logger = setup_logger()

class MainDialog(tk.Toplevel):
    """Main configuration window with basic and advanced settings."""
    def __init__(self, parent, config: AudioConfig):
        super().__init__(parent)
        self.root = parent
        self.config = config
        self.tk.call('wm', 'iconphoto', self._w, tk.PhotoImage(file=resource_path('logo.png')))
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
        self.shuffle_var = tk.BooleanVar(value=config.shuffle)
        self.repeat_var = tk.BooleanVar(value=config.repeat)

        self._create_widgets()
        self._setup_validation()
        self._layout_interface()

    def set_command(self, widget: tk.Widget, command: Callable):
        """Access method to set command for a specified button."""
        widget.config(command=command)

    def update_playback_button_states(self, playback_active: bool, paused: bool):
        """Update "Play" tab button states based on playback status."""
        has_file = bool(self.song_list_var.get())
        self.start_btn.config(state=tk.NORMAL if has_file and not playback_active else tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL if playback_active else tk.DISABLED)
        self.pause_btn.config(text="Resume" if paused else "Pause")
        self.stop_btn.config(state=tk.NORMAL if playback_active else tk.DISABLED)
        self.set_settings_enabled(False if playback_active else True)

    def update_playlist_button_states(self, playlist_selected: bool, playlist_saved: bool):
        """Update "Generate" tab button states based on playback status."""
        self.save_btn.config(state=tk.NORMAL if playlist_selected and not playlist_saved else tk.DISABLED)
        self.load_btn.config(state=tk.NORMAL if playlist_selected and playlist_saved else tk.DISABLED)

    def set_settings_enabled(self, enabled: bool):
        """Enable/disable configuration controls."""
        state = tk.NORMAL if enabled else tk.DISABLED
        for frame in [self.basic_frame, self.advanced_frame]:
            for child in frame.winfo_children():
                if isinstance(child, (ttk.Entry, ttk.Combobox, ttk.Button, ttk.Checkbutton)):
                    child.config(state=state)
            
    def display_playlist_songs(self, songs: list):
        # Clear existing songs
        for widget in self.songs_subframe.winfo_children():
            widget.destroy()
            
        # Add new songs
        for idx, song in enumerate(songs, 1):
            lbl = ttk.Label(
                self.songs_subframe,
                text=f"{idx}. {song['title']} ({song['artists']})"
            )
            lbl.pack(anchor=tk.W)
            
        self.songs_subframe.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def show_error(self, title: str, message: str):
        """Display an error message to the user."""
        messagebox.showerror(title, message)

    def ask_save_filename(self, **options) -> str:
        """Wrapper for filedialog.asksavefilename."""
        return filedialog.asksaveasfilename(**options)

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
            "latency": self.latency_var.get(),
            "shuffle": self.shuffle_var.get(),
            "repeat": self.repeat_var.get()
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
        self.shuffle_var.set(config.get("shuffle", False))
        self.repeat_var.set(config.get("repeat", False))

        if self.playlists_dir_var.get():
            self.playlists_dir_lbl.config(text=self.playlists_dir_var.get())
        if self.song_list_var.get():
            self.song_list_lbl.config(text=self.song_list_var.get())
        if self.audio_dir_var.get():
            self.audio_dir_lbl.config(text=self.audio_dir_var.get())

    def clear_results(self):
        self.results_list.delete(0, tk.END)

    def set_results(self, playlists: list):
        self.clear_results()
        ready_count = 0
        for i in range(len(playlists)):
            pl = playlists[i]
            if 'songs' in pl:
                ready_count += 1
                self.results_list.insert(tk.END, f"{pl['title']} ({pl['count']} songs)")
                self.results_list.itemconfig(i, {'fg': 'black'})
            else:
                self.results_list.insert(tk.END, f"{pl['title']} ({pl['count']} songs)")
                self.results_list.itemconfig(i, {'fg': 'grey'})
        return ready_count

    def clear_selection(self):
        """Clear the selection in the results list."""
        self.results_list.selection_clear(0, tk.END)

    def select_result_item(self, index: int):
        """Select an item in the results_list to simulate a mouse click."""
        try:
            self.results_list.selection_clear(0, tk.END)  # Clear any previous selection
            self.results_list.selection_set(index)  # Select the item at the specified index
            # self.results_list.see(index)  # Ensure the selected item is visible
            self.results_list.event_generate("<<ListboxSelect>>")  # Generate the selection event
        except Exception as e:
            logger.error(f"Error selecting result item: {str(e)}")
            self.show_error("Selection Error", f"Error selecting result item: {str(e)}")

    def ask_yes_no(self, title, message):
        return messagebox.askyesno(title, message)

    def _create_widgets(self):
        """Initialize UI components."""
        self.notebook = ttk.Notebook(self)
        self.search_frame = ttk.Frame(self.notebook)
        self.basic_frame = ttk.Frame(self.notebook)
        self.advanced_frame = ttk.Frame(self.notebook)
        
        # Search tab components
        self._create_search_components()
        
        # Search tab components
        self._create_basic_settings_components()
        
        # Search tab components
        self._create_advanced_settings_components()
        
        # Status and playback area (bottom of window)
        self._create_playback_components()

    def _create_search_components(self):
        # Search input area
        self.search_label = ttk.Label(self.search_frame, text="Playlist search", font=('Helvetica', 10, 'bold'))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(self.search_frame, textvariable=self.search_var, font=('Helvetica', 10))

        # Results ListBox with scrollbar
        self.results_frame = ttk.Frame(self.search_frame)
        self.results_list = tk.Listbox(self.results_frame, font=('Helvetica', 9), selectbackground='#E1E1E1')
        self.results_scrollbar = ttk.Scrollbar(self.results_frame, orient="vertical", command=self.results_list.yview)
        self.results_list.configure(yscrollcommand=self.results_scrollbar.set)

        # Songs preview area (with scrollbars)
        self.songs_frame = ttk.Frame(self.search_frame)
        self.canvas = tk.Canvas(self.songs_frame)
        self.canvas.config(width=200)  # Set fixed width for the canvas
        self.songs_scrollbar_y = ttk.Scrollbar(self.songs_frame, orient="vertical", command=self.canvas.yview)
        self.songs_scrollbar_x = ttk.Scrollbar(self.songs_frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.songs_scrollbar_y.set, xscrollcommand=self.songs_scrollbar_x.set)
        self.songs_subframe = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.songs_subframe, anchor="nw")

        # Save & Load buttons
        self.save_btn = ttk.Button(self.search_frame, text="Save Playlist", state=tk.DISABLED)
        self.load_btn = ttk.Button(self.search_frame, text="Load Playlist", state=tk.DISABLED)

    def _create_basic_settings_components(self):
        # Basic Settings
        self.file_btn = ttk.Button(
            self.basic_frame, text="Select Playlist"
        )
        self.song_list_lbl = ttk.Label(self.basic_frame, text="No file selected")
        self.clip_entry = ttk.Entry(self.basic_frame, textvariable=self.clip_var, width=10)
        self.fade_entry = ttk.Entry(self.basic_frame, textvariable=self.fade_var, width=10)
        self.shuffle_label = ttk.Label(self.basic_frame, text="Shuffle")
        self.shuffle_check = ttk.Checkbutton(self.basic_frame, variable=self.shuffle_var)
        self.repeat_label = ttk.Label(self.basic_frame, text="Repeat")
        self.repeat_check = ttk.Checkbutton(self.basic_frame, variable=self.repeat_var)

    def _create_advanced_settings_components(self):
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

    def _create_playback_components(self):
        self.playback_frame = ttk.Frame(self)
        self.status_var = tk.StringVar()
        self.status_lbl = ttk.Label(self.playback_frame, textvariable=self.status_var, 
                                  foreground="black", cursor="hand2")
        self.start_btn = ttk.Button(self.playback_frame, text="Start")
        self.pause_btn = ttk.Button(self.playback_frame, text="Pause")
        self.stop_btn = ttk.Button(self.playback_frame, text="Stop")

    def update_status(self, message: str, is_error: bool = False):
        self.status_var.set(message)
        self.status_lbl.config(foreground="red" if is_error else "green")

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
        self.notebook.add(self.search_frame, text="Generate")
        self.notebook.add(self.basic_frame, text="Configure")
        self.notebook.add(self.advanced_frame, text="Customize")
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.notebook.select(self.search_frame)  # Make search tab default

        # --- Generate Tab Layout ---
    
        # Top row: Search label and entry
        self.search_label.grid(row=0, column=0, padx=(10, 5), pady=5, sticky=tk.W)
        self.search_entry.grid(row=0, column=1, padx=(0, 10), pady=5, sticky=tk.EW, columnspan=2)

        # Middle row: Two sections (playlist results and songs preview)
        self.results_frame.grid(row=1, column=0, padx=(10, 5), pady=5, sticky=tk.NSEW, columnspan=2)
        self.results_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.results_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.songs_frame.grid(row=1, column=2, padx=(5, 10), pady=5, sticky=tk.NSEW)

        # Use grid layout for the canvas and scrollbars
        self.canvas.grid(row=0, column=0, sticky=tk.NSEW)
        self.songs_scrollbar_y.grid(row=0, column=1, sticky=tk.NS)
        self.songs_scrollbar_x.grid(row=1, column=0, sticky=tk.EW)

        # Allow resizing of the two sections
        self.search_frame.columnconfigure(0, weight=0)  # Search label column
        self.search_frame.columnconfigure(1, weight=2)  # Search entry and playlist results column
        self.search_frame.columnconfigure(2, weight=1)  # Songs preview column
        self.search_frame.rowconfigure(1, weight=1)

        self.songs_frame.columnconfigure(0, weight=1)
        self.songs_frame.columnconfigure(1, weight=0)
        self.songs_frame.rowconfigure(0, weight=1)
        self.songs_frame.rowconfigure(1, weight=0)

        # Bottom row: Save and Load buttons
        self.save_btn.grid(row=2, column=0, padx=10, pady=5, sticky=tk.W)
        self.load_btn.grid(row=2, column=1, padx=10, pady=5, sticky=tk.E, columnspan=2)

        # --- Basic Settings Layout ---

        ttk.Label(self.basic_frame, text="Playlist (JSON):").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.file_btn.grid(row=0, column=1, padx=5, pady=5)
        self.song_list_lbl.grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)
    
        ttk.Label(self.basic_frame, text="Clip Length (seconds):").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.clip_entry.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
    
        ttk.Label(self.basic_frame, text="Crossfade Duration (seconds):").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        self.fade_entry.grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)

        self.shuffle_label.grid(row=3, column=0, sticky=tk.W, padx=5, pady=5)  # Add shuffle label to layout
        self.shuffle_check.grid(row=3, column=1, sticky=tk.W, padx=5, pady=5)  # Add shuffle checkbox to layout

        self.repeat_label.grid(row=4, column=0, sticky=tk.W, padx=5, pady=5)  # Add repeat label to layout
        self.repeat_check.grid(row=4, column=1, sticky=tk.W, padx=5, pady=5)  # Add repeat checkbox to layout

        # --- Advanced Settings Layout ---

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

        # --- Status and Control Buttons (Outside Any Frame) ---

        self.playback_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
        self.stop_btn.pack(side=tk.RIGHT, padx=5)
        self.pause_btn.pack(side=tk.RIGHT, padx=5)
        self.start_btn.pack(side=tk.RIGHT, padx=5)
        self.status_lbl.pack(side=tk.LEFT, fill=tk.X, padx=5)

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
