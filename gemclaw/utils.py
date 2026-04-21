"""Utility functions for logging, port discovery, and helpers."""

import logging
import sys
import socket
from pathlib import Path
from datetime import datetime
from typing import Optional
from colorama import Fore, Style, init as colorama_init

from gemclaw import PORT_RANGE_START, PORT_RANGE_END

colorama_init(autoreset=True)


def setup_logging(
    log_file: Optional[str] = None,
    level: str = "INFO",
    verbose: bool = False,
    console_output: bool = True,
) -> tuple[logging.Logger, str]:
    """Set up logging to file and console.

    Returns: (logger, log_file_path)
    """
    if verbose:
        level = "DEBUG"

    # Create log directory
    if log_file is None:
        log_dir = Path.home() / ".gemproxy" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = str(log_dir / f"gemproxy-{timestamp}.log")
    else:
        log_file = str(Path(log_file).expanduser())
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    # Create logger
    logger = logging.getLogger("gemproxy")
    logger.setLevel(level)
    logger.handlers.clear()

    # File handler (always DEBUG)
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh_formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fh_formatter)
    logger.addHandler(fh)

    # Console handler (only if console_output is True)
    if console_output:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(getattr(logging, level))
        ch_formatter = logging.Formatter(
            f"{Fore.CYAN}[%(asctime)s]{Style.RESET_ALL} %(levelname)-8s %(message)s",
            datefmt="%H:%M:%S",
        )
        ch.setFormatter(ch_formatter)
        logger.addHandler(ch)

    if console_output:
        logger.info(f"Logging to {log_file}")
    return logger, log_file


def find_available_port(
    preferred_port: Optional[int] = None,
    logger: Optional[logging.Logger] = None,
) -> int:
    """Find an available port.

    If preferred_port is given, try that first.
    Otherwise, search in the range PORT_RANGE_START to PORT_RANGE_END.
    """
    if preferred_port:
        if is_port_available(preferred_port):
            if logger:
                logger.info(f"Using port {preferred_port} (specified)")
            return preferred_port
        else:
            if logger:
                logger.warning(f"Port {preferred_port} is in use, searching for alternative...")

    if logger:
        logger.debug(f"Searching for available port in range {PORT_RANGE_START}-{PORT_RANGE_END}")

    for port in range(PORT_RANGE_START, PORT_RANGE_END):
        if is_port_available(port):
            if logger:
                logger.info(f"Using port {port} (auto-detected)")
            return port

    raise RuntimeError(
        f"No available ports found in range {PORT_RANGE_START}-{PORT_RANGE_END}. "
        "Try specifying a port with --port"
    )


def is_port_available(port: int) -> bool:
    """Check if a port is available."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
            return True
    except OSError:
        return False


def log_info(logger: logging.Logger, msg: str, emoji: str = "✅"):
    """Log info with emoji prefix."""
    logger.info(f"{emoji} {msg}")


def log_error(logger: logging.Logger, msg: str, emoji: str = "❌"):
    """Log error with emoji prefix."""
    logger.error(f"{emoji} {msg}")


def log_debug_json(logger: logging.Logger, label: str, data: dict) -> None:
    """Log a dict as formatted JSON for debugging."""
    import json
    logger.debug(f"{label}:\n{json.dumps(data, indent=2)}")
