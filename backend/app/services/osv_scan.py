from __future__ import annotations

import asyncio
import json
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..models import Vulnerability


OSV_QUERY_URL = "https://api.osv.dev/v1/querybatch"
OSV_VULN_URL = "https://api.osv.dev/v1/vulns/{identifier}"
_CACHE_TTL_SECONDS = 300
_cache: dict[tuple[tuple[str, str, str], ...], tuple[float, list[Vulnerability]]] = {}


@dataclass(frozen=True, slots=True)
class Dependency:
    name: str
    version: str
    ecosystem: str


@dataclass(slots=True)
class DependencyScanResult:
    status: str
    vulnerabilities: list[Vulnerability] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dependency_count: int = 0


def parse_requirements(path: Path) -> list[Dependency]:
    dependencies: list[Dependency] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "--")) or "==" not in line:
            continue
        name, version = line.split("==", 1)
        name = name.strip()
        version = version.strip().split(";", 1)[0].strip()
        if (
            not name
            or not version
            or any(character in name for character in " <>=!~[]()")
            or any(character in version for character in " <>=!~*,")
        ):
            continue
        dependencies.append(Dependency(name, version, "PyPI"))
    return dependencies


def _npm_dependencies(data: dict[str, Any]) -> list[Dependency]:
    dependencies: list[Dependency] = []
    packages = data.get("packages")
    if isinstance(packages, dict):
        for package_path, metadata in packages.items():
            if not package_path or not isinstance(metadata, dict):
                continue
            version = metadata.get("version")
            if not isinstance(version, str) or not version:
                continue
            marker = "node_modules/"
            if marker not in package_path:
                continue
            name = package_path.rsplit(marker, 1)[-1]
            if name:
                dependencies.append(Dependency(name, version, "npm"))
    else:
        def visit(nodes: Any) -> None:
            if not isinstance(nodes, dict):
                return
            for name, metadata in nodes.items():
                if not isinstance(metadata, dict):
                    continue
                version = metadata.get("version")
                if isinstance(version, str) and version:
                    dependencies.append(Dependency(name, version, "npm"))
                visit(metadata.get("dependencies"))
        visit(data.get("dependencies"))
    return dependencies


def parse_lockfile(path: Path) -> list[Dependency]:
    if path.name == "requirements.txt":
        return parse_requirements(path)
    if path.name == "package-lock.json":
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("package-lock root must be an object")
        return _npm_dependencies(value)
    with path.open("rb") as source:
        value = tomllib.load(source)
    packages = value.get("package", [])
    if not isinstance(packages, list):
        return []
    dependencies = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        name, version = package.get("name"), package.get("version")
        if isinstance(name, str) and name and isinstance(version, str) and version:
            dependencies.append(Dependency(name, version, "PyPI"))
    return dependencies


def discover_dependencies(root: Path) -> tuple[list[Dependency], list[str]]:
    dependencies: list[Dependency] = []
    warnings: list[str] = []
    for name in ("requirements.txt", "package-lock.json", "poetry.lock", "uv.lock"):
        for path in sorted(root.rglob(name)):
            if path.is_symlink() or any(
                part in {".git", "node_modules", "vendor", ".venv", "venv"}
                for part in path.parts
            ):
                continue
            try:
                dependencies.extend(parse_lockfile(path))
            except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError):
                warnings.append(f"依赖清单 {path.relative_to(root).as_posix()} 无法解析，已跳过。")
    unique = sorted(set(dependencies), key=lambda item: (item.ecosystem, item.name.lower(), item.version))
    return unique, warnings


def _severity(vulnerability: dict[str, Any]) -> str:
    database = vulnerability.get("database_specific")
    if isinstance(database, dict) and isinstance(database.get("severity"), str):
        return database["severity"].lower()
    severity = vulnerability.get("severity")
    if isinstance(severity, list):
        for entry in severity:
            if isinstance(entry, dict) and str(entry.get("score", "")).replace(".", "", 1).isdigit():
                score = float(entry["score"])
                if score >= 9:
                    return "critical"
                if score >= 7:
                    return "high"
                if score >= 4:
                    return "medium"
                if score > 0:
                    return "low"
    return "unknown"


