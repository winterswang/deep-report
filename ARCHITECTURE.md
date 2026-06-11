# deep-report — 项目全貌

> LLM 驱动的财报深度分析引擎 | v3.6 | 2026-06-11

---

## 一、项目概览

### 定位

替代已废弃的 `annual_report`（NLP规则匹配），用 **LLM 直接理解财报原文**，输出结构化分析 + 海豚投研风格叙事报告。

### 一行总结

```
PDF/HTML 财报 → 文本提取 → LLM双Pass（KPI提取+叙事生成）→ financial-sdk校验 → 飞书文档
```

---

## 二、项目结构

```
/root/code/deep-report/
├── DESIGN.md              # 架构设计文档
├── README.md              # 快速入门
├── PLAN.md                # 开发计划
├── pyproject.toml         # Python 包配置
├── DAILY_LOG.md           # 开发日志
│
├── src/deep_report/
│   ├── __init__.py        # 包声明
│   ├── __main__.py        # python -m 入口 → 委托 cli.py
│   ├── cli.py             # 命令行处理（analyze子命令）
│   ├── fetcher.py         # 报告下载层
│   ├── analyzer.py        # 核心分析层（726行）
│   ├── writer.py          # 输出层 → 飞书文档
│   └── prompts/
│       ├── extract.md     # KPI提取 Prompt（87行）
│       └── analyze.md     # 叙事生成 Prompt（152行，v3.6）
│
├── downloads/             # 下载的原始报告（缓存）
├── data/                  # 运行时数据（SQLite缓存/审计）
└── tests/                 # 测试
```

### 代码量

| 文件 | 行数 | 职责 |
|------|:--:|------|
| `analyzer.py` | 726 | 核心：文本提取、LLM双Pass、校验、标准化 |
| `fetcher.py` | 203 | 调度 unified-downloader 下载报告 |
| `writer.py` | 150 | 生成 Markdown + 飞书文档 ready marker |
| `cli.py` | 44 | CLI 命令入口 |
| `extract.md` | 87 | KPI提取 Prompt |
| `analyze.md` | 152 | 叙事生成 Prompt（v3.6: 5段递进式） |
| **总计** | **1395** | |

---

## 三、核心架构（三层流水线）

```
┌─────────────────────────────────────────────────────────────┐
│                       deep-report                           │
│                                                             │
│  ┌──────────┐      ┌──────────────────┐      ┌───────────┐ │
│  │ Fetcher  │ ──→  │    Analyzer      │ ──→  │  Writer   │ │
│  │ 下载报告  │      │  ┌────────────┐  │      │ 飞书文档   │ │
│  └──────────┘      │  │ Extract Pass│  │      └───────────┘ │
│                    │  │ (每份→KPI)  │  │                     │
│                    │  ├────────────┤  │                     │
│                    │  │Analyze Pass │  │                     │
│                    │  │ (多期→报告) │  │                     │
│                    │  └────────────┘  │                     │
│                    │      ↓           │                     │
│                    │ financial-sdk    │                     │
│                    │ 交叉校验         │                     │
│                    └──────────────────┘                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 四、逐模块详解

### 4.1 Fetcher — 报告下载层（fetcher.py, 203行）

**职责**：确保当前+历史报告 PDF/HTML 本地可用

**输入**：股票代码 + 报告周期（如 `600519.SH`, `2025FY`）
**输出**：`[{period, file_path, type, market}, ...]`

**市场自动检测**：
- `^\d{6}\.(SZ|SH)$` → A股
- `^\d{4,5}\.HK$` → 港股
- 其他 → 美股

**下载策略**：
- 先检查本地缓存（`/root/code/unified-downloader/downloads/`）
- 未命中则调用 `unified-downloader` CLI 下载
- 美股保留 HTML 原文（表格结构比 PDF 好）
- 历史回溯：默认 +4 期历史（可配 `--history N`）

**报告类型映射**：
| 市场 | Q1 | Q2/Q3 | Q4/FY |
|------|----|----|----|
| A股 | q1_report | interim/q3_report | annual_report |
| 港股 | quarterly | interim/quarterly | annual_report |
| 美股 | 10-Q | 10-Q | 10-K (FPI→20F/6K自动) |

---

### 4.2 Analyzer — 核心分析层（analyzer.py, 726行）

**处理流程**（5步）：

```
Step 1: 文本提取
  ├── PDF → pdfplumber（文本 + 表格→Markdown）
  └── HTML → BeautifulSoup（XBRL降噪 + 表格→Markdown）

Step 2: LLM Pass 1 — KPI提取
  每份报告 → extract.md prompt → 8-15个结构化KPI
  - 14锚点双语采样器（中英双语，每锚3匹配+去重）
  - XBRL降噪：html ix:标签89%噪音 → 2%

Step 3: 字段标准化
  KPI field别名归一化：营收/营业收入/Revenue → revenue

