# CAD 分步建模辅助

本机工具负责解析、执行、检查；GitHub 保存工具与阶段记录；AI 只处理读图歧义、建模路线和结果评审。普通本地命令不调用 AI。

**2222 停在读图评审。上一版模型被用户否定，不能作为正确结果，也不自动继续修改。**

## 使用

本机双击 `打开进度.cmd` 查看中文工作台，双击 `本地工具.cmd` 使用菜单。无需反复询问 AI 获取进度。

首次使用：复制 `config.example.json` 为 `config.local.json`，填写已有软件路径；`python -m pip install -r requirements.txt` 安装 DXF 解析依赖。本机已配置已有环境，不重复安装。

```powershell
python cadflow.py doctor
python cadflow.py dashboard
python cadflow.py ingest --case sample --dwg C:/input.dwg
python cadflow.py import-review --case sample --file review.json
python cadflow.py check-plan --case sample --plan plan.json
python cadflow.py run --case sample --plan plan.json
```

## 工作流程

1. 复制原 DWG，登记 SHA256；只对副本导出 DXF；按哈希复用缓存。
2. 先标记中性区域 A/B/C/D，不按纸面位置自动认定主视、侧视或投影方式。
3. 核对单位、比例、视向、坐标、线条含义、尺寸依据及跨视图对应。详见 `docs/建模流程.md`。
4. 一阶段一个计划，先写几何目的、依据、命令和预期检查；可以建立独立工具体后并/差/交组合。
5. 调用本机已安装的 SolidWorks MCP 原生特征接口。每阶段另存；失败即停，不循环试参数。
6. 保存实体检查、三视图和阶段记录。程序检查通过后仍需造型评审，不能只凭实体有效认定还原正确。

## 能力边界

- 已实现 DXF 实体/标注清点、缓存、指定区域 SVG、点击源实体编号、评审模板、执行关口、中文进度和本机 SolidWorks 适配器。
- 布尔命令复用 `combine_bodies` 的 `add/subtract/common`，不另写几何内核。
- 程序只能检查评审资料完整性，**不能自动证明三视图理解正确**，也不能凭视图补全所有隐藏结构。
- GitHub Actions 仅运行不依赖 CAD 的自动检查；未配置常驻本机执行器。SolidWorks 仍在本机交互会话运行。
- 尚未完成复杂曲面命令的计划适配；需要时复用既有接口，经样本验证再加入，不宣称全命令已覆盖。
- 原 DWG、DXF、阶段 CAD 文件和本机路径留在本地，源码及轻量证据同步 GitHub，减少同步体积。

第三方依赖：本机 `solidworks-mcp`、[ezdxf](https://github.com/mozman/ezdxf)、AutoCAD 2023、SolidWorks 2026。仓库不分发商业软件或第三方包。
