#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════════════════╗
║              MiMo Auto 2API Server - OpenAI API 兼容代理服务器 v2.0.0              ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║  功能特性:                                                                    ║
║    • 服务端自动管理 JWT (无需客户端提供)                                        ║
║    • 接受任意 api_key (符合 OpenAI 标准)                                        ║
║    • 支持 /v1/models 模型列表                                                    ║
║    • 支持 /v1/chat/completions (流式/非流式)                                    ║
║    • 完整支持 tool_calls / function_call 转换                                    ║
║    • 自动 JWT 刷新 + 3次重试 + 指数退避                                          ║
╚═══════════════════════════════════════════════════════════════════════════════╝
"""

import hashlib
import json
import os
import platform
import random
import re
import string
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests


# ═══════════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════════
class Config:
    PORT = int(os.getenv("MIMO_PORT", "8080"))
    HOST = os.getenv("MIMO_HOST", "0.0.0.0")
    MIMO_BASE_URL = "https://api.xiaomimimo.com"
    BOOTSTRAP_URL = f"{MIMO_BASE_URL}/api/free-ai/bootstrap"
    CHAT_URL = f"{MIMO_BASE_URL}/api/free-ai/openai/chat"
    MODEL = "mimo-auto"
    MAX_RETRIES = 3
    RETRY_BASE = 2  # 指数退避基数 (秒)
    JWT_REFRESH_BUFFER = 300  # 提前 5 分钟刷新 JWT
    # 模型信息 (与 OpenAI API 对齐)
    MODELS = {
        "mimo-auto": {
            "id": "mimo-auto",
            "object": "model",
            "created": 1718323200,
            "owned_by": "xiaomi-mimo",
            "permission": [],
            "root": "mimo-auto",
            "parent": None,
        }
    }


# ═══════════════════════════════════════════════════════
#  MiMo 后端客户端 (服务端 JWT 管理)
# ═══════════════════════════════════════════════════════
class MiMoBackend:
    """
    服务端 MiMo API 客户端
    负责自动获取/刷新 JWT，对上层透明
    """

    def __init__(self):
        self._jwt = None
        self._jwt_exp = 0
        self._lock = threading.Lock()
        self._fingerprint = self._generate_fingerprint()

    @staticmethod
    def _get_fingerprint_components():
        """获取跨平台的指纹组件 (兼容 Windows 和 Linux)"""
        import socket as _socket

        # hostname: 跨平台
        try:
            hostname = _socket.gethostname()
        except Exception:
            hostname = "unknown-host"

        # username: 跨平台 (os.getlogin 在某些 Windows 环境或容器中会失败)
        username = "unknown-user"
        try:
            username = os.getlogin()
        except (AttributeError, OSError):
            username = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown-user"

        return {
            "hostname": hostname,
            "system": platform.system(),
            "machine": platform.machine() or "unknown-machine",
            "processor": platform.processor() or "unknown-cpu",
            "username": username,
        }

    @classmethod
    def _generate_fingerprint(cls):
        """生成客户端指纹 (跨平台兼容 Windows/Linux)"""
        components = cls._get_fingerprint_components()
        seed_parts = [
            components["hostname"],
            components["system"],
            components["machine"],
            components["processor"],
            components["username"],
        ]
        return hashlib.sha256("|".join(seed_parts).encode("utf-8")).hexdigest()

    def _bootstrap(self):
        """从 MiMo 获取 JWT Token"""
        for attempt in range(Config.MAX_RETRIES):
            try:
                resp = requests.post(
                    Config.BOOTSTRAP_URL,
                    headers={"Content-Type": "application/json"},
                    json={"client": self._fingerprint},
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    jwt = data.get("jwt")
                    if not jwt:
                        raise Exception("No JWT in response")

                    # 解析过期时间
                    try:
                        import base64

                        payload_b64 = jwt.split(".")[1]
                        padding = 4 - len(payload_b64) % 4
                        if padding != 4:
                            payload_b64 += "=" * padding
                        payload = json.loads(
                            base64.b64decode(payload_b64.replace("-", "+").replace("_", "/"))
                        )
                        exp = payload.get("exp", 0)
                    except:
                        exp = int(time.time()) + 3600

                    with self._lock:
                        self._jwt = jwt
                        self._jwt_exp = exp
                    print(f"[MiMo] JWT refreshed, expires at {exp}")
                    return jwt

            except requests.exceptions.RequestException:
                if attempt < Config.MAX_RETRIES - 1:
                    delay = Config.RETRY_BASE ** (attempt + 1)
                    time.sleep(delay)
                continue

        raise Exception("Failed to get JWT after retries")

    def _get_jwt(self):
        """获取有效的 JWT (自动刷新)"""
        with self._lock:
            jwt_cached = self._jwt
            jwt_exp = self._jwt_exp

        if jwt_cached and jwt_exp - time.time() > Config.JWT_REFRESH_BUFFER:
            return jwt_cached

        with self._lock:
            # 双重检查
            if self._jwt and self._jwt_exp - time.time() > Config.JWT_REFRESH_BUFFER:
                return self._jwt
        return self._bootstrap()

    def chat(self, payload, stream=False):
        """
        向 MiMo 发送聊天请求
        payload: OpenAI 标准格式的请求体
        """
        jwt = self._get_jwt()

        headers = {
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
            "X-Mimo-Source": "mimocode-cli-free",
        }

        # 转换 payload: 提取 messages，添加 stream
        mimo_payload = {
            "model": Config.MODEL,
            "messages": payload.get("messages", []),
            "stream": stream,
        }

        # 如果有 tools/functions，转换格式
        if "tools" in payload:
            mimo_payload["tools"] = payload["tools"]
        if "tool_choice" in payload:
            mimo_payload["tool_choice"] = payload["tool_choice"]

        # 发送请求
        resp = requests.post(
            Config.CHAT_URL,
            headers=headers,
            json=mimo_payload,
            stream=stream,
            timeout=120,
        )
        # 强制 UTF-8 编码，避免 MiMo API 返回的 text/event-stream
        # 因未声明 charset 而被默认当作 ISO-8859-1 解析导致中文乱码
        resp.encoding = "utf-8"

        # 401/403 -> 刷新 token 重试
        if resp.status_code in (401, 403):
            print(f"[MiMo] Token expired, refreshing...")
            with self._lock:
                self._jwt = None
            jwt = self._get_jwt()
            headers["Authorization"] = f"Bearer {jwt}"
            resp = requests.post(
                Config.CHAT_URL,
                headers=headers,
                json=mimo_payload,
                stream=stream,
                timeout=120,
            )
            resp.encoding = "utf-8"

        # 429 或 5xx -> 重试
        retry_count = 0
        while (resp.status_code == 429 or resp.status_code >= 500) and retry_count < Config.MAX_RETRIES:
            delay = Config.RETRY_BASE ** (retry_count + 1)
            print(f"[MiMo] HTTP {resp.status_code}, retrying in {delay}s...")
            time.sleep(delay)
            resp = requests.post(
                Config.CHAT_URL,
                headers=headers,
                json=mimo_payload,
                stream=stream,
                timeout=120,
            )
            resp.encoding = "utf-8"
            retry_count += 1

        if resp.status_code != 200:
            body = resp.text[:500] if hasattr(resp, "text") else ""
            raise Exception(f"Chat failed: {resp.status_code} - {body}")

        if stream:
            return self._stream_parse(resp.iter_lines(decode_unicode=True))

        # 非流式请求时，使用 text 属性 + 显式 UTF-8 解码，防止 json() 因编码猜错导致中文乱码
        return json.loads(resp.text)

    def _stream_parse(self, lines):
        """解析 SSE 流"""
        for line in lines:
            if line:
                line_str = line if isinstance(line, str) else line.decode("utf-8")
                if line_str.startswith("data:"):
                    data = line_str[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        if chunk.get("choices"):
                            yield chunk
                    except:
                        continue


# ═══════════════════════════════════════════════════════
#  OpenAI 请求/响应处理
# ═══════════════════════════════════════════════════════
class OpenAIHandler:
    """处理 OpenAI API 格式的请求和响应转换"""

    @staticmethod
    def generate_id(prefix="chatcmpl"):
        """生成 OpenAI 风格的 ID"""
        return f"{prefix}-{''.join(random.choices(string.ascii_letters + string.digits, k=24))}"

    @staticmethod
    def now_ts():
        return int(time.time())

    @staticmethod
    def convert_to_openai(mimo_resp, request_id):
        """将 MiMo 响应转换为标准 OpenAI 格式"""
        if not isinstance(mimo_resp, dict):
            return mimo_resp

        # 已经是 OpenAI 格式了，添加/修正一些字段
        mimo_resp["id"] = mimo_resp.get("id", request_id)
        mimo_resp["object"] = mimo_resp.get("object", "chat.completion")
        mimo_resp["created"] = mimo_resp.get("created", OpenAIHandler.now_ts())
        mimo_resp["model"] = mimo_resp.get("model", Config.MODEL)

        # 确保 choices 格式正确
        if "choices" in mimo_resp:
            for choice in mimo_resp["choices"]:
                # 处理 message 中的 tool_calls
                if "message" in choice and choice["message"]:
                    msg = choice["message"]
                    # 确保 tool_calls 格式正确
                    if "tool_calls" in msg and msg["tool_calls"]:
                        for tc in msg["tool_calls"]:
                            if "function" in tc:
                                # 确保 arguments 是字符串
                                func = tc["function"]
                                if "arguments" in func and isinstance(func["arguments"], dict):
                                    func["arguments"] = json.dumps(func["arguments"])

        return mimo_resp

    @staticmethod
    def stream_to_openai(mimo_chunk, request_id):
        """将单个 MiMo SSE chunk 转换为 OpenAI 格式"""
        if not mimo_chunk or not mimo_chunk.get("choices"):
            return None

        choice = mimo_chunk["choices"][0]
        delta = choice.get("delta", {})

        # 构建标准 OpenAI chunk
        openai_chunk = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": OpenAIHandler.now_ts(),
            "model": Config.MODEL,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": choice.get("finish_reason"),
                }
            ],
        }
        return openai_chunk


# ═══════════════════════════════════════════════════════
#  HTTP 请求处理器
# ═══════════════════════════════════════════════════════
backend = MiMoBackend()
handler = OpenAIHandler()


class APIHandler(BaseHTTPRequestHandler):
    """OpenAI API 兼容的 HTTP 请求处理器"""

    def log_message(self, format, *args):
        print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {self.address_string()} - {format % args}")

    def _send_json(self, status, data):
        """发送 JSON 响应"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, gen, request_id):
        """发送 SSE 流式响应"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            for chunk in gen:
                openai_chunk = handler.stream_to_openai(chunk, request_id)
                if openai_chunk:
                    data_str = f"data: {json.dumps(openai_chunk, ensure_ascii=False)}\n\n"
                    self.wfile.write(data_str.encode("utf-8"))
                    self.wfile.flush()

            # 结束流
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_body(self):
        """读取 HTTP 请求体"""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return None
        return self.rfile.read(content_length).decode("utf-8")

    def _parse_json(self, body):
        """解析 JSON，失败返回 None"""
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    def _send_cors(self):
        """发送 CORS 响应头"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    # ── 路由 ──
    def do_OPTIONS(self):
        self._send_cors()

    def do_GET(self):
        # GET /v1/models
        if self.path == "/v1/models" or self.path == "/models":
            self._send_json(200, {
                "object": "list",
                "data": list(Config.MODELS.values()),
            })
            return

        # GET /v1/ 根路径
        if self.path in ["/", "/v1", "/v1/"]:
            self._send_json(200, {
                "status": "ok",
                "message": "MiMo Auto 2API Server",
                "model": Config.MODEL,
                "endpoints": ["/v1/models", "/v1/chat/completions"],
            })
            return

        self._send_json(404, {"error": {"message": "Not found", "type": "not_found", "code": "404"}})

    def do_POST(self):
        # POST /v1/chat/completions
        if self.path == "/v1/chat/completions" or self.path == "/chat/completions":
            body = self._read_body()
            data = self._parse_json(body)

            if data is None:
                self._send_json(400, {"error": {"message": "Invalid JSON", "type": "invalid_request", "code": "400"}})
                return

            # 提取参数
            messages = data.get("messages", [])
            stream = data.get("stream", False)
            request_id = handler.generate_id()

            # 构建请求体
            mimo_payload = {
                "messages": messages,
            }

            # 传递 tools（如果有）
            if "tools" in data:
                mimo_payload["tools"] = data["tools"]
            if "tool_choice" in data:
                mimo_payload["tool_choice"] = data["tool_choice"]

            if stream:
                try:
                    gen = backend.chat(mimo_payload, stream=True)
                    self._send_sse(gen, request_id)
                except Exception as e:
                    self._send_json(500, {"error": {"message": str(e), "type": "internal_error", "code": "500"}})
            else:
                try:
                    result = backend.chat(mimo_payload, stream=False)
                    openai_result = handler.convert_to_openai(result, request_id)
                    self._send_json(200, openai_result)
                except Exception as e:
                    self._send_json(500, {"error": {"message": str(e), "type": "internal_error", "code": "500"}})
            return

        self._send_json(404, {"error": {"message": "Not found", "type": "not_found", "code": "404"}})


# ═══════════════════════════════════════════════════════
#  启动服务
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else Config.PORT
    HOST = sys.argv[2] if len(sys.argv) > 2 else Config.HOST

    server = HTTPServer((HOST, PORT), APIHandler)
    print(f"🚀 MiMo Auto 2API Server v2.0.0")
    print(f"   Listening on http://{HOST}:{PORT}")
    print(f"   Model: {Config.MODEL}")
    print(f"   Endpoints:")
    print(f"     GET  /v1/models            -> List models")
    print(f"     POST /v1/chat/completions  -> Chat (streaming/non-streaming)")
    print(f"   Features:")
    print(f"     • Accepts any api_key")
    print(f"     • Server-side JWT auto-refresh")
    print(f"     • OpenAI-compatible format")
    print(f"     • Tool calls / function calling")
    print(f"     • SSE streaming")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n✅ Server stopped")
