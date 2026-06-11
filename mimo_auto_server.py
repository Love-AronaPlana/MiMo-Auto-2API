#!/usr/bin/env python3
"""
╔═════════════════════════════════════════════════════════════════════════╗
║              MiMo Auto 2API Server - OpenAI API 兼容版 v1.1.0              ║
╠═════════════════════════════════════════════════════════════════════════╣
║  启动: python3 mimo_auto_server.py                                    ║
╠═════════════════════════════════════════════════════════════════════════╣
║  特性: 自动重试3次, 指数退避, 401自动刷新JWT                               ║
╚═════════════════════════════════════════════════════════════════════════╝

接口:
  GET  /v1/models           -> 获取可用模型列表
  POST /v1/chat/completions -> 聊天 (支持流式/非流式)

使用:
  curl http://127.0.0.1:8080/v1/chat/completions \\
    -H "Content-Type: application/json" \\
    -d '{"model":"mimo-auto","messages":[{"role":"user","content":"hi"}]}'
"""

import hashlib
import json
import os
import platform
import random
import string
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# --- 适配无 requests 环境 ---
try:
    import requests as req_lib
    HAVE_REQUESTS = True
except ImportError:
    HAVE_REQUESTS = False
    req_lib = None


# ═══════════════════════════════════════════════════════
#  MiMo Auto 客户端 (核心逆向逻辑 + 重试机制)
# ═══════════════════════════════════════════════════════
class MiMoAutoClient:
    BASE_URL = "https://api.xiaomimimo.com"
    BOOTSTRAP_URL = f"{BASE_URL}/api/free-ai/bootstrap"
    CHAT_URL = f"{BASE_URL}/api/free-ai/openai/chat"
    MODEL = "mimo-auto"
    MODEL_NAME = "MiMo Auto"
    MAX_RETRIES = 3
    RETRY_DELAY_BASE = 2  # 指数退避基数 (秒)

    def __init__(self):
        self._jwt = None
        self._jwt_exp = 0
        self._lock = threading.Lock()
        self._fingerprint = self._generate_fingerprint()

    # ── 指纹生成 ──
    @staticmethod
    def _generate_fingerprint():
        seed_parts = [
            os.uname().nodename,
            platform.system(),
            platform.machine(),
            platform.processor() or "unknown-cpu",
            os.getlogin() if hasattr(os, "getlogin") else "unknown-user"
        ]
        return hashlib.sha256("|".join(seed_parts).encode()).hexdigest()

    # ── 通用请求 (带重试 + 指数退避) ──
    def _request(self, method, url, headers=None, json_data=None, timeout=30, stream=False):
        """
        发送 HTTP 请求, 失败自动重试 3 次, 带指数退避
        返回: (status_code, headers, body_or_response)
        """
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = req_lib.request(
                    method, url,
                    headers=headers,
                    json=json_data,
                    timeout=timeout,
                    stream=stream
                )
                # 成功返回
                if resp.status_code == 200:
                    return resp.status_code, dict(resp.headers), resp
                # 401/403 -> 可能需要刷新 token
                if resp.status_code in (401, 403):
                    last_error = Exception(f"HTTP {resp.status_code}")
                    break  # 退出重试, 由调用方处理 token 刷新
                # 429/5xx ->  retryable
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_error = Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
                else:
                    # 其他 4xx -> 不重试
                    return resp.status_code, dict(resp.headers), resp
            except req_lib.exceptions.Timeout:
                last_error = Exception("Request timeout")
            except req_lib.exceptions.ConnectionError:
                last_error = Exception("Connection error")
            except Exception as e:
                last_error = e

            # 非最后一次 -> 指数退避
            if attempt < self.MAX_RETRIES - 1:
                delay = self.RETRY_DELAY_BASE ** (attempt + 1)
                print(f"[!] 请求失败 ({attempt + 1}/{self.MAX_RETRIES}): {last_error}, {delay}s 后重试...")
                time.sleep(delay)
            else:
                print(f"[!] 请求最终失败 (已重试 {self.MAX_RETRIES} 次)")
                break

        raise last_error if last_error else Exception(f"Request failed after {self.MAX_RETRIES} retries")

    # ── 获取 JWT (带重试) ──
    def _bootstrap(self):
        if not HAVE_REQUESTS:
            raise ImportError("需要安装 requests 库: pip install requests")

        # 直接请求, bootstrap 本身不重试 (避免无限递归)
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = req_lib.post(
                    self.BOOTSTRAP_URL,
                    headers={"Content-Type": "application/json"},
                    json={"client": self._fingerprint},
                    timeout=30
                )
                if resp.status_code == 200:
                    break
            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAY_BASE ** (attempt + 1)
                    print(f"[!] Bootstrap 失败 ({attempt + 1}/{self.MAX_RETRIES}), {delay}s 后重试...")
                    time.sleep(delay)
                else:
                    raise Exception(f"Bootstrap 失败, 已重试 {self.MAX_RETRIES} 次: {e}") from e
        else:
            raise Exception(f"Bootstrap 失败: HTTP {resp.status_code}")

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
            payload = json.loads(base64.b64decode(payload_b64.replace("-", "+").replace("_", "/")))
            exp = payload.get("exp", 0) * 1000
        except:
            exp = (time.time() + 50 * 60) * 1000

        with self._lock:
            self._jwt = jwt
            self._jwt_exp = exp
        print(f"[✓] JWT 获取成功 (过期: {int(exp/1000)})")
        return jwt

    def _get_jwt(self):
        with self._lock:
            if self._jwt and self._jwt_exp - time.time() * 1000 > 5 * 60 * 1000:
                return self._jwt
        return self._bootstrap()

    # ── 聊天请求 (带重试) ──
    def chat(self, messages, stream=False):
        """
        与 MiMo Auto 进行对话
        失败自动重试3次, 401自动刷新JWT
        """
        jwt = self._get_jwt()
        payload = {
            "model": self.MODEL,
            "messages": messages,
            "stream": stream,
        }
        headers = {
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
            "X-Mimo-Source": "mimocode-cli-free",
        }

        # 第一次请求
        resp = req_lib.post(
            self.CHAT_URL,
            headers=headers,
            json=payload,
            stream=stream,
            timeout=120
        )

        # 401/403 -> 刷新 token 重试
        if resp.status_code in (401, 403):
            print(f"[!] Token 过期 (HTTP {resp.status_code}), 刷新中...")
            with self._lock:
                self._jwt = None
            jwt = self._get_jwt()
            headers["Authorization"] = f"Bearer {jwt}"
            resp = req_lib.post(
                self.CHAT_URL,
                headers=headers,
                json=payload,
                stream=stream,
                timeout=120
            )

        # 429 或 5xx -> 重试
        retry_count = 0
        while (resp.status_code == 429 or resp.status_code >= 500) and retry_count < self.MAX_RETRIES:
            delay = self.RETRY_DELAY_BASE ** (retry_count + 1)
            print(f"[!] HTTP {resp.status_code}, {delay}s 后重试 ({retry_count + 1}/{self.MAX_RETRIES})...")
            time.sleep(delay)
            resp = req_lib.post(
                self.CHAT_URL,
                headers=headers,
                json=payload,
                stream=stream,
                timeout=120
            )
            retry_count += 1

        if resp.status_code != 200:
            raise Exception(f"Chat failed: {resp.status_code} - {resp.text[:500]}")

        if stream:
            return self._stream_parse(resp.iter_lines(decode_unicode=True))

        return resp.json()

    def _stream_parse(self, lines):
        """解析 SSE 流"""
        for line in lines:
            if line:
                line = line if isinstance(line, str) else line.decode("utf-8")
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        if chunk.get("choices"):
                            yield chunk
                    except:
                        continue