Step 4: financial-sdk 交叉校验
  营收/净利润/现金流/总资产对照
  - 偏差>50%或符号反转 → REJECT（不生成报告）
  - 偏差20-50% → WARN（标注警告）
  - 偏差<20% → OK

Step 5: LLM Pass 2 — 叙事生成
  多期KPI合并 → analyze.md prompt → 海豚投研风格分析报告
```

#### 4.2.1 文本提取细节

**PDF提取**（pdfplumber）：
- 逐页提取文本 + 表格
- 表格自动转 Markdown 格式
- A股/港股财报以 PDF 为主

**HTML提取**（BeautifulSoup + XBRL降噪）：
- 移除 script/style/meta/link 标签
- **XBRL降噪**（v3.4 核心修复）：
  - ix: 标签 → unwrap（保留文本，去掉标签壳）
  - link:/xbrli: 标签 → decompose（完全删除元数据）
  - display:none 元素 → decompose
  - 噪音从 89% 降至 2%
- 表格提取为 Markdown 后从 body 中移除（避免重复）

#### 4.2.2 14锚点双语采样器（v3.4 核心创新）

不是简单的 head/tail 截断，而是按语义定位关键段落：

```
中文锚点（沪深/港股）：
  利润表(2000字) → 资产负债表(2000字) → 现金流量表(2000字)
  → 营业收入(1500字) → 毛利率(1500字)

英文锚点（美股SEC）：
  income_statement → balance_sheet → cash_flow
  → total_revenue → gross_profit → MDA(3000字)
  → operating_income → net_income

每锚点最多3匹配 → 重叠>50%去重 → 80%水位线停止 → 零匹配fallback
```

#### 4.2.3 LLM Client

- **Primary**：DeepSeek-chat（大prompt更稳定）
- **Fallback**：doubao-seed-2.0-pro（thinking: disabled）
- 复用 morning-brief 的 `call_with_fallback` 机制
- 每次调用：temperature=0.3, max_tokens=8000, timeout=120s

#### 4.2.4 校验层

| 阈值 | 动作 | 含义 |
|:--:|------|------|
| <20% | OK | 数据可信 |
| 20-50% | WARN（标注） | 偏差较大，标注警告 |
| >50% 或 符号反转 | REJECT | 数据不可信，不生成报告 |

---

### 4.3 Writer — 输出层（writer.py, 150行）

**输出方式**：
1. 生成 Markdown 文件 → `/tmp/deep_report/{title}.md`
2. 写入 `.ready` marker → 飞书上传由外部 agent 处理

**输出结构**：
- 标题：`{公司名} {周期} 财报深度分析`
- 内容：narrative（由 LLM 生成的完整 Markdown）
- 附录：数据校验结果（如有警告）

**诊断模式**：当校验 REJECT 时，不生成叙事报告，改为输出详细的对比表 + 诊断建议。

---

## 五、Prompt 体系

### 5.1 extract.md — KPI提取 Prompt（87行）

```
结构：格式约束 → 字段映射规则 → 行业KPI速查 → 归因要求 → 输出规范

核心要求：
- 输出纯JSON，8-15个KPI
- value为纯数字字符串，单位在unit字段
- 每个KPI必须带reasoning（一句话归因）
- 16行中英文字段对照表（Revenue→revenue, Cost of revenue→cost_of_revenue, ...）
```

### 5.2 analyze.md — 叙事生成 Prompt（v3.6, 152行）

```
结构：
  核心写作原则(6条) → 行业术语速查(6行业) → 5段递进式框架
  → 写作风格参考(5段示例) → 输出要求

核心改进（v3.5 → v3.6）：
  ✅ 新增6行业术语速查表（50+专业指标+使用示例）
  ✅ 新版5段递进式框架（替代8-Panel并列式）
  ✅ 扩展few-shot示例（5段海豚投研原版风格参考）
  ✅ 双段金句标题要求
  ✅ 多因子对冲分析指导（St/Cy标注）
  ✅ 叙事逻辑评估
  ✅ 估值锚点+投资结论
```

详细 Prompt 版本演进见第七节。

---

## 六、使用指南

### 6.1 基础用法

```bash
# 安装
cd /root/code/deep-report
pip install -e . --break-system-packages

# 分析单只股票（自动下载+分析）
python -m deep_report analyze 600519.SH --period 2025FY

# 指定历史期数
python -m deep_report analyze MNSO --period 2026Q1 --history 4

# 仅下载不分析
python -m deep_report analyze 600519.SH --period 2025FY --dry-run

# 跳过校验（风险自负）
python -m deep_report analyze NVDA.US --period 2026Q1 --no-verify
```

### 6.2 编程调用

```python
from deep_report.analyzer import ReportAnalyzer

analyzer = ReportAnalyzer(verify=True)

reports = [
    {"period": "2025FY", "file_path": "/path/to/600519_2025_ANNUAL_REPORT.PDF",
     "market": "CN", "format": "pdf"},
]

