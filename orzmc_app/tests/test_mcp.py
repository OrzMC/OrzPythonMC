"""``orzmc mcp`` 离线单测:JSON-RPC 映射与 tools/call 转调(不打网络)。"""

from __future__ import annotations

import io
import json
from typing import Any

from orzmc_app import __version__
from orzmc_app.cli import app
from orzmc_app.cli.mcp import DEFAULT_PROTOCOL_VERSION, handle_message, serve


def _call(message: dict[str, Any]) -> dict[str, Any]:
    response = handle_message(app, message)
    assert response is not None
    return response


def _request(method: str, params: dict[str, Any] | None = None, msg_id: int = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _tool_result(response: dict[str, Any]) -> dict[str, Any]:
    assert "result" in response, response
    return response["result"]


def _envelope(response: dict[str, Any]) -> dict[str, Any]:
    text = _tool_result(response)["content"][0]["text"]
    return json.loads(text)


# ── initialize ──────────────────────────────────────────────────────────────


def test_initialize_echoes_protocol_version() -> None:
    response = _call(_request("initialize", {"protocolVersion": "2024-11-05"}))
    result = _tool_result(response)
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"] == {"name": "orzmc", "version": __version__}
    assert "tools" in result["capabilities"]


def test_initialize_default_protocol_version() -> None:
    result = _tool_result(_call(_request("initialize")))
    assert result["protocolVersion"] == DEFAULT_PROTOCOL_VERSION


# ── tools/list ──────────────────────────────────────────────────────────────


def test_tools_list_excludes_meta_commands() -> None:
    tools = _tool_result(_call(_request("tools/list")))["tools"]
    names = {tool["name"] for tool in tools}
    assert {"client", "server", "list", "app", "remove"} <= names
    assert names.isdisjoint({"capabilities", "completion", "mcp", "uninstall"})


def test_tools_list_schema_shapes() -> None:
    tools = {tool["name"]: tool for tool in _tool_result(_call(_request("tools/list")))["tools"]}

    app_schema = tools["app"]["inputSchema"]
    assert app_schema["properties"]["action"]["type"] == "string"
    assert app_schema["required"] == ["action"]
    assert app_schema["properties"]["yes"]["type"] == "boolean"

    remove_schema = tools["remove"]["inputSchema"]
    assert remove_schema["required"] == ["version"]

    client_schema = tools["client"]["inputSchema"]
    assert client_schema["properties"]["download-threads"]["type"] == "integer"
    # 位置参数与选项都来自 capabilities,未知键被拒。
    assert client_schema["additionalProperties"] is False


# ── tools/call:成功 ─────────────────────────────────────────────────────────


def test_tools_call_list_success(tmp_path) -> None:
    response = _call(_request("tools/call", {"name": "list", "arguments": {"root-dir": str(tmp_path)}}))
    result = _tool_result(response)
    assert result["isError"] is False
    envelope = _envelope(response)
    assert envelope["ok"] is True
    assert envelope["command"] == "list"
    assert envelope["data"] == {"versions": []}


# ── tools/call:业务失败 → isError ──────────────────────────────────────────


def test_tools_call_business_error_is_tool_error() -> None:
    # `client -t paper` 在解析类型时即失败(不发网络请求)。
    response = _call(_request("tools/call", {"name": "client", "arguments": {"type": "paper"}}))
    result = _tool_result(response)
    assert result["isError"] is True
    envelope = _envelope(response)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "E_UNSUPPORTED"


def test_tools_call_app_argument_maps_to_positional(tmp_path) -> None:
    # `app status` 在非 macOS 上由库显式拒绝;这里只断言参数被当成位置参数送达。
    response = _call(_request("tools/call", {"name": "app", "arguments": {"action": "status"}}))
    envelope = _envelope(response)
    assert envelope["command"] == "app"


# ── 参数校验(RPC 级错误)────────────────────────────────────────────────────


def test_tools_call_unknown_tool() -> None:
    response = _call(_request("tools/call", {"name": "nope", "arguments": {}}))
    assert response["error"]["code"] == -32602


def test_tools_call_unknown_argument() -> None:
    response = _call(_request("tools/call", {"name": "list", "arguments": {"bogus": 1}}))
    assert response["error"]["code"] == -32602


def test_tools_call_missing_required() -> None:
    response = _call(_request("tools/call", {"name": "remove", "arguments": {}}))
    assert response["error"]["code"] == -32602
    assert "version" in response["error"]["message"]


def test_tools_call_wrong_type() -> None:
    response = _call(_request("tools/call", {"name": "client", "arguments": {"download-threads": "many"}}))
    assert response["error"]["code"] == -32602


def test_unknown_method() -> None:
    response = _call(_request("tools/frobnicate"))
    assert response["error"]["code"] == -32601


# ── serve:stdio 帧 ──────────────────────────────────────────────────────────


def test_serve_roundtrip_and_clean_stdout() -> None:
    lines = [
        json.dumps(_request("initialize")) + "\n",
        "\n",
        json.dumps(_request("tools/list", msg_id=2)) + "\n",
    ]
    stdout = io.StringIO()
    serve(app, stdin=iter(lines), stdout=stdout)
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r["id"] for r in responses] == [1, 2]


def test_serve_parse_error_and_notification() -> None:
    lines = ["{not json}\n", json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"]
    stdout = io.StringIO()
    serve(app, stdin=iter(lines), stdout=stdout)
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(responses) == 1
    assert responses[0]["error"]["code"] == -32700
    assert responses[0]["id"] is None
