
---
## 2026-05-28

## FEATURE
- 设计并实现每日对话分析管线（`analyze_daily_conversations.py`）：从 session JSONL 提取昨日对话 → 按项目分组 → LLM 五分类（FEATURE/BUGFIX/DISCUSS/DECISION/TODO）→ 追加写入各项目 `DAILY_LOG.md` → 飞书卡片推送
- 确认每日对话分析采用动态 Top10 项目发现机制（大小过滤去心跳噪声 + 正则匹配路径/关键词）
- 确认分块机制必须保留（多 session 合并轻松超 70K token）
- 调整 `analyze_daily_conversations.py` 配置：`CHUNK_TOKEN_LIMIT = 70000`、`LLM_MAX_TOKENS = 30000`，适配 DeepSeek v4 1M 上下文

## BUGFIX
- 根因定位 5/28 cron 失败的真正原因链：bash 脚本在 `[2/4]` 爬取完成后中断 → cron agent 手动逐步骤执行 Python 脚本时绕过了 `.env` 加载 → `MINIMAX_API_KEY` 为空导致 LLM 调用失败
- Playwright ERROR：`Page.goto: Target page, context or browser has been closed`，发生在 04:06:01-04:06:08，可能是触发脚本中断的导火索
- Review 发现卡片跨项目重复问题（db.py 问题同时出现在 xueqiu-crawler / xueqiu-monitor / xueqiu-analyzer-skill 三个卡片）

## DISCUSS
- 用户询问 Playwright ERROR 的具体日志详情（待补充）
- 讨论原设计适配方案：确认数据源改用 LanceDB memory、取消分块（因日对话量小）、固定项目列表、飞书推送

## DECISION
- 根本修复方案：不依赖 shell 环境变量传递，改为 Python 代码内直接加载 `.env`（无论谁、从哪里调用 Python 脚本都能自给自足）
- 飞书卡片推送方式：使用 `feishu_im_user_message` + `interactive` 卡片

## TODO
- 实现 Python 代码内直接加载 `.env`（根本修复 MINIMAX_API_KEY 丢失问题）
- 跨项目去重：相同 BUGFIX 内容只保留到最匹配的项目下
- 优化 system prompt：强制中文、禁止把 cron 执行日志当 DISCUSS、无活动时给原因
- 放宽卡片截断：从 80→120 字符或不截断
- 输出规范模板：参考 `financial-sdk/report_template.md` 模式给 LLM 强制格式

---
## 2026-05-30

## FEATURE
- (不适用：当日对话全部为 cron 管线自动化执行，无功能开发)

## DISCUSS
- (不适用：当日对话全部为 cron 管线自动化执行，无方案讨论)

## DECISION
- (不适用：当日对话全部为 cron 管线自动化执行，无架构决策)

## TODO
- (不适用：当日对话全部为 cron 管线自动化执行，无新增待办)

---
## 2026-05-30

## FEATURE
- (不适用：当日对话为 cron 管线执行日志，无新功能开发)

## BUGFIX
- 修复 `generate_report.py` 语法错误（重复 `help` 参数）

## DISCUSS
- (不适用：当日对话为 cron 管线执行日志，无技术讨论)

## DECISION
- (不适用：当日对话为 cron 管线执行日志，无架构决策)

## TODO
- 手动登录雪球清除验证态，恢复下次完整爬取（当前爬虫在用户 czy710 处触发验证码，导致剩余 11 位用户被跳过）

---
## 2026-05-30

## FEATURE
- (none)

## BUGFIX
- 修复 `generate_report.py` 语法错误（重复 `help` 参数）

## DISCUSS
- (不适用：全部为 cron 执行结果报告，非开发讨论)

## DECISION
- (none)

## TODO
- [?] 手动登录雪球清除验证态（cron 建议用户操作，下次可恢复完整爬取）

---
## 2026-05-30

## FEATURE
- (none)

## BUGFIX
- 修复 `generate_report.py` 语法错误：移除重复的 `help` 参数

## DISCUSS
- (none)

