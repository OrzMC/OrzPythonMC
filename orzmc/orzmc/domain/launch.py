"""Pure assembly of the client launch command (no IO, fully testable)."""

from __future__ import annotations

import os
import shlex
import uuid

from orzmc.domain.libraries import os_arch, os_key, rules_allow
from orzmc.domain.options import RuntimeOptions
from orzmc.domain.paths import PathLayout

DEFAULT_MAIN_CLASS = "net.minecraft.client.main.Main"
LAUNCHER_BRAND = "orzmc"
LAUNCHER_VERSION = "2.0.0"

# Feature switches for rule-based ``arguments`` entries (same defaults as the
# official launcher for a plain offline session).
_DEFAULT_FEATURES = {
    "is_demo_user": False,
    "has_custom_resolution": False,
    "quick_play_singleplayer": False,
    "quick_play_multiplayer": False,
    "quick_play_realms": False,
}


def memory_args(options: RuntimeOptions) -> list[str]:
    """``-Xms/-Xmx`` from options — shared by the client & server command builders."""
    return [f"-Xms{options.min_mem}", f"-Xmx{options.max_mem}"]


def user_jvm_opts(options: RuntimeOptions) -> list[str]:
    """Extra JVM flags from ``options.jvm_opts`` (shell words); empty when unset."""
    return shlex.split(options.jvm_opts) if options.jvm_opts else []


def _resolve_rule_args(entries: list, os_name: str, arch: str, features: dict) -> list[str]:
    """Flatten Mojang ``arguments`` entries into plain strings.

    Entries are either plain strings, or ``{"rules": [...], "value": ...}``
    dicts whose ``value`` (str or list) is included only when the rules allow
    the current OS/arch/features — this is how macOS gets ``-XstartOnFirstThread``
    and how feature-gated game args (``--demo``, ``--width/--height``) resolve.
    """
    out: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, list):
            out.extend(str(item) for item in entry)
        elif isinstance(entry, dict) and "value" in entry:
            if "rules" in entry and not rules_allow(entry["rules"], os_name, arch, features):
                continue
            value = entry["value"]
            if isinstance(value, list):
                out.extend(str(item) for item in value)
            else:
                out.append(str(value))
    return out


def _subst(text: str, tokens: dict[str, str]) -> str:
    for key, value in tokens.items():
        text = text.replace(key, value)
    return text


def jvm_args(
    version_json: dict,
    natives_dir: str,
    os_name: str | None = None,
    arch: str | None = None,
) -> list[str]:
    """JVM flags from the version JSON (modern ``arguments.jvm`` or legacy template)."""
    os_name = os_name or os_key()
    arch = arch or os_arch()
    if "arguments" in version_json:
        raw = _resolve_rule_args(version_json["arguments"].get("jvm", []), os_name, arch, _DEFAULT_FEATURES)
    else:
        raw = ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"]
    tokens = {
        "${natives_directory}": natives_dir,
        "${launcher_name}": LAUNCHER_BRAND,
        "${launcher_version}": LAUNCHER_VERSION,
        "${classpath}": "",
    }
    result: list[str] = []
    for i, arg in enumerate(raw):
        if "${classpath}" in arg:
            continue  # the real classpath is appended explicitly
        if arg == "-cp" and i + 1 < len(raw) and "${classpath}" in raw[i + 1]:
            continue  # drop the -cp flag that belongs to the ${classpath} pair
        substituted = _subst(arg, tokens)
        if substituted:
            result.append(substituted)
    return result


def game_args(
    version_json: dict,
    options: RuntimeOptions,
    paths: PathLayout,
    os_name: str | None = None,
    arch: str | None = None,
    auth_uuid: str | None = None,
) -> list[str]:
    """Game arguments (modern ``arguments.game`` or legacy ``minecraftArguments``).

    Rule-based dict entries are resolved against the current OS/features, all
    ``${token}`` placeholders are substituted, and any essential auth flags the
    version JSON happens to omit are appended explicitly.

    ``auth_uuid`` defaults to a fresh random UUID — the game parses ``--uuid``
    with ``UUID.fromString`` so an empty string is an instant crash, and the
    official launcher likewise invents a random UUID for offline sessions.
    """
    os_name = os_name or os_key()
    arch = arch or os_arch()
    auth_uuid = auth_uuid or options.auth_uuid or str(uuid.uuid4())
    index_id = str((version_json.get("assetIndex") or {}).get("id", "") or "")
    tokens = {
        "${auth_player_name}": options.username,
        "${version_name}": options.version or "",
        "${game_directory}": paths.client_dir(),
        "${assets_root}": paths.client_assets_dir(),
        "${assets_index_name}": index_id,
        "${auth_uuid}": auth_uuid,
        "${auth_access_token}": options.auth_access_token or "0",
        "${user_type}": options.auth_user_type,
        "${version_type}": "release",
        "${user_properties}": "{}",
        "${resolution_width}": "854",
        "${resolution_height}": "480",
        "${clientid}": "",
        "${auth_xuid}": "",
    }
    if "arguments" in version_json:
        raw = _resolve_rule_args(version_json["arguments"].get("game", []), os_name, arch, _DEFAULT_FEATURES)
    else:
        template = version_json.get("minecraftArguments", "")
        raw = shlex.split(template)

    # Mojang stores each pair either as separate tokens ("--flag", "${value}")
    # or as one string "--flag ${value}"; the game's OptionParser wants flag and
    # value as separate argv tokens, so split on the first space. An empty value
    # (${auth_uuid} → "") is meaningful and must be kept to keep pairs aligned.
    args: list[str] = []
    for a in raw:
        substituted = _subst(a, tokens)
        if " " in substituted:
            flag, _, value = substituted.partition(" ")
            args += [flag, value]
        else:
            args.append(substituted)
    _ensure_essential_args(args, options, paths, index_id, auth_uuid)
    return args


def _ensure_essential_args(
    args: list[str],
    options: RuntimeOptions,
    paths: PathLayout,
    index_id: str,
    auth_uuid: str,
) -> None:
    """Append auth flags the JSON does not already provide (in place)."""
    essentials = [
        ("--username", options.username),
        ("--version", options.version or ""),
        ("--gameDir", paths.client_dir()),
        ("--assetsDir", paths.client_assets_dir()),
        ("--assetIndex", index_id),
        ("--uuid", auth_uuid),
        ("--accessToken", options.auth_access_token or "0"),
        ("--userType", options.auth_user_type),
    ]
    present = set(args)
    for flag, value in essentials:
        if flag not in present:
            args += [flag, value]


def build_launch_command(
    java_bin: str,
    version_json: dict,
    paths: PathLayout,
    options: RuntimeOptions,
    classpath: list[str],
    main_class: str,
    extra_jvm: list[str] | None = None,
    extra_game: list[str] | None = None,
) -> list[str]:
    """Assemble the full client launch command (pure)."""
    cmd = [java_bin, *memory_args(options)]
    jvm = jvm_args(version_json, paths.client_natives_dir())
    cmd += jvm
    if not any(arg.startswith("-Dminecraft.launcher.brand=") for arg in jvm):
        # Legacy templates have no brand/version token; add it like the real launcher.
        cmd += [f"-Dminecraft.launcher.brand={LAUNCHER_BRAND}", f"-Dminecraft.launcher.version={LAUNCHER_VERSION}"]
    cmd += user_jvm_opts(options)
    if extra_jvm:
        cmd += extra_jvm
    cmd += ["-cp", os.pathsep.join(classpath)]
    cmd.append(main_class)
    cmd += game_args(version_json, options, paths)
    if extra_game:
        cmd += extra_game
    return cmd
