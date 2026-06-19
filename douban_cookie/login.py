from __future__ import annotations

import time
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import ProxyHandler, build_opener

from .browser import DESKTOP_CHROME_UA, find_browser_executable, launch_environment
from .cookies import (
    AUTH_COOKIE_NAME,
    cookie_names,
    ensure_private_dir,
    has_auth_cookie,
    save_cookie_exports,
)
from .envfile import Credentials, read_credentials


LOGIN_URL = "https://accounts.douban.com/passport/login"
COOKIE_URLS = ["https://www.douban.com", "https://accounts.douban.com"]
PROXY_ENV_KEYS = (
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
)


class LoginError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoginResult:
    state_path: Path
    cookies_path: Path
    header_path: Path
    netscape_path: Path
    cookie_names: list[str]


@dataclass(frozen=True)
class RemoteDebugging:
    host: str
    port: int


def _import_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise LoginError(
            "Missing dependency: playwright. Run `python -m pip install -e .` first."
        ) from exc
    return sync_playwright, PlaywrightTimeoutError


def _click_first(page: Any, selectors: list[str], timeout_ms: int = 1000) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=timeout_ms):
                locator.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


def _fill_first(page: Any, selectors: list[str], value: str, timeout_ms: int = 1500) -> bool:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=timeout_ms):
                locator.fill(value, timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


def _open_password_tab(page: Any) -> None:
    clicked = _click_first(
        page,
        [
            "text=密码登录",
            "text=账号密码登录",
            "text=帐号密码登录",
            ".account-tab-account",
            ".account-body-tabs li:has-text('密码')",
            ".account-tab-switch-icon",
        ],
    )
    if clicked:
        page.wait_for_timeout(500)


def _accept_agreement_if_present(page: Any) -> None:
    selectors = [
        ".account-form-field input[type='checkbox']",
        "input[type='checkbox']",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=800) and not locator.is_checked(timeout=800):
                locator.check(timeout=800)
                return
        except Exception:
            continue


def _fill_credentials(page: Any, credentials: Credentials) -> None:
    user_filled = _fill_first(
        page,
        [
            "input[name='name']",
            "input[name='username']",
            "input[name='account']",
            "input[name='phone']",
            "input[type='email']",
            "input[type='tel']",
            "input[placeholder*='手机号']",
            "input[placeholder*='邮箱']",
            "input[placeholder*='账号']",
            ".global-phone-input-phone",
            ".account-form input[type='text']",
            ".account-form input:not([type])",
        ],
        credentials.username,
    )
    password_filled = _fill_first(
        page,
        [
            "input[type='password']",
            "input[name='password']",
            "input[placeholder*='密码']",
            ".account-form input[type='password']",
        ],
        credentials.password,
    )
    if not user_filled or not password_filled:
        raise LoginError(
            "Could not find Douban username/password fields. Try `login --headed` and finish manually."
        )


def _submit_login(page: Any) -> None:
    clicked = _click_first(
        page,
        [
            ".account-form-field-submit .btn",
            ".account-form .btn-account",
            "button:has-text('登录')",
            "a:has-text('登录')",
            "text=登录豆瓣",
            "text=登录",
        ],
        timeout_ms=1500,
    )
    if not clicked:
        raise LoginError("Could not find Douban login button.")


def _record_login_response(response: Any, events: list[str]) -> None:
    url = response.url
    if "login_error=" in url:
        query = parse_qs(urlparse(url).query)
        error = query.get("login_error", [""])[0]
        if error:
            events.append(error)
        return

    if "/j/mobile/login/basic" not in url:
        return

    try:
        data = response.json()
    except Exception:
        return
    if not isinstance(data, dict):
        return
    status = data.get("status")
    if status == "success":
        return
    for key in ("message", "description", "status", "code"):
        value = data.get(key)
        if value:
            events.append(str(value))
            return


def _login_event_summary(events: list[str]) -> str:
    ordered: list[str] = []
    for event in events:
        if event and event not in ordered:
            ordered.append(event)
    return ", ".join(ordered)


def _print_remote_debugging_help(remote_debugging: RemoteDebugging) -> None:
    print(
        "Chrome remote debugging is available at "
        f"http://{remote_debugging.host}:{remote_debugging.port}",
        flush=True,
    )
    print(
        "If this machine is remote, open another local terminal and run: "
        f"ssh -L {remote_debugging.port}:127.0.0.1:{remote_debugging.port} <user>@<host>",
        flush=True,
    )
    print(
        f"Then open http://127.0.0.1:{remote_debugging.port} in your local browser, "
        "choose the Douban page target, and finish the verification there.",
        flush=True,
    )


def _ensure_no_proxy_for_localhost() -> None:
    existing = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    values = [value.strip() for value in existing.split(",") if value.strip()]
    for value in ("127.0.0.1", "localhost"):
        if value not in values:
            values.append(value)
    os.environ["NO_PROXY"] = ",".join(values)
    os.environ["no_proxy"] = os.environ["NO_PROXY"]


def _unset_proxy_environment() -> dict[str, str | None]:
    previous = {key: os.environ.get(key) for key in PROXY_ENV_KEYS}
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    return previous


def _restore_proxy_environment(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _wait_for_remote_debugging(remote_debugging: RemoteDebugging, timeout_s: int = 15) -> None:
    opener = build_opener(ProxyHandler({}))
    url = f"http://{remote_debugging.host}:{remote_debugging.port}/json/version"
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    raise LoginError(f"Chrome remote debugging port did not start: {last_error}")


def _start_chrome_for_cdp(
    *,
    executable_path: str | None,
    remote_debugging: RemoteDebugging,
    profile_dir: Path,
    headless: bool,
    env: dict[str, str] | None,
) -> subprocess.Popen[bytes]:
    executable = executable_path or "chromium"
    command = [
        executable,
        f"--remote-debugging-address={remote_debugging.host}",
        f"--remote-debugging-port={remote_debugging.port}",
        f"--user-data-dir={profile_dir}",
        f"--user-agent={DESKTOP_CHROME_UA}",
        "--lang=zh-CN",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-sandbox",
    ]
    if headless:
        command.append("--headless=new")
    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def _wait_for_auth_cookie(context: Any, timeout_s: int) -> list[dict[str, Any]] | None:
    deadline = time.monotonic() + timeout_s
    last_cookies: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        last_cookies = context.cookies(COOKIE_URLS)
        if has_auth_cookie(last_cookies):
            return last_cookies
        time.sleep(1)
    return None


def _save_storage_state(context: Any, state_path: Path) -> None:
    ensure_private_dir(state_path.parent)
    context.storage_state(path=str(state_path))
    try:
        state_path.chmod(0o600)
    except OSError:
        pass


def login_and_save(
    *,
    env_path: Path,
    state_path: Path,
    cookies_path: Path,
    header_path: Path,
    netscape_path: Path,
    headless: bool,
    timeout_s: int,
    browser_executable: str | None,
    remote_debugging: RemoteDebugging | None = None,
) -> LoginResult:
    credentials = read_credentials(env_path)
    executable_path = find_browser_executable(browser_executable)
    sync_playwright, PlaywrightTimeoutError = _import_playwright()
    chrome_env = dict(os.environ)
    proxy_environment = _unset_proxy_environment() if remote_debugging else None

    try:
        with sync_playwright() as playwright:
            browser = None
            context = None
            chrome_process: subprocess.Popen[bytes] | None = None
            profile_tmp: tempfile.TemporaryDirectory[str] | None = None
            try:
                if remote_debugging:
                    if not executable_path:
                        raise LoginError(
                            "Remote debugging mode needs a system Chrome/Chromium executable. "
                            "Install google-chrome-stable/chromium or pass --browser-executable."
                        )
                    _ensure_no_proxy_for_localhost()
                    profile_tmp = tempfile.TemporaryDirectory(prefix="douban-cookie-cdp-")
                    chrome_process = _start_chrome_for_cdp(
                        executable_path=executable_path,
                        remote_debugging=remote_debugging,
                        profile_dir=Path(profile_tmp.name),
                        headless=headless,
                        env=chrome_env,
                    )
                    _wait_for_remote_debugging(remote_debugging)
                    browser = playwright.chromium.connect_over_cdp(
                        f"http://{remote_debugging.host}:{remote_debugging.port}"
                    )
                    context = browser.contexts[0]
                else:
                    launch_kwargs: dict[str, Any] = {"headless": headless}
                    launch_kwargs.update(launch_environment(headless))
                    if executable_path:
                        launch_kwargs["executable_path"] = executable_path
                    browser = playwright.chromium.launch(**launch_kwargs)
                    context = browser.new_context(
                        locale="zh-CN",
                        timezone_id="Asia/Shanghai",
                        user_agent=DESKTOP_CHROME_UA,
                        viewport={"width": 1280, "height": 900},
                    )

                page = context.new_page()
                try:
                    page.set_viewport_size({"width": 1280, "height": 900})
                except Exception:
                    pass
                login_events: list[str] = []
                page.on("response", lambda response: _record_login_response(response, login_events))
                if remote_debugging:
                    _print_remote_debugging_help(remote_debugging)

                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeoutError:
                    pass

                _open_password_tab(page)
                _accept_agreement_if_present(page)
                _fill_credentials(page, credentials)
                _submit_login(page)

                manual_prompted = False
                initial_wait = min(timeout_s, 8)
                cookies = _wait_for_auth_cookie(context, initial_wait)
                event_summary = _login_event_summary(login_events)
                if cookies is None and headless and event_summary and not remote_debugging:
                    raise LoginError(
                        "Douban login needs additional verification "
                        f"({event_summary}). Re-run `login --headed`, finish the verification "
                        "in the opened browser, then press Enter in the terminal."
                    )
                if cookies is None and headless and event_summary and remote_debugging:
                    print(
                        "Douban login needs additional verification "
                        f"({event_summary}). Finish it through the remote debugging page; "
                        "the script will save cookies automatically after login succeeds.",
                        flush=True,
                    )
                if cookies is None and event_summary and not headless:
                    print(
                        "Douban login needs additional verification "
                        f"({event_summary}). Finish it in the opened browser, then press "
                        "Enter here to save cookies.",
                        flush=True,
                    )
                    try:
                        input()
                    except EOFError:
                        pass
                    manual_prompted = True
                    cookies = _wait_for_auth_cookie(context, max(timeout_s - initial_wait, 10))

                if cookies is None:
                    wait_after_initial = (
                        max(timeout_s - initial_wait, 1)
                        if remote_debugging
                        else max(min(timeout_s, 45) - initial_wait, 1)
                    )
                    cookies = _wait_for_auth_cookie(
                        context,
                        wait_after_initial,
                    )
                if cookies is None and not headless and not manual_prompted:
                    print(
                        "If Douban asks for captcha, SMS, or device verification, finish it in "
                        "the opened browser, then press Enter here to save cookies.",
                        flush=True,
                    )
                    try:
                        input()
                    except EOFError:
                        pass
                    cookies = _wait_for_auth_cookie(context, max(timeout_s - 45, 10))

                if cookies is None:
                    raise LoginError(
                        f"Login did not produce the {AUTH_COOKIE_NAME!r} cookie before timeout."
                    )

                _save_storage_state(context, state_path)
                save_cookie_exports(cookies, cookies_path, header_path, netscape_path)
                return LoginResult(
                    state_path=state_path,
                    cookies_path=cookies_path,
                    header_path=header_path,
                    netscape_path=netscape_path,
                    cookie_names=cookie_names(cookies),
                )
            finally:
                if context and not remote_debugging:
                    context.close()
                if browser:
                    browser.close()
                if chrome_process:
                    chrome_process.terminate()
                    try:
                        chrome_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        chrome_process.kill()
                if profile_tmp:
                    profile_tmp.cleanup()
    finally:
        if proxy_environment is not None:
            _restore_proxy_environment(proxy_environment)
