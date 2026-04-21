"""FastAPI proxy server that translates Anthropic API calls to Gemini."""

import asyncio
import base64
import json
import logging
import os
import sys
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from google import genai
from pydantic import BaseModel, Field
from typing import Optional, Any

from gemclaw import GEMINI_MODEL, ANTHROPIC_VERSION, TIMEOUT_SECONDS, MAX_RETRIES
from gemclaw.translator import AnthropicToGeminiTranslator, GeminiToAnthropicTranslator
from gemclaw.utils import log_debug_json

logger = logging.getLogger("gemclaw.proxy")
logger.propagate = False

_proxy_log_path = os.environ.get("GEMCLAW_PROXY_LOG")
if _proxy_log_path:
    _fh = logging.FileHandler(_proxy_log_path)
    _fh.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(_fh)
    logger.setLevel(logging.DEBUG)
else:
    logger.addHandler(logging.NullHandler())


class MessageRequest(BaseModel):
    """Anthropic message request schema (partial)."""

    model: str
    max_tokens: int
    messages: list
    system: Optional[Any] = None
    tools: Optional[list] = None
    tool_choice: Optional[dict] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    stop_sequences: Optional[list] = None
    stream: Optional[bool] = False
    thinking: Optional[dict] = None


def create_app(cli_logger: logging.Logger = None) -> FastAPI:
    """Create and configure FastAPI application."""

    # Use provided logger or the module-level logger
    active_logger = cli_logger if cli_logger else logger

    app = FastAPI(title="GemClaw")
    anthropic_to_gemini = AnthropicToGeminiTranslator(active_logger)
    gemini_to_anthropic = GeminiToAnthropicTranslator(active_logger)

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "ok", "service": "gemproxy"}

    @app.post("/v1/messages")
    async def messages(request: Request):
        """Proxy for Anthropic /v1/messages endpoint."""
        try:
            # Parse request
            body = await request.json()
            logger.debug(f"Received Anthropic request for model: {body.get('model')}")
            log_debug_json(logger, "Request body", body)

            # Verify required fields
            if "messages" not in body:
                raise HTTPException(status_code=400, detail="Missing 'messages' field")
            if "max_tokens" not in body:
                raise HTTPException(status_code=400, detail="Missing 'max_tokens' field")

            # Override model to gemini-3.1-pro-preview
            original_model = body.get("model", "unknown")
            body["model"] = GEMINI_MODEL
            logger.info(f"Model override: {original_model} → {GEMINI_MODEL}")

            # Translate to Gemini format
            gemini_request = anthropic_to_gemini.translate_request(body)
            log_debug_json(logger, "Translated to Gemini format", gemini_request)

            # Call Gemini API
            google_api_key = os.environ.get("GEMINI_API_KEY")
            if not google_api_key:
                raise HTTPException(
                    status_code=500,
                    detail="GEMINI_API_KEY not set in proxy environment",
                )

            # Check if streaming
            is_streaming = body.get("stream", False)

            if is_streaming:
                return await _handle_streaming(
                    gemini_request,
                    google_api_key,
                    gemini_to_anthropic,
                    logger,
                )
            else:
                return await _handle_non_streaming(
                    gemini_request,
                    google_api_key,
                    gemini_to_anthropic,
                    logger,
                )

        except HTTPException:
            raise
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in request body")
        except Exception as e:
            logger.error(f"Error processing request: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

    return app


def _build_config(gemini_request: dict) -> Optional[Any]:
    """Build GenerateContentConfig from the translated gemini_request."""
    config_kwargs: dict = dict(gemini_request.get("generationConfig", {}) or {})

    system_instruction = gemini_request.get("systemInstruction")
    if system_instruction:
        parts = system_instruction.get("parts", []) if isinstance(system_instruction, dict) else []
        text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text"))
        if text:
            config_kwargs["system_instruction"] = text

    tools = gemini_request.get("tools")
    if tools:
        config_kwargs["tools"] = tools

    if not config_kwargs:
        return None
    return genai.types.GenerateContentConfig(**config_kwargs)


def _encode_signature(signature: Any) -> Optional[str]:
    if signature is None:
        return None
    if isinstance(signature, (bytes, bytearray)):
        return base64.b64encode(signature).decode("ascii")
    return signature


def _part_to_dict(part: Any) -> dict:
    """Convert a google.genai response Part to our internal dict format.

    Gemini 3.x returns reasoning/thinking parts with `part.thought == True`.
    These are not assistant-visible text — they're the model's internal
    reasoning and must not leak into Claude Code's rendered output. We drop
    their text but still return the signature so the caller can preserve it
    on adjacent function calls.
    """
    is_thought = bool(getattr(part, "thought", False))
    signature = _encode_signature(getattr(part, "thought_signature", None))

    fn_call = getattr(part, "function_call", None)
    if fn_call is not None:
        args = getattr(fn_call, "args", None) or {}
        if hasattr(args, "items"):
            args = dict(args)
        out: dict = {
            "functionCall": {
                "name": getattr(fn_call, "name", "") or "",
                "args": args,
            }
        }
        if signature:
            out["thoughtSignature"] = signature
        return out

    if is_thought:
        return {"_thoughtSignature": signature} if signature else {}

    text = getattr(part, "text", None)
    if text:
        out = {"text": text}
        if signature:
            out["thoughtSignature"] = signature
        return out

    return {}


async def _handle_non_streaming(
    gemini_request: dict,
    google_api_key: str,
    translator: GeminiToAnthropicTranslator,
    logger: logging.Logger,
) -> dict:
    """Handle non-streaming Gemini API call."""
    try:
        logger.debug(f"Calling Gemini API via SDK...")

        client = genai.Client(api_key=google_api_key)

        contents = gemini_request.get("contents", [])
        kwargs: dict = {"model": GEMINI_MODEL, "contents": contents}
        config = _build_config(gemini_request)
        if config is not None:
            kwargs["config"] = config

        response = client.models.generate_content(**kwargs)
        logger.debug("Received response from Gemini API")

        candidate = response.candidates[0]
        parts_out = [p for p in (_part_to_dict(part) for part in candidate.content.parts) if p]

        gemini_response = {
            "candidates": [
                {
                    "content": {
                        "role": candidate.content.role,
                        "parts": parts_out,
                    },
                    "finishReason": candidate.finish_reason.name if candidate.finish_reason else "STOP",
                }
            ],
            "usageMetadata": {
                "inputTokenCount": response.usage_metadata.prompt_token_count,
                "outputTokenCount": response.usage_metadata.candidates_token_count,
            },
        }

        # Translate response
        anthropic_response = translator.translate_response(gemini_response)
        log_debug_json(logger, "Translated response", anthropic_response)

        logger.info(
            f"Request complete. Usage: {anthropic_response['usage']['input_tokens']} "
            f"input, {anthropic_response['usage']['output_tokens']} output"
        )

        return anthropic_response

    except Exception as e:
        logger.error(f"Error calling Gemini API: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Gemini API error: {str(e)}")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _handle_streaming(
    gemini_request: dict,
    google_api_key: str,
    translator: GeminiToAnthropicTranslator,
    logger: logging.Logger,
):
    """Stream a Gemini call back as Anthropic SSE events.

    Runs Gemini's real streaming endpoint in a worker thread and forwards
    chunks onto an async queue. The generator emits `ping` events every 10s
    while idle to keep the HTTP connection alive during long thinking steps.

    State machine: Gemini returns interleaved text / thinking / function_call
    parts. We open one text content block and append deltas to it; each
    function_call becomes its own tool_use content block. Thought parts are
    filtered out of user-visible output but their signatures are attached to
    the following function call so replays work on the next turn.
    """
    import uuid as _uuid

    async def generate():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def worker():
            try:
                client = genai.Client(api_key=google_api_key)
                contents = gemini_request.get("contents", [])
                kwargs: dict = {"model": GEMINI_MODEL, "contents": contents}
                config = _build_config(gemini_request)
                if config is not None:
                    kwargs["config"] = config

                stream = client.models.generate_content_stream(**kwargs)
                for chunk in stream:
                    loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", e))

        asyncio.get_event_loop().run_in_executor(None, worker)

        message_id = f"msg-{_uuid.uuid4().hex[:12]}"
        input_tokens = 0
        output_tokens = 0

        current_text_index: Optional[int] = None
        next_block_index = 0
        pending_signature: Optional[str] = None
        message_started = False
        final_stop_reason = "end_turn"
        emitted_tool_use = False

        try:
            while True:
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    yield _sse("ping", {"type": "ping"})
                    continue

                if kind == "error":
                    raise payload

                if kind == "done":
                    break

                chunk = payload
                if not chunk.candidates:
                    continue
                cand = chunk.candidates[0]

                if not message_started:
                    usage_in = 0
                    if chunk.usage_metadata is not None:
                        usage_in = chunk.usage_metadata.prompt_token_count or 0
                    input_tokens = usage_in
                    yield _sse("message_start", {
                        "type": "message_start",
                        "message": {
                            "id": message_id,
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": "gemini-3.1-pro-preview",
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": input_tokens, "output_tokens": 0},
                        },
                    })
                    yield _sse("ping", {"type": "ping"})
                    message_started = True

                if cand.content and cand.content.parts:
                    for part in cand.content.parts:
                        is_thought = bool(getattr(part, "thought", False))
                        sig = _encode_signature(getattr(part, "thought_signature", None))
                        if sig:
                            pending_signature = sig

                        fn_call = getattr(part, "function_call", None)
                        if fn_call is not None:
                            if current_text_index is not None:
                                yield _sse("content_block_stop", {
                                    "type": "content_block_stop",
                                    "index": current_text_index,
                                })
                                current_text_index = None

                            tool_id = f"toolu_{_uuid.uuid4().hex[:12]}"
                            name = getattr(fn_call, "name", "") or ""
                            args = getattr(fn_call, "args", None) or {}
                            if hasattr(args, "items"):
                                args = dict(args)

                            from gemclaw.translator import TOOL_CALL_CACHE, _persist_cache
                            TOOL_CALL_CACHE[tool_id] = {
                                "name": name,
                                "args": args,
                                "signature": pending_signature,
                            }
                            _persist_cache()
                            pending_signature = None

                            idx = next_block_index
                            next_block_index += 1
                            emitted_tool_use = True

                            yield _sse("content_block_start", {
                                "type": "content_block_start",
                                "index": idx,
                                "content_block": {
                                    "type": "tool_use",
                                    "id": tool_id,
                                    "name": name,
                                    "input": {},
                                },
                            })
                            yield _sse("content_block_delta", {
                                "type": "content_block_delta",
                                "index": idx,
                                "delta": {
                                    "type": "input_json_delta",
                                    "partial_json": json.dumps(args),
                                },
                            })
                            yield _sse("content_block_stop", {
                                "type": "content_block_stop",
                                "index": idx,
                            })
                            continue

                        if is_thought:
                            continue

                        text = getattr(part, "text", None)
                        if text:
                            if current_text_index is None:
                                current_text_index = next_block_index
                                next_block_index += 1
                                yield _sse("content_block_start", {
                                    "type": "content_block_start",
                                    "index": current_text_index,
                                    "content_block": {"type": "text", "text": ""},
                                })
                            yield _sse("content_block_delta", {
                                "type": "content_block_delta",
                                "index": current_text_index,
                                "delta": {"type": "text_delta", "text": text},
                            })

                if cand.finish_reason:
                    final_stop_reason = {
                        "STOP": "tool_use" if emitted_tool_use else "end_turn",
                        "MAX_TOKENS": "max_tokens",
                    }.get(cand.finish_reason.name, "end_turn")

                if chunk.usage_metadata is not None:
                    if chunk.usage_metadata.candidates_token_count is not None:
                        output_tokens = chunk.usage_metadata.candidates_token_count or 0

            if current_text_index is not None:
                yield _sse("content_block_stop", {
                    "type": "content_block_stop",
                    "index": current_text_index,
                })
                current_text_index = None

            if not message_started:
                yield _sse("message_start", {
                    "type": "message_start",
                    "message": {
                        "id": message_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": "gemini-3.1-pro-preview",
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                })

            yield _sse("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": final_stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            })
            yield _sse("message_stop", {"type": "message_stop"})

            logger.info(
                f"Stream complete. Usage: {input_tokens} input, {output_tokens} output, stop={final_stop_reason}"
            )

        except Exception as e:
            logger.error(f"Error in streaming: {e}", exc_info=True)
            yield _sse("error", {
                "type": "error",
                "error": {"type": "internal_error", "message": str(e)},
            })

    return StreamingResponse(generate(), media_type="text/event-stream")


# Create module-level app instance for uvicorn
app = create_app(logger)
