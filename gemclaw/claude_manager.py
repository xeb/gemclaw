"""Manage Claude Code installation, version checking, and updates."""

import subprocess
import shutil
import logging
import re
from pathlib import Path


class ClaudeManager:
    """Handle Claude Code installation, detection, and updates."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.claude_path: str | None = None

    def ensure_installed(self) -> str:
        """Ensure Claude Code is installed, install if needed.

        Returns: Path to claude executable
        Raises: RuntimeError if installation fails
        """
        # Check if already installed
        path = shutil.which("claude")
        if path:
            self.claude_path = path
            version = self._get_version()
            self.logger.info(f"Found Claude Code v{version} at {path}")
            return path

        # Not installed, need to install
        self.logger.info("Claude Code not found, installing via uv tool install...")
        try:
            subprocess.run(
                ["uv", "tool", "install", "anthropics-claude-code"],
                check=True,
                capture_output=True,
                timeout=300,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Failed to install Claude Code: {e.stderr.decode() if e.stderr else str(e)}"
            )
        except FileNotFoundError:
            raise RuntimeError(
                "uv not found. Please install uv first: "
                "https://docs.astral.sh/uv/getting-started/installation/"
            )

        # Verify installation
        path = shutil.which("claude")
        if not path:
            raise RuntimeError("Claude Code installation succeeded but binary not found in PATH")

        self.claude_path = path
        version = self._get_version()
        self.logger.info(f"Claude Code installed successfully (v{version}) at {path}")
        return path

    def check_updates(self) -> None:
        """Check for Claude Code updates and offer to install."""
        if not self.claude_path:
            self.logger.debug("Claude manager not initialized, skipping update check")
            return

        try:
            current_version = self._get_version()
            self.logger.debug(f"Current Claude Code version: {current_version}")
            # Note: For now, we'll just log the current version
            # Full update check would require checking anthropics-claude-code release
            # This is a placeholder for future enhancement
        except Exception as e:
            self.logger.debug(f"Could not check for updates: {e}")

    def _get_version(self) -> str:
        """Get Claude Code version."""
        try:
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return "unknown"

            output = result.stdout.strip()
            # Expected format: "Claude Code v0.7.2" or similar
            match = re.search(r"v?(\d+\.\d+\.\d+)", output)
            if match:
                return match.group(1)
            return output
        except Exception as e:
            self.logger.debug(f"Error getting Claude Code version: {e}")
            return "unknown"

    def get_claude_path(self) -> str:
        """Get path to claude executable."""
        if not self.claude_path:
            self.ensure_installed()
        return self.claude_path
