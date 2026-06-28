# WeHireMonitor · 微岗哨

> 把"每天刷几十个公众号"变成"每天接收一份精准招聘日报"

[![GitHub](https://img.shields.io/badge/GitHub-jerry9692%2Fwehire--monitor-181717?logo=github)](https://github.com/jerry9692/wehire-monitor)
[![Gitee](https://img.shields.io/badge/Gitee-hongm_j%2Fwehire--monitor-C71D23?logo=gitee)](https://gitee.com/hongm_j/wehire-monitor)
[![License](https://img.shields.io/badge/license-MIT-blue)](#license)

面向个人低频使用的**微信公众号招聘情报监控管道**。聚焦金融、国企、央企、垂直社招机会,自动发现目标公众号新文章,经关键词预过滤,推送每日招聘日报到飞书/钉钉。

非商业爬虫平台,不追求高并发/账号池/代理池,不自动登录,不绕强风控。一个低频、轻量、可人工维护的本地自动化管道。

> **v0.2** — 当前版本实现:抓取→解析→预过滤→OCR+LLM 结构化提取→规则匹配→Markdown 表格日报推送。长图切片+Vision兜底(v0.3)、看板周报(v1.0)正在规划中。

---

## 核心价值(v0.2)

- **自动抓取**:定时(每日 08:30/20:30)抓取配置的公众号最新文章,限频防封
- **智能过滤**:关键词评分门控,自动过滤实习/校招/非招聘内容
- **结构化提取**:OCR(RapidOCR)+ 文本 LLM(DeepSeek)双路提取公司/岗位/地点/投递方式/截止日期
- **规则匹配**:加权评分(公司/岗位关键词/地点),置信度门控,低置信度进复核区
- **双重去重**:URL hash + 正文 hash,避免重复入库和重复推送
- **结构化日报**:Markdown 表格日报 + "需人工复核"区 + 邮箱脱敏
- **状态可控**:状态机驱动,单篇失败不影响整批,支持断点续跑
- **安全友好**:Cookie/Token 本地存储,配置校验,资源自动清理

## 功能特性

### v0.1 已实现 ✅

- 公众号订阅列表维护(YAML 配置,支持别名和优先级)
- 微信公众平台后台 Cookie 手动配置 + 过期检测(>24h 告警)
- `check-cookie` 命令:API 级验证 Cookie 有效性
- 低频定时抓取(每日 08:30 / 20:30),搜索/文章间隔限频
- 文章 HTML 解析、正文抽取、图片信息提取(BS4 解析)
- 关键词预过滤评分门控(强命中词/强排除词/投递词/邮箱检测)
- URL hash + 正文 hash 双重去重
- 全局/单账号文章数限制
- SQLite 存储,原子状态迁移,避免竞态
- 飞书/钉钉 Webhook 日报推送(列表格式,兼容双平台)
- 每模块 CLI 可单独运行(`run`/`fetch`/`parse`/`notify`/`check-cookie`/`schedule`)
- `--dry-run` 模式:只读不写不推,验证配置
- 日志文件轮转(30 天保留)+ 控制台彩色输出
- 上下文管理器确保所有 HTTP/DB 资源正确关闭

### v0.2 已实现 ✅

- OCR(RapidOCR) 提取图片中文字
- 文本 LLM(DeepSeek) 结构化提取(公司/岗位/地点/投递方式/截止日期)
- Markdown 表格日报 + "需人工复核"区
- 邮箱正则校验 + email_chars 一致性校验
- 置信度门控(<60 进复核区)
- 用户规则匹配(locations/job_keywords/companies 加权评分)
- Provider 抽象层(OCR/LLM 接口+工厂,支持 .env 切换)
- Prompt 模板外置
- `extract` / `match` CLI 命令独立运行
- run_logs 记录 llm_count/ocr_count

### v0.3+ 规划中

- 长图切片 + Vision API 兜底
- 预算控制(每日 VLM 花费上限)
- 本地 HTML 看板、周报统计
- 错误告警、一键重跑
- Docker 部署

## 技术栈

| 领域 | 选型 |
|---|---|
| 语言 | Python 3.11+ |
| 包管理 | `uv` |
| HTTP | `httpx` |
| HTML 解析 | `beautifulsoup4` + `lxml` |
| 图片处理 | `Pillow`(预留 `imagehash`) |
| OCR | `rapidocr-onnxruntime`(v0.2) |
| 数据校验 | `pydantic` v2 |
| 存储 | `sqlite3`(标准库) |
| 调度 | `apscheduler` |
| 配置 | `pyyaml` + `python-dotenv` |
| CLI | `typer` |
| 日志 | `loguru` |
| 测试 | `pytest` |

## 架构

分层单体 + 状态机驱动,可断点续跑。

```
配置层 → 调度器 → fetcher → parser → prefilter → [extractor → matcher v0.2+] → storage → notifier
```

v0.2 状态机:`discovered → fetched → parsed → candidate → extracted → validated → matched → notified → archived`
错误分支:`error_fetch` / `error_parse` / `error_ocr` / `error_llm` / `need_cookie` / `need_captcha` / `need_review`。

详细设计见 [`docs/WeHireMonitor-软件需求规格.md`](docs/WeHireMonitor-软件需求规格.md)。

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置
cp config/.env.example config/.env              # 填入 Cookie/Token/Webhook
cp config/accounts.yaml.example config/accounts.yaml  # 配置要监控的公众号
cp config/rules.yaml.example config/rules.yaml  # 调整规则(可选)
cp config/keywords.yaml.example config/keywords.yaml  # 自定义关键词(可选)

# 3. 检查 Cookie 是否有效
wehire-monitor check-cookie

# 4. 单次运行(抓取→解析→过滤→入库→推送)
wehire-monitor run

# 5. dry-run(验证配置,不写库不推送)
wehire-monitor run --dry-run

# 6. 定时调度(每日 08:30/20:30)
wehire-monitor schedule
```

### CLI 命令

| 命令 | 说明 |
|---|---|
| `wehire-monitor run` | 完整管道:抓取→解析→过滤→入库→推送 |
| `wehire-monitor run --dry-run` | 干跑模式:验证配置,不写库不推送 |
| `wehire-monitor fetch` | 仅抓取+解析+预过滤(不推送) |
| `wehire-monitor parse` | 仅解析已入库的待处理文章 |
| `wehire-monitor prefilter` | 仅预过滤(重新解析+评分) |
| `wehire-monitor extract` | 仅提取(CANDIDATE 文章 → LLM 结构化岗位) |
| `wehire-monitor match` | 仅匹配(已提取岗位 → 规则评分) |
| `wehire-monitor notify` | 仅推送当前候选文章 |
| `wehire-monitor check-cookie` | API 级验证 Cookie 有效性 |
| `wehire-monitor schedule` | 启动定时调度(阻塞运行) |

### 获取 Cookie

1. 浏览器登录 <https://mp.weixin.qq.com/>
2. F12 打开开发者工具 → Network 面板
3. 刷新页面,找到任意请求,复制 Request Headers 中的 `Cookie` 值
4. 同样在 URL 参数中找到 `token` 值
5. 填入 `config/.env` 的 `WECHAT_MP_COOKIE` 和 `WECHAT_MP_TOKEN`
6. 设置 `COOKIE_UPDATED_AT` 为当前时间(用于过期检测)

## 目录结构

```
wehire-monitor/
  config/                  # 配置文件
    accounts.yaml          # 公众号订阅列表
    rules.yaml             # 匹配规则/通知/调度/预算
    keywords.yaml          # 预过滤词库
    .env                   # 密钥(Cookie/Token/Webhook,不入库)
  data/                    # 运行时数据
    job_intel.sqlite       # SQLite 数据库
  src/wehire_monitor/
    pipeline/              # 编排器:状态机推进、run_id、dry-run、调度器
    modules/
      fetcher/             # 微信公众号抓取(搜索/列表/限流/异常检测)
      parser/              # HTML 解析(BS4)、正文/图片提取
      prefilter/           # 关键词评分门控
      extractor/           # v0.2 OCR+LLM 双路提取、质量评分、后处理
      matcher/             # v0.2 用户规则加权匹配
      notifier/            # 飞书/钉钉 Webhook 推送(结构化 Markdown 表格)
      storage/             # SQLite 仓储层(原子状态迁移、jobs 表)
    providers/             # v0.2 Provider 抽象层(OCR/LLM 接口+工厂)
    domain/                # 领域模型(ArticleMeta/ParsedArticle/Job/Status 等)
    config/                # 配置加载与校验(pydantic schema)
    infra/                 # 基础设施(限频器)
    main.py                # Typer CLI 入口
  docs/                    # 需求文档
  tests/                   # 单元+集成测试(114 tests)
  logs/                    # 日志文件(自动轮转,30天保留)
  pyproject.toml
```

## 开发路线图

| 阶段 | 目标 | 状态 |
|---|---|---|
| v0.1 | 能跑通:抓取→过滤→入库→推送标题链接 | ✅ 已完成 |
| v0.2 | 能提取:OCR + 文本 LLM 结构化岗位表 + Markdown 表格日报 | ✅ 已完成 |
| v0.3 | 能处理长图:切片 + VLM 兜底 + 预算上限 | 规划中 |
| v1.0 | 稳定日用:看板 + 周报 + 告警 + 重跑 + 可选容器化 | 规划中 |

## 风控与合规

- 仅用于个人信息整理,不公开再分发
- 尊重目标网站访问频率(搜索间隔20-60s,文章间隔5-20s),遇风控立即降频/暂停
- 不绕强验证码,不做账号池/代理池
- Cookie/Token/API Key 入 `.env`,不入日志
- 通知内容默认只发标题+摘要和原文链接

## 文档

- [软件需求规格说明书](docs/WeHireMonitor-软件需求规格.md)
- [v0.1 MVP 计划](docs/plans/2026-06-28-v0.1-mvp.md)
- [v0.2 MVP 计划](docs/plans/2026-06-28-v0.2-mvp.md)
- [原始 PRD](docs/个人%20AI%20招聘情报监控平台%20PRD.md)

## 参考项目

- [`wnma3mz/wechat_articles_spider`](https://github.com/wnma3mz/wechat_articles_spider) — URL 获取、token/cookie 维护、限频思路
- [`bzd6661/wechat-article-for-ai`](https://github.com/bzd6661/wechat-article-for-ai) — URL→Markdown、图片本地化(v0.2 适配)
- [`RapidAI/RapidOCR`](https://github.com/RapidAI/RapidOCR) — 默认 OCR(v0.2 集成)

## License

MIT
