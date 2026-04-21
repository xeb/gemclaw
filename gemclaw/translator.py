"""Translate between Anthropic and Gemini API formats."""

import json
import logging
import base64
import os
import uuid
from pathlib import Path
from typing import Any, Optional
from datetime import datetime


# Persistent cache: tool_use_id -> {"name": str, "args": dict, "signature": Optional[str]}
# Gemini 3.x requires `thought_signature` to be echoed back on function call replays.
# Claude Code only round-trips Anthropic-native fields, so we stash the signature
# here keyed by the tool_use id we emitted. Persisted to disk so Claude Code
# session resumes (`claude --resume <id>`) can replay tool history across
# gemclaw process restarts.
_CACHE_PATH = Path.home() / ".gemproxy" / "tool_call_cache.json"
_CACHE_MAX_ENTRIES = 5000


def _load_cache() -> dict[str, dict]:
    try:
        if _CACHE_PATH.exists():
            with _CACHE_PATH.open("r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _persist_cache() -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if len(TOOL_CALL_CACHE) > _CACHE_MAX_ENTRIES:
            # Drop oldest half (insertion-ordered dict)
            keys = list(TOOL_CALL_CACHE.keys())
            for k in keys[: len(keys) - _CACHE_MAX_ENTRIES // 2]:
                TOOL_CALL_CACHE.pop(k, None)
        tmp = _CACHE_PATH.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(TOOL_CALL_CACHE, f)
        os.replace(tmp, _CACHE_PATH)
    except Exception:
        pass


TOOL_CALL_CACHE: dict[str, dict] = _load_cache()


class AnthropicToGeminiTranslator:
    """Convert Anthropic Messages API format to Gemini API format."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def translate_request(self, anthropic_request: dict) -> dict:
        """Translate Anthropic /v1/messages request to Gemini format."""
        gemini_request = {}

        # Model
        gemini_request["model"] = anthropic_request.get("model", "gemini-3.1-pro-preview")

        # Generation config
        gen_config = {
            "maxOutputTokens": anthropic_request.get("max_tokens", 1024),
        }

        if "temperature" in anthropic_request:
            gen_config["temperature"] = anthropic_request["temperature"]
        if "top_p" in anthropic_request:
            gen_config["topP"] = anthropic_request["top_p"]
        if "top_k" in anthropic_request:
            gen_config["topK"] = anthropic_request["top_k"]
        if "stop_sequences" in anthropic_request:
            gen_config["stopSequences"] = anthropic_request["stop_sequences"]

        gemini_request["generationConfig"] = gen_config

        # System instruction
        system = anthropic_request.get("system")
        if system:
            if isinstance(system, str):
                gemini_request["systemInstruction"] = {
                    "role": "user",
                    "parts": [{"text": system}],
                }
            elif isinstance(system, list):
                parts = []
                for item in system:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append({"text": item["text"]})
                if parts:
                    gemini_request["systemInstruction"] = {
                        "role": "user",
                        "parts": parts,
                    }

        # Messages
        gemini_request["contents"] = self._translate_messages(anthropic_request.get("messages", []))

        # Tools
        if "tools" in anthropic_request:
            tools_dict = self._translate_tools(anthropic_request["tools"])
            if tools_dict:
                gemini_request["tools"] = tools_dict

        # Streaming
        if anthropic_request.get("stream"):
            gemini_request["stream"] = True

        return gemini_request

    def _translate_messages(self, messages: list) -> list:
        """Convert Anthropic messages to Gemini contents format."""
        tool_id_to_name = {}
        for msg in messages:
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tid = block.get("id")
                        name = block.get("name")
                        if tid and name:
                            tool_id_to_name[tid] = name

        gemini_contents = []

        for msg in messages:
            role = msg.get("role", "user")
            gemini_role = "user" if role == "user" else "model"
            content = msg.get("content", [])

            if isinstance(content, str):
                gemini_contents.append({
                    "role": gemini_role,
                    "parts": [{"text": content}],
                })
            elif isinstance(content, list):
                parts = self._translate_content_blocks(content, tool_id_to_name)
                if parts:
                    gemini_contents.append({
                        "role": gemini_role,
                        "parts": parts,
                    })

        return gemini_contents

    def _translate_content_blocks(self, blocks: list, tool_id_to_name: dict = None) -> list:
        """Convert Anthropic content blocks to Gemini parts."""
        if tool_id_to_name is None:
            tool_id_to_name = {}

        parts = []

        for block in blocks:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type")

            if block_type == "text":
                parts.append({"text": block["text"]})

            elif block_type == "image":
                image_part = self._translate_image(block)
                if image_part:
                    parts.append(image_part)

            elif block_type == "tool_use":
                tool_id = block.get("id")
                function_call = {
                    "name": block.get("name", "unknown"),
                    "args": block.get("input", {}) or {},
                }
                part: dict = {"functionCall": function_call}
                cached = TOOL_CALL_CACHE.get(tool_id) if tool_id else None
                if cached and cached.get("signature"):
                    part["thoughtSignature"] = cached["signature"]
                parts.append(part)

            elif block_type == "tool_result":
                tool_use_id = block.get("tool_use_id", "")
                tool_name = tool_id_to_name.get(tool_use_id, tool_use_id or "unknown")

                content_data = block.get("content", "")
                if isinstance(content_data, str):
                    response_payload = {"result": content_data}
                elif isinstance(content_data, list):
                    text_chunks = []
                    for item in content_data:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_chunks.append(item.get("text", ""))
                    response_payload = {"result": "\n".join(text_chunks)}
                else:
                    response_payload = {"result": ""}

                if block.get("is_error"):
                    response_payload["error"] = True

                parts.append({
                    "functionResponse": {
                        "name": tool_name,
                        "response": response_payload,
                    }
                })

        return parts

    def _translate_image(self, image_block: dict) -> Optional[dict]:
        """Convert image content block to Gemini format."""
        source = image_block.get("source", {})
        source_type = source.get("type")

        if source_type == "base64":
            media_type = source.get("media_type", "image/jpeg")
            data = source.get("data", "")
            return {
                "inlineData": {
                    "mimeType": media_type,
                    "data": data,
                }
            }
        elif source_type == "url":
            url = source.get("url", "")
            return {
                "fileData": {
                    "mimeType": source.get("media_type", "image/jpeg"),
                    "fileUri": url,
                }
            }

        return None

    def _translate_tools(self, tools: list) -> list:
        """Convert Anthropic tools to Gemini function declarations.

        Anthropic format (flat):
            {"name": "...", "description": "...", "input_schema": {...}}

        Returns a list of Tool objects in Gemini format:
            [{"function_declarations": [{name, description, parameters}, ...]}]
        """
        if not tools:
            return []

        tool_functions = []

        for tool in tools:
            if not isinstance(tool, dict):
                continue

            if "name" in tool:
                name = tool.get("name", "unknown")
                description = tool.get("description", "")
                parameters = tool.get("input_schema", {})
            elif tool.get("type") == "function" and "function" in tool:
                func = tool["function"]
                name = func.get("name", "unknown")
                description = func.get("description", "")
                parameters = func.get("input_schema") or func.get("parameters", {})
            else:
                continue

            gemini_func = {
                "name": name,
                "description": description,
            }

            if parameters:
                translated = self._translate_parameters(parameters)
                if translated.get("properties") or translated.get("type"):
                    gemini_func["parameters"] = translated

            tool_functions.append(gemini_func)

        if not tool_functions:
            return []

        return [{"function_declarations": tool_functions}]

    _JSON_TO_GEMINI_TYPE = {
        "string": "STRING",
        "number": "NUMBER",
        "integer": "INTEGER",
        "boolean": "BOOLEAN",
        "array": "ARRAY",
        "object": "OBJECT",
    }

    def _translate_parameters(self, schema: dict) -> dict:
        """Convert JSON schema to Gemini Schema format.

        Gemini requires uppercase type enum values (OBJECT, STRING, etc.) and
        doesn't support JSON Schema keywords like $schema, additionalProperties,
        oneOf/anyOf, etc.
        """
        if not isinstance(schema, dict):
            return {}

        gemini_schema = {}

        raw_type = schema.get("type")
        if isinstance(raw_type, list):
            raw_type = next((t for t in raw_type if t != "null"), None)
        if raw_type:
            gemini_schema["type"] = self._JSON_TO_GEMINI_TYPE.get(raw_type, raw_type.upper())

        if "description" in schema:
            gemini_schema["description"] = schema["description"]

        if "enum" in schema:
            gemini_schema["enum"] = [str(v) for v in schema["enum"]]
            gemini_schema.setdefault("type", "STRING")

        if "properties" in schema and isinstance(schema["properties"], dict):
            props = {}
            for prop_name, prop_schema in schema["properties"].items():
                translated = self._translate_parameters(prop_schema)
                if translated:
                    props[prop_name] = translated
            if props:
                gemini_schema["properties"] = props
                gemini_schema.setdefault("type", "OBJECT")

        if "required" in schema and isinstance(schema["required"], list):
            gemini_schema["required"] = list(schema["required"])

        if "items" in schema:
            items_schema = self._translate_parameters(schema["items"])
            if items_schema:
                gemini_schema["items"] = items_schema
                gemini_schema.setdefault("type", "ARRAY")

        return gemini_schema


class GeminiToAnthropicTranslator:
    """Convert Gemini API responses to Anthropic format."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def translate_response(self, gemini_response: dict) -> dict:
        """Convert Gemini response to Anthropic message format."""
        message_id = f"msg-{uuid.uuid4().hex[:12]}"
        content = []
        stop_reason = "end_turn"

        if "candidates" in gemini_response and gemini_response["candidates"]:
            candidate = gemini_response["candidates"][0]

            # Get stop reason
            finish_reason = candidate.get("finishReason", "STOP")
            stop_reason = self._map_stop_reason(finish_reason)

            if "content" in candidate and "parts" in candidate["content"]:
                for part in candidate["content"]["parts"]:
                    if "text" in part:
                        content.append({
                            "type": "text",
                            "text": part["text"],
                        })
                    elif "functionCall" in part:
                        fc = part["functionCall"]
                        tool_id = f"toolu_{uuid.uuid4().hex[:12]}"
                        name = fc.get("name", "")
                        args = fc.get("args", {}) or {}
                        signature = part.get("thoughtSignature")
                        TOOL_CALL_CACHE[tool_id] = {
                            "name": name,
                            "args": args,
                            "signature": signature,
                        }
                        _persist_cache()
                        content.append({
                            "type": "tool_use",
                            "id": tool_id,
                            "name": name,
                            "input": args,
                        })

        if any(block.get("type") == "tool_use" for block in content):
            stop_reason = "tool_use"

        usage = {"input_tokens": 0, "output_tokens": 0}
        if "usageMetadata" in gemini_response:
            meta = gemini_response["usageMetadata"]
            usage["input_tokens"] = meta.get("inputTokenCount", 0)
            usage["output_tokens"] = meta.get("outputTokenCount", 0)

        return {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": content if content else [{"type": "text", "text": ""}],
            "model": "gemini-3.1-pro-preview",
            "stop_reason": stop_reason,
            "usage": usage,
        }

    def translate_streaming_chunk(self, chunk_data: dict) -> Optional[list]:
        """Convert a Gemini streaming chunk to Anthropic SSE events.

        Returns list of dicts with 'event' and 'data' keys for SSE format.
        """
        if not chunk_data or "candidates" not in chunk_data:
            return None

        candidates = chunk_data.get("candidates", [])
        if not candidates:
            return None

        candidate = candidates[0]
        events = []

        # Extract content parts
        content_parts = []
        if "content" in candidate and "parts" in candidate["content"]:
            content_parts = candidate["content"]["parts"]

        # Emit content block events
        for part in content_parts:
            if "text" in part and part["text"]:
                # Text content
                events.append({
                    "event": "content_block_start",
                    "data": {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text"},
                    },
                })
                events.append({
                    "event": "content_block_delta",
                    "data": {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "text_delta",
                            "text": part["text"],
                        },
                    },
                })
                events.append({
                    "event": "content_block_stop",
                    "data": {"type": "content_block_stop", "index": 0},
                })

            elif "functionCall" in part:
                fc = part["functionCall"]
                tool_id = f"toolu_{uuid.uuid4().hex[:12]}"
                name = fc.get("name", "")
                args = fc.get("args", {}) or {}
                TOOL_CALL_CACHE[tool_id] = {
                    "name": name,
                    "args": args,
                    "signature": part.get("thoughtSignature"),
                }
                _persist_cache()
                events.append({
                    "event": "content_block_start",
                    "data": {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": name,
                            "input": {},
                        },
                    },
                })
                events.append({
                    "event": "content_block_delta",
                    "data": {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(args),
                        },
                    },
                })
                events.append({
                    "event": "content_block_stop",
                    "data": {"type": "content_block_stop", "index": 0},
                })

        # Message delta with stop reason
        if candidate.get("finishReason"):
            stop_reason = self._map_stop_reason(candidate["finishReason"])
            events.append({
                "event": "message_delta",
                "data": {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason},
                    "usage": {
                        "output_tokens": chunk_data.get("usageMetadata", {}).get("outputTokenCount", 0),
                    },
                },
            })

        # Message stop
        if candidate.get("finishReason"):
            events.append({
                "event": "message_stop",
                "data": {"type": "message_stop"},
            })

        return events if events else None

    def _map_stop_reason(self, finish_reason: str) -> str:
        """Map Gemini finish reason to Anthropic stop_reason."""
        mapping = {
            "STOP": "end_turn",
            "MAX_TOKENS": "max_tokens",
            "SAFETY": "end_turn",
            "RECITATION": "end_turn",
        }
        return mapping.get(finish_reason, "end_turn")
