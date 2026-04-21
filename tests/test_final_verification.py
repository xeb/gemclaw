#!/usr/bin/env python
"""Final verification that GemClaw installation and proxy work correctly."""

import subprocess
import sys
import time
import json
import requests
import os

def test_cli_works():
    """Test that gemclaw CLI is installed and responds."""
    print("\n✅ Testing CLI installation...")
    result = subprocess.run(["gemclaw", "--version"], capture_output=True, text=True)
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    assert "0.1.0" in result.stdout
    print(f"   Version: {result.stdout.strip()}")

def test_config_shows():
    """Test that config display works."""
    print("\n✅ Testing config display...")
    result = subprocess.run(["gemclaw", "--config"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "GEMINI_API_KEY" in result.stdout
    assert "gemini-3.1-pro-preview" in result.stdout
    assert "Claude Code" in result.stdout
    print("   Config display works")

def test_proxy_startup():
    """Test that proxy server starts correctly."""
    print("\n✅ Testing proxy startup...")

    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "uvicorn",
         "gemclaw.proxy:app", "--host", "127.0.0.1", "--port", "8894"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd="/media/xeb/GreyArea/projects/gemclaw",
    )

    # Wait for proxy to be ready
    for _ in range(30):
        try:
            resp = requests.get("http://127.0.0.1:8894/health", timeout=1)
            if resp.status_code == 200:
                print(f"   Proxy is running on port 8894")
                proc.terminate()
                proc.wait(timeout=5)
                return True
        except:
            pass
        time.sleep(0.5)

    proc.terminate()
    raise RuntimeError("Proxy failed to start")

def test_anthropic_to_gemini_translation():
    """Test that request translation works."""
    print("\n✅ Testing request translation...")

    from gemclaw.translator import AnthropicToGeminiTranslator
    import logging

    logger = logging.getLogger(__name__)
    translator = AnthropicToGeminiTranslator(logger)

    # Test simple request
    anthropic_req = {
        "model": "claude-opus",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}]
    }

    gemini_req = translator.translate_request(anthropic_req)

    assert gemini_req["model"] == "claude-opus"
    assert gemini_req["generationConfig"]["maxOutputTokens"] == 100
    assert len(gemini_req["contents"]) == 1
    assert gemini_req["contents"][0]["role"] == "user"
    print("   Request translation works")

def test_gemini_to_anthropic_translation():
    """Test that response translation works."""
    print("\n✅ Testing response translation...")

    from gemclaw.translator import GeminiToAnthropicTranslator
    import logging

    logger = logging.getLogger(__name__)
    translator = GeminiToAnthropicTranslator(logger)

    # Test response
    gemini_resp = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Hello!"}]
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "inputTokenCount": 10,
            "outputTokenCount": 5,
        },
    }

    anthropic_resp = translator.translate_response(gemini_resp)

    assert anthropic_resp["role"] == "assistant"
    assert len(anthropic_resp["content"]) > 0
    assert anthropic_resp["content"][0]["type"] == "text"
    assert "Hello" in anthropic_resp["content"][0]["text"]
    assert anthropic_resp["usage"]["input_tokens"] == 10
    assert anthropic_resp["usage"]["output_tokens"] == 5
    print("   Response translation works")

def main():
    """Run all verification tests."""
    print("=" * 60)
    print("GemClaw Final Verification Tests")
    print("=" * 60)

    if not os.environ.get("GEMINI_API_KEY"):
        print("⚠️  GEMINI_API_KEY not set - API tests will be skipped")

    try:
        test_cli_works()
        test_config_shows()
        test_proxy_startup()
        test_anthropic_to_gemini_translation()
        test_gemini_to_anthropic_translation()

        print("\n" + "=" * 60)
        print("✨ All verification tests PASSED!")
        print("=" * 60)
        print("\nGemClaw is installed and working correctly.")
        print("Usage: gemclaw [options]")
        print("  --port PORT              Port for proxy (auto-detect by default)")
        print("  --skip-updates           Skip Claude Code update check")
        print("  --verbose                Verbose logging")
        print("  --config                 Show configuration")
        print("  --version                Show version")
        return 0

    except Exception as e:
        print(f"\n❌ Verification failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