## DECISION
- (none)

## TODO
- 建议手动登录雪球清除验证态，以恢复完整爬取（剩余 11 位用户待执行）

---
## 2026-05-30

## FEATURE
- (none)

## BUGFIX
- 修复 `generate_report.py` 语法错误（重复 `help` 参数）

## DISCUSS
- (不适用：当日对话为 cron 管线执行结果报告，无开发讨论)

## DECISION
- (none)

## TODO
- 手动登录雪球账户清除验证码状态，恢复爬虫正常抓取能力
- 补爬剩余 11 位用户的数据

---
## 2026-05-31

## FEATURE
- (不适用：当日对话为 cron 管线自动化执行，无功能开发)

## BUGFIX
- (不适用：当日对话为 cron 管线自动化执行，无功能开发)

## DISCUSS
- (不适用：当日对话为 cron 管线自动化执行，无功能开发)

## DECISION
- (不适用：当日对话为 cron 管线自动化执行，无功能开发)

## TODO
- (不适用：当日对话为 cron 管线自动化执行，无功能开发)

---
## 2026-06-01

## FEATURE

- (不适用：当日对话全部为 cron 管线自动化执行，无功能开发)

## BUGFIX

- (不适用：当日对话全部为 cron 管线自动化执行，无功能开发)

## DISCUSS

- (不适用：当日对话全部为 cron 管线自动化执行，无功能开发)

## DECISION

- (不适用：当日对话全部为 cron 管线自动化执行，无功能开发)

## TODO

- (不适用：当日对话全部为 cron 管线自动化执行，无功能开发)

---
## 2026-06-02

## FEATURE
- (不适用：当日对话全部为 cron 管线自动化执行，无功能开发)

## BUGFIX
- (不适用：当日对话全部为 cron 管线自动化执行，无 bug 修复)

## DISCUSS
- (不适用：当日对话全部为 cron 管线自动化执行，无方案讨论)

## DECISION
- (不适用：当日对话全部为 cron 管线自动化执行，无技术决策)

## TODO
- (不适用：当日对话全部为 cron 管线自动化执行，无待办任务)

---
## 2026-06-03

## FEATURE

- (不适用：当日对话全部为 cron 管线自动化执行，无功能开发)

## BUGFIX

- (不适用：当日对话全部为 cron 管线自动化执行，无功能开发)

## DISCUSS

- (不适用：当日对话全部为 cron 管线自动化执行，无功能开发)

## DECISION

- (不适用：当日对话全部为 cron 管线自动化执行，无功能开发)

## TODO

- (不适用：当日对话全部为 cron 管线自动化执行，无功能开发)

---
## 2026-06-04

## FEATURE
- (不适用：当日对话为 cron 管线自动化执行，无功能开发对话)

## BUGFIX
- (不适用：当日对话为 cron 管线自动化执行，无 bug 修复讨论)

## DISCUSS
- (不适用：当日对话为 cron 管线进度报告，非方案讨论或技术选型对话)

## DECISION
- (不适用：当日对话为 cron 管线进度报告，无架构或技术决策)

## TODO
- (不适用：当日对话为 cron 管线进度报告，无待办任务记录)

---
## 2026-06-05

## FEATURE
- (none)

## BUGFIX
- (none)

## DISCUSS
- (none)

## DECISION
- (none)

## TODO
- **financial-sdk**: `fix/quiet-logging-and-code-quality` 分支无上游跟踪，需推送到远程
- **xueqiu-monitor**: PR #4 (cross-platform default paths) 待处理或合并

---
## 2026-06-05

## FEATURE
- (不适用：当日对话为 cron 管线自动化执行及 git 管理操作，无功能开发)

## BUGFIX
- (不适用：当日对话为 cron 管线自动化执行及 git 管理操作，无 bug 修复)

## DISCUSS
- (不适用：当日对话为 cron 管线自动化执行及 git 管理操作，无方案讨论)

## DECISION
- (不适用：当日对话为 cron 管线自动化执行及 git 管理操作，无架构决策)

