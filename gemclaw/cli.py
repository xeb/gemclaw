"""CLI entry point for GemClaw."""

import atexit
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


# Files in the user's Claude Code config dir that hold the cached claude.ai
# OAuth token. When an API key is also set (which gemclaw always sets), Claude
# Code emits "Auth conflict" warnings. We temporarily rename these during the
# session so the TUI stays clean, and restore them on exit.
_OAUTH_CREDENTIAL_FILES = [
    Path.home() / ".claude" / ".credentials.json",
    Path.home() / ".claude" / "credentials.json",
]
_SIDELINE_SUFFIX = ".gemclaw-sidelined"


class GemClawApp:
    """Main GemClaw application controller."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.proxy_process: Optional[subprocess.Popen] = None
        self.claude_process: Optional[subprocess.Popen] = None
        self.proxy_log_file = None
        self.proxy_log_path: Optional[Path] = None
        self.sidelined_credentials: list[tuple[Path, Path]] = []

    def _sideline_oauth_credentials(self) -> None:
        """Temporarily rename cached claude.ai OAuth credential files.

        Claude Code complains 'Auth conflict: Both a token (claude.ai) and
        an API key (ANTHROPIC_API_KEY) are set' when both are present. We
        need the API-key path (that's how requests reach our proxy), so
        shuffle the OAuth file out of the way for the duration of the
        session. Restored in restore_oauth_credentials().

        We don't use CLAUDE_CONFIG_DIR because the user may have real
        customizations in ~/.claude that they want active during the
        gemclaw session.
        """
        for path in _OAUTH_CREDENTIAL_FILES:
            if not path.exists() or not path.is_file():
                continue
            backup = path.with_name(path.name + _SIDELINE_SUFFIX)
            try:
                if backup.exists():
                    backup.unlink()
                path.rename(backup)
                self.sidelined_credentials.append((path, backup))
                self.logger.debug(f"Sidelined OAuth credentials: {path} → {backup.name}")
            except Exception as e:
                self.logger.warning(f"Could not sideline {path}: {e}")

        if self.sidelined_credentials:
            atexit.register(self.restore_oauth_credentials)
            for orig, bak in self.sidelined_credentials:
                self.logger.debug(f"If gemclaw is killed abnormally, restore with: mv '{bak}' '{orig}'")

    def restore_oauth_credentials(self) -> None:
        """Move sidelined credential files back to their original paths."""
        while self.sidelined_credentials:
            original, backup = self.sidelined_credentials.pop()
            try:
                if backup.exists():
                    if original.exists():
                        # Something (maybe /login mid-session) recreated
                        # the credentials. Keep the newer one, drop the backup.
                        backup.unlink()
                        self.logger.debug(f"Skipping restore of {original} — file was recreated during session")
                    else:
                        backup.rename(original)
                        self.logger.debug(f"Restored OAuth credentials: {original.name}")
            except Exception as e:
                self.logger.warning(f"Could not restore {original}: {e} — manual recovery: mv '{backup}' '{original}'")

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

        self._sideline_oauth_credentials()

        env = os.environ.copy()

        # Claude Code has four backend modes: Anthropic API, claude.ai
        # OAuth, Vertex AI, Bedrock. ANTHROPIC_BASE_URL is only honored
        # in Anthropic-API mode. On corp/Google machines the default
        # is often Vertex (CLAUDE_CODE_USE_VERTEX=1), which routes to
        # Vertex and ignores our proxy entirely. Explicitly unset every
        # mode-selector we know about to force Anthropic-API mode.
        for var in (
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_SKIP_VERTEX_AUTH",
            "ANTHROPIC_VERTEX_PROJECT_ID",
            "CLOUD_ML_REGION",
            "ANTHROPIC_BEDROCK_BASE_URL",
            "AWS_BEARER_TOKEN_BEDROCK",
        ):
            env.pop(var, None)

        # Anthropic-API mode requires an API key or it falls back to
        # the cached claude.ai OAuth token and ignores ANTHROPIC_BASE_URL.
        # The proxy doesn't validate the key — any non-empty value works.
        env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
        # Must match Anthropic's sk-ant-api03-... shape or Claude Code's
        # client-side key-format check rejects it and shows "Not logged in"
        # without ever calling the API. The proxy doesn't validate the value.
        env["ANTHROPIC_API_KEY"] = "sk-ant-api03-gemclaw-proxy-dummy-key-not-used-but-format-matters-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        env.pop("ANTHROPIC_AUTH_TOKEN", None)  # avoid auth-mode conflict

        # Show the real backend in Claude Code's header. The earlier
        # "model not found" error with this slug was actually Vertex
        # rejecting it, not Claude Code — in plain Anthropic-API mode
        # (routed to our proxy) the TUI just displays whatever slug we
        # pass. The proxy accepts any model name and overrides to
        # GEMINI_MODEL on the outbound Gemini call.
        env["ANTHROPIC_MODEL"] = GEMINI_MODEL
        env["ANTHROPIC_SMALL_FAST_MODEL"] = GEMINI_MODEL

        self.logger.debug("Claude Code environment:")
        self.logger.debug(f"  ANTHROPIC_BASE_URL={env['ANTHROPIC_BASE_URL']}")
        self.logger.debug(f"  ANTHROPIC_API_KEY=<dummy — forces API-key mode>")
        self.logger.debug(f"  ANTHROPIC_MODEL={env['ANTHROPIC_MODEL']} (proxy overrides per-request)")
        self.logger.debug(f"  CLAUDE_CODE_USE_VERTEX=<cleared>")
        self.logger.debug(f"  CLAUDE_CODE_USE_BEDROCK=<cleared>")

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

        self.restore_oauth_credentials()

        log_info(self.logger, "Cleanup complete")

    def run(self, port: Optional[int], skip_updates: bool, verbose: bool, quiet: bool) -> None:
        """Main application flow."""
        log_level = "WARNING"
        if verbose:
            log_level = "DEBUG"
        elif quiet:
            log_level = "WARNING"
        else:
            log_level = "INFO"

        logger, log_file = setup_logging(level=log_level, verbose=verbose, console_output=verbose)
        self.logger = logger

        # SIGTERM -> SystemExit so the finally block (credential restore,
        # process cleanup) still runs. Without this, `kill <gemclaw-pid>`
        # would leave OAuth credentials sidelined on disk.
        def _sigterm(signum, frame):
            raise SystemExit(128 + signum)
        signal.signal(signal.SIGTERM, _sigterm)

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
