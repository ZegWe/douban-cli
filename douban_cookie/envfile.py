from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


def _parse_value(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    try:
        parsed = shlex.split(raw, comments=False, posix=True)
    except ValueError:
        return raw.strip("'\"")
    if not parsed:
        return ""
    return parsed[0]


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        key, sep, raw_value = stripped.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        values[key] = _parse_value(raw_value)
    return values


def read_credentials(path: Path) -> Credentials:
    file_values = load_env_file(path)

    def get_any(*names: str) -> str | None:
        for name in names:
            value = os.environ.get(name)
            if value:
                return value
            value = file_values.get(name)
            if value:
                return value
        return None

    username = get_any("DOUBAN_USER", "DOUBAN_USERNAME", "DOUBAN_EMAIL", "DOUBAN_PHONE")
    password = get_any("DOUBAN_PASS", "DOUBAN_PASSWORD")
    if not username or not password:
        raise ConfigError(
            f"Missing Douban credentials. Put DOUBAN_USER and DOUBAN_PASS in {path}."
        )
    return Credentials(username=username, password=password)
