# Zotero 本机接口：保存、上传与核对

此通道不要求桌面点击。全文下载与 Zotero 文件上传是两件事；接口上传能力不提供学校数据库访问权限。

## 连接与能力

通常服务根地址为 `http://127.0.0.1:23119`，只向经过核对的本机 loopback 服务发送本地授权和文件；不将密钥发到外部地址。

```text
GET  /api/                         # 读取响应头中的 API 版本及 Server ID
POST /connector/getSelectedCollection   {}  # 只取当前目标字段，不输出文库全部标签
GET  /api/users/0/items/top?q=...&qmode=titleCreatorYear
GET  /api/users/0/items/<parentKey>/children
GET  /api/users/0/items/<attachmentKey>/file/view/url
```

根地址响应正文可能是普通文本。先检查应用和连接；不要因为正文不是 JSON 就判定接口失效。连接拒绝且应用未开时提醒打开；应用已运行时检查本机通信设置，不擅自更改安全设置。

按实际接口能力与当前官方说明选择，不用主版本号推断是否支持写入。官方文档与响应不一致时，可只读查看已安装程序中对应接口的源文件；不要读取整个文库、配置中的凭据，或修改应用文件。

官方参考：[本机 API](https://www.zotero.org/support/dev/web_api/v3/local_api)、[Connector Server](https://www.zotero.org/support/dev/client_coding/connector_http_server)、[写入请求](https://www.zotero.org/support/dev/web_api/v3/write_requests)、[文件上传](https://www.zotero.org/support/dev/web_api/v3/file_upload)。

## 保存新条目：已成功的路径

1. 先查 DOI、完整题名；题名查询为空时，用题名中的一两个特征词复核。候选不等于确认匹配，结合作者、年份、类型核对。没有候选才新建。
2. 确认当前目标文库/分类。创建随机 `sessionID` 和稳定的 connector item `id`，**在提交前**保存到任务状态文件，连同已核实元数据及开始时间；不保存 cookies 或 API key。
3. POST `/connector/saveItems`，请求头为 `Content-Type: application/json` 和当前支持的 `Zotero-Connector-API-Version`（本环境已使用 `3`）。载荷结构：

```json
{
  "sessionID": "<generated and persisted before the request>",
  "uri": "<verified source page URL>",
  "items": [{
    "id": "<stable task item ID>",
    "itemType": "journalArticle",
    "title": "<verified title>",
    "creators": [{"creatorType": "author", "firstName": "<given>", "lastName": "<family>"}],
    "date": "<verified year or date>",
    "url": "<verified source page URL>",
    "attachments": [],
    "notes": [],
    "tags": []
  }]
}
```

类型使用实际类型，杂志文章为 `magazineArticle`。按来源补卷期页码等字段，不填写占位值或猜测的 DOI。`attachments` 可包含已验证来源允许获取的文件链接，但设置 URL 不代表文件会保存。

4. **201 可以没有响应正文，甚至仍标注 JSON。** 读取原始响应后，正文非空且应为 JSON 才解析；空正文成功后继续核对。
5. 保存后重新查询条目并记录真实数据库 key。connector item `id`、数据库 key、session ID 是不同标识，不能混用。
6. 查询子附件；存在附件记录时继续检查实际文件。`saveItems` 成功仅证明进入保存流程，不保证自动下载 PDF 或 HTML。

Python 响应处理示例（对 `response` 读取一次）：

```python
raw = response.read()
status = response.status
content_type = response.headers.get('Content-Type', '')
body = None
if raw.strip():
    text = raw.decode('utf-8')
    body = json.loads(text) if 'json' in content_type.lower() else text
# 记录 status、目标与 session；无论有无 JSON，都重新核对入库结果。
```

超时、解析失败、409 `SESSION_EXISTS` 或结果不明后，先查已入库条目和子附件；原请求可能已生效。不要换新 session 盲目重发，也不要把原脚本从头执行当成重试。

## 给已有条目加文件：无需 Computer Use 的路径

通过受支持的本机写入接口给已有父条目建附件并上传，支持 PDF 或实际 HTML 文件。已有父条目不得再次用 `saveItems` 补文件，以免新建重复项。

### 本机写入授权

从 `GET /api/` 读取 `Zotero-Server-ID`。首次需要授权时：

```http
POST /api/local/authorize
Content-Type: application/json
Zotero-Server-ID: <current instance ID>

{"appName":"文献附件导入与整理"}
```

先向用户说明 Zotero 会弹出本机写入许可，由用户选择允许或拒绝，不替用户接受。“允许一次”的 key 在首次写请求通过验证时即可能被消耗，即使后续操作失败；多阶段上传可能需要再次授权。只有用户选择持久许可时才复用，不要求用户必须选“始终允许”。

key 仅留在内存，后续需要鉴权的请求使用 `Zotero-API-Key` 请求头；不要写到 URL、日志、脚本、会话状态或报告。写请求携带当前 `Zotero-Server-ID`。401 时按授权流程处理，403 拒绝后停止写入，412/428 时核对实例，不绕过。

### 建立附件及三阶段上传

1. 再读取父条目和子附件，避免用户刚保存的文件被重复导入。核对本地文件实际格式、内容及字节数。
2. POST `/api/users/0/items` 创建子附件。使用 `itemType: attachment`、`parentItem: <parentKey>`、`linkMode: imported_file`、准确 `filename` 和标题；PDF 为 `contentType: application/pdf`，HTML 为 `text/html`。检查批量响应中 `successful`/`failed`，取实际附件 key 和版本。
3. 向 `/api/users/0/items/<attachmentKey>/file` POST 表单：`md5`、`filename`、`filesize`、`mtime`（毫秒）及准确 `contentType`。新文件带 `If-None-Match: *`，覆盖已有文件带原 MD5 的 `If-Match`；不覆盖未经核对的既有附件。
4. 返回 `exists: 1` 时跳过传输，直接核对。否则检查返回 `url` 仍是当前 loopback 实例，再按实际返回的协议上传：默认模式发送 `prefix + 文件字节 + suffix`；请求 `params=1` 时按返回的字段构造 multipart，不混用两种模式。上传 key 只发给返回的本机端点，核对状态和哈希。
5. 向相同 `/file` 端点 POST `upload=<uploadKey>` 注册，携带所需鉴权和文件前提条件，核对 204；204 无正文，不调用 JSON 解析。
6. 重新读取子附件及 `/file/view/url`，解析本机路径。检查文件存在、大小非零、MD5/格式及内容符合目标。PDF 核对页数，HTML 核对正文而非登录页；只有实际验证后才能报告附件完成。

上传中断后记录空附件 key、已完成阶段和文件哈希；重试先核对是否已经注册，不新建多个空附件。无写入接口或用户未授权时报告具体阻碍，再考虑受支持的手动附加方式。

## 整理与会话附件边界

- 更新条目时保留当前 `data` 中独有内容，带当前版本或对应前提条件。分类、标签、关系合并保留；不整对象覆盖用户新改动。
- `/connector/saveAttachment`、`saveSingleFile` 等需要真实原保存会话和 connector item ID；只有会话仍在、接口协议已核对时使用，不把数据库 key 代入或猜 session。已保存 HTML 文件一般走上面的文件上传流程。
- 读取子附件只能证明元数据存在。不能根据 `contentType: application/pdf` 或插件的绿色提示报告全文已经保存。
- 只读助手用法：`python scripts/audit_zotero.py --manifest <papers.json> --output <audit.json>`。清单支持数组或 `{"references":[...]}`，每篇需要字符串 `title`，可带 `doi`/`DOI`、`year`、`id`/`n`。报告待人工核对候选不授权自动合并，也不统计 HTML；只查询本次论文。
