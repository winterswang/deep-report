# PLAN.md — deep-report 开发计划

## 总体策略

分批交付，每步可验证。先跑通 MNSO 端到端再做泛化。

## Sprint 1: 骨架 + Fetcher ✅

| Task | 内容 | 状态 | 备注 |
|------|------|------|------|
| 1.1 | 创建 pyproject.toml，安装依赖 | ✅ | |
| 1.2 | CLI 入口骨架 | ✅ | `python -m deep_report analyze MNSO --period 2026Q1` |
| 1.3 | Fetcher: 下载当前+历史报告 | ✅ | FPI 自动检测（10q→6K，10k→20F） |
| 1.4 | Fetcher: 保留 SEC HTML 原始文件 | ⚠️ | `--pdf` 会转换，HTML 需保留原文件。待后续优化 |

## Sprint 2: Analyzer — Extract Pass ✅

| Task | 内容 | 状态 | 备注 |
|------|------|------|------|
| 2.1 | PDFExtractor: pdfplumber 提取文本+表格 | ✅ | |
| 2.2 | HTMLExtractor: BeautifulSoup → Markdown | ✅ | 代码已实现，测试通过 |
| 2.3 | Extract Prompt: LLM KPI 提取 | ✅ | MNSO 6-K → KPI JSON ✅ |
| 2.4 | 批量 Extract: 并行处理 N 份报告 | ⚠️ | 当前顺序执行，并行留待 v0.2 |
| 2.5 | KPI 字段名标准化映射 | ✅ | 硬编码 alias 映射表，覆盖核心字段 |

## Sprint 3: Analyzer — Analyze Pass ✅

| Task | 内容 | 状态 | 备注 |
|------|------|------|------|
| 3.1 | Analyze Prompt: 多期 KPI → 分析报告 | ✅ | 2 期 KPI → 7KB 分析报告 |
| 3.2 | TrendBuilder: 数值化趋势表 | ✅ | `_build_trend_table` 实现 |
| 3.3 | Validator: financial-sdk 交叉校验 | ✅ | 已修复 import 路径问题 |

## Sprint 4: Writer + 端到端 ✅

| Task | 内容 | 状态 | 备注 |
|------|------|------|------|
| 4.1 | Writer: 生成飞书文档 | ✅ | `feishu_create_doc` + 文件兜底 |
| 4.2 | CLI 集成：一键 `analyze` | ✅ | MNSO 端到端通过 |
| 4.3 | 错误处理：降级 | ✅ | LLM 失败不崩溃，跳过缺失数据 |

## Sprint 5: Review + 二次修正 ✅

| Task | 内容 | 状态 | 备注 |
|------|------|------|------|
| 5.1 | 对照 DESIGN.md 检查遗漏 | ✅ | 发现 8 个 gap/defect |
| 5.2 | 修正 gap | ✅ | 全部修复 |
| 5.3 | 更新 DESIGN.md 和 PLAN.md | ✅ | 本文档 |

---

## Review 发现与修正

| # | 严重度 | 问题 | 修正 |
|---|--------|------|------|
| 1 | 🔴 | ARK (doubao) 持续 429 rate-limit | 主 provider 切换为 deepseek-chat，ARK 降为 fallback |
| 2 | 🔴 | financial-sdk `_validate` import 报错 | 修复 import path 和缩进 |
| 3 | 🟡 | LLM 输出含"好的，收到"前缀 | 添加 `_clean_llm_response` 清理 |
| 4 | 🟡 | 设计文档说"喂原文"但实际喂 KPI JSON | 更新 DESIGN.md——KPI JSON 更省 token |
| 5 | 🟡 | 并行 Extract 未实现 | 标注为 v0.2 改进 |
| 6 | 🟡 | HTML 原文保留未实现 | 标注为 v0.2 改进 |
| 7 | 🟡 | 公司名提取太长 | 标注待优化 |
| 8 | 🟡 | 历史报告只下载了 2 期 | MNSO 业绩公告仅 2 期可获取，非代码问题 |
| 9 | 🔴 | `_find_existing` last resort 回退捡错报告 | 2025 10k→20F 未找到，fallback 捡了 2025 Q1 6-K，当作 Q4 年报用。已修复：不匹配则返回 None，触发重下 |

## v0.2 待改进

- [ ] Extract 并行化（ThreadPoolExecutor）
- [ ] SEC HTML 原文保留（不转 PDF 或保留双份）
- [ ] 公司 KPI 模板机制（YAML，解决公司名/品牌拆分问题）
- [ ] 与 morning-brief 集成（earnings event → auto analyze）
- [ ] 更多公司测试（A 股、港股）
