from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


_BROWSER_CANDIDATES = (
    "google-chrome-stable",
    "google-chrome",
    "chromium",
    "chromium-browser",
)

DESKTOP_CHROME_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)


class BrowserConfigError(RuntimeError):
    pass


def default_headless() -> bool:
    env_value = os.environ.get("DOUBAN_HEADLESS")
    if env_value is not None:
        return env_value.strip().lower() in {"1", "true", "yes", "on"}
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _resolve_configured_browser(value: str, source: str) -> str:
    expanded = Path(value).expanduser()
    looks_like_path = expanded.is_absolute() or "/" in value
    if looks_like_path:
        if not expanded.exists():
            raise BrowserConfigError(f"{source} browser executable does not exist: {expanded}")
        if expanded.is_dir():
            raise BrowserConfigError(f"{source} browser executable is a directory: {expanded}")
        return str(expanded)

    found = shutil.which(value)
    if found:
        return found
    raise BrowserConfigError(f"{source} browser executable was not found on PATH: {value}")


def find_browser_executable(explicit: str | None = None) -> str | None:
    if explicit:
        return _resolve_configured_browser(explicit, "--browser-executable")

    env_path = os.environ.get("DOUBAN_BROWSER_EXECUTABLE")
    if env_path:
        return _resolve_configured_browser(env_path, "DOUBAN_BROWSER_EXECUTABLE")

    for name in _BROWSER_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def detected_wayland_display() -> str | None:
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return None

    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    candidates = ["wayland-1", "wayland-0"]
    candidates.extend(path.name for path in runtime_dir.glob("wayland-*"))
    for name in dict.fromkeys(candidates):
        if (runtime_dir / name).exists():
            return name
    return None


def launch_environment(headless: bool) -> dict[str, Any]:
    if headless:
        return {}

    wayland_display = detected_wayland_display()
    if not wayland_display:
        return {}

    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env["WAYLAND_DISPLAY"] = wayland_display
    return {
        "env": env,
        "args": ["--ozone-platform=wayland"],
    }
