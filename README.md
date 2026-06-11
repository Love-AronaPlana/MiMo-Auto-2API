# MiMo Auto 2API 逆向工程

> 从 `MiMo-Code` 项目中逆向出来的 **MiMo Auto** 免费大模型 API。

## 📦 项目信息

| 项目 | 详情 |
|------|------|
| **原始仓库** | [github.com/XiaomiMiMo/MiMo-Code](https://github.com/XiaomiMiMo/MiMo-Code) |
| **模型名称** | `mimo-auto` |
| **模型DisplayName** | MiMo Auto |
| **提供商** | 小米 (Xiaomi MiMo) |
| **授权方式** | 匿名 (无需登录, 自动获取临时 JWT) |
| **费用** | 免费 |

---

## 📡 API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `https://api.xiaomimimo.com/api/free-ai/bootstrap` | POST | 获取临时 JWT Token |
| `https://api.xiaomimimo.com/api/free-ai/openai/chat` | POST | 聊天接口 |

> 注意: 聊天接口端点把 `/v1/chat/completions` 改成了 `/chat`, 其余与 OpenAI 兼容。

---

## 🔐 认证机制

### 1. Bootstrap (获取临时 JWT)

```
POST https://api.xiaomimimo.com/api/free-ai/bootstrap
Content-Type: application/json

{"client": "<客户端指纹>"}
```

**响应:**
```json
{
  "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

### 2. 客户端指纹生成

```
fingerprint = SHA256(hostname + platform + machine + processor + username)
```

### 3. 请求头

```
Authorization: Bearer <jwt>
Content-Type: application/json
X-Mimo-Source: mimocode-cli-free
```

---

## 💬 聊天接口

### 请求

```
POST https://api.xiaomimimo.com/api/free-ai/openai/chat
Content-Type: application/json
Authorization: Bearer <jwt>
X-Mimo-Source: mimocode-cli-free

{
  "model": "mimo-auto",
  "messages": [...],
  "stream": false
}
```

### 响应 (非流式)

```json
{
  "id": "...",
  "choices": [{
    "finish_reason": "stop",
    "index": 0,
    "message": {
      "content": "回答内容",
      "role": "assistant",
      "tool_calls": null,
      "reasoning_content": "推理过程..."
    }
  }],
  "model": "mimo-auto",
  "object": "chat.completion",
  "usage": {
    "completion_tokens": 68,
    "prompt_tokens": 252,
    "total_tokens": 320,
    "completion_tokens_details": {"reasoning_tokens": 64},
    "prompt_tokens_details": {"cached_tokens": 192}
  }
}
```

### 响应 (流式 SSE)

```
data: {"id":"...","object":"chat.completion.chunk","created":...,"model":"mimo-auto","choices":[{"index":0,"delta":{...},"finish_reason":null}]}

data: [DONE]
```

---

## ⚡ 模型特性

| 特性 | 支持情况 |
|------|---------|
| 文本输入 | ✅ |
| 图像输入 | ✅ (modality 支持) |
| 文本输出 | ✅ |
| Tool Call | ✅ |
| Reasoning | ✅ (reasoning_content 字段) |
| 最大上下文 | 1,000,000 tokens |
| 最大输出 | 128,000 tokens |
| 费用 | 免费 ($0 输入/输出) |

---

## 🚀 2API Server 使用方法

### 启动服务

```bash
python3 mimo_auto_server.py
# 默认端口 8080, 自定义: python3 mimo_auto_server.py <port>
```

### API 调用

**获取模型列表:**
```bash
curl http://127.0.0.1:8080/v1/models
```

**非流式聊天:**
```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mimo-auto",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

**流式聊天:**
```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mimo-auto",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

---

## 🐍 Python 客户端使用

```python
from mimo_auto_2api import MiMoAutoClient

client = MiMoAutoClient()

# 简单问答
answer = client.ask("你好, 你是谁?")
print(answer)

# 流式输出
for chunk in client.ask_stream("Hello"):
    print(chunk, end="", flush=True)
```

---

## 📄 文件说明

| 文件 | 说明 |
|------|------|
| `mimo_auto_2api.py` | Python 客户端, 可直接导入使用 |
| `mimo_auto_server.py` | HTTP 代理服务器, OpenAI API 兼容 |
| `mimo_test.py` | 原始测试脚本 |

---

## ⚠️ 免责声明

本项目仅供学习研究目的。API 归属小米 (MiMo) 所有, 使用需遵守相关服务条款。该项目不保证服务的可用性和稳定性。

---

> 逆向完成时间: 2026-06-11
> 原始来源: MiMo-Code v0.1.0 `packages/opencode/src/plugin/mimo-free.ts`
