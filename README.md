# zotero-literature-import v2 · 文献全文获取与 Zotero Connector MCP

配套浏览器插件可从 [MCP Releases](https://github.com/belvme/zotero-connector-mcp/releases/latest) 下载；[安装说明](https://github.com/belvme/zotero-connector-mcp#使用预构建插件)包含本地自动配对步骤，无需自行构建上游 Connector。

让 AI 助手通过浏览器中的 Zotero Connector 保存论文，并检查全文是否真正进入文库。

面向知网、EBSCO 与其他 Zotero 支持的学术网站。项目包含一个可复用的 **文献导入 skill** 和一个 **Connector MCP 桥接服务**：skill 负责检索、核对与整理流程，MCP 负责调用真实插件并检查本机 PDF。

**v2（2026-10-24）** 在原有 skill 上增加配套 MCP、后台自动连接、保存与附件核验合并调用，以及 Windows 持续授权复用。原有 skill 安装路径不变，MCP 为新增可选组件。

## 能做什么

- **保存当前论文**：识别活动网页及翻译器，调用 Connector 原有的工具栏保存流程。
- **减少重复操作**：一次调用保存并等待附件；已有有效 PDF 时跳过保存，避免反复抓取元数据。
- **核对真实文件**：检查 PDF 文件头、页数和 MD5，返回首页片段供文献身份复核。
- **保留浏览器登录**：沿用用户的机构会话，不导出 Cookie，不要求重新登录每篇论文。
- **后台自动连接**：不必保留桥接标签页，也不用手填配对令牌。
- **持续本机授权**：在 Windows 中可将用户选择的“始终允许”授权保存到凭据管理器，供受支持的本机写入客户端复用。

## 两个组件

| 组件 | 用途 | 入口 |
| --- | --- | --- |
| `zotero-literature-import` skill | 检索、下载、去重、补附件和整理时的执行规则 | [SKILL.md](SKILL.md) |
| Connector MCP | 活动页识别、插件保存、PDF 核验与本机授权管理 | [独立仓库](https://github.com/belvme/zotero-connector-mcp) |

skill 可以独立使用。MCP 已拆分到独立仓库，需要构建并加载桥接版 Connector，不能直接操控未经修改的官方扩展。

## 使用示例

```text
使用 $zotero-literature-import，检查我指定分类中缺少全文的文献。
先确认数据库的机构登录，再用 Zotero 插件保存，核对真实 PDF。
已有条目请保留笔记和分类，避免重复入库。
```

```text
把当前论文用 Zotero 插件保存，等待 PDF 下载并告诉我是否已核验。
```

安装 skill：把 `skills/zotero-literature-import` 文件夹复制到所用助手支持的技能目录，例如 Codex 的 `~/.codex/skills/`。随后按 [MCP 安装说明](https://github.com/belvme/zotero-connector-mcp#安装)接通浏览器。

## 适用边界

真实环境中验证过知网与 EBSCO 的插件保存；部分文献可自动取得 PDF，部分只取得元数据。**保存请求成功不等于全文下载完成。** 阅读器的“下载即将完成”提示也需要实际文件验证。

MCP 不自动合并或删除文献。skill 中的补附件、元数据更新及归并需要客户端具备对应本机 API 能力，并遵循用户授权。浏览器下载接口在不同 Chromium 宿主中的兼容性仍需逐一验证；页面正常下载可作为受支持的后备路径。

当前部署与持续授权主要在 Windows 上验证。浏览器要求 Chromium 116+、允许加载解压扩展；本机写入授权要求支持写 API 的 Zotero 版本（Zotero 10+）。学校登录、验证码和全文获取仍遵循网站正常流程。

## 隐私与许可证

公开版本不包含个人学校入口、绝对个人路径、文库条目、PDF、截图、运行记录、配对令牌或授权缓存。配对配置在部署时本机生成；持续授权只在 Windows 凭据管理器中保存。

本项目不是 Zotero 官方产品。Connector 来自 [zotero/zotero-connectors](https://github.com/zotero/zotero-connectors)，其版权和原有声明必须保留。skill 延续原有 [MIT License](LICENSE.txt)；配套 MCP 使用 [AGPL-3.0](https://github.com/belvme/zotero-connector-mcp/blob/main/LICENSE.txt)。分发桥接版 Connector 时须保留上游许可证及声明，参见 [第三方说明](NOTICE.md)。

## v2.1：插件补全文与只留一条

入库后清理本次临时下载原件：先核验 Zotero 已保存独立、可读且哈希匹配的副本，再将下载原件移至回收站。链接附件、未完成入库和用户另行保留的交付件不自动清理。

正常下载未找到文件时，先检查 Connector 自动附件；用户手动插件保存后重新核验真实 PDF。用户要求只留一条时，可保留新导入的完整全文记录，归并独有笔记、附件、分类、标签和关系后，再将旧记录软删除。最终 key 可以变化，任务目标是每篇只留一条完整活动记录。

MCP 已独立维护：[belvme/zotero-connector-mcp](https://github.com/belvme/zotero-connector-mcp)。原 v2.0.0 版本保留，当前版本使用独立组件。
