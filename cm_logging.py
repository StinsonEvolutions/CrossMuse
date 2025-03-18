import logging
import os

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)  # Ensure the log directory exists
instances = {}  # Shared instances for all loggers (by name)

def setup_logger(log_name, level=logging.INFO):
    """
    Creates and configures a logger with a file handler and a shared console handler.
    
    Parameters:
        name (str): The name of the module (used for logger and filename).
        level (int): The logging level (e.g., logging.DEBUG, logging.INFO).
    
    Returns:
        logging.Logger: The configured logger.
    """

    # Check if this logger instance already exists
    if instances.get(log_name):
        return instances[log_name]

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    name = log_name
    log_file = f"{log_name}.log"

    # File Handler (Writes to a separate file for each module)
    file_handler = logging.FileHandler(os.path.join(LOG_DIR, log_file))
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    # Console Handler (Shared across all loggers, prints to stdout)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG)  # Print all messages to console

    # Create a logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # Capture all logs, handlers will filter as needed
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Save a reference in case we want to reuse the same logger across modules
    instances[log_name] = logger

    return logger
