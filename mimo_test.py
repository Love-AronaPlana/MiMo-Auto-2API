#!/usr/bin/env python3
"""
MiMo Auto API 逆向工程测试

API 域名: https://api.xiaomimimo.com
1. Bootstrap: POST /api/free-ai/bootstrap -> 获取临时 JWT
2. Chat:      POST /api/free-ai/openai/chat (替换 chat/completions)

模型: mimo-auto
支持: text/image input, text output, tool_call, reasoning
上下文: 1M tokens, 输出: 128K tokens
"""

import hashlib
import json
import os
import platform
import requests
import time
import uuid

# ==== 配置 ====
BASE_URL = "https://api.xiaomimimo.com"
BOOTSTRAP_URL = f"{BASE_URL}/api/free-ai/bootstrap"
CHAT_URL = f"{BASE_URL}/api/free-ai/openai/chat"

MODEL = "mimo-auto"

# ==== 生成客户端指纹 ====
def get_client_fingerprint():
    """生成唯一的客户端指纹 (Persistent)"""
    # 基于机器信息生成一个不变的指纹
    seed_parts = [
        os.uname().nodename,
        platform.system(),
        platform.machine(),
        platform.processor() or "unknown-cpu",
        os.getlogin() if hasattr(os, "getlogin") else "unknown-user"
    ]
    seed = "|".join(seed_parts)
    return hashlib.sha256(seed.encode()).hexdigest()


# ==== 获取 JWT ====
_cache = {"jwt": None, "exp": 0}

def bootstrap():
    """从 bootstrap 接口获取 JWT"""
    fingerprint = get_client_fingerprint()
    print(f"[*] 客户端指纹: {fingerprint[:32]}...")
    print(f"[*] 请求 Bootstrap: {BOOTSTRAP_URL}")

    resp = requests.post(
        BOOTSTRAP_URL,
        headers={"Content-Type": "application/json"},
        json={"client": fingerprint},
        timeout=30
    )
    print(f"[*] Bootstrap 响应状态: {resp.status_code}")
    if resp.status_code != 200:
        print(f"[!] Bootstrap 失败: {resp.status_code} - {resp.text[:500]}")
        return None

    data = resp.json()
    jwt = data.get("jwt")
    if not jwt:
        print(f"[!] 响应中无 JWT: {json.dumps(data, ensure_ascii=False)[:500]}")
        return None

    # 解析过期时间
    try:
        payload = json.loads(
            # jwt payload 中间那段
            jwt.split(".")[1].encode()
              .replace(b"-", b"+")
              .replace(b"_", b"/")
        )
        exp = payload.get("exp", 0) * 1000
    except:
        exp = (time.time() + 50 * 60) * 1000

    _cache["jwt"] = jwt
    _cache["exp"] = exp
    print(f"[*] 获取 JWT 成功, 过期时间: {int(exp/1000)}")
    return jwt


def get_jwt():
    """获取或刷新 JWT"""
    if _cache["jwt"] and _cache["exp"] - time.time() * 1000 > 5 * 60 * 1000:
        return _cache["jwt"]
    return bootstrap()


# ==== 发起聊天请求 ====
def chat_completion(messages, model=MODEL, stream=False, max_retries=2):
    """与 MiMo Auto 模型进行对话"""
    jwt = get_jwt()
    if not jwt:
        print("[!] 无法获取 JWT, 聊天失败")
        return None

    payload = {
        "model": model,
        "messages": messages,
        "stream": stream
    }

    headers = {
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
        "X-Mimo-Source": "mimocode-cli-free",
    }

    print(f"[*] 请求 Chat: {CHAT_URL}")
    print(f"[*] 模型: {model}, Stream: {stream}")

    try:
        resp = requests.post(
            CHAT_URL,
            headers=headers,
            json=payload,
            stream=stream,
            timeout=60
        )
        print(f"[*] 响应状态: {resp.status_code}")

        if resp.status_code in (401, 403) and max_retries > 0:
            print(f"[!] Token 可能过期, 重试中...")
            _cache["jwt"] = None
            return chat_completion(messages, model, stream, max_retries - 1)

        if resp.status_code != 200:
            print(f"[!] 请求失败: {resp.status_code} - {resp.text[:500]}")
            return None

        if stream:
            # SSE Stream
            for line in resp.iter_lines():
                if line:
                    line = line.decode("utf-8")
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            if "choices" in chunk and chunk["choices"]:
                                content = chunk["choices"][0].get("delta", {}).get("content", "")
                                if content:
                                    print(content, end="", flush=True)
                        except (json.JSONDecodeError, IndexError):
                            pass
            print()
        else:
            # 非流式
            try:
                result = resp.json()
                choice = result["choices"][0]
                content = choice["message"]["content"]
                return content
            except (KeyError, IndexError) as e:
                print(f"[!] 响应解析失败: {e}")
                print(f"原始响应: {resp.text[:500]}")
                return None
    except requests.exceptions.RequestException as e:
        print(f"[!] 请求异常: {e}")
        return None


# ==== 主程序 ====
if __name__ == "__main__":
    print("=" * 60)
    print("MiMo Auto API 逆向工程 - 测试脚本")
    print("=" * 60)
    print()

    # 测试 1: Bootstrap 获取 JWT
    print("[1] 测试 Bootstrap...")
    jwt = bootstrap()
    if not jwt:
        print("[!] Bootstrap 失败, 程序退出")
        exit(1)
    print()

    # 测试 2: 简单聊天 (非流式)
    print("[2] 测试简单聊天 (非流式)...")
    messages = [
        {"role": "system", "content": "你是一个 helpful assistant."},
        {"role": "user", "content": "你好, 请简单介绍一下你自己"}
    ]
    result = chat_completion(messages, stream=False)
    if result:
        print(f"[✓] 响应: {result[:300]}...")
    print()

    # 测试 3: 流式聊天
    print("[3] 测试流式聊天...")
    messages.append({"role": "assistant", "content": result or ""})
    messages.append({"role": "user", "content": "用一句话总结"})

    stream_result = chat_completion(messages, stream=True)
    print()
    print("[✓] 流式输出结束")
