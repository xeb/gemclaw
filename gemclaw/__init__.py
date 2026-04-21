"""GemProxy: Gemini API proxy for Claude Code."""

__version__ = "0.1.0"
__author__ = "GemProxy"

# Constants
GEMINI_MODEL = "gemini-3.1-pro-preview"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_PORT = 8082
PORT_RANGE_START = 49000
PORT_RANGE_END = 65000
TIMEOUT_SECONDS = 90
MAX_RETRIES = 2
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
