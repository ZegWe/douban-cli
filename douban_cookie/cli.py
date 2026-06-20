from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .browser import BrowserConfigError, default_headless
from .check import DEFAULT_CHECK_URL, CheckError, check_login
from .envfile import ConfigError
from .login import LoginError, RemoteDebugging, login_and_save
from .movie import MovieDetail, MovieError, MovieSearchResult, movie_detail, movie_search
from .qr_login import QrLoginError, QrLoginStatus, login_with_qr


DEFAULT_OUTPUT_DIR = Path(".douban")
DEFAULT_STATE_PATH = DEFAULT_OUTPUT_DIR / "storage_state.json"
DEFAULT_COOKIES_PATH = DEFAULT_OUTPUT_DIR / "cookies.json"
DEFAULT_HEADER_PATH = DEFAULT_OUTPUT_DIR / "cookie-header.txt"
DEFAULT_NETSCAPE_PATH = DEFAULT_OUTPUT_DIR / "cookies.netscape.txt"
DEFAULT_QR_PATH = DEFAULT_OUTPUT_DIR / "qr-login.png"


def _add_browser_flags(parser: argparse.ArgumentParser) -> None:
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headed", action="store_true", help="open a visible browser")
    mode.add_argument("--headless", action="store_true", help="run browser in headless mode")
    parser.add_argument(
        "--browser-executable",
        help="path to Chrome/Chromium executable; defaults to system Chrome when found",
    )


def _resolve_headless(args: argparse.Namespace) -> bool:
    if args.headed:
        return False
    if args.headless:
        return True
    return default_headless()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="douban-cli",
        description="Login to Douban with account/password and save local cookies.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser("login", help="login and save cookies")
    login_parser.add_argument("--env", default=".env", type=Path, help="path to .env")
    login_parser.add_argument("--state", default=DEFAULT_STATE_PATH, type=Path)
    login_parser.add_argument("--cookies", default=DEFAULT_COOKIES_PATH, type=Path)
    login_parser.add_argument("--cookie-header", default=DEFAULT_HEADER_PATH, type=Path)
    login_parser.add_argument("--netscape", default=DEFAULT_NETSCAPE_PATH, type=Path)
    login_parser.add_argument("--timeout", default=180, type=int, help="login timeout in seconds")
    login_parser.add_argument(
        "--remote-debugging-port",
        type=int,
        help="open a Chrome DevTools port for manual captcha handling over SSH",
    )
    login_parser.add_argument(
        "--remote-debugging-host",
        default="127.0.0.1",
        help="host for Chrome DevTools port; keep the default unless you know the risk",
    )
    _add_browser_flags(login_parser)

    qr_parser = subparsers.add_parser(
        "login-qr",
        help="login with a Douban App QR code without opening a browser",
    )
    qr_parser.add_argument("--state", default=DEFAULT_STATE_PATH, type=Path)
    qr_parser.add_argument("--cookies", default=DEFAULT_COOKIES_PATH, type=Path)
    qr_parser.add_argument("--cookie-header", default=DEFAULT_HEADER_PATH, type=Path)
    qr_parser.add_argument("--netscape", default=DEFAULT_NETSCAPE_PATH, type=Path)
    qr_parser.add_argument("--qr-output", default=DEFAULT_QR_PATH, type=Path)
    qr_parser.add_argument("--timeout", default=180, type=int, help="QR login timeout in seconds")
    qr_parser.add_argument(
        "--request-timeout",
        default=20,
        type=int,
        help="per-request timeout in seconds",
    )

    check_parser = subparsers.add_parser("check", help="check whether saved cookies still work")
    check_parser.add_argument("--state", default=DEFAULT_STATE_PATH, type=Path)
    check_parser.add_argument("--url", default=DEFAULT_CHECK_URL)
    check_parser.add_argument("--timeout", default=60, type=int, help="check timeout in seconds")

    movie_parser = subparsers.add_parser(
        "movie",
        help="fetch Douban movie details or search results with saved cookies",
    )
    movie_subparsers = movie_parser.add_subparsers(dest="movie_command", required=True)

    detail_parser = movie_subparsers.add_parser("detail", help="fetch a movie subject detail page")
    detail_parser.add_argument("subject", help="Douban movie subject id or subject URL")
    detail_parser.add_argument("--state", default=DEFAULT_STATE_PATH, type=Path)
    detail_parser.add_argument("--timeout", default=60, type=int, help="request timeout in seconds")
    detail_parser.add_argument("--json", action="store_true", help="print parsed detail as JSON")

    search_parser = movie_subparsers.add_parser("search", help="search Douban movie subjects")
    search_parser.add_argument("query", help="movie title or keyword")
    search_parser.add_argument("--state", default=DEFAULT_STATE_PATH, type=Path)
    search_parser.add_argument("--timeout", default=60, type=int, help="request timeout in seconds")
    search_parser.add_argument("--limit", default=10, type=int, help="maximum number of results")
    search_parser.add_argument("--json", action="store_true", help="print parsed results as JSON")

    return parser


