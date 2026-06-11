#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║                    MiMo Auto 2API (逆向工程) v1.1.0                      ║
╠══════════════════════════════════════════════════════════════════════╣
║  原始仓库: https://github.com/XiaomiMiMo/MiMo-Code                   ║
║  模型:    MiMo Auto (mimo-auto)                                      ║
║  功能:    无需登录, 免费使用的小米大模型 API                             ║
╚══════════════════════════════════════════════════════════════════════╝

API Endpoints:
  - Bootstrap: POST https://api.xiaomimimo.com/api/free-ai/bootstrap
  - Chat:      POST https://api.xiaomimimo.com/api/free-ai/openai/chat
  - 模型名:     mimo-auto

特点:
  ✓ 无需 API Key (匿名使用)
  ✓ 支持文本/图像输入
  ✓ 支持 Tool Call
  ✓ 支持 Reasoning
  ✓ 上下文 1M tokens, 输出 128K tokens
  ✓ 自动重试 (3次, 指数退避)
"""

import hashlib
import json as json_mod
import os
import platform
import time

# 优先用原生 HTTP, 性能更好


class MiMoAutoClient:
    """
    MiMo Auto API 客户端
    
    使用原生 Python 请求, 无需额外依赖 (除了标准库)
    
    示例:
        client = MiMoAutoClient()
        response = client.chat("Hello, how are you?")
        print(response)
    """

    BASE_URL = "https://api.xiaomimimo.com"
    BOOTSTRAP_URL = f"{BASE_URL}/api/free-ai/bootstrap"
    CHAT_URL = f"{BASE_URL}/api/free-ai/openai/chat"
    MODEL = "mimo-auto"
    MAX_RETRIES = 3  # 最大重试次数
    RETRY_DELAY_BASE = 2  # 基础退避秒数

    def __init__(self):
        self._jwt = None
        self._jwt_exp = 0
        self._fingerprint = self._generate_fingerprint()

    @staticmethod
    def _generate_fingerprint():
        """生成客户端指纹用于身份跟踪"""
        seed_parts = [
            os.uname().nodename,
            platform.system(),
            platform.machine(),
            platform.processor() or "unknown-cpu",
            os.getlogin() if hasattr(os, "getlogin") else "unknown-user"
        ]
        seed = "|".join(seed_parts)
        return hashlib.sha256(seed.encode()).hexdigest()

    # ────────────────────────────────
    # 通用重试请求 (带指数退避)
    # ────────────────────────────────
    def _request_with_retry(self, method, url, headers=None, body=None, timeout=30):
        """发送 HTTP 请求, 失败自动重试 3 次, 带指数退避"""
        import urllib.request
        import urllib.error
        import ssl
        import socket

        last_exception = None

        for attempt in range(self.MAX_RETRIES):
            try:
                req_body = None
                if body:
                    if isinstance(body, dict):
                        req_body = json_mod.dumps(body).encode("utf-8")
                    elif isinstance(body, str):
                        req_body = body.encode("utf-8")
                    else:
                        req_body = body

                req = urllib.request.Request(
                    url,
                    data=req_body,
                    method=method,
                    headers=headers or {}
                )

                # 禁用 SSL 验证 (某些环境需要)
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                    resp_body = resp.read()
                    status = resp.getcode()
                    return status, dict(resp.headers), resp_body

            except urllib.error.HTTPError as e:
                last_exception = e
                status = e.code
                # 401/403 不需要重试 (Token 问题, 直接刷新)
                if status in __import__('urllib.error').error.HTTPError.__bases__[0].__subclasses__():
                    pass
                # 4xx 客户端错误不重试 (除 429)
                if 400 <= status < 500 and status != 429:
                    raise Exception(f"HTTP {status}: {e.reason}") from e
                # 其他错误继续重试
                pass
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
                last_exception = e
                pass
            except Exception as e:
                last_exception = e
                pass

            # 非最后一次, 指数退避
            if attempt < self.MAX_RETRIES - 1:
                delay = self.RETRY_DELAY_BASE ** (attempt + 1)
                print(f"[!] 请求失败 ({attempt + 1}/{self.MAX_RETRIES}), {delay}s 后重试...")
                time.sleep(delay)

        raise Exception(f"请求失败, 已重试 {self.MAX_RETRIES} 次: {last_exception}") from last_exception

    # ────────────────────────────────
    # JWT 管理
    # ────────────────────────────────
    def _bootstrap(self):
        """从 bootstrap 接口获取 JWT Token (带重试)"""
        status, _, body = self._request_with_retry(
            "POST", self.BOOTSTRAP_URL,
            headers={"Content-Type": "application/json"},
            body={"client": self._fingerprint},
            timeout=30
        )

        if status != 200:
            raise Exception(f"Bootstrap failed: {status}")

        data = json_mod.loads(body.decode("utf-8"))
        jwt = data.get("jwt")
        if not jwt:
            raise Exception(f"No JWT in response: {body[:500]}")

        # 解析过期时间
        try:
            payload = json_mod.loads(
                jwt.split(".")[1]
                  .replace("-", "+")
                  .replace("_", "/")
                  .encode()
            )
            exp = payload.get("exp", 0) * 1000
        except:
            exp = (time.time() + 50 * 60) * 1000

        self._jwt = jwt
        self._jwt_exp = exp
        print(f"[✓] JWT 获取成功 (过期: {int(exp/1000)})")
        return jwt

    def _get_jwt(self):
        """获取有效的 JWT (自动刷新)"""
        if self._jwt and self._jwt_exp - time.time() * 1000 > 5 * 60 * 1000:
            return self._jwt
        return self._bootstrap()

    # ────────────────────────────────
    # 核心: 聊天的非流式请求 (带重试)
    # ────────────────────────────────
    def chat(self, messages, stream=False):
        """
        与 MiMo Auto 进行对话

        Args:
            messages: list of dict, e.g. [{"role": "user", "content": "Hello"}]
            stream: bool, 是否使用流式输出

        Returns:
            dict: 完整的 API response
        """
        jwt = self._get_jwt()

        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        payload = {
            "model": self.MODEL,
            "messages": messages,
            "stream": stream
        }

        headers = {
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
            "X-Mimo-Source": "mimocode-cli-free",
        }

        last_exception = None
        for attempt in range(self.MAX_RETRIES):
            try:
                status, _, body = self._request_with_retry(
                    "POST", self.CHAT_URL,
                    headers=headers,
                    body=payload,
                    timeout=60
                )

                if status in (401, 403):
                    # Token 过期, 刷新后重试
                    print(f"[!] Token 过期, 刷新中... (尝试 {attempt + 1})")
                    self._jwt = None
                    jwt = self._get_jwt()
                    headers["Authorization"] = f"Bearer {jwt}"
                    continue

                if status != 200:
                    raise Exception(f"Chat failed: {status} - {body[:500]}")

                return json_mod.loads(body.decode("utf-8"))

            except Exception as e:
                last_exception = e
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAY_BASE ** (attempt + 1)
                    print(f"[!] 聊天请求失败 ({attempt + 1}/{self.MAX_RETRIES}), {delay}s 后重试...")
                    time.sleep(delay)

        raise Exception(f"聊天请求失败, 已重试 {self.MAX_RETRIES} 次: {last_exception}") from last_exception

    # ────────────────────────────────
    # 便捷方法
    # ────────────────────────────────
    def ask(self, message, system="") -> str:
        """简单问一个问题, 返回完整回答"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": message})

        result = self.chat(messages, stream=False)
        if result and "choices" in result:
            return result["choices"][0]["message"]["content"]
        return str(result)


# ═══════════════════════════════════════════════
# 测试代码
# ═══════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    print("=" * 60)
    print("MiMo Auto 2API - Reverse Engineered by ChatGPT")
    print("=" * 60)
    print(f"最大重试次数: {MiMoAutoClient.MAX_RETRIES}, 指数退避基数: {MiMoAutoClient.RETRY_DELAY_BASE}s")
    print()

    client = MiMoAutoClient()

    # 测试 1: 简单问答
    print("[测试 1] 简单问答...")
    try:
        answer = client.ask("用一句话介绍一下你自己")
        print(f"[✓] 回答: {answer}")
    except Exception as e:
        print(f"[!] 失败: {e}")

    print()

    # 测试 2: 多轮对话
    print("[测试 2] 多轮对话...")
    try:
        result = client.chat([
            {"role": "system", "content": "你是一个 Python 专家。"},
            {"role": "user", "content": "Python 的装饰器是什么?"},
        ])
        print(f"[✓] 回答: {result['choices'][0]['message']['content'][:200]}...")
    except Exception as e:
        print(f"[!] 失败: {e}")
