# Zotero Connector MCP

通过本机 MCP 调用浏览器中的真实 Zotero Connector，自动连接当前论文页，并核对 PDF 附件是否落盘。

## 安装

以下命令在本目录执行。需要 Python、Git、Node.js/npm 和支持本地解压扩展的 Chromium 116+ 浏览器。Windows 已完成实际部署验证。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
git clone https://github.com/zotero/zotero-connectors.git connector-build
git -C connector-build checkout 876e41ad15139077f2e07b2f71a0fa94742e0b4a
git -C connector-build submodule update --init
npm --prefix connector-build ci --ignore-scripts
python build_windows.py
python prepare.py patch --source connector-build/build/manifestv3 --output connector-ready
python verify_build.py
```

`build_windows.py` 使用固定上游版本的资源和官方 Gulp 变换。输出目录已存在时拒绝覆盖；再次构建请先保留需要的内容并选择干净的构建目录。其他平台可按 [上游构建说明](https://github.com/zotero/zotero-connectors#building)生成未压缩 MV3 构建，再用 `prepare.py patch` 生成独立副本。找不到准确的官方工具栏钩子时，补丁会拒绝执行。

1. 在目标浏览器中加载 `connector-ready` 解压扩展，名称为 **Zotero Connector (MCP Bridge)**。避免同时启用原版和桥接版而重复保存。
2. 在 MCP 客户端注册 `server.py`，解释器使用上述虚拟环境中的 Python。参考 [配置示例](mcp-config.example.json)，替换为实际绝对路径。
3. 打开 Zotero，启用允许其他本机应用通信的设置。
4. 打开准确论文详情页并确认机构登录，调用 `connector_status` 或 `save_and_check_pdf`。

补丁自动生成 `.bridge-token` 和扩展内的 `mcp-private-config.json`。配对令牌与 Zotero 本机写入 key 是两种独立凭据；不要上传已经配对的扩展目录。后台连接不需要可见桥接页，也不用输入令牌。

MCP 使用 stdio，浏览器后台通过 `127.0.0.1:23130` WebSocket 连接。环境变量 `ZOTERO_MCP_WS_PORT` 控制服务端监听端口，扩展配对端口必须与之相同；通常保留默认值。同一时间只启动一个实例，诊断客户端与正式服务不能争用端口。

## 工具

| 工具 | 行为 |
| --- | --- |
| `connector_status(expected_url="")` | 查询当前论文、翻译器和 Zotero 在线状态 |
| `save_and_check_pdf(title, expected_url, doi="", wait_seconds=30)` | 已有有效 PDF 则跳过保存；否则触发一次插件保存并核对实际附件，最长等待 60 秒 |
| `save_current_page(expected_url="")` | 仅触发官方保存动作，需要后续核对 |
| `download_current_pdf(expected_url)` | 下载页面明确的 PDF 文件链接；兼容性取决于浏览器宿主，不把阅读器 HTML 当 PDF |
| `download_status(download_id)` | 查询本次桥接发起的下载 |
| `local_write_auth_status()` | 查看本应用授权缓存状态，不弹窗、不写入文献 |
| `authorize_local_writes()` | 复用保存的本机许可，或首次显示 Zotero 授权弹窗 |

`save_and_check_pdf` 返回父条目、附件 key、页数、字节、MD5 和首页片段。校验格式与哈希不等于确定文献身份，仍需核对题名、作者和版本。DOI 不一致时保留候选并标记待审，不自动当作同文献合并。返回待下载或超时后先核对副作用，不能直接重复保存。

## 知网与 EBSCO

同一平台有效机构会话可连续复用。知网优先论文详情页；EBSCO 从用户所属机构入口搜索，准确匹配题名、作者、年份后进入 `/search/details/`，由 `EBSCO Discovery Layer` 翻译器保存。

缺 PDF 才走正常页面下载。EBSCO 可进入页面明确显示的 `/viewer/pdf/` 阅读器，选择“下载 → 全文 → PDF → 下载”。阅读器 URL 是 HTML；成功提示不代替实际文件核验。EBSCO 可能提供 JSTOR DOI，不能不经核对就覆盖原有出版社 DOI。

## 一次持续授权（Windows）

```powershell
python local_api.py status
python local_api.py authorize
```

用户在 Zotero 中选择“始终允许”后，本客户端将 `remember:true` 的新授权保存到当前 Windows 用户的凭据管理器，按 Zotero 数据库实例隔离。客户端复用自己的固定凭据项，不读取其他应用凭据。普通“允许”只有一次写请求权限，多阶段操作会在写入前停止。

`local_api.py` 是供本机代码使用的客户端；本 MCP 没有公开任意 API 写入、上传或归并工具。持续授权不扩大用户原有任务范围。401 后清除本客户端失效缓存并停止；不重放结果不明的写入。

`python local_api.py forget` 只移除本客户端缓存。撤销服务端许可需在 Zotero 设置 → 高级 → “清除写入授权”操作。`status` 的缓存存在标记不证明服务端尚未撤销：本机 GET 不校验写入 key。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
node --test tests/test_extension.cjs
.\.venv\Scripts\python.exe tests/mcp_v2_smoke.py
```

集成测试使用真实 stdio MCP、WebSocket 和生产桥接脚本，浏览器 API 使用模拟宿主。JavaScript/集成测试依赖上游 `connector-build/node_modules`，需先完成安装步骤。Windows 凭据管理器测试仅使用随机虚构凭据项并清理；受限沙盒可能拒绝访问系统凭据库，需在当前用户的正常终端执行。

历史真实验证包括无桥接页连接、活动论文定位、知网及 EBSCO 保存、部分自动 PDF、重启自动恢复，以及持续许可下多阶段本机写入。它们不保证每篇论文或每个浏览器宿主都自动下载成功。

本机服务只绑定 loopback，拒绝非扩展来源和错误配对；活动页有歧义时拒绝写入；未知结果不自动重试。下载路径限定为明确链接，默认支持同源或知网域名族。请勿把本机端口转发或暴露到公网。
