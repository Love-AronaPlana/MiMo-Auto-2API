# MiMo Auto 2API Server

> 开源的 **MiMo Auto** (MiMo-v2.5) 代理服务器，支持 OpenAI API 标准格式，无需登录即可使用。

## ✨ 特性

- **🆓 免费使用**：服务端自动管理 JWT，无需 API Key
- **🔑 任意 api_key**：客户端可以传入任意值（符合 OpenAI SDK 习惯）
- **🤖 模型**：MiMo-v2.5（小米大模型，1M 上下文窗口）
- **📡 OpenAI 标准格式**：`curl` / `openai` SDK 直接兼容
- **🛠️ 工具调用**：完整支持 `tools` / `tool_choice` / `function_calling`
- **📊 流式输出**：SSE 流式与非流式双模式
- **🔄 自动重试**：JWT 过期自动刷新，3次失败重试 + 指数退避
- **💬 中文修复**：强制 UTF-8 编码解析响应，彻底修复 MiMo API 中文乱码
- **🖥️ 跨平台兼容**：支持 Windows / Linux / macOS / Docker 容器运行

---

## 📰 更新日志

### 2025-06-11 — v2.0.1

- **修复中文乱码**：MiMo API 返回 `text/event-stream` 未声明 `charset=utf-8`，导致 `requests` 默认用 `ISO-8859-1` 解码中文成乱码。现已在所有响应上强制 `resp.encoding = "utf-8"` 并改为 `json.loads(resp.text)`，彻底根治。
- **修复 JWT Base64 解析**：`mimo_auto_2api.py` 中 JWT payload 从来没真正 decode 过（直接从 base64url 字符串 `json.loads`），导致过期时间始终走 fallback。补全了 `base64.b64decode` + padding 补齐，现在正常解析 `exp`。
- **跨平台指纹生成**：将 `os.uname().nodename`（仅限 Unix）和裸 `os.getlogin()`（Windows/容器易崩）替换为通用方案：
  - `socket.gethostname()` — 全平台可用
  - 安全的 `os.getlogin()` + `os.environ.get("USERNAME" / "USER")` fallback
- **清理死代码**：删除 `mimo_auto_2api.py` 中一段 `__import__('urllib.error').error.HTTPError.__bases__[0].__subclasses__()` 逻辑错误代码。

---

## 📥 安装

```bash
git clone https://github.com/Love-AronaPlana/MiMo-Auto-2API.git
cd MiMo-Auto-2API
pip install requests
```

---

## 🚀 启动

```bash
python3 mimo_auto_server.py
# 默认端口 8080
```

可选环境变量：

```bash
MIMO_PORT=8080  # 自定义端口
MIMO_HOST=0.0.0.0  # 自定义绑定地址
```

---

## 📡 API 使用

### 获取模型列表

```bash
curl http://127.0.0.1:8080/v1/models
```

### 非流式聊天

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer any-key-you-want" \
  -d '{
    "model": "mimo-auto",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### 流式聊天

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer any-key-you-want" \
  -d '{
    "model": "mimo-auto",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

### 工具调用 (Tool Calling)

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer any-key-you-want" \
  -d '{
    "model": "mimo-auto",
    "messages": [{"role": "user", "content": "What's the weather in Beijing?"}],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "get_weather",
          "description": "Get weather for a location",
          "parameters": {
            "type": "object",
            "properties": {
              "location": {"type": "string"}
            },
            "required": ["location"]
          }
        }
      }
    ],
    "tool_choice": "auto"
  }'
```

---

## 🐍 Python 客户端使用

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key="any-key-you-want"  # 任意值即可
)

# 非流式
response = client.chat.completions.create(
    model="mimo-auto",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)

# 流式
stream = client.chat.completions.create(
    model="mimo-auto",
    messages=[{"role": "user", "content": "Count to 5"}],
    stream=True
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

---

## 🔧 逆向信息

| 项目 | 详情 |
|------|------|
| **原始仓库** | [github.com/XiaomiMiMo/MiMo-Code](https://github.com/XiaomiMiMo/MiMo-Code) |
| **模型** | MiMo-v2.5 (mimo-auto) |
| **上下文** | 1,000,000 tokens |
| **最大输出** | 128,000 tokens |
| **基础 URL** | `https://api.xiaomimimo.com` |
| **Bootstrap** | `POST /api/free-ai/bootstrap` |
| **Chat** | `POST /api/free-ai/openai/chat` |
| **认证** | 匿名（服务端自动获取 JWT） |

---

## ⚠️ 免责声明

本项目仅供学习研究目的。API 归属小米 (MiMo) 所有，使用需遵守相关服务条款。该项目不保证服务的可用性和稳定性。

---

> 逆向完成时间: 2024-06-11
