"""OrzMC Python 一致性适配器 (规范见父仓库 docs/spec/conformance.md).

由父仓库 ``scripts/conformance/run.py`` 以子进程方式驱动: stdin 收一个请求 JSON
(fixture 的 ``given`` + ``id``/``specVersion``), stdout 回一个响应 JSON
``{"ok": true, "data": {...}}`` 或 ``{"ok": false, "code": "...", "message": "..."}``.

本适配器只走 orzmc 库内部 API, 把库的 "纯计划" 语义序列化成规范形状;
不联网, 不碰系统 java, 所有临时文件都在 tmp 目录内 (无持久副作用).

已知桥接: ``PathLayout.java_bin`` 目前是宿主机绑定 (按 ``os.name`` 决定 ``.exe``),
而一致性操作按 ``given.platform`` 求值, 故适配器在宿主机与请求平台不一致时按平台后缀修正;
所有绝对路径归一为规范的逻辑分隔符 ``/``. 这两点在库暴露 "平台参数化纯计划 API" 后即可移除.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from orzmc.core import auth as auth_map
from orzmc.core.mojang import Mojang, VersionEntry
from orzmc.domain.java import required_java_major
from orzmc.domain.launch import DEFAULT_MAIN_CLASS, game_args, jvm_args, memory_args, user_jvm_opts
from orzmc.domain.libraries import os_arch, os_key, resolve_libraries
from orzmc.domain.options import MAX_DOWNLOAD_THREADS, RuntimeOptions
from orzmc.domain.paths import PathLayout
from orzmc.domain.types import GameType
from orzmc.infra.auth_store import FILENAME as AUTH_FILENAME
from orzmc.infra.cache import MetadataCache
from orzmc.infra.fs import FileStore, default_state_dir
from orzmc.infra.hashing import sha1_file
from orzmc.infra.http import HttpClient
from orzmc.infra.runner import normalize_exit_code
from orzmc.infra.transfer import PARALLEL_MIN_SIZE, PARALLEL_PARTS
from orzmc.services.selfinstall import MANIFEST_FILENAME
from orzmc_app.cli.completions import target_path

_DEFAULT_FIXTURES = Path(__file__).resolve().parents[4] / "docs" / "spec" / "fixtures"


# -- 协议辅助 ---------------------------------------------------------------
def ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message}


class ConformanceError(Exception):
    """Raised by an operation to report a spec-level error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> NoReturn:
    raise ConformanceError(code, message)