# ═══════════════════════════════════════════════════════
#  HTTP Handler
# ═══════════════════════════════════════════════════════
client = MiMoAutoClient()

def random_id(prefix="chatcmpl"):
    return f"{prefix}-{''.join(random.choices(string.ascii_letters + string.digits, k=24))}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {self.address_string()} - {format % args}")

    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, gen, request_id):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        created = int(time.time())
        try:
            for chunk in gen:
                if not chunk or not chunk.get("choices"):
                    continue

                choice = chunk["choices"][0]
                data = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": client.MODEL,
                    "choices": [{
                        "index": 0,
                        "delta": choice.get("delta", {}),
                        "finish_reason": choice.get("finish_reason", None)
                    }]
                }
                self.wfile.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()

            # [DONE]
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == "/v1/models":
            self._send_json(200, {
                "object": "list",
                "data": [
                    {
                        "id": client.MODEL,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "xiaomi-mimo",
                        "permission": [],
                        "root": client.MODEL,
                        "parent": None,
                    }
                ]
            })
            return
        self._send_json(404, {"error": {"message": "Not found", "type": "not_found"}})

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                self._send_json(400, {"error": {"message": "Empty request body", "type": "invalid_request"}})
                return

            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json(400, {"error": {"message": "Invalid JSON", "type": "invalid_request"}})
                return

            messages = data.get("messages", [])
            stream = data.get("stream", False)
            request_id = random_id()

            if stream:
                try:
                    gen = client.chat(messages, stream=True)
                    self._send_sse(gen, request_id)
                except Exception as e:
                    self._send_json(500, {"error": {"message": str(e), "type": "internal_error"}})
            else:
                try:
                    result = client.chat(messages, stream=False)
                    self._send_json(200, result)
                except Exception as e:
                    self._send_json(500, {"error": {"message": str(e), "type": "internal_error"}})
            return

        self._send_json(404, {"error": {"message": "Not found", "type": "not_found"}})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()


# ═══════════════════════════════════════════════════════
#  启动服务
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"🚀 MiMo Auto 2API Server 启动于 http://0.0.0.0:{PORT}")
    print(f"   接口: /v1/models, /v1/chat/completions")
    print(f"   模型: {client.MODEL} ({client.MODEL_NAME})")
    print(f"   重试: {client.MAX_RETRIES}次, 退避: {client.RETRY_DELAY_BASE}s")
    print(f"   按 Ctrl+C 停止服务")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n✅ 服务已停止")
