"""Main entry point for the CrossMuse application."""
import multiprocessing as mp
import tkinter as tk
from cm_controller import Controller
from cm_logging import configure_multiprocessing_logging, setup_logger

logger = setup_logger()

def main():
    """
    Main function to initialize and run the CrossMuse application.
    """
    try:
        # Configure multiprocessing logging
        configure_multiprocessing_logging()

        # Create the root Tkinter window
        root = tk.Tk()
        root.withdraw()  # Hide the root window
        
        # Create and start the controller
        controller = Controller(root)
    
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {str(e)}")
    finally:
        logger.info("Application shutting down.")

if __name__ == "__main__":
    # Ensure proper multiprocessing behavior, especially on Windows
    mp.freeze_support()
    main()
