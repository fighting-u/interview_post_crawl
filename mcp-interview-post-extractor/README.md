# Interview Crawl MCP

一个独立 MCP 服务，实现给定牛客或小红书分享链接获取正文文字和图片文字内容，并将提取信息保存到本地md文件。

## 安装依赖

```powershell
cd my_mcp
uv sync
```

## 启动 HTTP MCP 服务

```powershell
cd my_mcp
uv run interview-crawl-mcp
```

这个命令会启动一个常驻 HTTP MCP server，默认监听：

```text
http://0.0.0.0:8000/mcp
```

如果端口冲突，可以改端口：

```powershell
uv run interview-crawl-mcp --port 8010
```

默认使用 MCP 的 `streamable-http` transport。也可以显式指定：

```powershell
uv run interview-crawl-mcp --transport streamable-http --host 0.0.0.0 --port 8000 --path /mcp
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

如果 OpenClaw 支持配置 MCP server JSON，可以按它的 remote/http MCP 配置格式填写这个 URL；server 名称建议用 `interview-crawl`。

如果连接不上，优先检查：

- Windows 侧服务窗口是否还在运行。
- Windows 防火墙是否允许 Python/uv 监听的 8000 端口。
- WSL2 里是否能访问：`curl http://172.24.224.1:8000/mcp`。MCP endpoint 对普通 GET 未必返回正常业务内容，但至少不应是连接超时。

兼容老客户端时，也可以启动 SSE transport：

```powershell
uv run interview-crawl-mcp --transport sse --host 0.0.0.0 --port 8000
```

SSE 地址是：

```text
http://172.24.224.1:8000/sse
```

## 工具

`fetch_interview_post(share_text: str)`

- `share_text`：牛客或小红书 URL，也可以是带说明文字的分享文本。
- 返回：单个 `text`，格式为 `正文：...` 和 `图片ocr结果: ...`。
- 图片不会保存到本地，也不会作为 base64/Image 类型返回；MCP server 会直接把图片转成 base64 调用 OCR，再把多张图片的识别文字用 `\n` 拼接到 `图片ocr结果` 中。

小红书抓取时，MCP server 会在每次请求前从 `.browser_data/xhs` 重新读取最新 Cookie 字符串，然后调用详情接口。如果登录态失效，会提示先运行 `uv run maintain-xhs-login`。

`write_interview_markdown(filename: str, content: str)`

- `filename`：Markdown 文件名，可以不带 `.md` 后缀。
- `content`：要写入的 Markdown 内容。
- 固定写入 `my_mcp/面经` 目录；同名文件不会覆盖，会自动保存为 `文件名-1.md`、`文件名-2.md`。

## 维护小红书登录态

小红书登录态保存在当前项目内：

```text
my_mcp/.browser_data/xhs
```

需要登录或刷新时运行：

```powershell
cd my_mcp
uv run maintain-xhs-login
```

这个命令对应项目外层的 `maintain_xhs_login.py`，和 MCP server 本身解耦。脚本会先用 headless 打开小红书首页，确认登录入口可见且关键 Cookie 存在；有效则直接退出，无效则打开有头浏览器，让你扫码或验证，成功后保存到 `.browser_data/xhs`。

如果抓取接口明确返回“登录已过期”，可以跳过自动检查，直接强制重新登录：

```powershell
uv run maintain-xhs-login --force
```

## 集成测试

集成测试会真实访问牛客和小红书，并把返回内容打印到终端。小红书测试前请先确保登录态有效：

```powershell
cd my_mcp
uv run maintain-xhs-login
uv run python -m unittest -v tests.test_integration_fetch
uv run python -m unittest -v tests.test_integration_ocr   
```
