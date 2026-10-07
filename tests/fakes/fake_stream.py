"""Fake OpenCode Go server that streams slowly: THINK thinking chunks then
WORDS text chunks, DELAY seconds apart. Supports chat, responses, messages."""
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])
DELAY = float(os.environ.get("DELAY", "0.5"))
THINK = int(os.environ.get("THINK", "4"))
WORDS = int(os.environ.get("WORDS", "6"))


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def ev(self, data, event=None):
        out = (f"event: {event}\n" if event else "") + f"data: {data if isinstance(data, str) else json.dumps(data)}\n\n"
        try:
            self.wfile.write(out.encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise SystemExit

    def do_GET(self):
        b = json.dumps({"object": "list", "data": [{"id": "qwen3.8-max"}, {"id": "glm-5.3"}, {"id": "grok-4.7"}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        m = body["model"]
        assert body.get("stream"), "expected a streaming request"
        json_mode = "response_format" in body
        if json_mode and os.environ.get("REJECT_JSON"):
            b = json.dumps({"error": {"message": "response_format is not supported by this model",
                                      "type": "invalid_request_error"}}).encode()
            self.send_response(400); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            return
        self.sse_start()
        try:
            if self.path.endswith("/chat/completions"):
                base = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": m}
                for i in range(THINK):
                    time.sleep(DELAY); self.ev({**base, "choices": [{"index": 0, "delta": {"reasoning_content": "hmm "}, "finish_reason": None}]})
                if json_mode:      # tells the test the request asked for JSON mode
                    self.ev({**base, "choices": [{"index": 0, "delta": {"content": "JSON "}, "finish_reason": None}]})
                for i in range(WORDS):
                    time.sleep(DELAY); self.ev({**base, "choices": [{"index": 0, "delta": {"content": f"w{i} "}, "finish_reason": None}]})
                self.ev({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                self.ev("[DONE]")
            elif self.path.endswith("/responses"):
                resp = {"id": "r", "object": "response", "created_at": 0, "model": m, "status": "in_progress", "output": [],
                        "parallel_tool_calls": False, "tool_choice": "auto", "tools": []}
                seq = 0
                def send(t, extra):
                    nonlocal seq
                    seq += 1
                    self.ev({"type": t, "sequence_number": seq, **extra}, event=t)
                send("response.created", {"response": resp})
                for i in range(THINK):
                    time.sleep(DELAY); send("response.reasoning_text.delta", {"item_id": "rs", "output_index": 0, "content_index": 0, "delta": "hmm "})
                text = ""
                for i in range(WORDS):
                    time.sleep(DELAY); text += f"w{i} "
                    send("response.output_text.delta", {"item_id": "m", "output_index": 0, "content_index": 0, "delta": f"w{i} ", "logprobs": []})
                done = {**resp, "status": "completed", "output": [{"type": "message", "id": "m", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}]}]}
                send("response.completed", {"response": done})
            elif self.path.endswith("/v1/messages"):
                self.ev({"type": "message_start", "message": {"id": "msg", "type": "message", "role": "assistant", "model": m, "content": [],
                         "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}}, "message_start")
                self.ev({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}, "content_block_start")
                for i in range(THINK):
                    time.sleep(DELAY); self.ev({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm "}}, "content_block_delta")
                self.ev({"type": "content_block_stop", "index": 0}, "content_block_stop")
                self.ev({"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}, "content_block_start")
                for i in range(WORDS):
                    time.sleep(DELAY); self.ev({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": f"w{i} "}}, "content_block_delta")
                self.ev({"type": "content_block_stop", "index": 1}, "content_block_stop")
                self.ev({"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 9}}, "message_delta")
                self.ev({"type": "message_stop"}, "message_stop")
        except SystemExit:
            return


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
