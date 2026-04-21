# GemProxy: Full-Featured Gemini Proxy for Claude Code

**Project Goal**: Create a production-ready CLI tool called `gemproxy` (installed via `uv tool install`) that spawns a per-session Gemini API proxy for Claude Code, automatically manages Claude Code installation, and always routes to `gemini-3.1-pro-preview`.

---

## Overview

```
$ gemproxy

✅ Checking Claude Code installation...
✅ Claude Code v0.7.2 found at /home/xeb/.local/bin/claude
✅ Starting Gemini proxy (gemini-3.1-pro-preview only)
✅ Proxy listening on http://127.0.0.1:49152
✅ Launching Claude Code with proxy environment...

> Ready for commands (type 'help' or ask me anything)
```

When Claude Code exits, the proxy automatically shuts down.

---

## Architecture

### Project Structure
```
gemproxy/
├── pyproject.toml          # Package config + entry point definition
├── README.md              # Installation + usage guide
├── gemproxy/
│   ├── __init__.py        # Version, constants
│   ├── cli.py             # Main entry point, orchestration
│   ├── proxy.py           # FastAPI proxy server
│   ├── translator.py      # Anthropic ↔ Gemini format translation
│   ├── claude_manager.py  # Claude Code detection, install, updates
│   └── utils.py           # Port allocation, logging, helpers
└── tests/
    ├── test_translator.py # Translation layer tests
    └── test_proxy.py      # Proxy server tests
```

### Data Flow
```
gemproxy CLI invoked
    ↓
[cli.py]
  ├─ Check Claude Code installed (claude_manager.py)
  ├─ If missing, install via `uv tool install anthropics-claude-code`
  ├─ Find available port (utils.py)
  ├─ Start proxy server (proxy.py) in subprocess
  └─ Set env vars:
      ├ ANTHROPIC_BASE_URL=http://127.0.0.1:{port}
      ├ ANTHROPIC_API_KEY=dummy (proxy ignores)
      ├ ANTHROPIC_MODEL=gemini-3.1-pro-preview (hardcoded)
      ├ GEMINI_API_KEY={from user env}
      └ (pass through other Claude Code env vars)
    ↓
[Claude Code]
  │ (reads ANTHROPIC_BASE_URL, makes API calls)
    ↓
[proxy.py] ← POST /v1/messages
  │
  ├─ Log request (verbose)
  ├─ Parse Anthropic request
  ├─ Translate to Gemini format (translator.py)
  ├─ Call Gemini API (via LiteLLM)
  ├─ Handle streaming response
  ├─ Translate back to Anthropic SSE format
  └─ Stream response to Claude Code
    ↓
Claude Code displays response
    ↓
User kills Claude Code (Ctrl+C)
    ↓
[cli.py]
  └─ Kill proxy subprocess
  └─ Cleanup temp logs
```

---

## Installation & Usage

### Install GemProxy
```bash
uv tool install git+https://github.com/xebxeb/gemproxy.git
```

Or for local development:
```bash
cd /path/to/gemproxy
uv tool install -e .
```

### First Run
```bash
export GEMINI_API_KEY="your-google-ai-studio-key"
gemproxy
```

That's it! GemProxy will:
1. ✅ Check if Claude Code is installed (`claude --version`)
2. ✅ Install Claude Code if missing (`uv tool install anthropics-claude-code`)
3. ✅ Check for Claude Code updates (optional: `--skip-updates` flag)
4. ✅ Find an available port (default: random in 49000-65000 range)
5. ✅ Start proxy server (verbose logging)
6. ✅ Launch Claude Code with proxy configured
7. ✅ Auto-cleanup on exit (Ctrl+C)

### Advanced Usage

```bash
# Override port (useful for testing)
gemproxy --port 8082

# Skip Claude Code update check
gemproxy --skip-updates

# Disable verbose logging (WARNING level instead)
gemproxy --quiet

# Specify log file location (default: ~/.gemproxy/logs/gemproxy-{timestamp}.log)
gemproxy --log-file /tmp/my-proxy.log

# Dry-run (check environment, don't start proxy)
gemproxy --check

# Show current configuration
gemproxy --config
```

---

## Implementation Details

### 1. Entry Point (pyproject.toml)
```toml
[project.scripts]
gemproxy = "gemproxy.cli:main"
```

### 2. CLI Main Function (cli.py)
```python
def main():
    args = parse_args()
    logger = setup_logging(args.log_file, args.quiet)
    
    # Check GEMINI_API_KEY
    if not os.environ.get('GEMINI_API_KEY'):
        logger.error("GEMINI_API_KEY not set")
        sys.exit(1)
    
    # Check/install Claude Code
    claude_manager = ClaudeManager(logger)
    claude_path = claude_manager.ensure_installed()
    if args.skip_updates is False:
        claude_manager.check_updates()
    
    # Find port
    port = find_available_port(args.port, logger)
    
    # Start proxy
    proxy_proc = start_proxy_server(port, logger)
    
    try:
        # Launch Claude Code
        run_claude_code(port, logger, claude_path)
    finally:
        # Cleanup
        proxy_proc.terminate()
        proxy_proc.wait(timeout=5)
        logger.info("Proxy shut down")
```