## TODO
- 待处理 PR #4：cross-platform paths（跨平台路径兼容性）
- 待决定：是否继续处理 PR #4，还是先处理 financial-sdk 的分支

---
## 2026-06-06

## FEATURE
- (不适用：当日对话为 cron 管线自动化状态报告，无功能开发对话)

## BUGFIX
- (不适用：当日对话为 cron 管线自动化状态报告，无 bug 修复讨论)

## DISCUSS
- (不适用：当日对话为 cron 管线自动化状态报告，无方案讨论)

## DECISION
- (不适用：当日对话为 cron 管线自动化状态报告，无决策记录)

## TODO
- (不适用：当日对话为 cron 管线自动化状态报告，无待办任务讨论)

---
## 2026-06-07

## FEATURE
- (none)

## BUGFIX
- `morning_brief` cron 超时问题已修复：timeout 从 900s（15分钟）提升至 1200s（20分钟）。根因诊断显示 pipeline 本身仅耗时 171s，真正卡住的是后续 feishu API 调用（创建文档/发送卡片）
- `kline_refresh` cron 超时问题已修复：timeout 从 600s（10分钟）提升至 900s（15分钟），应对全量 K线刷新量大

## DISCUSS
- 根因分析：morning_brief pipeline 自身无问题（28信号+3公告+60只估值温度共171s完成），超时来自 cron 任务整体（包含飞书文档创建、卡片推送、LLM agent 汇报等多步骤）
- Gateway 重启导致 `earnings_weekly_scan` 和 `earnings_poll` 两个 cron 任务中断，原因待确认（计划内还是异常）
- `xueqiu-crawler` 偶发 JSON 解析异常（1/28），已有 fallback 输出 `[解析异常]`，低优先级待排查

## DECISION
- 根据实测数据（pipeline 171s vs cron timeout 900s 仍超时），判断超时瓶颈在外部 API 调用而非 pipeline 本身，据此决定 timeout 扩幅
- 持续使用 MiniMax M2.7 作为 LLM 引擎（已替代 ARK，避免 429 限流问题）

## TODO
- 下周一（6/8）早报验证 timeout 修复是否生效
- 确认 Gateway 重启是否为计划内操作，影响了下游 cron 任务
- 排查 xueqiu-crawler 的 1/28 JSON 解析异常（可能是个别文章格式触发 JSON 边界问题，低优先级）

---
## 2026-06-08

## FEATURE
- 完整 V3 管道跑通：爬取 → 质检 → LLM评估 → 深度分析报告 → IMA发布（Okta $118.72 高估分析，飞书+IMA 双渠道）
- 端到端验证通过（114 单元测试 + opencli 不可用时 Playwright 自动回退 + 贵州茅台 SH600519 股票识别 + 讨论爬取 + 报告生成发布）
- 日报自动化扫描（11,141 会话文件，提取 4 个活跃项目 LLM 分析并推送飞书卡片）

## BUGFIX
- `python-dotenv` 未安装导致 `.env` 从未被加载（`DEEPSEEK_API_KEY` 形同虚设）
- `config.yaml` 的 `api_key` 指向 `${DEEPSEEK_API_KEY}` 但 env 实际配的是 `${ARK_API_KEY}`，key 名不匹配
- `config.yaml` 的 `base_url` 仍是 `api.deepseek.com`，与 Ark key 不兼容
- API key `sk-61d...` 本身已失效（401 invalid），换为新 key 后恢复正常
- 标题前缀重复问题：提取 `prepend_report_title()` 公共函数，`cli.py` + `orchestrator` 共用
- `import os` 定义在函数体内 → 提到 `ima_publisher.py` 模块顶部
- `_FALLBACK_KEYS` 定义在函数内部 → 提到 `config.py` 模块级常量
- orchestrator 懒导入 `ima_publisher` → 提到模块顶部
- 新增 `test_ima_publisher.py`，14 个测试用例

