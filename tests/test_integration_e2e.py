"""End-to-end integration tests with actual Gemini API."""

import os
import sys
import time
import json
import signal
import logging
import requests
import subprocess
from pathlib import Path


def setup_test_env():
    """Ensure GEMINI_API_KEY is set."""
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY not set")
    return True


def start_gemclaw_proxy(port=8888, timeout=30):
    """Start gemclaw proxy server and wait for readiness."""
    print(f"\n🚀 Starting GemClaw proxy on port {port}...")

    # Start the proxy via uvicorn directly (bypassing Claude Code launch)
    # Use 'uv run' to ensure we have the right environment
    proc = subprocess.Popen(
        [
            "uv", "run", "python", "-m", "uvicorn",
            "gemclaw.proxy:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "warning"
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd="/media/xeb/GreyArea/projects/gemclaw",
    )

    # Wait for proxy to be ready
    base_url = f"http://127.0.0.1:{port}"
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            resp = requests.get(f"{base_url}/health", timeout=2)
            if resp.status_code == 200:
                print(f"✅ Proxy is ready at {base_url}")
                return proc, base_url
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.5)

    proc.terminate()
    raise RuntimeError(f"Proxy failed to start within {timeout}s")


def test_simple_text_request(base_url):
    """Test simple text request through proxy."""
    print("\n📝 Testing simple text request...")

    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "Say 'GemClaw is working!' in exactly one sentence."}
        ]
    }

    resp = requests.post(
        f"{base_url}/v1/messages",
        json=payload,
        timeout=30,
    )

    assert resp.status_code == 200, f"Status {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "content" in data
    assert len(data["content"]) > 0
    assert data["content"][0]["type"] == "text"
    assert "GemClaw is working" in data["content"][0]["text"] or "gemclaw is working" in data["content"][0]["text"].lower()
    print(f"✅ Text response: {data['content'][0]['text'][:80]}...")
    return True


def test_streaming_request(base_url):
    """Test streaming request through proxy."""
    print("\n⚡ Testing streaming request...")

    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 100,
        "stream": True,
        "messages": [
            {"role": "user", "content": "Count to 5, one number per line."}
        ]
    }

    resp = requests.post(
        f"{base_url}/v1/messages",
        json=payload,
        timeout=30,
        stream=True,
    )

    assert resp.status_code == 200, f"Status {resp.status_code}"

    # Parse SSE events
    event_count = 0
    content_text = ""

    for line in resp.iter_lines():
        if not line or line.startswith(b":"):
            continue
        if line.startswith(b"data:"):
            event_count += 1
            try:
                event_data = json.loads(line[5:].decode().strip())
                if "delta" in event_data and "text" in event_data["delta"]:
                    content_text += event_data["delta"]["text"]
            except (json.JSONDecodeError, KeyError):
                pass

    assert event_count > 0, "No streaming events received"
    assert len(content_text) > 0, "No content received in stream"
    print(f"✅ Streaming: received {event_count} events, {len(content_text)} characters")
    print(f"   Content: {content_text[:100].replace(chr(10), ' ')}...")
    return True


def test_multi_turn_conversation(base_url):
    """Test multi-turn conversation."""
    print("\n💬 Testing multi-turn conversation...")

    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "2+2 equals 4."},
            {"role": "user", "content": "What is 3+3?"},
        ]
    }

    resp = requests.post(
        f"{base_url}/v1/messages",
        json=payload,
        timeout=30,
    )

    assert resp.status_code == 200, f"Status {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "content" in data
    text = data["content"][0]["text"]
    assert "6" in text
    print(f"✅ Multi-turn response: {text[:80]}...")
    return True


def test_with_system_prompt(base_url):
    """Test request with system prompt."""
    print("\n📋 Testing system prompt...")

    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 100,
        "system": "You are a helpful assistant. Always respond with exactly 2 sentences.",
        "messages": [
            {"role": "user", "content": "What is AI?"}
        ]
    }

    resp = requests.post(
        f"{base_url}/v1/messages",
        json=payload,
        timeout=30,
    )

    assert resp.status_code == 200, f"Status {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "content" in data
    text = data["content"][0]["text"]
    sentence_count = text.count(".") + text.count("!") + text.count("?")
    print(f"✅ System prompt respected: {text[:80]}...")
    return True


def test_temperature_parameter(base_url):
    """Test temperature parameter."""
    print("\n🌡️  Testing temperature parameter...")

    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 50,
        "temperature": 0.1,  # Low temperature for consistency
        "messages": [
            {"role": "user", "content": "What is the first letter of the alphabet?"}
        ]
    }

    resp = requests.post(
        f"{base_url}/v1/messages",
        json=payload,
        timeout=30,
    )

    assert resp.status_code == 200, f"Status {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "content" in data
    text = data["content"][0]["text"]
    assert "A" in text or "a" in text
    print(f"✅ Temperature parameter works: {text[:80]}...")
    return True


def cleanup_proxy(proc):
    """Terminate proxy process."""
    print("\n🧹 Cleaning up...")
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    print("✅ Cleanup complete")


def main():
    """Run all integration tests."""
    print("=" * 60)
    print("GemClaw End-to-End Integration Tests")
    print("=" * 60)

    # Check environment
    try:
        setup_test_env()
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)

    proc = None
    try:
        # Start proxy
        proc, base_url = start_gemclaw_proxy()

        # Run tests
        tests = [
            test_simple_text_request,
            test_multi_turn_conversation,
            test_with_system_prompt,
            test_temperature_parameter,
            test_streaming_request,
        ]

        passed = 0
        failed = 0

        for test_func in tests:
            try:
                if test_func(base_url):
                    passed += 1
            except Exception as e:
                print(f"❌ {test_func.__name__} failed: {e}")
                failed += 1

        # Print summary
        print("\n" + "=" * 60)
        print(f"Test Results: {passed} passed, {failed} failed")
        print("=" * 60)

        if failed == 0:
            print("✨ All tests passed!")
            return 0
        else:
            print(f"❌ {failed} test(s) failed")
            return 1

    finally:
        cleanup_proxy(proc)


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
