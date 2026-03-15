# 适合中国个人投资者盘前快速浏览的晨报系统

该项目每天北京时间 **07:30** 自动运行，抓取五大板块全球公开情报并推送到飞书机器人，输出为：

1. 今日最高优先级（5-10条）
2. 五大板块摘要
3. 重点公司异动池
4. 原文链接池

同时包含：
- A/B/C 公司分级跟踪
- 去重与降噪（优先高质量信源、过滤纯股价新闻）
- 主线雷达（偏强/中性/偏弱）
- 未来7天重点事件预告
- 失败容错（单板块失败不影响全局，全部失败会主动告警）

## 文件结构
- `scripts/daily_brief.py`：抓取、清洗、评分、汇总、飞书推送主程序
- `config/watchlist.json`：板块/公司分级/标签规则/A股映射/事件预告配置
- `.github/workflows/feishu-daily-intel.yml`：每日 07:30（北京时间）定时任务

## 飞书推送设计（手机友好）
- 飞书消息只发“精华摘要 + 主线雷达 + 全量链接”
- 全量内容以 Markdown 文件落地到 `output/`，并尝试自动发布到 `https://paste.rs`（无需大模型 key）
- 若外网受限，仍会提供本地 Markdown 路径，流程不中断

## 使用方式

### 1) 配置飞书机器人 webhook
在仓库 `Secrets` 中新增：
- `FEISHU_WEBHOOK`：你的飞书机器人地址

### 2) 启用定时任务
工作流已配置：
- cron: `30 23 * * *`（UTC）= 北京时间每日 07:30

### 3) 本地调试
```bash
export FEISHU_WEBHOOK='https://open.feishu.cn/open-apis/bot/v2/hook/xxx'
python scripts/daily_brief.py
```

若仅测试生成内容，不想发飞书，可不设置 `FEISHU_WEBHOOK`，程序会打印精华并退出。

## 可配置项
在 `config/watchlist.json` 中重点调：
- `max_top_items`, `max_sector_items`, `max_company_moves`
- `high_quality_sources`
- `price_noise_keywords`
- `event_tag_rules`
- `sectors[].companies[].tier`（A/B/C）
- `sectors[].companies[].a_share_mapping`
- `upcoming_events`