def _load(rel: str) -> dict[str, Any]:
    base = Path(os.environ.get("ORZMC_CONFORMANCE_FIXTURES") or _DEFAULT_FIXTURES)
    with open(base / rel, encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
        return data


def _norm(path: str) -> str:
    """逻辑路径归一为 ``/`` (规范: 逻辑路径用 /, 各实现按平台转换)."""
    return path.replace("\\", "/")


def _java_exe(lay: PathLayout, major: int, platform: dict[str, str]) -> str:
    exe = _norm(lay.java_bin(major))
    want_exe = (platform.get("os") or os_key()) == "windows"
    if want_exe and not exe.endswith(".exe"):
        exe += ".exe"
    elif not want_exe and exe.endswith(".exe"):
        exe = exe[:-4]
    return exe


def _first_of_channel(manifest: dict[str, Any], channel: str) -> VersionEntry | None:
    for v in manifest.get("versions", []):
        entry = VersionEntry(id=v["id"], type=v.get("type") or "unknown")
        if entry.channel == channel:
            return entry
    return None


# -- operations -------------------------------------------------------------
def op_plan_layout(g: dict[str, Any]) -> dict[str, Any]:
    platform = g.get("platform") or {}
    lay = PathLayout(root=g["root"], version=g["version"], game_type=g.get("type", "vanilla"))
    out: dict[str, Any] = {
        "root": _norm(lay.root),
        "versionsDir": _norm(lay.versions_dir()),
        "versionDir": _norm(lay.version_dir()),
        "clientDir": _norm(lay.client_dir()),
        "clientAssetsDir": _norm(lay.client_assets_dir()),
        "clientIndexesDir": _norm(lay.client_indexes_dir()),
        "clientObjectsDir": _norm(lay.client_objects_dir()),
        "clientLibrariesDir": _norm(lay.client_libraries_dir()),
        "clientNativesDir": _norm(lay.client_natives_dir()),
        "clientJarPath": _norm(lay.client_jar_path()),
        "clientProfilesDir": _norm(lay.client_profiles_dir()),
        "javaDir": _norm(lay.java_dir()),
        "cacheDir": _norm(lay.cache_dir()),
        "versionManifestPath": _norm(lay.version_manifest_path()),
        "versionJsonsDir": _norm(lay.version_jsons_dir()),
        "downloadTmpDir": _norm(lay.download_tmp_dir()),
        "backupDir": _norm(lay.backup_dir()),
    }
    if g.get("type"):
        out.update(
            {
                "serverDir": _norm(lay.server_dir()),
                "serverJarName": os.path.basename(lay.server_jar_path()),
                "serverJarPath": _norm(lay.server_jar_path()),
                "serverEulaPath": _norm(lay.server_eula_path()),
                "serverPropertiesPath": _norm(lay.server_properties_path()),
                "serverPluginsDir": _norm(lay.server_plugins_dir()),
                "serverWorldDir": _norm(lay.server_world_dir()),
            }
        )
    if g.get("javaMajor") is not None:
        out["javaMajorDir"] = _norm(lay.java_major_dir(g["javaMajor"]))
        out["javaExecutable"] = _java_exe(lay, g["javaMajor"], platform)
    if g.get("objectSha1"):
        out["clientObjectPath"] = _norm(lay.client_object_path(g["objectSha1"]))
    return out


def op_required_java(g: dict[str, Any]) -> dict[str, Any]:
    vj = _load(g["snapshot"])
    # distribution / runtimeKind 是规范常量 (库尚未建模), major 来自真实解析.
    return {"majorVersion": required_java_major(vj), "distribution": "temurin", "runtimeKind": "jre"}


def op_verify_artifact(g: dict[str, Any]) -> dict[str, Any]:
    if (g.get("hashType") or "sha1") != "sha1":
        _fail("E_UNSUPPORTED_OP", f"仅支持 sha1, 收到 {g.get('hashType')}")
    with tempfile.TemporaryDirectory() as d:
        blob = os.path.join(d, "blob")
        with open(blob, "wb") as fh:
            fh.write((g.get("dataUtf8") or "").encode("utf-8"))
        digest = sha1_file(blob)
    return {"valid": digest == (g.get("expectedSha1") or "").lower()}


class _OfflineHttp(HttpClient):
    """Any network call fails -- used to exercise cache fallback/error branches."""

    def get_json(self, url: str, params: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> Any:
        raise RuntimeError("offline (conformance)")


def op_cache_decision(g: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as d:
        fs = FileStore()
        cache = MetadataCache(
            _OfflineHttp(), fs, d, ttl=g["ttl"], refresh=g.get("refresh", False), now=lambda: g["now"]
        )
        path = os.path.join(d, "entry.json")
        if g.get("hasStale"):
            fs.write_json(path, {"stale": True})
            os.utime(path, (g["mtime"], g["mtime"]))
        if cache.is_fresh(path):
            return {"action": "hit"}
        if g.get("refresh"):
            return {"action": "refresh"}
        if g.get("offline"):
            try:
                cache.get_json(path, "http://example.invalid/conformance")
            except Exception:
                return {"action": "error"}
            return {"action": "fallback"}
        return {"action": "miss"}


def op_resolve_version(g: dict[str, Any]) -> dict[str, Any]:
    entry = _first_of_channel(_load(g["snapshot"]), g["channel"])
    if entry is None:
        _fail("E_VERSION_NOT_FOUND", f"渠道 {g['channel']} 无版本")
    return {"id": entry.id, "channel": entry.channel, "type": entry.type}


def op_plan_client_download(g: dict[str, Any]) -> dict[str, Any]:
    gtype = g.get("type", "vanilla")
    try:
        gt = GameType.parse(gtype)
    except ValueError:
        _fail("E_UNSUPPORTED_TYPE", f"未知类型 {gtype}")
    if not gt.is_client_capable:
        _fail("E_UNSUPPORTED_TYPE", f"类型 {gtype} 不支持客户端")

    vj = _load(g["snapshot"])
    platform = g.get("platform") or {}
    os_name = platform.get("os") or os_key()
    arch = platform.get("arch") or os_arch()
    lay = PathLayout(root=g["root"], version=g["version"], game_type=gtype)

    items: list[dict[str, Any]] = []
    client = (vj.get("downloads") or {}).get("client") or {}
    if client.get("url"):
        items.append(
            {
                "url": client["url"],
                "path": _norm(lay.client_jar_path()),
                "sha1": client.get("sha1"),
                "size": client.get("size"),
            }
        )
    index = vj.get("assetIndex") or {}
    if index.get("url"):
        index_path = os.path.join(lay.client_indexes_dir(), f"{index.get('id', '')}.json")
        items.append(
            {"url": index["url"], "path": _norm(index_path), "sha1": index.get("sha1"), "size": index.get("size")}
        )
    if g.get("assetIndex"):
        objects = _load(g["assetIndex"]).get("objects") or {}
        for meta in objects.values():
            h = meta.get("hash")
            if not h:
                continue
            items.append(
                {
                    "url": Mojang.assets_object_url(h),
                    "path": _norm(lay.client_object_path(h)),
                    "sha1": h,
                    "size": meta.get("size"),
                }
            )
    for lib in resolve_libraries(vj, os_name, arch):
        if lib.url and lib.path:
            items.append(
                {
                    "url": lib.url,
                    "path": _norm(lay.client_library_path(lib.path)),
                    "sha1": lib.sha1,
                    "size": lib.size,
                }
            )
    return {"items": items, "count": len(items)}


def op_plan_server_launch(g: dict[str, Any]) -> dict[str, Any]:
    opts = g.get("options") or {}
    major = int(g.get("javaMajor") or 8)
    version = g.get("version") or ""
    gtype = g.get("type", "vanilla")
    lay = PathLayout(root=g["root"], version=version, game_type=gtype)
    options = RuntimeOptions(
        version=version,
        game_type=gtype,
        min_mem=opts.get("minMem") or "512M",
        max_mem=opts.get("maxMem") or "2G",
        jvm_opts=" ".join(opts.get("jvmOpts") or []) or None,
        server_args=" ".join(opts.get("userServerArgs") or []) or None,
        nogui=bool(opts.get("nogui")),
        force_upgrade=bool(opts.get("forceUpgrade")),
    )
    jvm = user_jvm_opts(options) + memory_args(options)
    user_args = shlex.split(options.server_args) if options.server_args else []
    server_args: list[str] = []
    if options.nogui and "nogui" not in user_args:
        server_args.append("nogui")
    server_args += user_args
    if options.force_upgrade and "--forceUpgrade" not in server_args:
        server_args.append("--forceUpgrade")
    return {
        "executable": _norm(lay.java_bin(major)),
        "cwd": _norm(lay.server_dir()),
        "jarPath": _norm(lay.server_jar_path()),
        "jvmArgs": jvm,
        "serverArgs": server_args,
    }


def op_plan_server_deploy(g: dict[str, Any]) -> dict[str, Any]:
    gtype = g.get("type", "vanilla")
    if gtype != "vanilla":
        _fail("E_UNSUPPORTED_OP", f"{gtype} 无离线部署计划")
    snap = _load(g["snapshot"])
    server = (snap.get("downloads") or {}).get("server")
    if not server:
        _fail("E_NO_SERVER_DOWNLOAD", "快照缺少服务端下载")
    version = snap.get("id", g.get("version", ""))
    lay = PathLayout(root=g["root"], version=version, game_type=gtype)
    name = GameType.parse(gtype).server_jar_name(version)
    return {
        "version": version,
        "type": gtype,
        "workdir": _norm(lay.server_dir()),
        "jarName": name,
        "eula": True,
        "coreArtifact": {
            "url": server["url"],
            "sha1": server.get("sha1"),
            "size": server.get("size"),
            "filename": name,
        },
    }


def op_plan_download_concurrency(g: dict[str, Any]) -> dict[str, Any]:
    if g["sizeBytes"] >= PARALLEL_MIN_SIZE:
        return {"mode": "ranged", "parts": PARALLEL_PARTS}
    return {"mode": "single", "parts": 1}


def op_resolve_concurrency(g: dict[str, Any]) -> dict[str, Any]:
    return {"concurrency": max(1, min(MAX_DOWNLOAD_THREADS, int(g["threads"])))}


def op_non_tty_defaults(g: dict[str, Any]) -> dict[str, Any]:
    manifest = _load(g["snapshot"])
    entry = _first_of_channel(manifest, "release")
    defaults = RuntimeOptions()
    return {
        "version": g.get("providedVersion") or (entry.id if entry else ""),
        "username": g.get("providedUsername") or defaults.username,
        "interactive": False,
        "confirm": True,
    }


def op_normalize_progress(g: dict[str, Any]) -> dict[str, Any]:
    i = g["input"]
    return {
        "kind": i["kind"],
        "description": i.get("description"),
        "total": i.get("total"),
        "current": i.get("current"),
        "phase": i.get("phase"),
        "indefinite": i.get("total") is None,
    }


def op_normalize_exit(g: dict[str, Any]) -> dict[str, Any]:
    raw = int(g.get("returnCode", 0))
    return {"exitCode": normalize_exit_code(raw), "signaled": raw < 0, "signal": -raw if raw < 0 else None}


# -- auth(正版授权纯映射,见 docs/spec/auth.md)-------------------------------
def op_auth_device_code_request(g: dict[str, Any]) -> dict[str, Any]:
    req = auth_map.device_code_request(g["clientId"], g.get("scope") or auth_map.DEFAULT_SCOPE)
    return {"method": req.method, "url": req.url, "form": dict(req.form or {})}


def op_auth_device_code_result(g: dict[str, Any]) -> dict[str, Any]:
    d = auth_map.device_code_result(g["response"])
    return {
        "deviceCode": d.device_code,
        "userCode": d.user_code,
        "verificationUri": d.verification_uri,
        "expiresIn": d.expires_in,
        "interval": d.interval,
        "message": d.message,
    }


def op_auth_xbox_request(g: dict[str, Any]) -> dict[str, Any]:
    req = auth_map.xbox_request(g["msaAccessToken"])
    return {"method": req.method, "url": req.url, "headers": dict(req.headers), "body": req.body}


def op_auth_xbox_result(g: dict[str, Any]) -> dict[str, Any]:
    t = auth_map.xbox_result(g["response"])
    return {"token": t.token, "userHash": t.user_hash}


def op_auth_xsts_request(g: dict[str, Any]) -> dict[str, Any]:
    req = auth_map.xsts_request(g["xblToken"], g.get("relyingParty") or auth_map.XSTS_RELYING_PARTY)
    return {"method": req.method, "url": req.url, "headers": dict(req.headers), "body": req.body}


def op_auth_xsts_result(g: dict[str, Any]) -> dict[str, Any]:
    t = auth_map.xsts_result(g["response"])
    return {"token": t.token, "userHash": t.user_hash}


def op_auth_minecraft_login_request(g: dict[str, Any]) -> dict[str, Any]:
    req = auth_map.minecraft_login_request(g["userHash"], g["xstsToken"])
    return {"method": req.method, "url": req.url, "headers": dict(req.headers), "body": req.body}


def op_auth_minecraft_login_result(g: dict[str, Any]) -> dict[str, Any]:
    t = auth_map.minecraft_login_result(g["response"])
    return {"accessToken": t.access_token, "expiresIn": t.expires_in}


def op_auth_profile_request(g: dict[str, Any]) -> dict[str, Any]:
    req = auth_map.profile_request(g["accessToken"])
    return {"method": req.method, "url": req.url, "headers": dict(req.headers)}


def op_auth_profile_result(g: dict[str, Any]) -> dict[str, Any]:
    p = auth_map.profile_result(g["response"])
    return {"uuid": p.uuid, "name": p.name}


def op_auth_token_state(g: dict[str, Any]) -> dict[str, Any]:
    st = auth_map.token_state(g["expiresAt"], g["now"], g.get("skewSeconds", auth_map.AUTH_SKEW_SECONDS))
    return {"state": st.state, "remainingSeconds": st.remaining_seconds}


def op_auth_classify_token_error(g: dict[str, Any]) -> dict[str, Any]:
    a = auth_map.classify_token_error(g.get("error"))
    return {"action": a.action, "code": a.code}


def op_auth_classify_xsts_error(g: dict[str, Any]) -> dict[str, Any]:
    return {"code": auth_map.classify_xsts_error(int(g.get("status") or 0), g.get("xerr"))}


def op_client_launch(g: dict[str, Any]) -> dict[str, Any]:
    gtype = g.get("type", "vanilla")
    try:
        gt = GameType.parse(gtype)
    except ValueError:
        _fail("E_UNSUPPORTED_TYPE", f"未知类型 {gtype}")
    if not gt.is_client_capable:
        _fail("E_UNSUPPORTED_TYPE", f"类型 {gtype} 不支持客户端")

    vj = _load(g["snapshot"])
    platform = g.get("platform") or {}
    os_name = platform.get("os") or os_key()
    arch = platform.get("arch") or os_arch()
    opts_in = g.get("options") or {}
    opts = RuntimeOptions(
        version=g["version"],
        username=opts_in.get("username") or "guest",
        min_mem=opts_in.get("minMem") or "512M",
        max_mem=opts_in.get("maxMem") or "2G",
        root_dir=g["root"],
    )
    lay = PathLayout(root=g["root"], version=g["version"], game_type=gtype)
    major = required_java_major(vj)
    libs = resolve_libraries(vj, os_name, arch)
    classpath = [_norm(lay.client_library_path(lib.path)) for lib in libs]
    classpath.append(_norm(lay.client_jar_path()))
    sep = ";" if os_name == "windows" else ":"
    jvm = [_norm(a) for a in jvm_args(vj, _norm(lay.client_natives_dir()), os_name, arch)]
    game = [_norm(a) for a in game_args(vj, opts, lay, os_name, arch, auth_uuid="0" * 32)]
    index_id = str((vj.get("assetIndex") or {}).get("id", "") or "")
    return {
        "executable": _java_exe(lay, major, platform),
        "cwd": _norm(lay.client_dir()),
        "mainClass": vj.get("mainClass") or DEFAULT_MAIN_CLASS,
        "jvmArgs": jvm,
        "gameArgs": game,
        "classpath": classpath,
        "classpathString": sep.join(classpath),
        "memory": {"min": opts.min_mem, "max": opts.max_mem},
        "tokens": {"assets_index_name": index_id, "version_type": vj.get("type", "release")},
        "features": {},
    }


def op_state_paths(g: dict[str, Any]) -> dict[str, Any]:
    """``statePaths``: 上报两端**实际使用**的共享落点 (规范 ``docs/spec/state-paths.json``).

    平台与环境都从 ``given`` 注入, 而不是读宿主机 -- 否则 Windows 形态在 macOS 上永远测不到.
    分隔符统一成 ``/`` (与其它 fixture 一致).
    """
    env = dict(g.get("env") or {})
    windows = (g.get("platform") or {}).get("os") == "windows"
    state_dir = default_state_dir(environ=env, is_windows=windows)
    home = env.get("USERPROFILE" if windows else "HOME", "")
    data: dict[str, Any] = {
        "stateDir": state_dir,
        "installRecord": os.path.join(state_dir, MANIFEST_FILENAME),
        "authSession": os.path.join(state_dir, AUTH_FILENAME),
        "gameRoot": os.path.join(home, "minecraft"),
    }
    if not windows:
        # 补全落点复用应用层唯一的 target_path (不再抄一遍路径).
        data["completion"] = {shell: str(target_path(shell, Path(home))) for shell in ("bash", "zsh", "fish")}
    return {
        key: (
            {k: v.replace(os.sep, "/") for k, v in value.items()}
            if isinstance(value, dict)
            else value.replace(os.sep, "/")
        )
        for key, value in data.items()
    }


OPS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "statePaths": op_state_paths,
    "planLayout": op_plan_layout,
    "requiredJava": op_required_java,
    "verifyArtifact": op_verify_artifact,
    "cacheDecision": op_cache_decision,
    "resolveVersion": op_resolve_version,
    "planServerLaunch": op_plan_server_launch,
    "planServerDeploy": op_plan_server_deploy,
    "planDownloadConcurrency": op_plan_download_concurrency,
    "resolveConcurrency": op_resolve_concurrency,
    "nonTtyDefaults": op_non_tty_defaults,
    "normalizeProgress": op_normalize_progress,
    "normalizeExit": op_normalize_exit,
    "authDeviceCodeRequest": op_auth_device_code_request,
    "authDeviceCodeResult": op_auth_device_code_result,
    "authXboxRequest": op_auth_xbox_request,
    "authXboxResult": op_auth_xbox_result,
    "authXstsRequest": op_auth_xsts_request,
    "authXstsResult": op_auth_xsts_result,
    "authMinecraftLoginRequest": op_auth_minecraft_login_request,
    "authMinecraftLoginResult": op_auth_minecraft_login_result,
    "authProfileRequest": op_auth_profile_request,
    "authProfileResult": op_auth_profile_result,
    "authTokenState": op_auth_token_state,
    "authClassifyTokenError": op_auth_classify_token_error,
    "authClassifyXstsError": op_auth_classify_xsts_error,
    "planClientLaunch": op_client_launch,
    "planClientDownload": op_plan_client_download,
}


def dispatch(req: dict[str, Any]) -> dict[str, Any]:
    fn = OPS.get(req.get("operation", ""))
    if fn is None:
        return err("E_UNSUPPORTED_OP", f"未实现操作 {req.get('operation')}")
    try:
        return ok(fn(req))
    except ConformanceError as exc:
        return err(exc.code, exc.message)
    except Exception as exc:  # surface any impl error as a fixture failure
        return err("E_INTERNAL", f"{type(exc).__name__}: {exc}")


def main() -> int:
    req = json.load(sys.stdin)
    print(json.dumps(dispatch(req), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