## TODO
- 雪球 PUA 字符污染（`\ue62d` `\ue64b` 等 icon font 字符混入 60/60 条讨论内容），建议在 `_parse_single_discussion` 或数据清洗层加 `re.sub(r'[\ue000-\uf8ff]+', '', text)` 过滤

---
## 2026-06-08

## FEATURE
- DeepSeek V3 完整管道跑通：爬取 → 质检 → LLM评估 → 深度分析报告 → IMA发布
- PR #15：新增 `test_ima_publisher.py`，14 个测试用例覆盖 IMA 发布逻辑

## BUGFIX
- `config.yaml` 的 `api_key` 环境变量从 `${DEEPSEEK_API_KEY}` 修正（之前指向不存在的 `${ARK_API_KEY}`）
- `config.yaml` 的 `base_url` 从 `ark.cn-beijing.volces.com` 修正为 `api.deepseek.com`（适配新 DeepSeek key）
- `python-dotenv` 未安装导致 `.env` 未加载 → 已安装
- PR #15：提取 `prepend_report_title()` 公共函数，解决 cli.py 和 orchestrator 标题前缀重复问题
- PR #15：`import os` 从函数体内移到 `ima_publisher.py` 模块顶部
- PR #15：`_FALLBACK_KEYS` 从函数内定义移到 `config.py` 模块级常量
- PR #15：orchestrator 懒导入 `ima_publisher` 改为模块顶部导入

## DECISION
- 接入新 DeepSeek API key（sk-61d...3bcd），使用官方 `api.deepseek.com` + `deepseek-chat` 模型

## TODO
- [新] 创建 issue：雪球讨论内容含 PUA icon font 字符（`U+E62D` `U+E64B` 等），建议在 `_parse_single_discussion` 或清洗层加 `re.sub(r'[\ue000-\uf8ff]+', '', text)` 过滤
- [已知] ROE/毛利率=0：`financial-sdk` 未安装，依赖雪球 PE/PB/市值
- [已知] 资讯/公告/文章爬取为空：雪球页面改版，DOM 无 `.timeline__item`

---
## 2026-06-09

## FEATURE
- (不适用：当日对话全部为 cron 管线自动化执行，无功能开发)

## BUGFIX
- 飞书推送卡片失败：card template 缺少 `title/icon`，错误信息 `title and icon cannot be empty at the same time`
  - 卡片 JSON 已保存至 `/root/code/analyze-daily/card_2026-06-08.json`，待修复卡片模板的 title 配置

## DISCUSS
- (none)

## DECISION
- (none)

## TODO
- 检查飞书卡片模板源码，修复 title/icon 字段配置问题，使 Feishu 推送恢复正常

---
## 2026-06-10

## FEATURE

- **deep-report** 新增数据校验层（data validation layer），提升报告质量

## BUGFIX

- **deep-report** Code Review 修复：解决 2 个红色（严重问题）+ 6 个黄色（警告）+ 3 个绿色（轻微）问题

## DISCUSS

- (none)

## DECISION

- (none)

## TODO

- (none)

---
## 2026-06-10

## FEATURE

- deep-report：新增数据校验层（`/validators/` 或类似模块）
- 海豚投研：52 篇研报 → 提炼分析框架（知识资产沉淀）
- Skills 全量 Review：完成 7 个 skill 的 Review

## BUGFIX

- deep-report Code Review 修复：2 个🔴 严重 + 6 个🟡 警告 + 3 个🟢 建议
- Cron 优化：修复执行报错

## DISCUSS

- (不适用：对话内容为 cron 管线自动输出的日报总结，无实际技术讨论)

## DECISION

- (none)

## TODO

- (none)

---
## 2026-06-10

## FEATURE
- deep-report 新增数据校验层（data validation layer）
- 海豚投研蒸馏完成：从 52 篇提炼出分析框架（analysis framework）
- Skills 全量 Review 完成：7 个 skill 完成 review

## BUGFIX
- deep-report Code Review 修复（2🔴 + 6🟡 + 3🟢）
- Cron 管线报错修复

## DISCUSS
- (none)

## DECISION
- (none)

## TODO
- (none)
