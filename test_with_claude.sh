#!/bin/bash
# Integration test: Start proxy and test with real Claude Code

set -e

echo "╔════════════════════════════════════════════════════════════╗"
echo "║     GemProxy + Claude Code Integration Test               ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Check GEMINI_API_KEY
if [ -z "$GEMINI_API_KEY" ]; then
    echo "❌ GEMINI_API_KEY not set"
    echo ""
    echo "To run this test with a real Gemini API key:"
    echo "  1. Get a key from: https://aistudio.google.com/app/apikey"
    echo "  2. Set it: export GEMINI_API_KEY='AIza...'"
    echo "  3. Run: $0"
    echo ""
    echo "Without a real key, use the dry-run test:"
    echo "  python /tmp/integration_test.py"
    exit 1
fi

echo "✅ GEMINI_API_KEY is set"
echo ""

# Start proxy in background
echo "🚀 Starting proxy server..."
PROXY_PORT=49996
gemproxy --port $PROXY_PORT &
PROXY_PID=$!
sleep 3

# Verify proxy is running
if ! ps -p $PROXY_PID > /dev/null; then
    echo "❌ Proxy failed to start"
    exit 1
fi

echo "✅ Proxy started (PID: $PROXY_PID)"
echo "✅ Listening on http://127.0.0.1:$PROXY_PORT"
echo ""

# Function to cleanup
cleanup() {
    echo ""
    echo "Shutting down proxy..."
    kill $PROXY_PID 2>/dev/null || true
    sleep 1
    echo "✅ Proxy stopped"
}

trap cleanup EXIT

# Run a quick test to make sure proxy can connect to Gemini
echo "Testing proxy connectivity to Gemini..."
python3 << 'PYTHON'
import os
import sys
import requests
import json

base_url = "http://127.0.0.1:49996"
gemini_api_key = os.environ.get('GEMINI_API_KEY')

# Simple health check
try:
    response = requests.get(f"{base_url}/health", timeout=5)
    if response.status_code == 200:
        print("✅ Proxy health check passed")
    else:
        print(f"❌ Health check failed: {response.status_code}")
        sys.exit(1)
except Exception as e:
    print(f"❌ Could not connect to proxy: {e}")
    sys.exit(1)

# Test with a simple message
try:
    print("Testing simple message translation...")
    response = requests.post(
        f"{base_url}/v1/messages",
        json={
            "model": "claude-opus-4-7",
            "max_tokens": 100,
            "messages": [
                {"role": "user", "content": "Say 'Hello from Gemini'"}
            ]
        },
        timeout=30,
    )

    if response.status_code == 200:
        data = response.json()
        if 'content' in data and len(data['content']) > 0:
            msg = data['content'][0].get('text', '')
            print(f"✅ Proxy translation works")
            print(f"   Response: {msg[:100]}")
        else:
            print(f"❌ Response missing content")
            sys.exit(1)
    else:
        print(f"❌ Request failed: {response.status_code}")
        print(f"   Error: {response.text[:200]}")
        sys.exit(1)

except Exception as e:
    print(f"❌ Error testing proxy: {e}")
    sys.exit(1)

print("")
print("✅ Proxy is working correctly with Gemini API!")
PYTHON

if [ $? -ne 0 ]; then
    exit 1
fi

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║           Testing with Claude Code                        ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "Claude Code will now launch with the proxy."
echo "Try these commands:"
echo "  - read <file> and explain it"
echo "  - what's 2+2?"
echo "  - describe an image (upload/paste)"
echo ""
echo "Type 'exit' or press Ctrl+C to quit."
echo ""
read -p "Press Enter to launch Claude Code..."

# Launch Claude Code with proxy
export ANTHROPIC_BASE_URL="http://127.0.0.1:$PROXY_PORT"
export ANTHROPIC_API_KEY="dummy"
export ANTHROPIC_MODEL="gemini-3.1-pro-preview"

echo "Launching Claude Code..."
echo ""

claude || true

echo ""
echo "✅ Claude Code session ended"