### 3. Proxy Server (proxy.py)
```python
# FastAPI app
app = FastAPI(title="GemProxy")

@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    logger.debug(f"Received request: {body}")
    
    # Always override model to gemini-3.1-pro-preview
    body["model"] = "gemini-3.1-pro-preview"
    
    # Translate
    gemini_request = translate_anthropic_to_gemini(body)
    logger.debug(f"Translated to Gemini: {gemini_request}")
    
    # Call Gemini (via LiteLLM)
    if body.get("stream"):
        return StreamingResponse(stream_gemini_response(gemini_request))
    else:
        response = call_gemini(gemini_request)
        anthropic_response = translate_gemini_to_anthropic(response)
        logger.debug(f"Response: {anthropic_response}")
        return anthropic_response
```

### 4. Translation Layer (translator.py)
Handles all Anthropic ↔ Gemini format conversion:
- Message structure (different role/content layouts)
- Tool definitions (Anthropic tools → Gemini functions)
- Tool use/results (Anthropic tool_use blocks → Gemini function_calls)
- Images (base64 data URL format conversion)
- Streaming chunks (convert Gemini SSE → Anthropic SSE)
- Stop reasons (STOP → end_turn, MAX_TOKENS → max_tokens)
- Usage stats (tokens in/out)

### 5. Claude Manager (claude_manager.py)
```python
class ClaudeManager:
    def ensure_installed(self) -> str:
        """Check if claude is installed, install if needed"""
        try:
            result = subprocess.run(['claude', '--version'], capture_output=True)
            if result.returncode == 0:
                version = result.stdout.decode().strip()
                logger.info(f"Found Claude Code: {version}")
                return shutil.which('claude')
        except FileNotFoundError:
            pass
        
        # Not found, install
        logger.info("Installing Claude Code via uv...")
        subprocess.run(['uv', 'tool', 'install', 'anthropics-claude-code'], check=True)
        return shutil.which('claude')
    
    def check_updates(self) -> None:
        """Check for Claude Code updates"""
        logger.info("Checking for Claude Code updates...")
        # Use uv tool list or similar to check versions
        # Install updates if available
```

---

## Logging & Debugging

### Log Output
Every run generates a detailed log file at:
```
~/.gemproxy/logs/gemproxy-2026-04-19_14-32-45.log
```

### Log Contents
```
[2026-04-19 14:32:45] INFO: GemProxy v0.1.0 started
[2026-04-19 14:32:45] INFO: Checking Claude Code installation...
[2026-04-19 14:32:46] INFO: Found Claude Code v0.7.2 at /home/xeb/.local/bin/claude
[2026-04-19 14:32:46] INFO: Finding available port...
[2026-04-19 14:32:46] INFO: Using port 49152
[2026-04-19 14:32:46] DEBUG: Starting proxy server with PID 12345
[2026-04-19 14:32:47] INFO: Proxy listening on http://127.0.0.1:49152
[2026-04-19 14:32:47] INFO: Launching Claude Code...
[2026-04-19 14:32:48] DEBUG: Claude Code PID 12346
[2026-04-19 14:32:50] DEBUG: Proxy received request:
{
  "model": "claude-opus-4-7",
  "messages": [...],
  "max_tokens": 8192,
  ...
}
[2026-04-19 14:32:50] DEBUG: Model override: claude-opus-4-7 → gemini-3.1-pro-preview
[2026-04-19 14:32:50] DEBUG: Translated to Gemini format, calling API...
[2026-04-19 14:32:52] DEBUG: Gemini response received (streaming)
[2026-04-19 14:32:52] DEBUG: Translating back to Anthropic SSE format
[2026-04-19 14:32:55] INFO: Stream complete, usage: 234 input, 567 output
[2026-04-19 15:45:00] INFO: Claude Code exited (user Ctrl+C)
[2026-04-19 15:45:01] INFO: Terminating proxy (PID 12345)
[2026-04-19 15:45:02] INFO: Cleanup complete
```

### Verbose Mode
```bash
gemproxy --verbose  # Default: INFO level
# Logs every request body, response, translation step
```

### Quiet Mode
```bash
gemproxy --quiet    # WARNING level, errors only
```

---

## Configuration

### Environment Variables
```bash
# Required
GEMINI_API_KEY="your-google-ai-studio-key"

# Optional
GEMPROXY_LOG_LEVEL="DEBUG"           # DEBUG, INFO, WARNING, ERROR
GEMPROXY_PORT="8082"                 # Override port detection
GEMPROXY_LOG_DIR="~/.gemproxy/logs"  # Where to write logs
GEMPROXY_SKIP_UPDATES="true"         # Don't check Claude Code updates
```

### Config File (Optional)
```yaml
# ~/.gemproxy/config.yaml
log_level: INFO
log_dir: ~/.gemproxy/logs
skip_claude_updates: false
port: null  # Auto-detect
```

