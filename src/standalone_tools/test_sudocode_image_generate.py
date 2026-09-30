import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest

from src.standalone_tools import sudocode_image_generate


SCRIPT_PATH = Path(__file__).with_name("sudocode_image_generate.py")


class ImageApiHandler(BaseHTTPRequestHandler):
    request_path = ""
    authorization = ""
    user_agent = ""
    request_json: dict[str, object] = {}

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        request_body = self.rfile.read(content_length)
        type(self).request_path = self.path
        type(self).authorization = self.headers.get("Authorization", "")
        type(self).user_agent = self.headers.get("User-Agent", "")
        type(self).request_json = json.loads(request_body.decode("utf-8"))

        response = json.dumps(
            {
                "data": [
                    {
                        "b64_json": base64.b64encode(b"fake-image").decode("ascii")
                    }
                ]
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: object) -> None:
        pass


class SudocodeImageGenerateTest(unittest.TestCase):
    def test_cli_script_exists(self) -> None:
        self.assertTrue(SCRIPT_PATH.is_file())

    def test_cli_help_describes_prompt_and_output_options(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("--prompt", result.stdout)
        self.assertIn("--prompt-file", result.stdout)
        self.assertIn("--output", result.stdout)

    def test_build_payload_omits_unused_optional_fields(self) -> None:
        payload = sudocode_image_generate.build_payload(
            prompt="画一只猫",
            model="gpt-image-2",
        )

        self.assertEqual(
            payload,
            {"model": "gpt-image-2", "prompt": "画一只猫"},
        )

    def test_request_image_uses_sudocode_generation_contract(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), ImageApiHandler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)

        payload = {"model": "gpt-image-2", "prompt": "画一只猫"}
        image_bytes = sudocode_image_generate.request_image(
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="sk-test",
            payload=payload,
            timeout=5,
        )

        self.assertEqual(image_bytes, b"fake-image")
        self.assertEqual(ImageApiHandler.request_path, "/v1/images/generations")
        self.assertEqual(ImageApiHandler.authorization, "Bearer sk-test")
        self.assertEqual(ImageApiHandler.user_agent, "sudocode-image-client/1.0")
        self.assertEqual(ImageApiHandler.request_json, payload)


if __name__ == "__main__":
    unittest.main()
