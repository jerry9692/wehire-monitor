# WeHireMonitor · 微岗哨

> 把"每天刷几十个公众号"变成"每天接收一份精准招聘日报"

[![GitHub](https://img.shields.io/badge/GitHub-jerry9692%2Fwehire--monitor-181717?logo=github)](https://github.com/jerry9692/wehire-monitor)
[![Gitee](https://img.shields.io/badge/Gitee-hongm_j%2Fwehire--monitor-C71D23?logo=gitee)](https://gitee.com/hongm_j/wehire-monitor)
[![License](https://img.shields.io/badge/license-MIT-blue)](#license)

面向个人低频使用的**微信公众号招聘情报监控管道**。聚焦金融、国企、央企、垂直社招机会,自动发现目标公众号新文章,经关键词预过滤、OCR、文本 LLM、VLM 混合提取,结构化出公司/岗位/地点/投递渠道/截止日期,按用户偏好推送每日精准招聘日报到飞书/钉钉。

非商业爬虫平台,不追求高并发/账号池/代理池,不自动登录,不绕强风控。一个低频、轻量、可人工维护的本地自动化管道。

---

## 核心价值

- **精准**:三层门控(预过滤 + OCR 质量 + 预算上限)把 VLM 调用压到候选文章 ≤20%,非招聘文章不进 LLM
- **省钱**:OCR 优先本地跑(RapidOCR),文本 LLM 用低成本模型,VLM 只处理关键切片
- **可控**:状态机驱动,每阶段可断点续跑、单独重跑,单篇失败不影响整批
- **可插拔**:LLM/VLM/OCR 经抽象接口,供应商经 `.env` 切换,默认 DeepSeek + Qwen-VL + RapidOCR
- **可观测**:每次运行生成 `run_id`,原始 HTML/Markdown/OCR/LLM JSON 留档,`run_logs` 记录统计与花费

## 功能特性

- 公众号订阅列表维护(YAML 配置)
- 微信公众平台后台 Cookie 手动配置 + 失效检测告警
- 低频定时抓取(每日 08:30 / 20:30),限频防封
- 文章 HTML 解析、正文抽取、图片本地化(封装 `wechat-article-for-ai` + BS4 兜底)
- 关键词预过滤评分门控(强命中词/强排除词)
- OCR + 文本 LLM 结构化提取(邮箱逐字符 + 正则校验)
- 长图切片 + Vision API 兜底(岗位行列不错配)
- SQLite 存储、多重去重(URL/正文/图片/岗位 hash)
- 飞书/钉钉 Webhook 日报推送(Markdown 表格 + 复核清单)
- 每模块 CLI 可单独运行,支持 `dry-run`
- 本地 HTML 看板、周报统计、错误告警、一键重跑(v1.0)

## 技术栈

| 领域 | 选型 |
|---|---|
| 语言 | Python 3.11+ |
| 包管理 | `uv` |
| HTTP | `httpx` |
| HTML 解析 | `beautifulsoup4` + `lxml`,兜底 `playwright` |
| 图片处理 | `Pillow` + `imagehash` |
| OCR | `rapidocr-onnxruntime`(默认)/ `paddleocr`(备选) |
| 数据校验 | `pydantic` v2 |
| 存储 | `sqlite3`(标准库) |
| 调度 | `apscheduler` |
| 配置 | `pyyaml` + `python-dotenv` |
| CLI | `typer` |
| 日志 | `loguru` |
| 测试 | `pytest` |
| LLM | DeepSeek(默认,可切换 Qwen/GPT/Claude) |
| VLM | Qwen-VL(默认,可切换 GPT-4o/Claude) |

## 架构

分层单体 + 插件式抽象,状态机驱动,可断点续跑。

```
配置层 → 调度器 → fetcher → parser → prefilter → extractor → matcher → storage → notifier
                                                              ↑
                                              providers(llm/vlm/ocr 抽象层)
```

状态机:`discovered → fetched → parsed → candidate → ocr_done → extracted → validated → matched → notified → archived`,失败分支 `error_*` / `need_cookie` / `need_captcha` / `need_review`。

详细设计见 [`docs/WeHireMonitor-软件需求规格.md`](docs/WeHireMonitor-软件需求规格.md)。

## 快速开始

> 当前处于 v0.1 开发阶段,以下为目标使用方式。

```bash
# 安装
uv sync

# 配置
cp config/.env.example config/.env       # 填入 Cookie/Token/LLM Key/Webhook
cp config/accounts.yaml.example config/accounts.yaml
cp config/rules.yaml.example config/rules.yaml

# 检查 Cookie
wehire-monitor fetch --check-cookie

# 单次运行(抓取→过滤→入库→推送)
wehire-monitor run

# dry-run(只读不推不写)
wehire-monitor run --dry-run

# 单模块运行
wehire-monitor fetch
wehire-monitor parse
wehire-monitor prefilter
wehire-monitor extract
wehire-monitor notify
```

## 目录结构

```
wehire-monitor/
  config/
    accounts.yaml          # 公众号订阅
    rules.yaml             # 匹配规则/通知/调度/预算
    keywords.yaml          # 预过滤词库
    .env                   # 密钥(不入库)
  data/
    job_intel.sqlite
    raw_html/  markdown/  images/  ocr/  llm_outputs/
    report.html
  src/wehire_monitor/
    cli/                   # Typer 命令入口
    pipeline/              # 编排器:状态机推进、run_id、dry-run
    modules/
      fetcher/  parser/  prefilter/  extractor/
      matcher/  notifier/  storage/
    providers/
      llm/  vlm/  ocr/  prompts/  factory.py
    domain/                # 领域模型
    config/                # 配置加载与校验
    infra/                 # HTTP 客户端、限频器、重试
    main.py
  docs/
  tests/
  logs/
  pyproject.toml
  Dockerfile
  docker-compose.yml
```

## 开发路线图

| 阶段 | 目标 | 状态 |
|---|---|---|
| v0.1 | 能跑通:抓取→过滤→入库→推送标题链接 | 开发中 |
| v0.2 | 能提取:OCR + 文本 LLM 结构化岗位表 | 规划 |
| v0.3 | 能处理长图:切片 + VLM 兜底 + 预算上限 | 规划 |
| v1.0 | 稳定日用:看板 + 周报 + 告警 + 重跑 + 可选容器化 | 规划 |

## 验收指标

| 指标 | 目标 |
|---|---|
| 非招聘文章过滤准确率 | ≥85% |
| 招聘文章漏检率 | ≤10% |
| 邮箱提取准确率 | ≥98%(不确定进复核) |
| 公司/岗位/地点整体可用率 | ≥90% |
| 单次任务耗时(20–30 号) | ≤45 分钟 |
| VLM 调用占比(候选文章) | ≤20% |
| 重复推送率 | ≤1% |
| Cookie 失效可感知 | 100% 推送提醒 |

## 风控与合规

- 仅用于个人信息整理,不公开再分发
- 尊重目标网站访问频率,遇风控立即降频/暂停
- 不绕强验证码,不做账号池/代理池
- Cookie/Token/API Key 入 `.env`,不入日志;通知内容默认只发摘要和原文链接
- 日志中邮箱可配置脱敏

## 文档

- [软件需求规格说明书](docs/WeHireMonitor-软件需求规格.md)
- [原始 PRD](docs/个人%20AI%20招聘情报监控平台%20PRD.md)

## 参考项目

- [`wnma3mz/wechat_articles_spider`](https://github.com/wnma3mz/wechat_articles_spider) — URL 获取、token/cookie 维护、限频思路
- [`bzd6661/wechat-article-for-ai`](https://github.com/bzd6661/wechat-article-for-ai) — URL→Markdown、图片本地化、验证码检测
- [`RapidAI/RapidOCR`](https://github.com/RapidAI/RapidOCR) — 默认 OCR
- [`PaddlePaddle/PaddleOCR`](https://github.com/PaddlePaddle/PaddleOCR) — 高精度备选 OCR

## License

MIT