---

## Dependencies

```toml
[project]
name = "gemproxy"
version = "0.1.0"
description = "Full-featured Gemini API proxy for Claude Code"

[project.dependencies]
fastapi = ">=0.115"
uvicorn = ">=0.34"
httpx = ">=0.25"
pydantic = ">=2.0"
litellm = ">=1.40"
python-dotenv = ">=1.0"
click = ">=8.1"          # CLI arg parsing (nicer than argparse)
colorama = ">=0.4"       # Colored terminal output

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
    "pytest-cov>=4.1",
    "black>=23.0",
    "ruff>=0.1"
]

[project.scripts]
gemproxy = "gemproxy.cli:main"
```

---

## Features - Full Specification

### Phase 1: MVP (Core - must have)
- ✅ Text messages (user/assistant)
- ✅ Streaming responses (Anthropic SSE format)
- ✅ Model hardcoding (gemini-3.1-pro-preview only)
- ✅ Per-session proxy lifecycle
- ✅ Claude Code auto-install
- ✅ Verbose logging to file
- ✅ Error handling & recovery
- ✅ Port auto-discovery

### Phase 2: Images & Tools (will implement)
- ⚠️ Image content blocks (base64, URLs)
- ⚠️ Tool definitions & function calling
- ⚠️ Tool use blocks (Anthropic) → function_calls (Gemini)
- ⚠️ Tool results handling

### Phase 3: Advanced (scope TBD)
- ⚠️ System prompt caching (if Gemini supports)
- ⚠️ Extended thinking/reasoning (if Gemini supports)
- ⚠️ Batch API support
- ⚠️ Multi-file uploads

---

## Testing

### Unit Tests (translator.py)
```bash
pytest tests/test_translator.py -v
```
Test cases:
- Anthropic message → Gemini content conversion
- Tool definition mapping
- Streaming chunk translation
- Error responses

### Integration Tests (with real Gemini API)
```bash
export GEMINI_API_KEY="your-key"
pytest tests/test_proxy.py -v -s
```
Test cases:
- End-to-end message flow
- Streaming responses
- Tool use flow
- Error handling

### Manual Testing
```bash
gemproxy --port 8082
# In another terminal:
export ANTHROPIC_BASE_URL=http://127.0.0.1:8082
export ANTHROPIC_API_KEY=dummy
export ANTHROPIC_MODEL=gemini-3.1-pro-preview
export GEMINI_API_KEY="your-key"
claude
```

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `GEMINI_API_KEY` not set | Exit with clear error message |
| Claude Code not installed | Install automatically via `uv tool install` |
| No available ports | Try increasing range, then error |
| Gemini API error (401) | Log error, exit with helpful message |
| Gemini API timeout | Retry 2x with exponential backoff, then stream error |
| Malformed Claude Code request | Return 422 with details |
| Proxy subprocess crash | Log stacktrace, exit main process |

---

## Success Criteria

✅ Single command installation: `uv tool install git+https://github.com/xebxeb/gemproxy.git`
✅ Single command execution: `gemproxy` (with GEMINI_API_KEY set)
✅ Auto-installs Claude Code if missing
✅ Claude Code connects and works end-to-end
✅ Images are supported (base64 + URLs)
✅ Tool definitions and tool use work
✅ Streaming responses display correctly
✅ All requests logged with full details
✅ Graceful shutdown on Ctrl+C
✅ Comprehensive error messages

---

## Implementation Phases

### Week 1: Foundation
- [x] Project structure & pyproject.toml
- [x] CLI scaffolding (Click)
- [x] Claude Code manager
- [x] Port discovery & logging
- [ ] FastAPI proxy skeleton

### Week 2: Translation Layer
- [ ] Anthropic → Gemini translator
- [ ] Gemini → Anthropic translator
- [ ] Streaming format conversion
- [ ] Unit tests for translator

### Week 3: Integration
- [ ] Proxy-to-Claude Code flow
- [ ] Real Gemini API testing
- [ ] Error handling & retries
- [ ] Integration tests

### Week 4: Polish
- [ ] Tool support (images + function calling)
- [ ] Documentation
- [ ] Performance optimization
- [ ] Release packaging

---

## References

- [uv tool install docs](https://docs.astral.sh/uv/concepts/tools/)
- [FastAPI streaming](https://fastapi.tiangolo.com/advanced/response-streams/)
- [LiteLLM Anthropic provider](https://docs.litellm.ai/docs/providers/anthropic)
- [Anthropic Messages API](https://docs.anthropic.com/en/api/messages)
- [Google Gemini API](https://ai.google.dev/docs)
- [Claude Code custom endpoint](https://code.claude.com/docs/en/llm-gateway)

---

## Next Steps

1. ✅ Review this spec
2. ⏳ Create project structure
3. ⏳ Implement CLI & Claude manager
4. ⏳ Build FastAPI proxy
5. ⏳ Create translator layer
6. ⏳ Integration testing
7. ⏳ Ship!
