from __future__ import annotations

import asyncio
import importlib.metadata
import logging
import os
import shutil
import sys
import time
from pathlib import Path

import aiohttp

from .isolated import worker_env

logger = logging.getLogger(__name__)
PACKAGES = ("yt-dlp", "yt-dlp-ejs", "gallery-dl")


class ToolUpdater:
    def __init__(self, root: Path, *, interval_hours: int, enabled: bool = True):
        self.root = root
        self.interval = interval_hours * 3600
        self.enabled = enabled
        self._marker = root / "current"

    @property
    def path(self) -> Path | None:
        try:
            name = self._marker.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        candidate = self.root / name
        return candidate if name and candidate.is_dir() else None

    def activate(self) -> None:
        overlay = self.path
        base = [
            item
            for item in os.environ.get("PYTHONPATH", "").split(os.pathsep)
            if item and Path(item).parent != self.root
        ]
        os.environ["PYTHONPATH"] = os.pathsep.join(
            [str(overlay), *base] if overlay is not None else base
        )

    def installed_versions(self) -> dict[str, str]:
        overlay = self.path
        versions: dict[str, str] = {}
        if overlay is not None:
            for dist in importlib.metadata.distributions(path=[str(overlay)]):
                versions[dist.metadata["Name"].lower().replace("_", "-")] = dist.version
        for name in PACKAGES:
            if name not in versions:
                try:
                    versions[name] = importlib.metadata.version(name)
                except importlib.metadata.PackageNotFoundError:
                    continue
        return versions

    async def latest_versions(self) -> dict[str, str]:
        result: dict[str, str] = {}
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for name in PACKAGES:
                async with session.get(f"https://pypi.org/pypi/{name}/json") as response:
                    response.raise_for_status()
                    payload = await response.json()
                result[name] = str(payload["info"]["version"])
        return result

    async def run(self) -> None:
        self.activate()
        if not self.enabled:
            return
        while True:
            try:
                await self.update_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Tool update failed: %s", exc)
            await asyncio.sleep(self.interval)

    async def update_once(self) -> bool:
        installed = self.installed_versions()
        latest = await self.latest_versions()
        outdated = [name for name, version in latest.items() if installed.get(name) != version]
        if not outdated:
            return False
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"v{int(time.time())}"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--no-deps",
            "--target",
            str(target),
            *(f"{name}=={version}" for name, version in latest.items()),
            env={**os.environ, "PIP_USER": "0", "PIP_REQUIRE_VIRTUALENV": "0"},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
        except TimeoutError:
            process.kill()
            await process.wait()
            shutil.rmtree(target, ignore_errors=True)
            raise RuntimeError("pip install timeout") from None
        if process.returncode or not await self._verify(target):
            shutil.rmtree(target, ignore_errors=True)
            raise RuntimeError(stderr.decode("utf-8", errors="replace")[-500:] or "verify failed")
        temporary = self._marker.with_suffix(".tmp")
        temporary.write_text(target.name, encoding="utf-8")
        os.replace(temporary, self._marker)
        self.activate()
        self._prune(keep={target.name})
        logger.info(
            "Updated tools: %s",
            ", ".join(f"{name} {installed.get(name, '?')} -> {latest[name]}" for name in outdated),
        )
        return True

    @staticmethod
    async def _verify(target: Path) -> bool:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import yt_dlp, gallery_dl",
            env=worker_env(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            return await asyncio.wait_for(process.wait(), timeout=60) == 0
        except TimeoutError:
            process.kill()
            await process.wait()
            return False

    def _prune(self, keep: set[str]) -> None:
        versions = sorted(
            (item for item in self.root.glob("v*") if item.is_dir()),
            key=lambda item: item.name,
        )
        stale = [item for item in versions if item.name not in keep][:-1]
        for item in stale:
            shutil.rmtree(item, ignore_errors=True)
