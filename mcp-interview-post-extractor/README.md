# Interview Post Crawl MCP

一个独立 MCP 服务，提供牛客或小红书(分享链接)面经爬取服务。爬取帖子正文和图片ocr结果。

## 安装依赖

```powershell
cd my_mcp
uv sync
```

## 启动 HTTP MCP 服务

```powershell
cd my_mcp
uv run interview-post-crawl
```

这个命令会启动一个常驻 HTTP MCP server，默认监听：

```text
http://0.0.0.0:8000/mcp
```

如果端口冲突，可以改端口：

```powershell
uv run interview-post-crawl --port 8010
```

默认使用 MCP 的 `streamable-http` transport。也可以显式指定：

```powershell
uv run interview-post-crawl --transport streamable-http --host 0.0.0.0 --port 8000 --path /mcp
```

## 在 WSL2 / OpenClaw 中连接

因为服务跑在 Win11，OpenClaw 跑在 WSL2 Ubuntu 里，不能直接用 Windows 里的 stdio 命令启动 MCP。推荐在 Windows 侧保持上面的 HTTP 服务运行，然后在 WSL2 里连接 Windows 宿主机地址。

在 WSL2 里获取 Windows 宿主 IP：

```bash
ip route | awk '/default/ {print $3}'
```

假设输出是 `172.24.224.1`，OpenClaw 里配置 MCP URL：

```text
http://172.24.224.1:8000/mcp
```

如果 OpenClaw 支持配置 MCP server JSON，可以按它的 remote/http MCP 配置格式填写这个 URL；server 名称建议用 `interview-post-crawl`。

如果连接不上，优先检查：

- Windows 侧服务窗口是否还在运行。
- Windows 防火墙是否允许 Python/uv 监听的 8000 端口。
- WSL2 里是否能访问：`curl http://172.24.224.1:8000/mcp`。MCP endpoint 对普通 GET 未必返回正常业务内容，但至少不应是连接超时。

兼容老客户端时，也可以启动 SSE transport：

```powershell
uv run interview-post-crawl --transport sse --host 0.0.0.0 --port 8000
```

SSE 地址是：

```text
http://172.24.224.1:8000/sse
```

## 工具

每次启动会将服务的终端输出（标准输出、标准错误、访问日志及异常）同步保存到 MCP 项目内的 `logs/<启动时间>-<进程号>.log`，采用 UTF-8 编码，并在启动时打印日志文件路径。默认仍为 INFO 级别；需要抓取阶段细节时使用 `uv run interview-post-crawl --log-level DEBUG`。日志文件已加入 `.gitignore`。

OCR 返回 `FailedOperation.ImageNoText` 时按空文本处理，不计入图片失败；空文本提示使用 DEBUG 级别。其他图片处理异常会在 WARNING 日志中记录处理阶段、异常类型和详细错误（腾讯云 SDK 异常包含错误码、原因及 RequestId），并保留在工具返回的 `image_ocr_errors` 中。

`fetch_interview_post(share_text: str)`

- `share_text`：牛客或小红书 URL，也可以是带说明文字的分享文本。
- 返回单个 `text`，格式如下。时间均为北京时间，帖子发布时间缺失或无法解析时为“未知”。

```text
帖子发布时间：2026-11-11T15:57:00+08:00
抓取时间：2026-11-11T16:00:00+08:00
你应保存文件的地址为：D:/projects/others/interview_post_crawl/面经/2026/11/11/<标题>.md

下面是正文的文字和图片内容，
正文文字：
……

正文中的图片ocr结果:
……
```

### 具体说明

- 以 MCP 项目目录的父目录（与 MCP、skill 同级）为存储根目录，按抓取开始时的北京时间划分 `年/月/日`，不依赖启动时的工作目录。
- 抓取的图片保存到 `面经图片/YYYY/MM/DD/<UUIDv7>/`，图片按正文顺序命名为 `001.webp`、`002.jpg` 等，扩展名优先取响应的图片类型；`metadata.json` 记录 `url`（抓取的帖子 URL，短链接解析后地址）和 `fetched_at`（抓取时间）。无图片的帖子也保存元数据。已保存的图片在 OCR 失败时仍保留。
- 工具返回 `面经/YYYY/MM/DD/` 的绝对目录地址；LLM 根据 skill 生成标题、清理文件名，再用系统文件工具保存 `<标题>.md`。MCP 不提供 Markdown 写入工具。
- 图片不作为 base64/Image 类型返回(因为图片太多容易超出模型上下文)；MCP 将图片转成 base64 调用 OCR，返回识别文字。

小红书抓取时，MCP server 按北京时间在每天首次抓取时从 `.browser_data/xhs` 打开浏览器读取 Cookie，成功后在内存中保存 Cookie 和读取时间，当天后续抓取直接复用，不再访问浏览器。并发请求共用一次成功读取；读取失败不会更新缓存。缓存不持久化，MCP 重启后首次抓取会重新读取。如果登录态失效，会提示先运行 `uv run maintain-xhs-login`；当天已缓存 Cookie 时，重新登录后需重启 MCP 以重新读取。

同一篇小红书帖子的图片按顺序串行下载、保存；第一张不额外等待，后续每张在上一张下载结束后等待 0.3 秒再开始。每张下载完成立即启动 OCR，下一张下载不等待上一张 OCR，多个 OCR 可并发执行。牛客的下载和 OCR 均并发，不额外增加等待。结果按原图片顺序汇总，单张失败不影响其他图片。下载和腾讯云 SDK 的同步调用通过工作线程异步执行，实际并发数由线程池调度。

## 维护小红书登录态

小红书登录态保存在当前项目内：

```text
my_mcp/.browser_data/xhs
```

需要登录或刷新时运行：

```powershell
cd mcp-interview-post-extractor
uv run maintain-xhs-login
```

这个命令对应项目外层的 `maintain_xhs_login.py`，和 MCP server 本身解耦。脚本会先用 headless 打开小红书首页，确认登录入口可见且关键 Cookie 存在；有效则直接退出，无效则打开有头浏览器，让你扫码或验证，成功后保存到 `.browser_data/xhs`。

如果抓取接口明确返回“登录已过期”，可以跳过自动检查，直接强制重新登录：

```powershell
uv run maintain-xhs-login --force
```

## 集成测试

分别是刷新登录信息，测试ocr，单测小红书，测试牛客和小红书+mcp；

```powershell
cd mcp-interview-post-extractor
uv run maintain-xhs-login
uv run python -m unittest -v tests.test_integration_ocr 
uv run python -m unittest -v tests.test_integration_xhs
uv run python -m unittest -v tests.test_integration_fetch
```
