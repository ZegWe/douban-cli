from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from .browser import DESKTOP_CHROME_UA
from .cookies import (
    AUTH_COOKIE_NAME,
    cookie_names,
    ensure_private_dir,
    has_auth_cookie,
    save_cookie_exports,
)


QR_CODE_URL = "https://accounts.douban.com/j/mobile/login/qrlogin_code"
QR_STATUS_URL = "https://accounts.douban.com/j/mobile/login/qrlogin_status"
LOGIN_REFERER = "https://accounts.douban.com/passport/login"


class QrLoginError(RuntimeError):
    pass


@dataclass(frozen=True)
class QrLoginResult:
    state_path: Path
    cookies_path: Path
    header_path: Path
    netscape_path: Path
    qr_path: Path
    cookie_names: list[str]


@dataclass(frozen=True)
class QrLoginStatus:
    login_status: str
    message: str
    description: str


@dataclass(frozen=True)
class _HttpResult:
    status: int
    url: str
    body: bytes


StatusCallback = Callable[[QrLoginStatus], None]
QrCallback = Callable[[Path], None]


def _request(
    *,
    opener: Any,
    url: str,
    timeout_s: int,
    accept: str = "application/json, text/javascript, */*; q=0.01",
) -> _HttpResult:
    request = Request(
        url,
        headers={
            "User-Agent": DESKTOP_CHROME_UA,
            "Accept": accept,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": LOGIN_REFERER,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    try:
        with opener.open(request, timeout=timeout_s) as response:
            return _HttpResult(
                status=response.status,
                url=response.geturl(),
                body=response.read(),
            )
    except HTTPError as exc:
        return _HttpResult(status=exc.code, url=exc.geturl(), body=exc.read())
    except URLError as exc:
        raise QrLoginError(f"Douban QR login request failed: {exc.reason}") from exc


def _json_response(result: _HttpResult, label: str) -> dict[str, Any]:
    if result.status >= 400:
        body = result.body[:500].decode("utf-8", errors="replace")
        raise QrLoginError(f"{label} returned HTTP status {result.status}: {body}")
    try:
        data = json.loads(result.body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        body = result.body[:500].decode("utf-8", errors="replace")
        raise QrLoginError(f"{label} did not return JSON: {body}") from exc
    if not isinstance(data, dict):
        raise QrLoginError(f"{label} returned an unexpected JSON shape.")
    return data


def _payload(data: dict[str, Any], label: str) -> dict[str, Any]:
    payload = data.get("payload")
    if not isinstance(payload, dict):
        raise QrLoginError(f"{label} response does not contain a payload object.")
    return payload


def _cookie_same_site(cookie: Any) -> str:
    same_site = cookie._rest.get("SameSite") or cookie._rest.get("samesite") or "Lax"
    return same_site if same_site in {"Strict", "Lax", "None"} else "Lax"


def _cookie_http_only(cookie: Any) -> bool:
    return bool(cookie._rest.get("HttpOnly") or cookie._rest.get("httponly"))


def _cookiejar_to_storage_cookies(jar: CookieJar) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    for cookie in jar:
        cookies.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path or "/",
                "expires": cookie.expires if cookie.expires is not None else -1,
                "httpOnly": _cookie_http_only(cookie),
                "secure": bool(cookie.secure),
                "sameSite": _cookie_same_site(cookie),
            }
        )
    return cookies


def _write_storage_state(path: Path, cookies: list[dict[str, Any]]) -> None:
    ensure_private_dir(path.parent)
    path.write_text(
        json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _write_qr(path: Path, data: bytes) -> None:
    ensure_private_dir(path.parent)
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _status_from_response(data: dict[str, Any]) -> QrLoginStatus:
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    return QrLoginStatus(
        login_status=str(payload.get("login_status") or ""),
        message=str(data.get("message") or ""),
        description=str(data.get("description") or ""),
    )


def login_with_qr(
    *,
    state_path: Path,
    cookies_path: Path,
    header_path: Path,
    netscape_path: Path,
    qr_path: Path,
    timeout_s: int,
    request_timeout_s: int = 20,
    qr_callback: QrCallback | None = None,
    status_callback: StatusCallback | None = None,
) -> QrLoginResult:
    if timeout_s < 1:
        raise QrLoginError("--timeout must be at least 1")

    jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar))

    code_data = _json_response(
        _request(opener=opener, url=QR_CODE_URL, timeout_s=request_timeout_s),
        "qrlogin_code",
    )
    code_payload = _payload(code_data, "qrlogin_code")
    code = code_payload.get("code")
    image_url = code_payload.get("img")
    if not code or not image_url:
        raise QrLoginError("qrlogin_code response does not contain code/img.")

    image_result = _request(
        opener=opener,
        url=str(image_url).replace("\\/", "/"),
        timeout_s=request_timeout_s,
        accept="image/png,image/*,*/*;q=0.8",
    )
    if image_result.status >= 400 or not image_result.body:
        raise QrLoginError(f"QR image download returned HTTP status {image_result.status}.")
    _write_qr(qr_path, image_result.body)
    if qr_callback:
        qr_callback(qr_path)

    deadline = time.monotonic() + timeout_s
    last_status: QrLoginStatus | None = None
    while time.monotonic() < deadline:
        status_url = QR_STATUS_URL + "?" + urlencode({"code": str(code)})
        status_data = _json_response(
            _request(opener=opener, url=status_url, timeout_s=request_timeout_s),
            "qrlogin_status",
        )
        status = _status_from_response(status_data)
        if status != last_status:
            if status_callback:
                status_callback(status)
            last_status = status

        cookies = _cookiejar_to_storage_cookies(jar)
        if has_auth_cookie(cookies):
            _write_storage_state(state_path, cookies)
            save_cookie_exports(cookies, cookies_path, header_path, netscape_path)
            return QrLoginResult(
                state_path=state_path,
                cookies_path=cookies_path,
                header_path=header_path,
                netscape_path=netscape_path,
                qr_path=qr_path,
                cookie_names=cookie_names(cookies),
            )

        time.sleep(2)

    raise QrLoginError(
        f"QR login did not produce the {AUTH_COOKIE_NAME!r} cookie before timeout."
    )