def _vulnerability(dependency: Dependency, identifier: str, detail: dict[str, Any]) -> Vulnerability:
    references = detail.get("references", [])
    urls = [
        item.get("url")
        for item in references
        if isinstance(item, dict) and isinstance(item.get("url"), str)
    ]
    return Vulnerability(
        package_name=dependency.name,
        package_version=dependency.version,
        ecosystem=dependency.ecosystem,
        vulnerability_id=identifier,
        summary=str(detail.get("summary") or detail.get("details") or "OSV 未提供摘要。")[:8000],
        severity=_severity(detail),
        references=urls[:30],
    )


class OSVScanner:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self._client = client

    async def _post_batch(
        self,
        client: httpx.AsyncClient,
        dependencies: list[Dependency],
    ) -> dict[str, Any]:
        payload = {
            "queries": [
                {
                    "package": {"name": item.name, "ecosystem": item.ecosystem},
                    "version": item.version,
                }
                for item in dependencies
            ]
        }
        for attempt in range(self.max_retries + 1):
            try:
                response = await client.post(
                    OSV_QUERY_URL,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < self.max_retries:
                        await asyncio.sleep(min(2.0, 0.25 * (2**attempt)))
                        continue
                    raise RuntimeError("OSV unavailable")
                if response.status_code >= 400:
                    raise RuntimeError("OSV rejected query")
                value = response.json()
                if not isinstance(value, dict) or not isinstance(value.get("results"), list):
                    raise RuntimeError("OSV incompatible response")
                return value
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2.0, 0.25 * (2**attempt)))
                    continue
                raise RuntimeError("OSV request failed") from exc
        raise RuntimeError("OSV request failed")

    async def _details(
        self,
        client: httpx.AsyncClient,
        identifier: str,
        partial: dict[str, Any],
    ) -> dict[str, Any]:
        if any(key in partial for key in ("summary", "details", "references", "database_specific", "severity")):
            return partial
        try:
            response = await client.get(
                OSV_VULN_URL.format(identifier=identifier),
                timeout=self.timeout_seconds,
            )
            if response.status_code == 200:
                value = response.json()
                if isinstance(value, dict):
                    return value
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        return partial

    async def scan(self, root: Path) -> DependencyScanResult:
        try:
            dependencies, warnings = await asyncio.to_thread(discover_dependencies, root)
        except OSError:
            return DependencyScanResult(status="unavailable", warnings=["依赖清单读取失败。"])
        if not dependencies:
            return DependencyScanResult(status="completed", warnings=warnings)
        cache_key = tuple((item.ecosystem, item.name, item.version) for item in dependencies)
        cached = _cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
            return DependencyScanResult(
                status="completed",
                vulnerabilities=cached[1],
                warnings=warnings,
                dependency_count=len(dependencies),
            )
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
        )
        vulnerabilities: list[Vulnerability] = []
        try:
            for offset in range(0, len(dependencies), 1000):
                batch = dependencies[offset : offset + 1000]
                response = await self._post_batch(client, batch)
                results = response["results"]
                if len(results) != len(batch):
                    raise RuntimeError("OSV result count mismatch")
                for dependency, result in zip(batch, results, strict=True):
                    if not isinstance(result, dict):
                        continue
                    raw_vulnerabilities = result.get("vulns", [])
                    if not isinstance(raw_vulnerabilities, list):
                        continue
                    for raw in raw_vulnerabilities[:100]:
                        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                            continue
                        detail = await self._details(client, raw["id"], raw)
                        vulnerabilities.append(_vulnerability(dependency, raw["id"], detail))
            unique = {
                (
                    item.ecosystem,
                    item.package_name,
                    item.package_version,
                    item.vulnerability_id,
                ): item
                for item in vulnerabilities
            }
            vulnerabilities = list(unique.values())
            _cache[cache_key] = (time.monotonic(), vulnerabilities)
            return DependencyScanResult(
                status="completed",
                vulnerabilities=vulnerabilities,
                warnings=warnings,
                dependency_count=len(dependencies),
            )
        except asyncio.CancelledError:
            raise
        except (RuntimeError, OSError):
            return DependencyScanResult(
                status="unavailable",
                warnings=warnings + ["OSV 服务不可用，依赖漏洞扫描未完成。"],
                dependency_count=len(dependencies),
            )
        finally:
            if owns_client:
                await client.aclose()
