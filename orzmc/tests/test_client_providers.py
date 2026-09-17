"""Client provider strategies: registry, fabric addon, forge installer harvest.

The forge provider runs the official installer headlessly; the test fakes that
with a real zipfile installer jar (``version.json`` embedded) plus a
``FakeProcess`` that produces the harvested libraries on disk.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from typing import Any

from fakes import FakeHttp, FakeProcess, FakeReporter

from orzmc.core.client import ClientProvider
from orzmc.core.client.base import ClientPrepare
from orzmc.core.forge import PROMOTIONS_URL
from orzmc.domain.paths import PathLayout
from orzmc.domain.types import GameType
from orzmc.infra.cache import MetadataCache
from orzmc.infra.fs import FileStore
from orzmc.infra.runner import ProcessRunner


def _client_prepare(
    tmp_path,
    *,
    http: FakeHttp,
    process: ProcessRunner,
    game_type: str = "vanilla",
    installer_bytes: bytes | None = None,
) -> ClientPrepare:
    """Wire a ClientPrepare with fake seams; the download seam writes on disk."""
    paths = PathLayout(root=str(tmp_path), version="1.20.4", game_type=game_type)
    fs = FileStore()

    def fake_download(url: str, dest: str, desc: str, sha1: str | None = None, force: bool = False) -> bool:
        data = installer_bytes if (installer_bytes and "installer.jar" in url) else b"jar"
        fs.ensure_dir(os.path.dirname(dest))
        with open(dest, "wb") as f:
            f.write(data)
        return True

    return ClientPrepare(
        version="1.20.4",
        version_json={},
        major=17,
        force_download=False,
        confirm_java=None,
        paths=paths,
        fs=fs,
        reporter=FakeReporter(),
        http=http,
        cache=MetadataCache(http, fs, paths.cache_dir()),
        process=process,
        download=fake_download,
        resolve_build_java=lambda major, need_jdk=False, confirm=None: "/fake/java",
    )


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class TestClientProviderRegistry:
    def test_registry_covers_client_types(self) -> None:
        for gt in (GameType.VANILLA, GameType.FABRIC, GameType.FORGE):
            assert ClientProvider.for_type(gt) is not None, f"缺少客户端 provider: {gt}"
        assert ClientProvider.for_type(GameType.PAPER) is None  # server-only


class TestFabricClientProvider:
    def test_addon_resolves_profile(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses = {
            "/versions/loader/1.20.4": [{"loader": {"version": "0.19.3", "stable": True}}],
            "/versions/installer": [{"version": "1.1.2", "stable": True}],
            "/versions/loader/1.20.4/0.19.3/profile/json": {
                "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
                "libraries": [
                    {
                        # fabric-meta now returns just the repository base here
                        "name": "net.fabricmc:fabric-loader:0.19.3",
                        "url": "https://maven.fabricmc.net/",
                        "sha1": "abc",
                    }
                ],
                "arguments": {"jvm": ["-DFabricMcEmu= net.minecraft.client.main.Main"], "game": []},
            },
        }
        prepare = _client_prepare(tmp_path, http=http, process=FakeProcess(), game_type="fabric")
        provider = ClientProvider.for_type(GameType.FABRIC)
        assert provider is not None
        addon = provider.addon(prepare)

        assert addon is not None
        assert addon.main_class == "net.fabricmc.loader.impl.launch.knot.KnotClient"
        assert addon.jvm_args == ["-DFabricMcEmu= net.minecraft.client.main.Main"]
        assert addon.uses_own_client_jar is False
        assert len(addon.libraries) == 1
        lib = addon.libraries[0]
        assert lib.path == "net/fabricmc/fabric-loader/0.19.3/fabric-loader-0.19.3.jar"
        # the addon library was downloaded into our layout by the provider
        assert prepare.fs.is_file(prepare.paths.client_library_path(lib.path))


FORGE_JSON: dict[str, Any] = {
    "mainClass": "net.minecraftforge.bootstrap.ForgeBootstrap",
    "arguments": {
        "jvm": ["-Djava.net.preferIPv6Addresses=system"],
        "game": ["--launchTarget", "forge_client"],
    },
    "libraries": [
        {
            "name": "net.minecraftforge:eventbus:6.2.33",
            "downloads": {
                "artifact": {
                    "path": "net/minecraftforge/eventbus/6.2.33/eventbus-6.2.33.jar",
                    "url": "https://maven.minecraftforge.net/net/minecraftforge/eventbus/6.2.33/eventbus-6.2.33.jar",
                }
            },
        },
        {
            "name": "net.minecraftforge:forge:1.20.4-49.2.8:client",
            "downloads": {
                "artifact": {
                    "path": "net/minecraftforge/forge/1.20.4-49.2.8/forge-1.20.4-49.2.8-client.jar",
                    "url": "",
                }
            },
        },
    ],
}


class TestForgeClientProvider:
    def test_addon_harvests_installer_client(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses[PROMOTIONS_URL] = {"promos": {"1.20.4-latest": "49.2.8"}}
        installer_bytes = _zip_bytes({"version.json": json.dumps(FORGE_JSON)})

        paths = PathLayout(root=str(tmp_path), version="1.20.4", game_type="forge")
        install_dir = os.path.join(paths.client_dir(), "forge", "install")
        created = [
            os.path.join(install_dir, "libraries", lib["downloads"]["artifact"]["path"])
            for lib in FORGE_JSON["libraries"]
        ]
        process = FakeProcess(created=created)
        prepare = _client_prepare(
            tmp_path, http=http, process=process, game_type="forge", installer_bytes=installer_bytes
        )
        provider = ClientProvider.for_type(GameType.FORGE)
        assert provider is not None
        addon = provider.addon(prepare)

        assert addon is not None
        assert addon.main_class == "net.minecraftforge.bootstrap.ForgeBootstrap"
        assert addon.uses_own_client_jar is True  # merged client jar is the game jar
        assert addon.jvm_args == ["-Djava.net.preferIPv6Addresses=system"]
        assert addon.game_args == ["--launchTarget", "forge_client"]
        assert len(addon.libraries) == 2
        assert any(lib.path.endswith("-client.jar") for lib in addon.libraries)
        # harvested jars moved from the temporary install dir into our layout
        harvested = "net/minecraftforge/forge/1.20.4-49.2.8/forge-1.20.4-49.2.8-client.jar"
        assert prepare.fs.is_file(prepare.paths.client_library_path(harvested))
        assert not os.path.exists(install_dir)  # temp install dir cleaned up

    def test_old_bootstraplauncher_rejected(self, tmp_path) -> None:
        http = FakeHttp()
        http.json_responses[PROMOTIONS_URL] = {"promos": {"1.20.4-latest": "49.2.8"}}
        old_json = dict(FORGE_JSON)
        old_json["mainClass"] = "cpw.mods.bootstraplauncher.BootstrapLauncher"
        installer_bytes = _zip_bytes({"version.json": json.dumps(old_json)})
        prepare = _client_prepare(
            tmp_path, http=http, process=FakeProcess(), game_type="forge", installer_bytes=installer_bytes
        )
        provider = ClientProvider.for_type(GameType.FORGE)
        assert provider is not None
        try:
            provider.addon(prepare)
            raise AssertionError("expected RuntimeError for old bootstrap launcher")
        except RuntimeError as exc:
            assert "旧版启动器" in str(exc)
