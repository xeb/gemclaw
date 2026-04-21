"""CLI entry point for GemClaw."""

import os
import sys
import signal
import subprocess
import logging
import click
from typing import Optional
from pathlib import Path
from datetime import datetime

from gemclaw import __version__, GEMINI_MODEL
from gemclaw.utils import setup_logging, find_available_port, log_info, log_error
from gemclaw.claude_manager import ClaudeManager
from gemclaw.proxy import create_app
import uvicorn


class GemClawApp:
    """Main GemClaw application controller."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.proxy_process: Optional[subprocess.Popen] = None
        self.claude_process: Optional[subprocess.Popen] = None
        self.proxy_log_file = None
        self.proxy_log_path: Optional[Path] = None

    def validate_environment(self) -> None:
        """Validate that required environment variables are set."""
        if not os.environ.get("GEMINI_API_KEY"):
            log_error(
                self.logger,
                "GEMINI_API_KEY environment variable not set",
            )
            self.logger.error("Get a key from: https://aistudio.google.com/app/apikey")
            sys.exit(1)
        log_info(self.logger, "GEMINI_API_KEY is set")

    def check_and_install_claude(self) -> str:
        """Ensure Claude Code is installed."""
        log_info(self.logger, "Checking Claude Code installation...")
        claude_manager = ClaudeManager(self.logger)
        try:
            claude_path = claude_manager.ensure_installed()
            return claude_path
        except RuntimeError as e:
            log_error(self.logger, str(e))
            sys.exit(1)

    def start_proxy(self, port: int) -> int:
        """Start the proxy server in a subprocess.

        Returns: PID of proxy process
        """
        log_info(self.logger, f"Starting Gemini proxy on port {port}")

        # Create FastAPI app
        app = create_app(self.logger)

        # Open log file for proxy subprocess
        log_dir = Path.home() / ".gemproxy" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        proxy_log_path = log_dir / f"gemclaw-proxy-{timestamp}.log"
        self.proxy_log_path = proxy_log_path
        self.proxy_log_file = open(proxy_log_path, "w")

        proxy_env = os.environ.copy()
        proxy_env["GEMCLAW_PROXY_LOG"] = str(proxy_log_path)

        self.logger.debug(f"Starting uvicorn with: {sys.executable} -m uvicorn gemclaw.proxy:app")
        self.proxy_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "gemclaw.proxy:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "critical",
            ],
            env=proxy_env,
            stdout=self.proxy_log_file,
            stderr=self.proxy_log_file,
        )

        # Wait for proxy to start
        import time
        time.sleep(2)

        if self.proxy_process.poll() is not None:
            # Process died immediately
            log_error(self.logger, f"Proxy failed to start (exit code: {self.proxy_process.returncode})")
            sys.exit(1)

        log_info(self.logger, f"Proxy listening on http://127.0.0.1:{port}")
        return self.proxy_process.pid

    def run_claude_code(self, port: int, claude_path: str) -> None:
        """Launch Claude Code with proxy configuration."""
        log_info(self.logger, "Launching Claude Code with proxy environment...")

        # Set up environment
        env = os.environ.copy()
        env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
        # Remove ANTHROPIC_API_KEY to avoid auth conflict with cached claude.ai token
        env.pop("ANTHROPIC_API_KEY", None)
        env["ANTHROPIC_MODEL"] = GEMINI_MODEL

        self.logger.debug(f"Claude Code environment:")
        self.logger.debug(f"  ANTHROPIC_BASE_URL={env['ANTHROPIC_BASE_URL']}")
        self.logger.debug(f"  ANTHROPIC_API_KEY=<unset>")
        self.logger.debug(f"  ANTHROPIC_MODEL={env['ANTHROPIC_MODEL']}")

        # Launch Claude Code
        try:
            self.claude_process = subprocess.Popen(
                [claude_path],
                env=env,
            )

            log_info(self.logger, f"Claude Code running (PID {self.claude_process.pid})")

            # Wait for Claude Code to exit
            self.claude_process.wait()
            log_info(self.logger, "Claude Code exited")

        except KeyboardInterrupt:
            log_info(self.logger, "Received interrupt, shutting down...")
            if self.claude_process:
                self.claude_process.terminate()
                try:
                    self.claude_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.claude_process.kill()
        except Exception as e:
            log_error(self.logger, f"Error running Claude Code: {e}")
            sys.exit(1)

    def cleanup(self) -> None:
        """Clean up running processes."""
        log_info(self.logger, "Cleaning up...")

        if self.claude_process:
            try:
                self.claude_process.terminate()
                self.claude_process.wait(timeout=2)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                pass

        if self.proxy_process:
            try:
                self.proxy_process.terminate()
                self.proxy_process.wait(timeout=2)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                pass

        if self.proxy_log_file:
            try:
                self.proxy_log_file.close()
            except Exception:
                pass

        log_info(self.logger, "Cleanup complete")

    def run(self, port: Optional[int], skip_updates: bool, verbose: bool, quiet: bool) -> None:
        """Main application flow."""
        # Setup logging
        log_level = "WARNING"
        if verbose:
            log_level = "DEBUG"
        elif quiet:
            log_level = "WARNING"
        else:
            log_level = "INFO"

        logger, log_file = setup_logging(level=log_level, verbose=verbose, console_output=verbose)
        self.logger = logger

        # Banner (only in verbose mode)
        if verbose:
            logger.info("=" * 60)
            logger.info(f"GemClaw v{__version__} - Gemini API proxy for Claude Code")
            logger.info("=" * 60)

        try:
            # Validate environment
            self.validate_environment()

            # Check/install Claude Code
            claude_path = self.check_and_install_claude()

            # Find available port
            final_port = find_available_port(port, logger)

            # Start proxy
            self.start_proxy(final_port)

            # Run Claude Code (silent unless verbose)
            if verbose:
                logger.info("")
                logger.info("✨ Claude Code is ready (proxied through Gemini 3.1 Pro)")
                logger.info("")

            self.run_claude_code(final_port, claude_path)

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            self.cleanup()
            logger.info(f"Full logs saved to: {log_file}")
            click.echo(f"gemclaw log: {log_file}", err=True)
            if self.proxy_log_path is not None:
                click.echo(f"proxy log:   {self.proxy_log_path}", err=True)


@click.command()
@click.option(
    "--port",
    type=int,
    default=None,
    help="Port for proxy server (default: auto-detect)",
)
@click.option(
    "--skip-updates",
    is_flag=True,
    help="Skip Claude Code update check",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Verbose output (DEBUG level logging)",
)
@click.option(
    "--quiet",
    is_flag=True,
    help="Quiet mode (WARNING level logging only)",
)
@click.option(
    "--check",
    is_flag=True,
    help="Check configuration and exit",
)
@click.option(
    "--config",
    is_flag=True,
    help="Show current configuration and exit",
)
@click.version_option(version=__version__)
def main(
    port: Optional[int],
    skip_updates: bool,
    verbose: bool,
    quiet: bool,
    check: bool,
    config: bool,
) -> None:
    """GemClaw: Gemini API proxy for Claude Code.

    Set GEMINI_API_KEY environment variable before running:
        export GEMINI_API_KEY="your-google-ai-studio-key"

    Then simply run:
        gemproxy
    """

    # Create logger for early messages (silent unless verbose)
    logger, _ = setup_logging(
        level="DEBUG" if verbose else ("WARNING" if quiet else "INFO"),
        verbose=verbose,
        console_output=verbose,
    )

    if config or check:
        # Show config
        logger.info("GemProxy Configuration")
        logger.info("=" * 50)
        logger.info(f"GEMINI_API_KEY: {'Set' if os.environ.get('GEMINI_API_KEY') else 'NOT SET'}")
        logger.info(f"Hardcoded Model: {GEMINI_MODEL}")

        # Check Claude Code
        claude_manager = ClaudeManager(logger)
        try:
            claude_path = claude_manager.ensure_installed()
            version = claude_manager._get_version()
            logger.info(f"Claude Code Path: {claude_path}")
            logger.info(f"Claude Code Version: v{version}")
        except RuntimeError as e:
            logger.warning(f"Claude Code: Not installed ({e})")

        log_dir = Path.home() / ".gemproxy" / "logs"
        logger.info(f"Log Directory: {log_dir}")

        if config:
            return

    # Run main app
    app = GemClawApp(logger)
    app.run(port, skip_updates, verbose, quiet)


if __name__ == "__main__":
    main()
