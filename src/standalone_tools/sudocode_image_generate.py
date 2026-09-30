#!/usr/bin/env python3
"""Call SudoCode's image generation API and save the returned image."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from urllib import error, request


DEFAULT_BASE_URL = "https://api.sudocode.chat/v1"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_TIMEOUT = 600


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def resolve_prompt(prompt: str | None, prompt_file: str | None) -> str:
    if prompt_file:
        value = Path(prompt_file).expanduser().read_text(encoding="utf-8-sig")
    else:
        value = prompt or ""

    value = value.strip()
    if not value:
        raise ValueError("提示词不能为空")
    return value


def build_payload(
    prompt: str,
    model: str,
    size: str | None = None,
    quality: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"model": model, "prompt": prompt}
    if size:
        payload["size"] = size
    if quality:
        payload["quality"] = quality
    return payload


def request_image(
    base_url: str,
    api_key: str,
    payload: dict[str, object],
    timeout: int,
) -> bytes:
    api_request = request.Request(
        url=f"{base_url.rstrip('/')}/images/generations",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "sudocode-image-client/1.0",
        },
        method="POST",
    )

    try:
        with request.urlopen(api_request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {response_body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"请求失败: {exc.reason}") from exc

    try:
        response_json = json.loads(response_body)
        image_base64 = response_json["data"][0]["b64_json"]
        return base64.b64decode(image_base64, validate=True)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"响应中没有有效的 data[0].b64_json: {response_body}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="调用 SudoCode gpt-image-2 生成图片")
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="图片提示词")
    prompt_group.add_argument("--prompt-file", help="UTF-8 提示词文件")
    parser.add_argument("--output", help="输出图片路径，默认保存到 image_api_outputs")
    parser.add_argument("--size", help="图片尺寸，例如 1024x1024")
    parser.add_argument("--quality", help="图片质量，例如 low、medium、high")
    parser.add_argument("--api-key", help="SudoCode API Key，默认读取 SUDOCODE_API_KEY")
    parser.add_argument(
        "--base-url",
        default=os.getenv("SUDOCODE_API_BASE_URL", DEFAULT_BASE_URL),
        help=f"API 基础地址，默认 {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("SUDOCODE_IMAGE_MODEL", DEFAULT_MODEL),
        help=f"图片模型，默认 {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("SUDOCODE_API_TIMEOUT", str(DEFAULT_TIMEOUT))),
        help=f"请求超时时间（秒），默认 {DEFAULT_TIMEOUT}",
    )
    return parser


def main() -> int:
    load_dotenv(Path.cwd() / ".env")
    args = build_parser().parse_args()

    api_key = args.api_key or os.getenv("SUDOCODE_API_KEY")
    if not api_key:
        print("错误: 缺少 SUDOCODE_API_KEY，请在 .env 中配置或使用 --api-key", file=sys.stderr)
        return 1

    try:
        prompt = resolve_prompt(args.prompt, args.prompt_file)
        payload = build_payload(prompt, args.model, args.size, args.quality)
        image_bytes = request_image(args.base_url, api_key, payload, args.timeout)

        if args.output:
            output_path = Path(args.output).expanduser()
        else:
            filename = f"sudocode_{datetime.now():%Y%m%d_%H%M%S}.png"
            output_path = Path.cwd() / "image_api_outputs" / filename

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    print(f"图片已保存: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
