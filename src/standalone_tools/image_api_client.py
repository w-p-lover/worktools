#!/usr/bin/env python3
"""
Standalone client for:
1. POST /v1/images/generations
2. POST /v1/images/edits
3. POST /v1/chat/completions

This file is intentionally isolated from the existing project so it can be used
without affecting current functionality.
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import mimetypes
import os
import re
import ssl
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlsplit


DEFAULT_MODEL = os.getenv("IMAGE_API_MODEL", "gpt-image-2")
DEFAULT_OUTPUT_DIR = Path.cwd() / "image_api_outputs"


def load_dotenv_if_exists() -> None:
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def fail(message: str, exit_code: int = 1) -> None:
    print(f"错误: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def ensure_base_url(value: Optional[str]) -> str:
    base_url = value or os.getenv("IMAGE_API_BASE_URL")
    if not base_url:
        fail("缺少 base URL，请通过 --base-url 或 .env/环境变量 IMAGE_API_BASE_URL 提供")
    return base_url.rstrip("/")


def ensure_api_key(value: Optional[str]) -> str:
    api_key = value or os.getenv("IMAGE_API_KEY")
    if not api_key:
        fail("缺少 API key，请通过 --api-key 或 .env/环境变量 IMAGE_API_KEY 提供")
    return api_key


def ensure_image(path_value: Optional[str]) -> Path:
    if not path_value:
        fail("缺少 --image 参数")
    image_path = Path(path_value).expanduser().resolve()
    if not image_path.exists():
        fail(f"图片文件不存在: {image_path}")
    return image_path


def request_json(
    url: str,
    method: str,
    headers: Dict[str, str],
    data: bytes,
    insecure: bool = False,
) -> dict:
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    if parsed.scheme == "https":
        context = ssl._create_unverified_context() if insecure else None
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port or 443,
            timeout=120,
            context=context,
        )
    elif parsed.scheme == "http":
        connection = http.client.HTTPConnection(
            parsed.hostname,
            parsed.port or 80,
            timeout=120,
        )
    else:
        fail(f"不支持的协议: {parsed.scheme}")

    try:
        connection.request(method=method, url=path, body=data, headers=headers)
        response = connection.getresponse()
        status = response.status
        body = response.read().decode("utf-8", errors="replace")
    except ssl.SSLError as exc:
        fail(f"HTTPS 证书校验失败: {exc}。如果是内网自签名证书，可以追加 --insecure")
    except OSError as exc:
        fail(f"请求失败，网络错误: {exc}")
    finally:
        connection.close()

    if status < 200 or status >= 300:
        fail(f"请求失败，状态码 {status}，响应内容: {body}")

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        fail(f"接口返回不是合法 JSON: {body}")
    return {}


def guess_extension_from_base64(image_b64: str) -> str:
    if image_b64.startswith("/9j/"):
        return ".jpg"
    if image_b64.startswith("UklGR"):
        return ".webp"
    return ".png"


def build_output_path(output_dir: Path, filename: Optional[str], image_b64: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ext = guess_extension_from_base64(image_b64)
    if filename:
        name = filename if Path(filename).suffix else f"{filename}{ext}"
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"image_{stamp}_{uuid.uuid4().hex[:8]}{ext}"
    return output_dir / name


def save_base64_image(image_b64: str, output_dir: Path, filename: Optional[str]) -> Path:
    output_path = build_output_path(output_dir, filename, image_b64)
    output_path.write_bytes(base64.b64decode(image_b64))
    return output_path


def extract_markdown_data_image(content: str) -> Optional[str]:
    match = re.search(r"data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=]+)", content or "")
    return match.group(1) if match else None


def get_image_mime_type(image_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(image_path))
    return mime_type or "application/octet-stream"


def build_multipart_form_data(fields: Dict[str, str], file_field: str, file_path: Path) -> tuple[str, bytes]:
    boundary = f"----ImageApiBoundary{uuid.uuid4().hex}"
    chunks = []

    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )

    mime_type = get_image_mime_type(file_path)
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(file_path.read_bytes())
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))

    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


def handle_generate(args: argparse.Namespace) -> None:
    base_url = ensure_base_url(args.base_url)
    api_key = ensure_api_key(args.api_key)
    payload = {
        "model": args.model,
        "prompt": args.prompt,
    }

    response = request_json(
        url=f"{base_url}/v1/images/generations",
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}",
        },
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        insecure=args.insecure,
    )

    data_item = (response.get("data") or [{}])[0]
    image_b64 = data_item.get("b64_json")
    if not image_b64:
        fail(f"响应中未找到 data[0].b64_json: {json.dumps(response, ensure_ascii=False)}")

    output_path = save_base64_image(image_b64, Path(args.output_dir), args.filename)
    print(f"图片已保存: {output_path}")
    if data_item.get("revised_prompt"):
        print(f"revised_prompt: {data_item['revised_prompt']}")


def handle_edit(args: argparse.Namespace) -> None:
    base_url = ensure_base_url(args.base_url)
    api_key = ensure_api_key(args.api_key)
    image_path = ensure_image(args.image)

    content_type, body = build_multipart_form_data(
        fields={
            "model": args.model,
            "prompt": args.prompt,
        },
        file_field="image",
        file_path=image_path,
    )

    response = request_json(
        url=f"{base_url}/v1/images/edits",
        method="POST",
        headers={
            "Content-Type": content_type,
            "Authorization": f"Bearer {api_key}",
        },
        data=body,
        insecure=args.insecure,
    )

    data_item = (response.get("data") or [{}])[0]
    image_b64 = data_item.get("b64_json")
    if not image_b64:
        fail(f"响应中未找到 data[0].b64_json: {json.dumps(response, ensure_ascii=False)}")

    output_path = save_base64_image(image_b64, Path(args.output_dir), args.filename)
    print(f"图片已保存: {output_path}")
    if data_item.get("revised_prompt"):
        print(f"revised_prompt: {data_item['revised_prompt']}")


def handle_chat_edit(args: argparse.Namespace) -> None:
    base_url = ensure_base_url(args.base_url)
    api_key = ensure_api_key(args.api_key)
    image_path = ensure_image(args.image)
    mime_type = get_image_mime_type(image_path)
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")

    payload = {
        "model": args.model,
        "role": "user",
        "content": [
            {"type": "text", "text": args.prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{image_b64}"
                },
            },
        ],
    }

    response = request_json(
        url=f"{base_url}/v1/chat/completions",
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}",
        },
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        insecure=args.insecure,
    )

    choices = response.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    content = message.get("content", "")
    result_image_b64 = extract_markdown_data_image(content)
    if not result_image_b64:
        fail(f"响应中未找到 markdown/base64 图片内容: {json.dumps(response, ensure_ascii=False)}")

    output_path = save_base64_image(result_image_b64, Path(args.output_dir), args.filename)
    print(f"图片已保存: {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="独立的图片接口调用工具，不影响现有项目文件。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_arguments(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--base-url", help="接口基础地址，例如 https://api.example.com")
        subparser.add_argument("--api-key", help="接口密钥，不传则读取 IMAGE_API_KEY")
        subparser.add_argument("--model", default=DEFAULT_MODEL, help=f"模型名，默认 {DEFAULT_MODEL}")
        subparser.add_argument("--prompt", required=True, help="提示词")
        subparser.add_argument(
            "--insecure",
            action="store_true",
            help="跳过 HTTPS 证书校验，适用于内网自签名证书场景",
        )
        subparser.add_argument(
            "--output-dir",
            default=str(DEFAULT_OUTPUT_DIR),
            help=f"输出目录，默认 {DEFAULT_OUTPUT_DIR}",
        )
        subparser.add_argument("--filename", help="输出文件名，可不带后缀")

    parser_generate = subparsers.add_parser("generate", help="调用 /v1/images/generations")
    add_common_arguments(parser_generate)
    parser_generate.set_defaults(func=handle_generate)

    parser_edit = subparsers.add_parser("edit", help="调用 /v1/images/edits")
    add_common_arguments(parser_edit)
    parser_edit.add_argument("--image", required=True, help="待编辑图片路径")
    parser_edit.set_defaults(func=handle_edit)

    parser_chat_edit = subparsers.add_parser("chat-edit", help="调用 /v1/chat/completions 改图")
    add_common_arguments(parser_chat_edit)
    parser_chat_edit.add_argument("--image", required=True, help="待编辑图片路径")
    parser_chat_edit.set_defaults(func=handle_chat_edit)

    return parser


def main() -> None:
    load_dotenv_if_exists()
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