result = analyzer.analyze(code="600519.SH", period="2025FY", reports=reports)

# result = {
#   "kpis": [{field, name, value, unit, yoy, reasoning}, ...],
#   "trends": [{field, values: [{period, value}, ...]}, ...],
#   "narrative": "Markdown 分析报告",
#   "validation": {status, checks, summary},
#   "_rejected": False
# }
```

### 6.3 股票代码格式

| 市场 | 格式 | 示例 |
|------|------|------|
| A股 | 6位代码.SH/.SZ | 600519.SH, 000858.SZ |
| 港股 | 4-5位代码.HK | 00700.HK, 09698.HK |
| 美股 | 字母代码 | NVDA, MNSO, PDD |

---

## 七、Prompt 版本演进

### 从 v3.0 到 v3.6

| 版本 | 框架 | 关键改进 | E2E验证 |
|:--:|------|------|:--:|
| v3.0 | 8模块并列式（零售模板） | 初版，按海豚投研名创优品风格硬编码 | ⚠️ 非零售公司不适配 |
| v3.1 | 8模块 + 3维度蒸馏 | 6元方法交叉验证，extract字段对齐 | — |
| v3.2 | 提取稳定性修复 | pipeline兼容 + 判断标注 | — |
| v3.3 | NVDA对比测试 | 发现XBRL噪音问题 | ⚠️ 英伟达数据偏差 |
| v3.4 | 8模块 + 三市场修复 | XBRL降噪(89%→2%) + 14锚点采样 | ✅ 三市场全通 |
| v3.5 | 5段递进式（替代8模块） | 合并重叠模块 + 多因子对冲 + 估值+结论 | ✅ 飞书文档+IMA |
| **v3.6** | **5段递进式 + 术语注入** | **6行业术语速查 + 5段few-shot + E2E验证** | ✅ 10项改善全过 |

### 框架对比

| | v3.4 8模块 | v3.6 5段递进式 |
|------|------|------|
| 结构 | 并列数据陈列 | 递进叙事 |
| 标题 | "核心观点" | 金句双段标题 |
| 预期差 | 营收分析中一笔带过 | 独立章节+完整对比表 |
| 多因子对冲 | 无 | 量化St/Cy分解 |
| 竞争格局 | 无 | 指名竞品+份额变化 |
| 叙事逻辑 | 无 | "提价叙事→放量叙事" |
| 估值锚点 | 无 | PE分位+同业对比 |
| 投资建议 | 无 | 明确建议+观察节点 |

---

## 八、与其他项目关系

```
  morning-brief（季报事件触发）
         │
         ├──→ unified-downloader（下载PDF/HTML）
         │         │
         │         └──→ 本地缓存 /root/code/unified-downloader/downloads/
         │
         ├──→ deep-report（分析引擎，本仓库）
         │         │
         │         ├──→ financial-sdk（交叉校验核心字段）
         │         │         └──→ 五维度分析、FCF、Piotroski、内在价值
         │         │
         │         ├──→ distill-framework（方法论蒸馏 + Prompt生成）
         │         │         └──→ 6元方法 + 6行业KPI
         │         │
         │         └──→ LLM Provider（DeepSeek/doubao）
         │                   └──→ 复用 morning-brief 的 LLM client
         │
         └──→ feishu（输出飞书文档）
                   └──→ IMA（同步笔记+知识库）
```

---

## 九、已知限制与建议

| 限制 | 影响 | 建议 |
|------|------|------|
| 美股 financial-sdk 校验不可用 | NVDA等股票无法交叉校验 | P2：补全美股SDK数据源 |
| 单位归一化仅支持元→亿 | 部分A股报告用"万元"可能误判 | 扩展 _UNIT_MULTIPLIER |
| 仅支持PDF/HTML原文 | 扫描件/图片无法提取 | 接入OCR或要求用户提供文本 |
| 单次LLM调用超时120s | 超长报告可能截断 | 增大timeout或进一步分段 |
| LLM温度固定0.3 | 叙事风格一致性 vs 多样性 | 可配置temperature参数 |
| 无估值实时数据 | 估值段依赖LLM内置知识 | P2：接入估值温度计 |
| 无产业链实时数据 | 交叉验证依赖LLM推理 | P2：接入CoWoS/Capex/社零API |

---

## 十、技术亮点

1. **XBRL降噪算法**：SEC HTML 89% 噪音 → 2%，独有创新
2. **14锚点双语采样器**：关键字段100%覆盖，按语义定位而非盲截
3. **双LLM Pass**：提取与分析分离，中间KPI层可人工审核
4. **financial-sdk校验**：闭环数据质量控制，偏差>50%拒绝生成
5. **市场自适应**：A股/港股/美股自动检测 + FPI（外国私人发行人）自动适配
6. **5段递进式叙事**：基于52篇海豚投研文章的6元方法蒸馏

---

📝 v3.6 | deep-report PR #6 | distill-framework → winterswang/distill-framework
