"""Main module for Parallel Clip Composer audio streaming application."""
from dataclasses import asdict
import json
import logging
import os
import tkinter as tk
import multiprocessing as mp

from cm_gui import SettingsDialog
from cm_loader import SongLoader
from cm_player import AudioPlayer
from cm_settings import AudioConfig
from cm_logging import setup_logger

# Configure Logging
logger = setup_logger(__name__, level=logging.DEBUG)

def main() -> None:
    """Main application entry point with GUI integration."""
    root = tk.Tk()
    root.withdraw()
    
    # Show configuration dialog
    config = SettingsDialog(root).save_settings()
    if config is None:
        logger.info("GUI closed, exiting...")
        return
    
    # Grab and use config values saved from the GUI
    logger.debug("Starting with config: " + json.dumps(asdict(config), indent=4));
    
    # Load song list and initialize processing
    with open(config.song_list) as f:
        songs = json.load(f)
    
    config.output_dir = os.path.join(os.getcwd(), "Audio Files")
    os.makedirs(config.output_dir, exist_ok=True)
    processed_clips_queue = mp.Queue()
    
    # Start loader process
    loader_process = mp.Process(
        target=run_song_loader,
        args=(songs, config, processed_clips_queue)
    )
    loader_process.start()
    
    # Start audio playback
    try:
        player = AudioPlayer(config, processed_clips_queue)
        logger.info("Starting playback...")
        player.start()
    except KeyboardInterrupt:
        logger.info("Playback stopped by user")
    finally:
        logger.info("Shutting down...")
        player.stop()
        loader_process.join(timeout=5)
        if loader_process.is_alive():
            logger.warning("Forcibly terminating loader process")
            loader_process.terminate()
        logger.info("Application shutdown complete")

def run_song_loader(songs: list, config: AudioConfig, queue: mp.Queue) -> None:
    """Orchestrates song loading in a dedicated process."""
    loader = SongLoader(config, queue)
    loader.add_songs(songs)
    loader.start_processing()

if __name__ == "__main__":
    main()