"""``orzmc mcp``:stdio JSON-RPC 2.0 server(规范 ``cli-surface.md`` §8.2)。

把 CLI 命令暴露为 MCP tools——``tools/list`` 由 ``capabilities``(``introspect``)生成,
``tools/call`` 在**进程内**转调对应命令的 ``--json`` 路径,信封原样作为 tool 结果返回。
不新增运行时依赖:JSON-RPC 是规范约定的最小子集,手写实现。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from typing import IO

import typer
from typer.testing import CliRunner

from orzmc_app import __version__ as APP_VERSION
from orzmc_app.cli.introspect import command_specs

# 不暴露给 AI 的元 / 自毁命令(§8.2):无操作价值或可能破坏工具自身。
EXCLUDED_TOOLS = frozenset({"capabilities", "completion", "mcp", "uninstall"})

# 缺省 MCP 协议版本;客户端在 initialize 里报了版本就回显它。
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

_RUNNER = CliRunner()

# ── JSON-RPC 错误码 ─────────────────────────────────────────────────────────
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


class _InvalidParams(Exception):
    """工具参数不合法(未知参数 / 类型不符 / 缺必填)。"""


# ── 消息构造 ────────────────────────────────────────────────────────────────


def _result(msg_id: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: object, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


# ── capabilities → MCP tools ───────────────────────────────────────────────


def _names(option: dict[str, object]) -> list[str]:
    names = option["names"]
    assert isinstance(names, list)
    return [str(name) for name in names]


def _long_name(names: list[str]) -> str | None:
    """取长选项(``--x``);位置参数没有长名,返回 ``None``。"""
    return next((name for name in names if name.startswith("--")), None)


def _parameter_name(option: dict[str, object]) -> str:
    names = _names(option)
    long = _long_name(names)
    return long[2:] if long is not None else names[0]


def _schema_type(option: dict[str, object]) -> str:
    if option["flag"]:
        return "boolean"
    type_name = option.get("type")
    if type_name in ("int", "integer"):
        return "integer"
    if type_name == "float":
        return "number"
    if type_name in ("bool", "boolean"):
        return "boolean"
    return "string"


def _input_schema(options: list[dict[str, object]]) -> dict[str, object]:
    properties: dict[str, object] = {}
    required: list[str] = []
    for option in options:
        name = _parameter_name(option)
        prop: dict[str, object] = {"type": _schema_type(option)}
        if option.get("help"):
            prop["description"] = option["help"]
        if option.get("default") is not None and not option["flag"]:
            prop["default"] = option["default"]
        if option.get("enum"):
            prop["enum"] = option["enum"]
        properties[name] = prop
        if option.get("required"):
            required.append(name)
    schema: dict[str, object] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def build_tools(tool_app: typer.Typer) -> list[dict[str, object]]:
    """每个规范命令一个 tool(排除 §8.2 的元 / 自毁命令)。"""
    tools: list[dict[str, object]] = []
    for spec in command_specs(tool_app):
        if spec["name"] in EXCLUDED_TOOLS:
            continue
        options = spec["options"]
        assert isinstance(options, list)
        tools.append(
            {
                "name": spec["name"],
                "description": spec["help"] or "",
                "inputSchema": _input_schema(options),
            }
        )
    return tools


# ── tools/call:参数 → argv ──────────────────────────────────────────────────


def _coerce(option: dict[str, object], name: str, value: object) -> object:
    if option["flag"]:
        if not isinstance(value, bool):
            raise _InvalidParams(f"参数 {name} 应为布尔值")
        return value
    schema_type = _schema_type(option)
    if schema_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise _InvalidParams(f"参数 {name} 应为整数")
        return value
    if not isinstance(value, str):
        raise _InvalidParams(f"参数 {name} 应为字符串")
    return value


def build_argv(options: list[dict[str, object]], arguments: dict[str, object]) -> list[str]:
    """按命令声明的顺序把工具参数拼成 CLI argv(位置参数保持原位)。"""
    by_name = {_parameter_name(option): option for option in options}

    unknown = sorted(set(arguments) - set(by_name))
    if unknown:
        raise _InvalidParams(f"未知参数: {', '.join(unknown)}")

    argv: list[str] = []
    for option in options:
        name = _parameter_name(option)
        if name not in arguments:
            if option.get("required"):
                raise _InvalidParams(f"缺少必填参数: {name}")
            continue
        value = _coerce(option, name, arguments[name])
        long = _long_name(_names(option))
        if option["flag"]:
            argv.append(long if long is not None else f"--{name}")
        elif long is None:
            argv.append(str(value))
        else:
            argv.extend([long, str(value)])
    return argv


def _find_envelope(stdout: str) -> dict[str, object] | None:
    """从命令 stdout 里取回 ``--json`` 信封(stdout 应恰好一条,容错取最后一条)。"""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("ok"), bool):
            return payload
    return None


def call_tool(tool_app: typer.Typer, spec: dict[str, object], arguments: dict[str, object]) -> dict[str, object]:
    """转调命令的 ``--json`` 路径,把信封包装为 MCP tool 结果。"""
    options = spec["options"]
    assert isinstance(options, list)
    argv = build_argv(options, arguments)
    result = _RUNNER.invoke(tool_app, ["--json", str(spec["name"]), *argv])
    envelope = _find_envelope(result.stdout)
    if envelope is None:
        stderr_lines = [line for line in (result.stderr or "").splitlines() if line.strip()]
        message = stderr_lines[-1] if stderr_lines else f"命令 {spec['name']} 失败(退出码 {result.exit_code})"
        envelope = {
            "ok": False,
            "command": spec["name"],
            "data": None,
            "error": {"code": "E_RUNTIME", "message": message},
        }
    return {
        "content": [{"type": "text", "text": json.dumps(envelope, ensure_ascii=False)}],
        "isError": not bool(envelope.get("ok")),
    }


# ── 消息分发 ────────────────────────────────────────────────────────────────


def handle_message(tool_app: typer.Typer, message: object) -> dict[str, object] | None:
    """处理一条 JSON-RPC 消息;通知(无 ``id``)返回 ``None``(不回复)。"""
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "invalid request")
    msg_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        if msg_id is None:
            return None
        return _error(msg_id, INVALID_REQUEST, "invalid request")
    if msg_id is None and method.startswith("notifications/"):
        return None
    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _error(msg_id, INVALID_PARAMS, "invalid params")
    if method == "initialize":
        protocol = params.get("protocolVersion")
        return _result(
            msg_id,
            {
                "protocolVersion": protocol if isinstance(protocol, str) and protocol else DEFAULT_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "orzmc", "version": APP_VERSION},
            },
        )
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": build_tools(tool_app)})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error(msg_id, INVALID_PARAMS, "invalid params")
        specs = {str(spec["name"]): spec for spec in command_specs(tool_app)}
        spec = specs.get(name)
        if spec is None or name in EXCLUDED_TOOLS:
            return _error(msg_id, INVALID_PARAMS, f"未知 tool: {name}")
        try:
            tool_result = call_tool(tool_app, spec, arguments)
        except _InvalidParams as exc:
            return _error(msg_id, INVALID_PARAMS, str(exc))
        return _result(msg_id, tool_result)
    return _error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")


def serve(
    tool_app: typer.Typer,
    *,
    stdin: Iterable[str] | None = None,
    stdout: IO[str] | None = None,
) -> None:
    """stdio 循环:逐行读 JSON-RPC、逐行写响应(进程内转调)。

    stdin / stdout 可注入,便于离线测试;缺省用真实 stdio。stdout **只**写
    JSON-RPC(日志 / 进度由被转调命令写到 stderr,或被 CliRunner 捕获)。
    """
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    for raw in input_stream:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response: dict[str, object] | None = _error(None, PARSE_ERROR, "parse error")
        else:
            response = handle_message(tool_app, message)
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            output_stream.flush()
