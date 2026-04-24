#!/usr/bin/env python3
"""
Standalone compatibility server for:
  POST /v1/images/generations
  POST /v1/images/edits
  POST /v1/chat/completions

This file is isolated from the rest of the project.

Quick start:
  pip install fastapi uvicorn httpx python-multipart
  set OPENAI_API_KEY=sk-...
  uvicorn src.standalone_tools.image_api_proxy_server:app --host 0.0.0.0 --port 8000

Optional env vars:
  OPENAI_BASE_URL=https://api.openai.com
  UPSTREAM_IMAGE_MODEL=gpt-image-1.5
  LOCAL_API_KEY=your-local-proxy-key
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from typing import Any, Dict, Iterable, Optional, Tuple

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse


OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")
UPSTREAM_IMAGE_MODEL = os.getenv("UPSTREAM_IMAGE_MODEL", "gpt-image-1.5")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LOCAL_API_KEY = os.getenv("LOCAL_API_KEY", "")

MODEL_ALIASES = {
    "gpt-image-2": UPSTREAM_IMAGE_MODEL,
}

JSON_FORWARD_FIELDS = (
    "background",
    "input_fidelity",
    "moderation",
    "n",
    "output_compression",
    "output_format",
    "quality",
    "size",
    "style",
    "user",
)

app = FastAPI(title="Image API Compatibility Server", version="1.0.0")


def require_upstream_api_key() -> None:
    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Missing OPENAI_API_KEY environment variable on the server.",
        )


def check_local_auth(request: Request) -> None:
    if not LOCAL_API_KEY:
        return

    auth = request.headers.get("authorization", "")
    expected = f"Bearer {LOCAL_API_KEY}"
    if auth != expected:
        raise HTTPException(status_code=401, detail="Invalid local API key.")


def resolve_model(requested_model: Optional[str]) -> str:
    if not requested_model:
        return UPSTREAM_IMAGE_MODEL
    return MODEL_ALIASES.get(requested_model, requested_model)


def pick_fields(source: Dict[str, Any], field_names: Iterable[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for name in field_names:
        value = source.get(name)
        if value is not None:
            result[name] = value
    return result


def detect_mime_type_from_b64(image_b64: str) -> str:
    if image_b64.startswith("/9j/"):
        return "image/jpeg"
    if image_b64.startswith("UklGR"):
        return "image/webp"
    return "image/png"


def parse_data_url(value: str) -> Tuple[str, bytes]:
    if not value.startswith("data:") or ";base64," not in value:
        raise HTTPException(status_code=400, detail="Only base64 data URLs are supported.")

    header, encoded = value.split(",", 1)
    mime_type = header[5:].split(";")[0] or "image/png"
    try:
        content = base64.b64decode(encoded)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64 image data: {exc}") from exc
    return mime_type, content


def normalize_image_response(
    upstream_response: Dict[str, Any],
    fallback_prompt: str,
) -> Dict[str, Any]:
    data = upstream_response.get("data") or []
    normalized = []
    for item in data:
        normalized.append(
            {
                "b64_json": item.get("b64_json"),
                "revised_prompt": item.get("revised_prompt") or fallback_prompt,
            }
        )

    return {
        "created": upstream_response.get("created", int(time.time())),
        "data": normalized,
    }


def extract_message_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                return message
        if isinstance(messages[-1], dict):
            return messages[-1]

    if "role" in payload and "content" in payload:
        return payload

    raise HTTPException(status_code=400, detail="Missing user message content.")


def extract_prompt_and_image(message: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    content = message.get("content")
    if isinstance(content, str):
        return content, None

    if not isinstance(content, list):
        raise HTTPException(status_code=400, detail="message.content must be a string or an array.")

    prompt_parts = []
    image_url: Optional[str] = None

    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text":
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                prompt_parts.append(text.strip())
        elif part_type == "image_url":
            image_obj = part.get("image_url") or {}
            url = image_obj.get("url")
            if isinstance(url, str) and url:
                image_url = url

    prompt = "\n".join(prompt_parts).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="No text prompt found in message.content.")
    return prompt, image_url


async def call_openai_json(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    require_upstream_api_key()
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json; charset=utf-8",
    }
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            f"{OPENAI_BASE_URL}{path}",
            headers=headers,
            json=payload,
        )

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


async def call_openai_multipart(
    path: str,
    data: Dict[str, Any],
    files: Dict[str, Tuple[str, bytes, str]],
) -> Dict[str, Any]:
    require_upstream_api_key()
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            f"{OPENAI_BASE_URL}{path}",
            headers=headers,
            data=data,
            files=files,
        )

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/images/generations")
async def create_image(request: Request) -> JSONResponse:
    check_local_auth(request)
    payload = await request.json()

    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required.")

    upstream_payload = {
        "model": resolve_model(payload.get("model")),
        "prompt": prompt,
        "response_format": "b64_json",
    }
    upstream_payload.update(pick_fields(payload, JSON_FORWARD_FIELDS))

    upstream_response = await call_openai_json("/v1/images/generations", upstream_payload)
    return JSONResponse(normalize_image_response(upstream_response, prompt))


@app.post("/v1/images/edits")
async def edit_image(request: Request) -> JSONResponse:
    check_local_auth(request)
    form = await request.form()

    prompt = form.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required.")

    image = form.get("image")
    if not isinstance(image, UploadFile):
        raise HTTPException(status_code=400, detail="image file is required.")

    model = resolve_model(form.get("model") if isinstance(form.get("model"), str) else None)
    image_bytes = await image.read()
    content_type = image.content_type or "application/octet-stream"

    data = {
        "model": model,
        "prompt": prompt,
        "response_format": "b64_json",
    }

    for key in JSON_FORWARD_FIELDS:
        value = form.get(key)
        if isinstance(value, str) and value != "":
            data[key] = value

    upstream_response = await call_openai_multipart(
        "/v1/images/edits",
        data=data,
        files={
            "image": (image.filename or "image.png", image_bytes, content_type),
        },
    )
    return JSONResponse(normalize_image_response(upstream_response, prompt))


@app.post("/v1/chat/completions")
async def compatible_chat_completions(request: Request) -> JSONResponse:
    check_local_auth(request)
    payload = await request.json()

    requested_model = payload.get("model")
    resolved_model = resolve_model(requested_model)
    user_message = extract_message_payload(payload)
    prompt, image_url = extract_prompt_and_image(user_message)

    if image_url:
        if image_url.startswith("data:"):
            mime_type, image_bytes = parse_data_url(image_url)
            upstream_response = await call_openai_multipart(
                "/v1/images/edits",
                data={
                    "model": resolved_model,
                    "prompt": prompt,
                    "response_format": "b64_json",
                },
                files={
                    "image": ("chat-input", image_bytes, mime_type),
                },
            )
        else:
            upstream_response = await call_openai_json(
                "/v1/images/edits",
                {
                    "model": resolved_model,
                    "prompt": prompt,
                    "response_format": "b64_json",
                    "images": [{"image_url": image_url}],
                },
            )
    else:
        upstream_response = await call_openai_json(
            "/v1/images/generations",
            {
                "model": resolved_model,
                "prompt": prompt,
                "response_format": "b64_json",
            },
        )

    normalized = normalize_image_response(upstream_response, prompt)
    data_item = (normalized.get("data") or [{}])[0]
    image_b64 = data_item.get("b64_json")
    if not image_b64:
        raise HTTPException(status_code=502, detail="Upstream response did not include b64_json.")

    mime_type = detect_mime_type_from_b64(image_b64)
    markdown_image = f"![image_1](data:{mime_type};base64,{image_b64})"

    return JSONResponse(
        {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": requested_model or "gpt-image-2",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": markdown_image,
                    },
                    "finish_reason": "stop",
                }
            ],
        }
    )
