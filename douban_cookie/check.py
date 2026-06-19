from __future__ import annotations

import json
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

from .browser import DESKTOP_CHROME_UA
from .cookies import AUTH_COOKIE_NAME, cookie_names, has_auth_cookie


DEFAULT_CHECK_URL = "https://www.douban.com/mine/"


class CheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class CheckResult:
    logged_in: bool
    url: str
    title: str
    reason: str
    cookie_names: list[str]


@dataclass(frozen=True)
class _HttpResult:
    url: str
    title: str
    status: int


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_title = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._parts.append(data)

    @property
    def title(self) -> str:
        return " ".join("".join(self._parts).split())


def _load_storage_cookies(state_path: Path) -> list[dict[str, Any]]:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CheckError(f"Cookie state file is not valid JSON: {state_path}") from exc
    except OSError as exc:
        raise CheckError(f"Could not read cookie state file: {state_path}") from exc

    cookies = state.get("cookies") if isinstance(state, dict) else None
    if not isinstance(cookies, list):
        raise CheckError(f"Cookie state file does not contain a cookies list: {state_path}")
    return [cookie for cookie in cookies if isinstance(cookie, dict)]


def _cookie_expires(value: Any) -> int | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    if value < time.time():
        return None
    return int(value)


def _unexpired_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = time.time()
    return [
        cookie
        for cookie in cookies
        if not (
            isinstance(cookie.get("expires"), (int, float))
            and cookie["expires"] > 0
            and cookie["expires"] < now
        )
    ]


def _cookie_jar(cookies: list[dict[str, Any]]) -> CookieJar:
    jar = CookieJar()
    for item in cookies:
        name = str(item.get("name", ""))
        value = str(item.get("value", ""))
        domain = str(item.get("domain", ""))
        if not name or not domain:
            continue

        expires = _cookie_expires(item.get("expires"))
        raw_expires = item.get("expires")
        if expires is None and isinstance(raw_expires, (int, float)) and raw_expires > 0:
            continue

        path = str(item.get("path") or "/")
        jar.set_cookie(
            Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=True,
                domain_initial_dot=domain.startswith("."),
                path=path,
                path_specified=True,
                secure=bool(item.get("secure")),
                expires=expires,
                discard=expires is None,
                comment=None,
                comment_url=None,
                rest={"HttpOnly": item.get("httpOnly")},
                rfc2109=False,
            )
        )
    return jar


def _response_title(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    for part in content_type.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            charset = value
            break

    parser = _TitleParser()
    parser.feed(body.decode(charset, errors="replace"))
    return parser.title


def _fetch_check_page(
    *,
    cookies: list[dict[str, Any]],
    check_url: str,
    timeout_s: int,
) -> _HttpResult:
    opener = build_opener(HTTPCookieProcessor(_cookie_jar(cookies)))
    request = Request(
        check_url,
        headers={
            "User-Agent": DESKTOP_CHROME_UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with opener.open(request, timeout=timeout_s) as response:
            body = response.read(128 * 1024)
            return _HttpResult(
                url=response.geturl(),
                title=_response_title(body, response.headers.get("content-type", "")),
                status=response.status,
            )
    except HTTPError as exc:
        body = exc.read(128 * 1024)
        return _HttpResult(
            url=exc.geturl(),
            title=_response_title(body, exc.headers.get("content-type", "")),
            status=exc.code,
        )
    except URLError as exc:
        raise CheckError(f"HTTP check request failed: {exc.reason}") from exc


def _is_login_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname == "accounts.douban.com" and parsed.path.startswith("/passport/login")


def check_login(
    *,
    state_path: Path,
    check_url: str,
    timeout_s: int,
) -> CheckResult:
    if not state_path.exists():
        raise CheckError(f"Cookie state file does not exist: {state_path}")

    cookies = _unexpired_cookies(_load_storage_cookies(state_path))
    names = cookie_names(cookies)
    if not has_auth_cookie(cookies):
        return CheckResult(
            logged_in=False,
            url=check_url,
            title="",
            reason=f"Missing {AUTH_COOKIE_NAME!r} auth cookie.",
            cookie_names=names,
        )

    result = _fetch_check_page(cookies=cookies, check_url=check_url, timeout_s=timeout_s)
    if _is_login_url(result.url):
        return CheckResult(
            logged_in=False,
            url=result.url,
            title=result.title,
            reason="Douban redirected the check page to login.",
            cookie_names=names,
        )
    if result.status >= 400:
        return CheckResult(
            logged_in=False,
            url=result.url,
            title=result.title,
            reason=f"Douban returned HTTP status {result.status}.",
            cookie_names=names,
        )
    return CheckResult(
        logged_in=True,
        url=result.url,
        title=result.title,
        reason="Cookie state is accepted by Douban.",
        cookie_names=names,
    )
