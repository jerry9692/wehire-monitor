"""Pipeline 编排器

状态机推进:fetcher → parser → prefilter → notifier
- 生成 run_id,写 run_logs
- dry-run 模式:走完全流程但不写库不推送(如果有已入库文章会处理但不推送)
- 单篇失败不影响整批
- 资源安全:支持上下文管理器,所有 HTTP 客户端/DB 连接正确关闭
- 配置校验:启动时检查必要配置项
- 去重:URL hash + content hash 双重去重
- 全局文章数限制:遵守 max_articles_per_run
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Asia/Shanghai")
except ImportError:
    _TZ = None

from wehire_monitor.config.loader import ConfigLoader
from wehire_monitor.config.schemas import AccountConfig, RulesConfig
from wehire_monitor.domain.models import ArticleMeta, ParsedArticle, PrefilterResult
from wehire_monitor.domain.status import Status
from wehire_monitor.modules.fetcher.fetcher import Fetcher
from wehire_monitor.modules.fetcher.exceptions import (
    CookieInvalidError,
    CaptchaRequiredError,
    AccountNotFoundError,
)
from wehire_monitor.modules.notifier.notifier import (
    Notifier,
    DailyReport,
    ReportItem,
    NotifyResult,
)
from wehire_monitor.modules.parser.parser import Parser
from wehire_monitor.modules.prefilter.prefilter import Prefilter
from wehire_monitor.modules.storage.repository import Repository
from wehire_monitor.modules.extractor.postprocess import needs_review

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_str() -> str:
    if _TZ:
        return datetime.now(_TZ).strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


class PipelineRunner:
    """管道编排器(支持上下文管理器确保资源关闭)"""

    def __init__(
        self,
        db_path: str = "data/job_intel.sqlite",
        config_dir: str | None = None,
        accounts_path: str | None = None,
        rules_path: str | None = None,
        data_dir: str = "data",
        dry_run: bool = False,
        stages: set[str] | None = None,
    ):
        """
        Args:
            stages: 要执行的阶段集合,如 {"fetch","parse","notify"}。None 表示全部阶段。
        """
        self.dry_run = dry_run
        self.stages = stages or {"fetch", "parse", "prefilter", "extract", "match", "notify"}
        self.config_loader = ConfigLoader(
            config_dir=config_dir,
            accounts_path=accounts_path,
            rules_path=rules_path,
        )

        # 解析路径为绝对路径(相对于项目根目录)
        self._resolve_paths(db_path, data_dir, config_dir)

        # 确保数据目录存在
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)

        # 初始化仓库
        self.repo = Repository(self.db_path)
        self.repo.init_db()

        # 生成 run_id
        self.run_id = f"run-{uuid.uuid4().hex[:8]}"
        self.started_at = _now_iso()

        # 加载配置
        self.keywords = self.config_loader.load_keywords()
        self.prefilter = Prefilter(self.keywords)
        self.rules = self.config_loader.load_rules()

        # 初始化模块(延迟初始化 fetcher,需要 Cookie)
        self.parser = Parser(
            data_dir=self.data_dir,
            user_agent=self.config_loader.get_user_agent(),
        )

        feishu_hook = self.config_loader.get_feishu_webhook()
        dingtalk_hook = self.config_loader.get_dingtalk_webhook()
        self.notifier = Notifier(
            feishu_webhook=feishu_hook or None,
            dingtalk_webhook=dingtalk_hook or None,
            max_per_run=self.rules.notify.max_per_run,
            push_when_empty=self.rules.notify.push_when_empty,
            email_mask=self.rules.notify.email_mask,
        )

        self.fetcher: Fetcher | None = None
        self.extractor = None
        self.matcher = None

        # 非 dry-run 且含 fetch 阶段时校验 Cookie/Token(抛异常而非 sys.exit,确保资源清理)
        if not self.dry_run and "fetch" in self.stages:
            missing = self.config_loader.validate_required_config()
            if missing:
                self.close()
                raise CookieInvalidError(
                    f"缺少必要配置项: {', '.join(missing)},请在 config/.env 中配置后重试"
                )
        # 含 notify 阶段时校验至少一个 webhook
        if "notify" in self.stages and not self.dry_run:
            if not self.config_loader.get_feishu_webhook() and not self.config_loader.get_dingtalk_webhook():
                self.close()
                raise ValueError(
                    "推送阶段需要配置 FEISHU_WEBHOOK 或 DINGTALK_WEBHOOK,请在 config/.env 中配置后重试"
                )

    def _resolve_paths(self, db_path: str, data_dir: str, config_dir: str | None) -> None:
        """将相对路径解析为基于项目根目录的绝对路径"""
        # db_path
        p = Path(db_path)
        self.db_path = str(p) if p.is_absolute() else str(_PROJECT_ROOT / db_path)
        # data_dir
        p = Path(data_dir)
        self.data_dir = str(p) if p.is_absolute() else str(_PROJECT_ROOT / data_dir)

    def __enter__(self) -> "PipelineRunner":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """关闭所有资源"""
        try:
            if self.fetcher is not None:
                self.fetcher.close()
                self.fetcher = None
        except Exception:
            pass
        try:
            if self.extractor is not None:
                if hasattr(self.extractor, 'llm') and hasattr(self.extractor.llm, 'close'):
                    self.extractor.llm.close()
                if hasattr(self.extractor, 'ocr') and self.extractor.ocr is not None \
                        and hasattr(self.extractor.ocr, 'close'):
                    self.extractor.ocr.close()
                if hasattr(self.extractor, 'vlm') and self.extractor.vlm is not None \
                        and hasattr(self.extractor.vlm, 'close'):
                    self.extractor.vlm.close()
                self.extractor = None
        except Exception:
            pass
        try:
            self.matcher = None
        except Exception:
            pass
        try:
            self.parser.close()
        except Exception:
            pass
        try:
            self.notifier.close()
        except Exception:
            pass
        try:
            self.repo.close()
        except Exception:
            pass

    def _init_fetcher(self) -> Fetcher:
        """延迟初始化 Fetcher(需要 Cookie)"""
        cookie = self.config_loader.get_cookie()
        token = self.config_loader.get_token()
        ua = self.config_loader.get_user_agent()
        if not cookie or not token:
            raise CookieInvalidError(
                "WECHAT_MP_COOKIE 或 WECHAT_MP_TOKEN 未配置,请在 config/.env 中设置"
            )
        return Fetcher(cookie=cookie, token=token, user_agent=ua)

    def run(self) -> None:
        """执行完整管道"""
        stages_str = ",".join(sorted(self.stages))
        logger.info(f"=== 开始运行 {self.run_id} (dry_run={self.dry_run}, stages={stages_str}) ===")
        self.repo.log_run(self.run_id, self.started_at)

        fetched_count = 0
        candidate_count = 0
        matched_count = 0
        total_llm_calls = 0
        total_ocr_calls = 0
        total_vlm_calls = 0
        total_cost = 0.0
        error_summary: str | None = None
        fatal_error = False
        all_articles: list[ArticleMeta] = []

        # ========== 阶段1: 抓取 ==========
        if "fetch" in self.stages and not self.dry_run:
            result = self._do_fetch()
            if result["fatal"]:
                error_summary = result["error"]
                fatal_error = True
            else:
                all_articles = result["articles"]
                fetched_count = len(all_articles)
        elif self.dry_run or "fetch" not in self.stages:
            # dry-run 或不含 fetch 阶段:跳过 HTTP 抓取,加载已入库的待处理文章
            if self.dry_run:
                logger.info("dry-run 模式:跳过 HTTP 抓取")
            else:
                logger.info("跳过抓取阶段,加载已入库的待处理文章")
            pending = self.repo.query_pending_articles()
            if pending:
                logger.info(f"发现 {len(pending)} 篇待处理文章,继续解析/预过滤")
                for row in pending:
                    all_articles.append(ArticleMeta(
                        account_name=row["account_name"],
                        title=row["title"],
                        url=row["url"],
                        publish_time=datetime.fromisoformat(row["publish_time"]),
                    ))
            elif self.dry_run:
                logger.info("dry-run:无待处理文章,仅验证配置")

        if not fatal_error:
            # ========== 阶段2: 解析 + 预过滤 ==========
            if "parse" in self.stages or "prefilter" in self.stages:
                candidate_count = self._process_articles(all_articles)

            # ========== 阶段2.5: 提取 (v0.3 含 VLM) ==========
            if "extract" in self.stages:
                stats = self._do_extract()
                total_llm_calls = stats["llm_calls"]
                total_ocr_calls = stats["ocr_calls"]
                total_vlm_calls = stats.get("vlm_calls", 0)
                total_cost = stats.get("cost_estimate", 0.0)

            # ========== 阶段2.6: 匹配 (v0.2) ==========
            if "match" in self.stages:
                matched_count = self._do_match()

            # ========== 阶段3: 推送 ==========
            if "notify" in self.stages:
                self._notify(fetched_count, candidate_count, matched_count)

        # 更新运行日志
        self.repo.update_run(
            self.run_id,
            ended_at=_now_iso(),
            fetched_count=fetched_count,
            candidate_count=candidate_count,
            llm_count=total_llm_calls,
            ocr_count=total_ocr_calls,
            vlm_count=total_vlm_calls,
            cost_estimate=total_cost,
            error_summary=error_summary,
        )
        logger.info(
            f"=== 运行结束 {self.run_id}: fetched={fetched_count}, "
            f"candidates={candidate_count}, matched={matched_count}, "
            f"llm_calls={total_llm_calls}, ocr_calls={total_ocr_calls}, "
            f"vlm_calls={total_vlm_calls}, cost={total_cost:.4f}元 ==="
        )

    def _do_fetch(self) -> dict:
        """执行抓取阶段,返回 {articles, fatal, error}"""
        # Cookie 过期检测(基于时间)
        if self.config_loader.is_cookie_stale():
            logger.warning("Cookie 已过期,请手动更新!")
            self.notifier.send_alert(
                "Cookie 过期提醒",
                "WECHAT_MP_COOKIE 已超过 24 小时未更新,请手动更新 Cookie 和 Token 后重试。",
            )
            return {"articles": [], "fatal": True, "error": "Cookie expired"}

        try:
            self.fetcher = self._init_fetcher()
        except CookieInvalidError as e:
            logger.error(str(e))
            return {"articles": [], "fatal": True, "error": str(e)}

        accounts = self.config_loader.load_accounts()
        # 按 priority 排序:high > medium > low
        priority_order = {"high": 0, "medium": 1, "low": 2}
        accounts.sort(key=lambda a: priority_order.get(a.priority, 1))

        all_articles: list[ArticleMeta] = []
        max_total = self.rules.schedule.max_articles_per_run
        error = None
        fatal = False

        for account in accounts:
            if not account.enabled:
                continue
            remaining = max_total - len(all_articles)
            if remaining <= 0:
                logger.info(f"已达全局上限 {max_total} 篇,停止抓取")
                break
            try:
                new_articles = self._fetch_account(account, max_new=remaining)
                all_articles.extend(new_articles)
                if len(all_articles) >= max_total:
                    logger.info(f"已达全局上限 {max_total} 篇,停止抓取")
                    break
            except (CookieInvalidError, CaptchaRequiredError) as e:
                logger.error(f"致命错误,停止抓取: {e}")
                self.notifier.send_alert("Cookie/验证码错误", str(e))
                error = str(e)
                fatal = True
                break
            except AccountNotFoundError as e:
                logger.warning(f"公众号未找到,跳过: {e}")
                continue
            except Exception as e:
                logger.error(f"公众号 {account.name} 抓取失败: {e}")
                continue

        return {"articles": all_articles, "fatal": fatal, "error": error}

    def _fetch_account(self, account: AccountConfig, max_new: int = 80) -> list[ArticleMeta]:
        """抓取单个公众号,最多返回 max_new 篇新文章(超出的不入库)"""
        assert self.fetcher is not None
        logger.info(f"抓取公众号: {account.name} (priority={account.priority})")
        account_meta = self.fetcher.search_account(
            account.name,
            account.alias,
            max_articles=self.rules.schedule.max_articles_per_account,
        )
        articles = self.fetcher.list_articles(
            account_meta,
            window_hours=self.rules.schedule.window_hours,
            max_count=self.rules.schedule.max_articles_per_account,
        )

        # URL 去重 + 入库(仅入配额内的,超出配额的不入库避免卡死 DISCOVERED)
        new_articles: list[ArticleMeta] = []
        for a in articles:
            if len(new_articles) >= max_new:
                logger.debug(f"已达单号配额 {max_new},剩余文章下次处理")
                break
            url_hash = _url_hash(a.url)
            if self.repo.is_url_seen(url_hash):
                logger.debug(f"URL 已存在,跳过: {a.title}")
                continue
            if not self.dry_run:
                self.repo.upsert_article(
                    article_id=url_hash,
                    account_name=a.account_name,
                    title=a.title,
                    url=a.url,
                    publish_time=a.publish_time.isoformat(),
                    status=Status.DISCOVERED,
                )
            new_articles.append(a)

        logger.info(f"公众号 {account.name}: 新增 {len(new_articles)} 篇")
        return new_articles

    def _process_articles(self, articles: list[ArticleMeta]) -> int:
        """解析 + 预过滤,返回 candidate 数量"""
        candidate_count = 0
        seen_content_hashes: set[str] = set()

        for meta in articles:
            url_hash = _url_hash(meta.url)
            article = None
            try:
                # 状态迁移:DISCOVERED→FETCHED 或 ERROR_*→FETCHED(重试)
                if not self.dry_run:
                    article = self.repo.get_article(url_hash)
                    if article:
                        current_status = article["status"]
                        if current_status == Status.DISCOVERED.value:
                            try:
                                self.repo.transition(url_hash, Status.DISCOVERED, Status.FETCHED)
                            except ValueError:
                                self.repo.force_status(url_hash, Status.FETCHED)
                        elif current_status in (
                            Status.ERROR_FETCH.value,
                            Status.ERROR_PARSE.value,
                            Status.FETCHED.value,
                            Status.PARSED.value,
                        ):
                            # 重试:直接 force 到 FETCHED 重新走解析流程
                            self.repo.force_status(url_hash, Status.FETCHED)

                # 解析(包含 HTML 下载)
                parsed = self.parser.parse(meta)

                # content_hash 去重
                if not self.dry_run and parsed.content_hash:
                    if parsed.content_hash in seen_content_hashes:
                        logger.info(f"内容重复,跳过: {meta.title}")
                        self.repo.force_status(url_hash, Status.ARCHIVED)
                        continue
                    # 检查 DB 中是否已有相同 content_hash
                    existing = self.repo.conn.execute(
                        "SELECT id FROM articles WHERE content_hash = ? AND id != ?",
                        (parsed.content_hash, url_hash),
                    ).fetchone()
                    if existing:
                        logger.info(f"内容与已有文章重复(hash={parsed.content_hash[:8]}),跳过: {meta.title}")
                        self.repo.force_status(url_hash, Status.ARCHIVED)
                        continue
                    seen_content_hashes.add(parsed.content_hash)

                if not self.dry_run:
                    current = self.repo.get_article(url_hash)
                    if current and current["status"] == Status.FETCHED.value:
                        try:
                            self.repo.transition(url_hash, Status.FETCHED, Status.PARSED)
                        except ValueError:
                            self.repo.force_status(url_hash, Status.PARSED)
                    self.repo.upsert_article(
                        article_id=url_hash,
                        account_name=meta.account_name,
                        title=meta.title,
                        url=meta.url,
                        publish_time=meta.publish_time.isoformat(),
                        status=Status.PARSED,
                        content_hash=parsed.content_hash,
                    )

                # 预过滤
                pf_result = self.prefilter.score(parsed)
                if not self.dry_run:
                    reasons_json = json.dumps(pf_result.reasons, ensure_ascii=False)
                    if pf_result.decision == "ignore":
                        self.repo.transition(url_hash, Status.PARSED, Status.IGNORED)
                        # IGNORED 直接归档,避免数据膨胀(但保留 prefilter_reasons 用于调试)
                        self.repo.upsert_article(
                            article_id=url_hash,
                            account_name=meta.account_name,
                            title=meta.title,
                            url=meta.url,
                            publish_time=meta.publish_time.isoformat(),
                            status=Status.IGNORED,
                            content_hash=parsed.content_hash,
                            prefilter_score=pf_result.score,
                            prefilter_reasons=reasons_json,
                        )
                        self.repo.transition(url_hash, Status.IGNORED, Status.ARCHIVED)
                    else:
                        self.repo.transition(url_hash, Status.PARSED, Status.CANDIDATE)
                        self.repo.upsert_article(
                            article_id=url_hash,
                            account_name=meta.account_name,
                            title=meta.title,
                            url=meta.url,
                            publish_time=meta.publish_time.isoformat(),
                            status=Status.CANDIDATE,
                            content_hash=parsed.content_hash,
                            prefilter_score=pf_result.score,
                            prefilter_reasons=reasons_json,
                        )
                        candidate_count += 1
                else:
                    # dry-run 模式只打印结果
                    decision = pf_result.decision
                    logger.info(
                        f"[dry-run] {meta.account_name} | {meta.title} | "
                        f"score={pf_result.score} | decision={decision}"
                    )
                    if decision != "ignore":
                        candidate_count += 1

            except Exception as e:
                logger.error(f"文章处理失败: {meta.title} — {e}")
                if not self.dry_run:
                    article = self.repo.get_article(url_hash)
                    current_status = article["status"] if article else None
                    try:
                        if current_status == Status.DISCOVERED.value:
                            self.repo.force_status(url_hash, Status.ERROR_FETCH)
                        else:
                            self.repo.force_status(url_hash, Status.ERROR_PARSE)
                    except Exception as mark_err:
                        logger.error(f"标记错误状态失败: {mark_err}")

        return candidate_count

    def _init_extractor(self):
        """初始化 Extractor(含 VLM/Stitcher/Slicer/Budget,延迟初始化)"""
        from wehire_monitor.providers.factory import (
            create_llm_provider, create_ocr_provider, create_vlm_provider,
        )
        from wehire_monitor.modules.extractor.extractor import Extractor
        from wehire_monitor.modules.extractor.slicer import LongImageSlicer
        from wehire_monitor.modules.extractor.stitcher import ImageStitcher
        from wehire_monitor.modules.extractor.budget import BudgetManager

        llm = create_llm_provider()
        ocr = None
        try:
            ocr = create_ocr_provider()
        except Exception as e:
            logger.warning(f"OCR Provider 初始化失败(将跳过 OCR 路径): {e}")

        vlm = None
        try:
            vlm = create_vlm_provider()
            if vlm:
                logger.info(f"VLM Provider 已初始化: {vlm.name}")
        except Exception as e:
            logger.warning(f"VLM Provider 初始化失败(将跳过 VLM 路径): {e}")

        # Stitcher 始终初始化(短图拼接是图片预处理步骤,不依赖 VLM)
        stitcher = ImageStitcher(data_dir=self.data_dir)
        slicer = LongImageSlicer(data_dir=self.data_dir) if vlm else None
        budget = BudgetManager(
            daily_budget_cny=self.rules.budget.daily_vlm_budget_cny,
            max_slices_per_article=self.rules.budget.max_slices_per_article,
        ) if vlm else None

        return Extractor(
            llm_provider=llm,
            ocr_provider=ocr,
            vlm_provider=vlm,
            slicer=slicer,
            stitcher=stitcher,
            budget_manager=budget,
        )

    def _init_matcher(self):
        """初始化 Matcher"""
        from wehire_monitor.modules.matcher.matcher import Matcher
        return Matcher(self.rules.match_rules)

    def _do_extract(self) -> dict:
        """对 CANDIDATE 文章执行 LLM/VLM 提取(v0.3)

        返回 {"llm_calls": int, "ocr_calls": int, "vlm_calls": int, "cost_estimate": float}
        """
        if self.dry_run:
            logger.info("dry-run 模式:跳过提取")
            return {"llm_calls": 0, "ocr_calls": 0, "vlm_calls": 0, "cost_estimate": 0.0}

        try:
            self.extractor = self._init_extractor()
        except Exception as e:
            logger.error(f"Extractor 初始化失败: {e}")
            return {"llm_calls": 0, "ocr_calls": 0, "vlm_calls": 0, "cost_estimate": 0.0}

        candidates = self.repo.query_by_status(Status.CANDIDATE)
        logger.info(f"提取阶段: {len(candidates)} 篇候选文章")

        total_llm = 0
        total_ocr = 0
        total_vlm = 0
        total_cost = 0.0

        for c in candidates:
            article_id = c["id"]
            try:
                meta = ArticleMeta(
                    account_name=c["account_name"],
                    title=c["title"],
                    url=c["url"],
                    publish_time=datetime.fromisoformat(c["publish_time"]),
                )
                parsed = self.parser.parse(meta)
                # 将 publish_time 写入 ParsedArticle 供后处理校验使用
                parsed.publish_time = c["publish_time"]

                pf_result = PrefilterResult(
                    score=c.get("prefilter_score", 0) or 0,
                    reasons=json.loads(c.get("prefilter_reasons", "[]") or "[]"),
                    decision="extract",
                )

                extraction = self.extractor.extract(parsed, pf_result)
                total_llm += extraction.llm_calls
                total_ocr += extraction.ocr_calls
                total_vlm += getattr(extraction, 'vlm_calls', 0)
                # 使用 ExtractionResult 中的准确成本(而非硬编码估算)
                total_cost += getattr(extraction, 'cost_estimate', 0.0)

                # 检查是否需要人工复核
                needs_review_flag = any(
                    "need_review" in w for w in extraction.warnings
                )

                if needs_review_flag:
                    # need_review: 直接标记为 NEED_REVIEW,不进入匹配
                    self.repo.force_status(article_id, Status.NEED_REVIEW)
                    self.repo.upsert_article(
                        article_id=article_id,
                        account_name=c["account_name"], title=c["title"],
                        url=c["url"], publish_time=c["publish_time"],
                        status=Status.NEED_REVIEW,
                        content_hash=c.get("content_hash"),
                        prefilter_score=c.get("prefilter_score"),
                        prefilter_reasons=c.get("prefilter_reasons"),
                        article_type=extraction.article_type,
                    )
                    logger.info(
                        f"文章 {article_id[:8]}: 需人工复核, warnings={extraction.warnings}"
                    )
                    continue

                # 状态迁移: CANDIDATE → EXTRACTED
                self.repo.force_status(article_id, Status.EXTRACTED)

                if extraction.jobs:
                    # 写入 jobs(返回 job_id 列表)
                    job_ids = self.repo.upsert_jobs(article_id, extraction.jobs)
                    # 更新 article_type(保护已有字段)
                    self.repo.upsert_article(
                        article_id=article_id,
                        account_name=c["account_name"], title=c["title"],
                        url=c["url"], publish_time=c["publish_time"],
                        status=Status.EXTRACTED,
                        content_hash=c.get("content_hash"),
                        prefilter_score=c.get("prefilter_score"),
                        prefilter_reasons=c.get("prefilter_reasons"),
                        article_type=extraction.article_type,
                    )
                    logger.info(
                        f"文章 {article_id[:8]}: 提取 {len(extraction.jobs)} 岗位, "
                        f"type={extraction.article_type}"
                    )
                else:
                    # 无岗位 → 归档
                    self.repo.force_status(article_id, Status.ARCHIVED)
                    self.repo.upsert_article(
                        article_id=article_id,
                        account_name=c["account_name"], title=c["title"],
                        url=c["url"], publish_time=c["publish_time"],
                        status=Status.ARCHIVED,
                        content_hash=c.get("content_hash"),
                        prefilter_score=c.get("prefilter_score"),
                        prefilter_reasons=c.get("prefilter_reasons"),
                        article_type=extraction.article_type,
                    )
                    logger.info(
                        f"文章 {article_id[:8]}: 无岗位信息,归档, type={extraction.article_type}"
                    )

            except Exception as e:
                logger.error(f"提取失败: {c['title']} — {e}")
                if not self.dry_run:
                    try:
                        self.repo.force_status(article_id, Status.ERROR_LLM)
                    except Exception:
                        pass

        return {
            "llm_calls": total_llm, "ocr_calls": total_ocr,
            "vlm_calls": total_vlm, "cost_estimate": total_cost,
        }

    def _do_match(self) -> int:
        """对已提取岗位计算匹配分(v0.2),返回命中岗位数"""
        if self.dry_run:
            logger.info("dry-run 模式:跳过匹配")
            return 0

        self.matcher = self._init_matcher()
        matched_count = 0
        notify_min = self.rules.match_rules.notify_min_score

        # 处理 EXTRACTED 状态文章
        extracted = self.repo.query_by_status(Status.EXTRACTED)
        logger.info(f"匹配阶段: {len(extracted)} 篇已提取文章")

        for art in extracted:
            article_id = art["id"]
            try:
                jobs_rows = self.repo.query_jobs_by_article(article_id)
                if not jobs_rows:
                    # 无岗位 → 归档
                    self.repo.force_status(article_id, Status.ARCHIVED)
                    continue

                # 从 DB 行重建 Job 对象
                from wehire_monitor.domain.models import Job as DJob, Deadline
                jobs = []
                for row in jobs_rows:
                    jobs.append(DJob(
                        company_name=row["company_name"] or None,
                        job_name=row["job_name"] or None,
                        location=row["location"] or None,
                        apply_channel=row["apply_channel"],
                        email=row["email"],
                        email_chars=json.loads(row["email_chars"] or "[]"),
                        deadline=Deadline(
                            date=row["deadline_date"] or None,
                            inferred=bool(row["deadline_inferred"]),
                        ),
                        source_evidence=json.loads(row["source_evidence"] or "{}"),
                        confidence=row["confidence"] or 0,
                    ))

                matched = self.matcher.match(jobs)

                # 更新 match_score(使用 upsert_jobs 返回的 id 列表保证一致性)
                for m, row in zip(matched, jobs_rows):
                    self.repo.conn.execute(
                        "UPDATE jobs SET match_score = ? WHERE id = ?",
                        (m.match_score, row["id"]),
                    )
                self.repo.conn.commit()

                any_matched = any(m.match_score >= notify_min for m in matched)
                if any_matched:
                    # 直接 force 到 MATCHED(跳过中间 VALIDATED 简化状态机)
                    self.repo.force_status(article_id, Status.MATCHED)
                    matched_count += sum(
                        1 for m in matched if m.match_score >= notify_min
                    )
                    # 保护已有字段
                    self.repo.upsert_article(
                        article_id=article_id,
                        account_name=art["account_name"], title=art["title"],
                        url=art["url"], publish_time=art["publish_time"],
                        status=Status.MATCHED,
                        content_hash=art.get("content_hash"),
                        prefilter_score=art.get("prefilter_score"),
                        prefilter_reasons=art.get("prefilter_reasons"),
                        article_type=art.get("article_type"),
                    )
                else:
                    self.repo.force_status(article_id, Status.ARCHIVED)
                    self.repo.upsert_article(
                        article_id=article_id,
                        account_name=art["account_name"], title=art["title"],
                        url=art["url"], publish_time=art["publish_time"],
                        status=Status.ARCHIVED,
                        content_hash=art.get("content_hash"),
                        prefilter_score=art.get("prefilter_score"),
                        prefilter_reasons=art.get("prefilter_reasons"),
                        article_type=art.get("article_type"),
                    )

                logger.info(
                    f"文章 {article_id[:8]}: 匹配 "
                    f"{sum(1 for m in matched if m.match_score >= notify_min)}/"
                    f"{len(matched)} 个岗位达标"
                )

            except Exception as e:
                logger.error(f"匹配失败: {art['title']} — {e}")
                try:
                    self.repo.force_status(article_id, Status.ERROR_LLM)
                except Exception:
                    pass

        return matched_count

    def _notify(self, fetched_count: int, candidate_count: int, matched_count: int) -> None:
        """推送结构化日报(v0.2): 查询 MATCHED 岗位,生成表格+复核区 Markdown

        质量门控(SRS §4.4): confidence<60 / email_mismatch / email_invalid /
        deadline_before_publish 的岗位进复核区,不进主表。
        """
        if self.dry_run:
            logger.info("dry-run 模式:跳过推送")
            return

        # 查询匹配分达标的岗位(未通知,文章状态为 matched)
        min_score = self.rules.match_rules.notify_min_score
        jobs_rows = self.repo.query_jobs_for_notify(min_score=min_score)
        # 查询 NEED_REVIEW 状态文章(复核区)
        review_articles = self.repo.query_by_status(Status.NEED_REVIEW)

        if not jobs_rows and not review_articles:
            if self.rules.notify.push_when_empty:
                report = DailyReport(
                    date=_today_str(),
                    items=[],
                    total_fetched=fetched_count,
                    total_candidates=candidate_count,
                )
                self.notifier.send_daily(report)
            else:
                logger.info("无命中岗位且无复核项,跳过推送")
            return

        # 将 jobs_rows 转换为 MatchedJob(含 account_name)
        # 按 needs_review 拆分: 需复核的进 review_jobs, 达标的进 matched_jobs
        from wehire_monitor.domain.models import (
            Job as DJob, Deadline, MatchedJob,
        )
        matched_jobs: list[MatchedJob] = []
        job_ids: list[str] = []
        review_jobs: list[MatchedJob] = []
        for row in jobs_rows:
            job = DJob(
                company_name=row["company_name"] or None,
                job_name=row["job_name"] or None,
                location=row["location"] or None,
                apply_channel=row["apply_channel"],
                email=row["email"],
                email_chars=json.loads(row["email_chars"] or "[]"),
                deadline=Deadline(
                    date=row["deadline_date"] or None,
                    inferred=bool(row["deadline_inferred"]),
                ),
                source_evidence=json.loads(row["source_evidence"] or "{}"),
                confidence=row["confidence"] or 0,
            )
            mj = MatchedJob(
                job=job,
                match_score=row["match_score"] or 0,
                match_reasons=[],
                account_name=row.get("account_name", "-"),
                article_title=row.get("article_title", ""),
                article_url=row.get("article_url", ""),
            )
            # 质量门控: needs_review 的岗位进复核区,不进主表
            if needs_review(job):
                review_jobs.append(mj)
            else:
                matched_jobs.append(mj)
                job_ids.append(row["id"])

        # 复核区: NEED_REVIEW 文章构造为简化条目
        for art in review_articles:
            # 查询该文章的 jobs(如有)
            art_jobs = self.repo.query_jobs_by_article(art["id"])
            if art_jobs:
                for jrow in art_jobs:
                    rjob = DJob(
                        company_name=jrow["company_name"] or None,
                        job_name=jrow["job_name"] or None,
                        location=jrow["location"] or None,
                        apply_channel=jrow["apply_channel"],
                        email=jrow["email"],
                        email_chars=json.loads(jrow["email_chars"] or "[]"),
                        deadline=Deadline(
                            date=jrow["deadline_date"] or None,
                            inferred=bool(jrow["deadline_inferred"]),
                        ),
                        source_evidence=json.loads(jrow["source_evidence"] or "{}"),
                        confidence=jrow["confidence"] or 0,
                    )
                    rj = MatchedJob(
                        job=rjob, match_score=0, match_reasons=[],
                        account_name=art["account_name"],
                        article_title=art["title"],
                        article_url=art["url"],
                    )
                    review_jobs.append(rj)
            else:
                # 无 jobs 的 need_review 文章(如 OCR 质量过低)
                # 使用文章的 prefilter_reasons 作为复核原因
                pf_reasons = json.loads(art.get("prefilter_reasons") or "[]")
                review_reason = ", ".join(pf_reasons) if pf_reasons else "需人工复核"
                rjob = DJob(
                    company_name=None, job_name=art["title"], location=None,
                    apply_channel=None, email=None, email_chars=[],
                    deadline=Deadline(date=None, inferred=False),
                    source_evidence={"_warnings": [review_reason]},
                    confidence=0,
                )
                rj = MatchedJob(
                    job=rjob, match_score=0, match_reasons=[],
                    account_name=art["account_name"],
                    article_title=art["title"],
                    article_url=art["url"],
                )
                review_jobs.append(rj)

        shown = matched_jobs[: self.rules.notify.max_per_run]
        shown_ids = job_ids[: self.rules.notify.max_per_run]

        # 构建结构化 Markdown 并推送到各平台
        results: list[NotifyResult] = []
        if self.config_loader.get_feishu_webhook():
            md = self.notifier.build_structured_markdown(
                date=_today_str(),
                matched_jobs=shown,
                review_jobs=review_jobs,
                total_fetched=fetched_count,
                total_candidates=candidate_count,
                platform="feishu",
            )
            results.append(self.notifier._send_feishu(md))
        if self.config_loader.get_dingtalk_webhook():
            md = self.notifier.build_structured_markdown(
                date=_today_str(),
                matched_jobs=shown,
                review_jobs=review_jobs,
                total_fetched=fetched_count,
                total_candidates=candidate_count,
                platform="dingtalk",
            )
            results.append(self.notifier._send_dingtalk(md))

        if not results:
            logger.warning("未配置任何 Webhook")
            return

        any_success = any(r.success for r in results)
        if any_success and shown_ids:
            # 标记已通知
            self.repo.mark_jobs_notified(shown_ids)
            # 将相关 MATCHED 文章归档
            notified_article_ids = set()
            for row in jobs_rows[: self.rules.notify.max_per_run]:
                notified_article_ids.add(row["article_id"])
            for aid in notified_article_ids:
                try:
                    art = self.repo.get_article(aid)
                    if art and art["status"] == Status.MATCHED.value:
                        self.repo.force_status(aid, Status.ARCHIVED)
                except Exception as e:
                    logger.warning(f"归档失败: {aid[:8]} — {e}")
            logger.info(
                f"推送成功: 展示 {len(shown_ids)} 个岗位, "
                f"复核 {len(review_jobs)} 项"
            )
        elif any_success:
            logger.info("推送成功(无命中岗位,仅空提示或复核区)")
        else:
            logger.error(
                f"推送失败: {'; '.join(r.message for r in results)}"
            )

    def check_cookie(self) -> bool:
        """检查 Cookie 有效性(调用微信 API 验证)"""
        try:
            self.fetcher = self._init_fetcher()
        except CookieInvalidError as e:
            logger.error(str(e))
            return False

        try:
            status = self.fetcher.check_cookie()
            if status.is_valid:
                logger.info(f"Cookie 有效 (昵称: {status.nickname or '未知'})")
                return True
            else:
                logger.error(f"Cookie 无效: {status.message}")
                return False
        except Exception as e:
            logger.error(f"Cookie 检查失败: {e}")
            return False
