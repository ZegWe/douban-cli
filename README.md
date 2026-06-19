# douban-cookie

用 `.env` 里的豆瓣账号密码打开真实浏览器登录，登录成功后把 cookie 保存到本地文件，并提供 `check` 命令验证 cookie 是否仍可用。

## 准备

`.env` 已支持下面两个变量名：

```dotenv
DOUBAN_USER=你的手机号或邮箱
DOUBAN_PASS=你的密码
```

安装依赖：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

本机已经有 `google-chrome-stable` 时不需要下载 Playwright 浏览器。如果没有 Chrome/Chromium，再执行：

```bash
python -m playwright install chromium
```

## 登录并保存 cookie

```bash
python -m douban_cookie login --headed
```

默认输出到 `.douban/`：

- `.douban/storage_state.json`: Playwright storage state，给后续浏览器自动化复用。
- `.douban/cookies.json`: 豆瓣域名 cookie JSON。
- `.douban/cookie-header.txt`: `Cookie:` 请求头可直接使用的 `name=value; ...` 内容。
- `.douban/cookies.netscape.txt`: Netscape cookie jar 格式，方便给部分命令行工具使用。

如果豆瓣要求验证码、短信或设备验证，直接在脚本打开的浏览器里完成，然后回到终端按 Enter，脚本会继续保存 cookie。

`--headless` 适合 cookie 已稳定、环境无风控时使用；如果返回 `captcha_required`，请改用 `--headed`。

### 无桌面服务器二维码登录

不需要 Playwright 或浏览器时，可以直接生成豆瓣 App 扫码登录二维码：

```bash
python -m douban_cookie login-qr --qr-output /tmp/douban-qr.png --timeout 180
```

命令会把二维码 PNG 保存到 `--qr-output`，轮询扫码状态；用豆瓣 App 扫码并确认后，会保存和 `login` 命令相同的 cookie 文件：

- `.douban/storage_state.json`
- `.douban/cookies.json`
- `.douban/cookie-header.txt`
- `.douban/cookies.netscape.txt`

这个流程只使用 HTTP 请求，适合没有显示桌面的 Linux 服务器或聊天 agent。不要把生成的二维码公开到不可信渠道；二维码过期前可以用于登录当前会话。

### 只有 SSH、没有可见桌面时

先在本地机器开一个 SSH 端口转发：

```bash
ssh -L 9222:127.0.0.1:9222 <user>@<host>
```

然后在 SSH 会话里的项目目录运行：

```bash
python -m douban_cookie login --headless --remote-debugging-port 9222 --timeout 600
```

脚本会在远端 headless Chrome 里打开豆瓣登录页。你在本地浏览器打开 `http://127.0.0.1:9222`，选择 Douban 页面 target，完成验证码或设备验证；脚本检测到登录 cookie 后会自动保存到 `.douban/`。

## 校验 cookie

```bash
python -m douban_cookie check
```

`check` 会加载 `.douban/storage_state.json`，访问 `https://www.douban.com/mine/`。如果被重定向回登录页，说明 cookie 无效或已过期。
校验直接使用 HTTP 请求，不会打开或启动浏览器。

## 获取影视信息

这些命令会复用 `.douban/storage_state.json` 里的 cookie，通过豆瓣网页读取影视条目和搜索结果，不使用旧的 `api.douban.com` 接口。

```bash
python -m douban_cookie movie detail 1292052
python -m douban_cookie movie detail https://movie.douban.com/subject/1292052/ --json
python -m douban_cookie movie search 肖申克 --limit 5
python -m douban_cookie movie search 肖申克 --json
```

`movie detail` 会解析条目页里的结构化数据和页面信息块；`movie search` 会解析搜索页内嵌的结果数据。默认输出适合终端阅读；加 `--json` 可以拿到完整结构化结果。

## 常用参数

```bash
python -m douban_cookie login --env .env --state .douban/storage_state.json --headed
python -m douban_cookie login-qr --qr-output /tmp/douban-qr.png
python -m douban_cookie check --state .douban/storage_state.json
python -m douban_cookie login --browser-executable /usr/bin/google-chrome-stable
```

## 开发验证

不登录、不触发验证码时，可以先跑这些检查：

```bash
python -m unittest discover -s tests
python -m compileall douban_cookie tests
```

## 安全说明

`.env` 和 `.douban/` 已加入 `.gitignore`。这些文件包含账号凭据或登录 cookie，不要提交到仓库，也不要贴到聊天窗口或日志里。
