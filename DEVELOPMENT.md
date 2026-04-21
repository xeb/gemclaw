# GemProxy Development Guide

## Setup

### First Time Setup
```bash
cd /path/to/gemproxy
uv tool install -e .
```

This installs gemproxy as an editable tool, meaning changes to the code are reflected immediately (no reinstall needed).

### Running During Development

```bash
export GEMINI_API_KEY="your-key"
gemproxy --verbose
```

Or check configuration without starting:
```bash
gemproxy --config
```

## Architecture

### Module Organization

```
gemproxy/
├── __init__.py           # Version, constants
├── cli.py               # Click CLI entry point, main orchestration
├── claude_manager.py    # Check/install Claude Code
├── proxy.py             # FastAPI application
├── translator.py        # Anthropic ↔ Gemini format translation
└── utils.py             # Logging, port discovery, helpers

tests/
├── __init__.py
└── test_translator.py   # Format translation tests
```

### Data Flow

```
User runs: gemproxy
  ↓
[cli.py:main()]
  ├─ Setup logging
  ├─ Validate GEMINI_API_KEY
  ├─ Check/install Claude Code (claude_manager.py)
  ├─ Find available port (utils.py)
  ├─ Start proxy server subprocess
  │   └─ uvicorn runs (proxy.py)
  └─ Launch Claude Code with proxy env vars
       └─ ANTHROPIC_BASE_URL=http://127.0.0.1:{port}

Claude Code makes request
  ↓
[proxy.py:/v1/messages]
  ├─ Parse request (Anthropic format)
  ├─ Validate GEMINI_API_KEY
  ├─ Translate to Gemini (translator.py)
  ├─ Call Gemini API (via LiteLLM)
  ├─ Handle streaming
  ├─ Translate response (translator.py)
  └─ Return to Claude Code (Anthropic SSE format)
```

## Testing

### Manual Integration Tests
```bash
# Test translator
python -c "
from gemproxy.translator import AnthropicToGeminiTranslator
import logging
logger = logging.getLogger()
t = AnthropicToGeminiTranslator(logger)
result = t.translate_request({'model': 'claude-opus', 'max_tokens': 100, 'messages': [{'role': 'user', 'content': 'Hi'}]})
print('✅ Translator works')
"

# Test proxy endpoints (with TestClient)
uv run python -c "
from fastapi.testclient import TestClient
from gemproxy.proxy import app
client = TestClient(app)
resp = client.get('/health')
assert resp.status_code == 200
print('✅ Proxy works')
"
```

### Full Integration Test
```bash
uv run python /tmp/test_gemproxy.py  # Run the comprehensive test
```

### Testing with Real Gemini API

To test the full flow with a real Gemini API key:

```bash
export GEMINI_API_KEY="AIza..."
uv run python -c "
import os
os.environ['GEMINI_API_KEY'] = os.environ.get('GEMINI_API_KEY')
from fastapi.testclient import TestClient
from gemproxy.proxy import app
client = TestClient(app)

response = client.post('/v1/messages', json={
    'model': 'claude-opus',
    'max_tokens': 100,
    'messages': [{'role': 'user', 'content': 'What is 2+2?'}],
})
print('Status:', response.status_code)
print('Response:', response.json())
"
```

## Debugging

### Enable Debug Logging
```bash
export GEMINI_API_KEY="your-key"
gemproxy --verbose
```

This logs:
- All requests and responses (JSON pretty-printed)
- Translation steps
- Proxy startup/shutdown
- Error details with stack traces

### View Logs
```bash
# Last log file
tail -f ~/.gemproxy/logs/gemproxy-*.log | tail -f

# Search for errors
grep ERROR ~/.gemproxy/logs/gemproxy-*.log

# Search for specific request
grep "model.*claude-opus" ~/.gemproxy/logs/gemproxy-*.log
```

### Test Specific Components

Test translator:
```bash
uv run python -m pytest tests/test_translator.py -v
```

Test proxy health:
```bash
export GEMINI_API_KEY="test"
gemproxy --port 9999 &
sleep 2
curl http://127.0.0.1:9999/health
kill %1
```

## Common Issues

### "Module not found: fastapi"
Make sure you're running with `uv run` for one-off commands, or have installed with `uv tool install -e .`

### "GEMINI_API_KEY not set"
```bash
export GEMINI_API_KEY="your-actual-key-from-ai.google.com"
```

### "Port already in use"
```bash
gemproxy --port 8082  # Use different port
```

### Claude Code not launching
Check the log file for details:
```bash
tail -f ~/.gemproxy/logs/gemproxy-*.log
```

## Making Changes

### Adding a New Feature

1. Identify which module it belongs in
2. Add the feature
3. Add tests in `tests/`
4. Test manually with `gemproxy --verbose`
5. Check logs for any issues

### Modifying Translation Logic

Changes to `translator.py`:
1. Edit the translation methods
2. Add test cases in `tests/test_translator.py`
3. Test with: `python -c "from gemproxy.translator import ..."`

### Adding New CLI Options

1. Edit `cli.py`: Add `@click.option()` decorator
2. Add parameter to `main()` function
3. Document in README.md and USAGE_EXAMPLE.md

### Modifying Proxy Endpoints

1. Edit `proxy.py`
2. Test with TestClient in Python
3. Test with real HTTP requests if streaming involved

## Code Style

- No type hints required (dynamic Python)
- Descriptive variable names
- Comments for non-obvious logic only
- Logging for debugging (use `logger.debug()`)
- Error messages should be user-friendly

## Performance Considerations

- Proxy startup: <1 second
- Per-request overhead: ~100ms (Gemini API latency dominates)
- Memory: ~150MB (FastAPI + LiteLLM + Claude Code)
- Streaming: Real-time (chunk-by-chunk, no buffering)

## Useful Commands

```bash
# Install in dev mode
uv tool install -e .

# Check configuration
gemproxy --config

# Run verbose
gemproxy --verbose

# Check health (in another terminal)
curl http://127.0.0.1:49152/health

# View last log
cat ~/.gemproxy/logs/gemproxy-*.log | tail -100

# Run all tests
uv run pytest tests/ -v

# Run specific test
uv run pytest tests/test_translator.py::TestAnthropicToGemini::test_simple_text_message -v
```

## Further Reading

- [SPEC.md](SPEC.md) - Full technical specification
- [USAGE_EXAMPLE.md](USAGE_EXAMPLE.md) - User examples
- [README.md](README.md) - Quick start guide

## Next Steps

Possible enhancements:
- Add more comprehensive error handling
- Add metrics/observability
- Add config file support (~/.gemproxy/config.yaml)
- Add model aliases (mapgemini-2.0 → gemini-2.0-flash)
- Add request/response caching layer
- Add batch API support
- Add file upload support
