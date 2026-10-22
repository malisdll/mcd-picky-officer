"""麦当劳 MCP 客户端（Streamable HTTP + JSON-RPC）。

只用标准库，不引入 mcp SDK —— 目的是让「三行命令跑起来」真的成立，
而不是先花二十分钟配环境。

Token 只从环境变量读取，任何函数都不接受「把 token 写进命令行参数」的用法，
避免它出现在 shell history 和进程列表里。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "mcd-picky-officer"
CLIENT_VERSION = "0.1.0"


class McpError(RuntimeError):
    """MCP 调用失败。消息里不会包含 Token。"""


def token_from_env() -> str:
    return os.environ.get("MCD_MCP_TOKEN", "").strip()


def mask(token: str) -> str:
    if not token:
        return "(未设置)"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}{'*' * 12}{token[-4:]}"


class McdClient:
    def __init__(self, url: str | None = None, token: str | None = None, timeout: int = 30) -> None:
        self.url = (url or os.environ.get("MCD_MCP_URL") or DEFAULT_URL).rstrip("/")
        self.token = (token or token_from_env()).strip()
        if not self.token:
            raise McpError(
                "缺少 MCD_MCP_TOKEN。请先在 https://open.mcd.cn/mcp 申请 Token，"
                "然后设置环境变量 MCD_MCP_TOKEN（不要写进代码或配置文件）。"
            )
        self.timeout = timeout
        self.session_id: str | None = None
        self._id = 0

    # ---- 协议层 ----
    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": f"{CLIENT_NAME}/{CLIENT_VERSION}",
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _post(self, payload: dict[str, Any], expect_response: bool = True) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                ctype = (resp.headers.get("Content-Type") or "").lower()
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            if e.code == 429:
                raise McpError("触发限流（429）。官方限制为每分钟 600 次请求，请降低频率。") from e
            if e.code in (401, 403):
                raise McpError(
                    f"鉴权失败（HTTP {e.code}）。三种可能：\n"
                    "  1. Token 粘贴时带了多余空格或引号\n"
                    "  2. Token 已失效，请到 https://open.mcd.cn/mcp 控制台重新激活\n"
                    "  3. 环境变量没生效（换一个终端窗口再试）\n"
                    f"  服务端返回：{detail[:200]}"
                ) from e
            raise McpError(f"HTTP {e.code}：{detail}") from e
        except urllib.error.URLError as e:
            raise McpError(f"网络错误：{e.reason}") from e

        if not expect_response or not raw.strip():
            return {}
        if "text/event-stream" in ctype:
            parsed = self._parse_sse(raw)
        else:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                raise McpError(f"响应不是合法 JSON：{raw[:200]}") from e
        if "error" in parsed:
            err = parsed["error"]
            raise McpError(f"MCP 返回错误：{err.get('message', err)}")
        return parsed

    @staticmethod
    def _parse_sse(raw: str) -> dict[str, Any]:
        """解析 SSE 流，取最后一个能当作 JSON-RPC 响应的 data 块。"""
        last: dict[str, Any] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                obj = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                last = obj
        if not last:
            raise McpError("SSE 响应里没有找到 JSON-RPC 结果")
        return last

    # ---- 语义层 ----
    def initialize(self) -> dict[str, Any]:
        res = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        })
        # 握手完成的通知，服务端不回包
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)
        return res.get("result", {})

    def list_tools(self) -> list[dict[str, Any]]:
        res = self._post({"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}})
        return res.get("result", {}).get("tools", []) or []

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        res = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        result = res.get("result", {})
        if result.get("isError"):
            raise McpError(f"工具 {name} 执行失败：{result}")

        # 麦当劳的业务错误不走 JSON-RPC error，而是塞在 structuredContent.success 里。
        # 不检查这一层的话，会把「城市名不能为空」当成成功返回，然后静默拿到空数据。
        sc = result.get("structuredContent")
        if isinstance(sc, dict) and sc.get("success") is False:
            raise McpError(f"工具 {name} 业务失败（code={sc.get('code')}）：{sc.get('message')}")
        return result

    # ---- 便捷方法（工具名以实际 tools/list 结果为准，这里做存在性检查）----
    def tool_names(self) -> list[str]:
        return [t.get("name", "") for t in self.list_tools()]

    def pick_tool(self, *candidates: str) -> str | None:
        names = self.tool_names()
        lowered = {n.lower(): n for n in names}
        for c in candidates:
            if c.lower() in lowered:
                return lowered[c.lower()]
        for c in candidates:
            for n in names:
                if c.lower() in n.lower():
                    return n
        return None