def _print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _format_people(names: list[str], limit: int = 10) -> str:
    shown = names[:limit]
    value = " / ".join(shown)
    if len(names) > limit:
        suffix = f" ... (+{len(names) - limit} more)"
        return value + suffix if value else suffix.lstrip()
    return value


def _print_detail(detail: MovieDetail) -> None:
    print(f"id:        {detail.subject_id}")
    print(f"title:     {detail.title}")
    print(f"url:       {detail.url}")
    if detail.rating.value is not None:
        count = f" ({detail.rating.count})" if detail.rating.count is not None else ""
        print(f"rating:    {detail.rating.value:g}{count}")
    if detail.directors:
        print(f"director:  {_format_people(detail.directors)}")
    if detail.writers:
        print(f"writer:    {_format_people(detail.writers)}")
    if detail.actors:
        print(f"cast:      {_format_people(detail.actors)}")
    if detail.genres:
        print(f"genres:    {' / '.join(detail.genres)}")
    if detail.date_published:
        print(f"date:      {detail.date_published}")
    if detail.duration:
        print(f"duration:  {detail.duration}")
    for key in ("制片国家/地区", "语言", "上映日期", "片长", "IMDb"):
        value = detail.info.get(key)
        if value:
            print(f"{key}: {value}")
    if detail.summary:
        print(f"summary:   {detail.summary}")


def _print_search_results(results: list[MovieSearchResult]) -> None:
    for index, result in enumerate(results, start=1):
        rating = ""
        if result.rating.value is not None and result.rating.value > 0:
            rating = f" rating={result.rating.value:g}"
            if result.rating.count is not None:
                rating += f"({result.rating.count})"
        elif result.rating.info:
            rating = f" rating={result.rating.info}"
        print(f"{index}. {result.subject_id} {result.title}{rating}")
        print(f"   {result.url}")
        if result.abstract:
            print(f"   {result.abstract}")
        if result.abstract_2:
            print(f"   {result.abstract_2}")


def _print_qr_status(status: QrLoginStatus) -> None:
    value = status.login_status or "unknown"
    extra = status.description or status.message
    if extra:
        print(f"qr_status: {value} ({extra})", flush=True)
    else:
        print(f"qr_status: {value}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "login":
            headless = _resolve_headless(args)
            remote_debugging = None
            if args.remote_debugging_port:
                remote_debugging = RemoteDebugging(
                    host=args.remote_debugging_host,
                    port=args.remote_debugging_port,
                )
            result = login_and_save(
                env_path=args.env,
                state_path=args.state,
                cookies_path=args.cookies,
                header_path=args.cookie_header,
                netscape_path=args.netscape,
                headless=headless,
                timeout_s=args.timeout,
                browser_executable=args.browser_executable,
                remote_debugging=remote_debugging,
            )
            print("Login cookie saved.")
            print(f"storage_state: {result.state_path}")
            print(f"cookies_json:   {result.cookies_path}")
            print(f"cookie_header:  {result.header_path}")
            print(f"netscape_file:  {result.netscape_path}")
            print("cookie_names:   " + ", ".join(result.cookie_names))
            return 0

        if args.command == "login-qr":
            result = login_with_qr(
                state_path=args.state,
                cookies_path=args.cookies,
                header_path=args.cookie_header,
                netscape_path=args.netscape,
                qr_path=args.qr_output,
                timeout_s=args.timeout,
                request_timeout_s=args.request_timeout,
                qr_callback=lambda path: print(f"qr_image: {path}", flush=True),
                status_callback=_print_qr_status,
            )
            print("Login cookie saved.")
            print(f"storage_state: {result.state_path}")
            print(f"cookies_json:   {result.cookies_path}")
            print(f"cookie_header:  {result.header_path}")
            print(f"netscape_file:  {result.netscape_path}")
            print("cookie_names:   " + ", ".join(result.cookie_names))
            return 0

        if args.command == "check":
            result = check_login(
                state_path=args.state,
                check_url=args.url,
                timeout_s=args.timeout,
            )
            print("logged_in: " + ("yes" if result.logged_in else "no"))
            print(f"reason:    {result.reason}")
            print(f"url:       {result.url}")
            print(f"title:     {result.title}")
            print("cookies:   " + ", ".join(result.cookie_names))
            return 0 if result.logged_in else 2

        if args.command == "movie":
            if args.movie_command == "detail":
                detail = movie_detail(
                    subject=args.subject,
                    state_path=args.state,
                    timeout_s=args.timeout,
                )
                if args.json:
                    _print_json(detail.to_dict())
                else:
                    _print_detail(detail)
                return 0

            if args.movie_command == "search":
                results = movie_search(
                    query=args.query,
                    state_path=args.state,
                    timeout_s=args.timeout,
                    limit=args.limit,
                )
                if args.json:
                    _print_json([result.to_dict() for result in results])
                else:
                    _print_search_results(results)
                return 0

    except (
        LoginError,
        QrLoginError,
        CheckError,
        ConfigError,
        BrowserConfigError,
        MovieError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command: {args.command}")
    return 1
