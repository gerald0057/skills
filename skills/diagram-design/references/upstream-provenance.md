# 上游来源与本地适配

- 地址：https://github.com/cathrynlavery/diagram-design
- 固定 commit：`11b08eade8049c441ed3df1895e3b527c26c0fbe`
- 导入目录：`skills/diagram-design/`（全部 286 个文件）
- 导入日期：2026-10-14（UTC）；上游 frontmatter 版本：2.6。
- 原始文件 SHA-256 清单：[upstream-files.json](upstream-files.json)。更新必须重新审阅及固定 commit，不自动追踪 main。

本地差异：SKILL.md frontmatter 仅保留 name/description；增加本地 HTML/SVG、离线中文字体规则及开发工具边界；style-guide.md 的 Font source 改为 system。其余上游技能文件保持字节一致。

本技能的 MIT 原文位于 ../LICENSE；../THIRD_PARTY_LICENSES.md 原样保留上游全部声明（其中部分条目对应未导入的上游插件或开发资源）。这不改变仓库或其他技能的许可证。实际打包图标使用的完整许可文本位于 licenses/tabler-icons-LICENSE、licenses/log-z-logos-LICENSE、licenses/simple-icons-LICENSE.md。

第三方许可文本取自上游声明所列公开地址，下载日期为 2026-10-14：
- https://raw.githubusercontent.com/tabler/tabler-icons/main/LICENSE
- https://raw.githubusercontent.com/log-z/logos/main/LICENSE
- https://raw.githubusercontent.com/simple-icons/simple-icons/develop/LICENSE.md

五个 Python 工具只使用标准库。静态入口审阅未发现网络、子进程或动态执行入口；self_check.py 读取待验证文件及本技能模板，export_svg.py 读取 HTML 并写入指定 SVG，导入器读取本地输入并可写指定摘要。仅本地验证与 SVG 导出工具经过本次运行验证；导入器未做功能验证。上游开发脚本、PNG 工具和插件安装器未运行。
