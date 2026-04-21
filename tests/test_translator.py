"""Tests for Anthropic ↔ Gemini format translation."""

import logging
import pytest
from gemclaw.translator import AnthropicToGeminiTranslator, GeminiToAnthropicTranslator

logger = logging.getLogger(__name__)


class TestAnthropicToGemini:
    """Test Anthropic to Gemini request translation."""

    def setup_method(self):
        self.translator = AnthropicToGeminiTranslator(logger)

    def test_simple_text_message(self):
        """Test translating a simple text message."""
        anthropic_req = {
            "model": "claude-opus",
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": "Hello, how are you?"}
            ]
        }

        result = self.translator.translate_request(anthropic_req)

        assert result["model"] == "claude-opus"
        assert result["generationConfig"]["maxOutputTokens"] == 1024
        assert len(result["contents"]) == 1
        assert result["contents"][0]["role"] == "user"
        assert len(result["contents"][0]["parts"]) > 0

    def test_multiple_messages(self):
        """Test translating multiple messages (conversation)."""
        anthropic_req = {
            "model": "claude-opus",
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": "2+2 equals 4."},
                {"role": "user", "content": "What about 3+3?"},
            ]
        }

        result = self.translator.translate_request(anthropic_req)

        assert len(result["contents"]) == 3
        assert result["contents"][0]["role"] == "user"
        assert result["contents"][1]["role"] == "model"
        assert result["contents"][2]["role"] == "user"

    def test_system_prompt(self):
        """Test translating system prompt."""
        anthropic_req = {
            "model": "claude-opus",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hi"}],
            "system": "You are a helpful assistant.",
        }

        result = self.translator.translate_request(anthropic_req)

        assert "systemInstruction" in result
        assert result["systemInstruction"]["role"] == "user"
        assert len(result["systemInstruction"]["parts"]) > 0

    def test_temperature_and_top_p(self):
        """Test translating generation parameters."""
        anthropic_req = {
            "model": "claude-opus",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hi"}],
            "temperature": 0.7,
            "top_p": 0.9,
        }

        result = self.translator.translate_request(anthropic_req)

        assert result["generationConfig"]["temperature"] == 0.7
        assert result["generationConfig"]["topP"] == 0.9

    def test_image_content(self):
        """Test translating image content blocks."""
        anthropic_req = {
            "model": "claude-opus",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                            },
                        },
                        {"type": "text", "text": "What's in this image?"},
                    ],
                }
            ],
        }

        result = self.translator.translate_request(anthropic_req)

        # Should have created a Gemini message with image parts
        assert len(result["contents"]) > 0
        parts = result["contents"][0]["parts"]
        assert any("inlineData" in part for part in parts)

    def test_stop_sequences(self):
        """Test translating stop sequences."""
        anthropic_req = {
            "model": "claude-opus",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hi"}],
            "stop_sequences": ["STOP", "END"],
        }

        result = self.translator.translate_request(anthropic_req)

        assert "stopSequences" in result["generationConfig"]
        assert result["generationConfig"]["stopSequences"] == ["STOP", "END"]


class TestGeminiToAnthropicResponse:
    """Test Gemini response to Anthropic translation."""

    def setup_method(self):
        self.translator = GeminiToAnthropicTranslator(logger)

    def test_simple_text_response(self):
        """Test translating a simple text response."""
        gemini_resp = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Hello! I'm doing well, thank you for asking."}]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "inputTokenCount": 10,
                "outputTokenCount": 15,
            },
        }

        result = self.translator.translate_response(gemini_resp)

        assert result["role"] == "assistant"
        assert len(result["content"]) > 0
        assert result["content"][0]["type"] == "text"
        assert "Hello" in result["content"][0]["text"]
        assert result["usage"]["input_tokens"] == 10
        assert result["usage"]["output_tokens"] == 15
        assert result["stop_reason"] == "end_turn"

    def test_max_tokens_stop_reason(self):
        """Test translating MAX_TOKENS stop reason."""
        gemini_resp = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "This is a long response..."}]},
                    "finishReason": "MAX_TOKENS",
                }
            ],
            "usageMetadata": {
                "inputTokenCount": 5,
                "outputTokenCount": 100,
            },
        }

        result = self.translator.translate_response(gemini_resp)

        assert result["stop_reason"] == "max_tokens"

    def test_streaming_chunk(self):
        """Test translating a streaming chunk."""
        chunk = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Hello"}]},
                    "index": 0,
                }
            ]
        }

        result = self.translator.translate_streaming_chunk(chunk)

        assert result is not None
        # Result should be a list of events
        if isinstance(result, list):
            assert len(result) > 0
        else:
            assert "event" in result
