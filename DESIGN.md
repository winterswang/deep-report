# deep-report — 财报深度分析引擎

## 定位

独立分析引擎，替代 `annual_report`（废弃）。不依赖 NLP 规则匹配，用 LLM 直接理解财报文本，输出结构化分析 + 叙事报告。

## 核心理念

```
old: PDF → 规则NLP → 碎片字段 (annual_report, 废弃)
new: 报告原文 → LLM理解提取 → 跨期对比 → LLM叙事 (deep-report)
```

## 精简三层架构

```
┌─────────────────────────────────────────────────┐
│                    deep-report                   │
│                                                  │
│  ┌──────────┐   ┌──────────────┐   ┌──────────┐ │
│  │ Fetcher  │→  │   Analyzer   │→  │  Writer  │ │
│  │ 下载报告  │   │  LLM提取+分析 │   │ 飞书文档  │ │
│  └──────────┘   └──────┬───────┘   └──────────┘ │
│                        │                         │
│           ┌────────────┴────────────┐            │
│           ▼                         ▼            │
│    Extract Pass               Analyze Pass       │
│    每份报告 → KPI JSON        多期KPI → 叙事+表   │
│    (并行调用)                  (一次调用)          │
│                                                  │
│  交叉校验: financial-sdk (营收/利润/现金流)        │
└─────────────────────────────────────────────────┘
```

### 关键设计决策

| # | 决策 | 理由 |
|---|------|------|
| 1 | SEC 报告直接用 HTML 原文，不转 PDF 再提取 | HTML 保留表格结构、章节层级，PDF 丢失 |
| 2 | A 股/港股用 pdfplumber 提取 | 原生 PDF，无 HTML 源 |
| 3 | 六层管线精简为三层 | 减少中间产物，LLM 可一次完成提取+对比 |
| 4 | 不用 YAML 模板预设字段 | LLM 自发现关键指标 + 映射到标准字段名 |
| 5 | 多期报告一次喂入 → KPI JSON 作为中间层 | 减少 token；Extract 提取结构化 KPI，Analyze 基于 KPI 做跨期分析 |
| 6 | financial-sdk 做交叉校验（辅助） | 营收/利润/现金流等核心字段对照 |
| 7 | 主 LLM provider: DeepSeek-chat | doubao 频繁 429，deepseek-chat 更稳定 |

## 三层职责

### Fetcher（下载层）

```
职责：确保当前+历史报告本地可用
输入：股票代码 + 报告周期（如 MNSO, Q1 2026）
输出：[报告路径列表]，按时间倒序

逻辑：
  - 当前季度PDF是否已下载 → 未下载则调用 unified-downloader
  - 最近 N 份历史季度是否已下载 → 补下
  - SEC 报告保留 HTML 原始文件
  - 覆盖 FPI（6-K/20-F）和本土公司（10-Q/10-K）
```

### Analyzer（分析层）⭐ 核心

```
职责：从报告文本中提取 KPI + 跨期对比 + 生成分析叙事
输入：[报告路径列表]
输出：验证后的结构化数据 + Markdown 分析报告

两个 LLM Pass：

Pass 1 — Extract（每份报告并行）:
  输入: 单份报告全文（HTML文本或PDF提取文本）
  输出: KPI JSON [{field, value, unit, yoy, source_para}, ...]
  策略: LLM 自发现关键指标，无需预设模板
  实际: 顺序执行（O(n)），后续可改为 ThreadPoolExecutor 并行

Pass 2 — Analyze（多期合并）:
  输入: N 期 KPI JSON 合并
  输出: 趋势表 + 分析叙事 + 异常标记
  风格: 仿雪球海豚君——先核心观点，再逐模块拆解

校验:
  financial-sdk 交叉验证营收、净利润、现金流等核心数据
  偏差 > 5% 标记 ⚠️
```

### Writer（输出层）

```
职责：将分析结果输出为飞书文档
输入：结构化数据 + 分析报告 Markdown
输出：飞书文档 URL

模块顺序:
  1. 核心观点（一句话定调）
  2. 营收分析（总量 + 分品牌/地区）
  3. 渠道与门店
  4. 同店表现
  5. 毛利率
  6. 费用结构
  7. 利润质量（剔除非经常性）
  8. 现金流与财务健康
  9. 展望与风险
```

## 与现有项目关系

```
morning-brief (季报事件触发)
       │
       ├─→ unified-downloader (下载PDF/HTML)
       └─→ deep-report (分析+生成报告)
                │
                ├─→ financial-sdk (交叉校验)
                └─→ 飞书 (输出文档)
```

## 目录结构

```
/root/code/deep-report/
├── DESIGN.md              # 本文件
├── PLAN.md                # 开发计划
├── pyproject.toml
├── src/deep_report/
│   ├── __init__.py
│   ├── __main__.py        # python -m 入口
│   ├── cli.py             # 命令处理
│   ├── fetcher.py         # 报告下载
│   ├── analyzer.py        # LLM 提取+分析
│   ├── writer.py          # 飞书文档生成
│   └── prompts/           # LLM 提示词
│       ├── extract.md     # KPI 提取 prompt
│       └── analyze.md     # 分析报告 prompt
└── tests/
```

## 使用方式

```bash
# 手动触发
cd /root/code/deep-report
python -m src.deep_report.cli analyze MNSO --period 2026Q1

# 被 morning-brief 自动触发
# (earnings event → download → deep-report analyze)
```
