"""Logging configuration module for CrossMuse application."""
import logging
from threading import Lock
import multiprocessing as mp
from typing import Dict

class LoggerManager:
    """Manages logger instances across the application."""
    _default_log = "crossmuse"
    _instance = None
    _lock = Lock()
    _loggers: Dict[tuple, logging.Logger] = {}

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(LoggerManager, cls).__new__(cls)
        return cls._instance

    @classmethod
    def get_logger(cls, log_name: str, level: int = logging.INFO) -> logging.Logger:
        """
        Retrieves or creates a logger with the specified name and level.
        
        Args:
            log_name (str): The name of the logger.
            level (int): The logging level (e.g., logging.DEBUG, logging.INFO).
        
        Returns:
            logging.Logger: The configured logger.
        """
        with cls._lock:
            key = (log_name, level)
            if key not in cls._loggers:
                logger = cls._create_logger(log_name, level)
                cls._loggers[key] = logger
            return cls._loggers[key]

    @staticmethod
    def _create_logger(log_name: str, level: int) -> logging.Logger:
        """
        Creates and configures a new logger.
        
        Args:
            log_name (str): The name of the logger.
            level (int): The logging level.
        
        Returns:
            logging.Logger: The newly created and configured logger.
        """
        formatter = logging.Formatter('%(asctime)s - %(processName)s - %(name)s - %(levelname)s - %(message)s')
        log_file = f"{log_name}.log"

        # File Handler (Writes to a separate file for each module)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)

        # Console Handler (Shared across all loggers, prints to stdout)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)

        # Create a logger
        logger = logging.getLogger(f"{log_name}_{level}")
        logger.setLevel(logging.DEBUG)  # Capture all logs, handlers will filter as needed
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        return logger

def setup_logger(log_name: str = LoggerManager._default_log, level: int = logging.INFO) -> logging.Logger:
    """
    Creates and configures a logger with a file handler and a shared console handler.
    
    Args:
        log_name (str): The name of the module (used for logger and filename).
        level (int): The logging level (e.g., logging.DEBUG, logging.INFO).
    
    Returns:
        logging.Logger: The configured logger.
    """
    return LoggerManager.get_logger(log_name, level)

def configure_multiprocessing_logging():
    """
    Configure logging to work correctly with multiprocessing.
    Call this function in the main process before starting any child processes.
    """
    logger = setup_logger()
    
    # Ensure child processes don't add duplicate handlers
    logger.propagate = False
    
    # Configure the multiprocessing logger
    mp_logger = mp.get_logger()
    mp_logger.setLevel(logging.INFO)
    mp_logger.addHandler(logging.StreamHandler())
    
    # Patch the multiprocessing.Process class to use our logging configuration
    original_process_init = mp.Process.__init__
    def patched_process_init(self, *args, **kwargs):
        original_process_init(self, *args, **kwargs)
        self._logger = setup_logger()
    mp.Process.__init__ = patched_process_init

