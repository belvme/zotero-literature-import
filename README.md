# zotero-literature-import · 文献全文获取与 Zotero 导入

获取并核对 PDF 或 HTML 全文，为已有条目补附件，并检查重复、整理目标分类。

由 **belvme** 维护。技能规则见 [SKILL.md](SKILL.md)，配套参考文件和脚本随仓库提供。

## 使用方法

将本仓库下载到所用 AI 工具的技能目录，文件夹命名为 `zotero-literature-import`，确保其下直接包含 `SKILL.md`。

任务输入：文献清单、目标分类。

> 使用 zotero-literature-import，为这些文献查找全文并导入指定分类，核对实际附件，报告缺失项和重复候选。

## 运行条件与交付

Zotero 桌面应用、可用的本机 API；浏览器用于需要登录的全文来源；Python 3；pypdf 为可选依赖。

依照技能说明执行并核对实际输出。需使用本机应用、数据库或浏览器登录时，请在自己的环境中配置路径、来源和权限。

## 文件结构

- `SKILL.md`：触发条件、工作流程及核对要求。
- `agents/openai.yaml`：Agent 显示名称与默认提示。
- `references/`：按任务需要读取的补充说明。
- `scripts/`：配套检查脚本；使用方法见技能说明。

## 许可证

[MIT License](LICENSE.txt)。
