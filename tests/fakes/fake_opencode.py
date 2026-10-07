"""Fake OpenCode Go server for tests (chat / responses / messages, non-streaming JSON +
SSE for messages). Behaviour via env: TRUNC_BELOW, REJECT_ABOVE, REJECT_THINKING.
usage: python3 fake_opencode.py <mode: ua|inactive> <port> <log file>"""
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as HTTPServer

MODE = sys.argv[1] if len(sys.argv) > 1 else "ua"   # ua | inactive
KEY = "ockey"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else (18710 if MODE == "ua" else 18711)
LOG = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "oc.log")


def log(x):
    with open(LOG, "a") as f:
        f.write(x + "\n")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def authed(self):
        auth = self.headers.get("Authorization", "")
        xkey = self.headers.get("x-api-key", "")
        if not self.headers.get("x-opencode-session"):
            self.send(400, {"type": "error", "error": {"type": "MissingSessionID", "message": "Request is missing x-opencode-session and cannot be routed efficiently."}})
            return False
        log(f"session={self.headers.get('x-opencode-session')[:22]}")
        if MODE == "inactive":
            self.send(403, {"error": {"message": "Go subscription inactive"}})
            return False
        if auth != f"Bearer {KEY}" and xkey != KEY:
            self.send(401, {"error": {"message": "invalid api key"}})
            return False
        return True

    def do_GET(self):
        if self.headers.get("User-Agent", "").startswith("Python-urllib"):
            b = b"<html><title>403 Forbidden</title><body>cloudflare</body></html>"
            self.send_response(403); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            return
        if not self.authed():
            return
        self.send(200, {"object": "list", "data": [{"id": m} for m in ["glm-5.3", "grok-4.7", "qwen3.8-max", "kimi-k3"]]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not self.authed():
            return
        mt = body.get("max_tokens") or body.get("max_output_tokens")
        log(f"{self.path} {body.get('model')} limit={mt}")
        reject_above = int(os.environ.get("REJECT_ABOVE", "0"))
        if reject_above and mt and mt > reject_above:
            return self.send(400, {"type": "error", "error": {"type": "invalid_request_error", "message": f"max_tokens: {mt} > {reject_above}, which is the maximum allowed"}})
        cut = mt is not None and mt < int(os.environ.get("TRUNC_BELOW", "0"))
        if self.path.endswith("/chat/completions"):
            log(f"chat enable_thinking={body.get('enable_thinking')}")
            if body["model"] == "nonexistent-model":
                return self.send(404, {"error": {"message": "model not found"}})
            return self.send(200, {"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                                   "choices": [{"index": 0, "finish_reason": "length" if cut else "stop",
                                                "message": {"role": "assistant", "content": f"CHAT:{body['model']}"}}]})
        if self.path.endswith("/responses"):
            assert "instructions" in body and "input" in body
            return self.send(200, {"id": "resp_1", "object": "response", "created_at": 0, "status": "incomplete" if cut else "completed",
                                   "model": body["model"], "output": [{"type": "message", "id": "m1", "status": "completed",
                                   "role": "assistant", "content": [{"type": "output_text", "text": f"RESP:{body['model']}", "annotations": []}]}],
                                   "parallel_tool_calls": False, "tool_choice": "auto", "tools": []})
        if self.path.endswith("/v1/messages"):
            assert body.get("system") and body.get("stream")
            log(f"messages temperature={body.get('temperature')} thinking={body.get('thinking')}")
            if os.environ.get("REJECT_THINKING") and "thinking" in body:
                return self.send(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "thinking: unsupported parameter"}})
            text = f"MSG:{body['model']}"
            events = [
                ("message_start", {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                                   "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}}),
                ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "max_tokens" if cut else "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 3}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            data = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send(404, {"error": {"message": "not found"}})


HTTPServer(("127.0.0.1", PORT), H).serve_forever()
