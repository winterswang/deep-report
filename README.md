# deep-report

财报深度分析引擎 — LLM 驱动的季度/年度财务报告分析工具。

废弃 `annual_report` 的 NLP 规则匹配，改用 **LLM 直接理解财报原文**，输出结构化分析 + 叙事报告。

## 架构

```
Fetcher → Analyzer → Writer
  │           │           │
  │      ┌────┴────┐      │
  │   Extract   Analyze   │
  │   每份→KPI   多期→报告  │
  │      (LLM)   (LLM)    │
  ▼           │           ▼
unified-dl  financial-sdk  feishu
```

## 快速开始

```bash
cd /root/code/deep-report
pip install -e . --break-system-packages

# 分析 MNSO 2026Q1 季度报告
python -m deep_report analyze MNSO --period 2026Q1

# 指定历史期数
python -m deep_report analyze MNSO --period 2026Q1 --history 4

# dry-run（仅下载，不分析）
python -m deep_report analyze MNSO --period 2026Q1 --dry-run
```

## 工作原理

1. **Fetcher** — 调度 `unified-downloader` 下载当前+历史 SEC/交易所报告
2. **Analyzer** — 两个 LLM Pass：
   - **Extract**：从每份报告提取 KPI → 结构化 JSON
   - **Analyze**：多期 KPI 合并 → 趋势分析 + 叙事报告（仿雪球海豚君风格）
3. **Writer** — 输出为飞书文档 + 自动同步 IMA 知识库

## 分析维度

| 模块 | 内容 |
|------|------|
| 🧭 核心观点 | 一句话定调，抓住核心矛盾 |
| 一、营收 | 总量 + 分品牌/地区 + vs 指引 |
| 二、渠道 | 门店数、净增、直营占比 |
| 三、同店 | 国内/海外同店增速 |
| 四、毛利率 | 变动归因（成本/结构） |
| 五、费用 | 销售/管理费率 + 费用增速 |
| 六、利润质量 | 剔除非经常性，判断核心盈利能力 |
| 七、现金流 | 经营现金流、资本开支、FCF |
| 八、展望 | 管理层指引 + 催化剂 + 风险 |

## 与现有项目关系

```
morning-brief (季报事件触发)
       │
       ├─→ unified-downloader (下载PDF)
       └─→ deep-report (分析+报告)
                │
                ├─→ financial-sdk (交叉校验)
                └─→ feishu + IMA (输出)
```

## 兼容性

- 🇺🇸 美股：SEC 10-K/10-Q/20-F/6-K/8-K（自动检测 FPI）
- 🇭🇰 港股：港交所年报/半年报/季报
- 🇨🇳 A股：沪深年报/半年报/季报

## License

MIT
