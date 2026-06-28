"""Pipeline 编排器

状态机推进:fetcher → parser → prefilter → notifier
- 生成 run_id,写 run_logs
- dry-run 模式:只读不推不写
- 单篇失败不影响整批
"""
import os
import uuid
from datetime import datetime, timezone

from loguru import logger

from wehire_monitor.config.loader import ConfigLoader
from wehire_monitor.config.schemas import AccountConfig, RulesConfig
from wehire_monitor.domain.models import ArticleMeta, ParsedArticle
from wehire_monitor.domain.status import Status
from wehire_monitor.modules.fetcher.fetcher import Fetcher
from wehire_monitor.modules.fetcher.exceptions import (
    CookieInvalidError,
    CaptchaRequiredError,
    AccountNotFoundError,
)
from wehire_monitor.modules.notifier.notifier import Notifier, DailyReport, ReportItem
from wehire_monitor.modules.parser.parser import Parser
from wehire_monitor.modules.prefilter.prefilter import Prefilter
from wehire_monitor.modules.storage.repository import Repository


class PipelineRunner:
    """管道编排器"""

    def __init__(
        self,
        db_path: str = "data/job_intel.sqlite",
        accounts_path: str | None = None,
        rules_path: str | None = None,
        data_dir: str = "data",
        dry_run: bool = False,
    ):
        self.dry_run = dry_run
        self.config_loader = ConfigLoader(
            accounts_path=accounts_path,
            rules_path=rules_path,
        )

        # 初始化仓库
        self.repo = Repository(db_path)
        self.repo.init_db()

        # 生成 run_id
        self.run_id = f"run-{uuid.uuid4().hex[:8]}"
        self.started_at = datetime.now(timezone.utc).isoformat()

        # 初始化模块(延迟初始化 fetcher,需要 Cookie)
        self.parser = Parser(data_dir=data_dir)
        self.keywords = self.config_loader.load_keywords()
        self.prefilter = Prefilter(self.keywords)
        self.rules = self.config_loader.load_rules()

        feishu_hook = os.environ.get("FEISHU_WEBHOOK")
        dingtalk_hook = os.environ.get("DINGTALK_WEBHOOK")
        self.notifier = Notifier(
            feishu_webhook=feishu_hook,
            dingtalk_webhook=dingtalk_hook,
            max_per_run=self.rules.notify.max_per_run,
            push_when_empty=self.rules.notify.push_when_empty,
        )

        self.fetcher: Fetcher | None = None

    def _init_fetcher(self) -> Fetcher:
        """延迟初始化 Fetcher(需要 Cookie)"""
        cookie = self.config_loader.get_cookie()
        token = self.config_loader.get_token()
        ua = self.config_loader.get_user_agent()
        return Fetcher(cookie=cookie, token=token, user_agent=ua)

    def run(self) -> None:
        """执行完整管道"""
        logger.info(f"=== 开始运行 {self.run_id} (dry_run={self.dry_run}) ===")
        self.repo.log_run(self.run_id, self.started_at)

        # Cookie 检测
        if self.config_loader.is_cookie_stale():
            logger.warning("Cookie 已过期,请手动更新!")
            if not self.dry_run:
                self._notify_cookie_expired()
            self.repo.update_run(
                self.run_id,
                ended_at=datetime.now(timezone.utc).isoformat(),
                error_summary="Cookie expired",
            )
            return

        # 抓取
        if self.dry_run:
            logger.info("dry-run 模式:跳过实际抓取")
            self.repo.update_run(
                self.run_id,
                ended_at=datetime.now(timezone.utc).isoformat(),
            )
            return

        self.fetcher = self._init_fetcher()
        accounts = self.config_loader.load_accounts()
        all_articles: list[ArticleMeta] = []

        for account in accounts:
            if not account.enabled:
                continue
            try:
                all_articles.extend(self._fetch_account(account))
            except (CookieInvalidError, CaptchaRequiredError) as e:
                logger.error(f"致命错误,停止抓取: {e}")
                self._notify_cookie_expired()
                break
            except Exception as e:
                logger.error(f"公众号 {account.name} 抓取失败: {e}")
                continue

        # 处理文章
        self._process_articles(all_articles)

        # 推送
        self._notify()

        # 更新运行日志
        self.repo.update_run(
            self.run_id,
            ended_at=datetime.now(timezone.utc).isoformat(),
            fetched_count=len(all_articles),
        )
        logger.info(f"=== 运行结束 {self.run_id} ===")

    def _fetch_account(self, account: AccountConfig) -> list[ArticleMeta]:
        """抓取单个公众号"""
        assert self.fetcher is not None
        logger.info(f"抓取公众号: {account.name}")
        account_meta = self.fetcher.search_account(account.name, account.alias)
        articles = self.fetcher.list_articles(
            account_meta, window_hours=self.rules.schedule.window_hours
        )

        # URL 去重 + 入库
        new_articles: list[ArticleMeta] = []
        for a in articles:
            import hashlib
            url_hash = hashlib.sha256(a.url.encode()).hexdigest()
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

        return new_articles

    def _process_articles(self, articles: list[ArticleMeta]) -> None:
        """解析 + 预过滤"""
        import hashlib

        for meta in articles:
            url_hash = hashlib.sha256(meta.url.encode()).hexdigest()
            try:
                # 解析
                parsed = self.parser.parse(meta)
                if not self.dry_run:
                    self.repo.transition(url_hash, Status.DISCOVERED, Status.FETCHED)
                    self.repo.transition(url_hash, Status.FETCHED, Status.PARSED)
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
                    if pf_result.decision == "ignore":
                        self.repo.transition(url_hash, Status.PARSED, Status.IGNORED)
                    else:
                        self.repo.transition(url_hash, Status.PARSED, Status.CANDIDATE)
                        self.repo.upsert_article(
                            article_id=url_hash,
                            account_name=meta.account_name,
                            title=meta.title,
                            url=meta.url,
                            publish_time=meta.publish_time.isoformat(),
                            status=Status.CANDIDATE,
                            prefilter_score=pf_result.score,
                            prefilter_reasons=str(pf_result.reasons),
                        )

            except Exception as e:
                logger.error(f"文章处理失败: {meta.title} — {e}")
                if not self.dry_run:
                    try:
                        self.repo.transition(url_hash, Status.FETCHED, Status.ERROR_PARSE)
                    except ValueError:
                        pass  # 状态不匹配时忽略

    def _notify(self) -> None:
        """推送日报"""
        if self.dry_run:
            logger.info("dry-run 模式:跳过推送")
            return

        candidates = self.repo.query_by_status(Status.CANDIDATE)
        items = [
            ReportItem(
                title=c["title"],
                url=c["url"],
                account_name=c["account_name"],
            )
            for c in candidates
        ]

        report = DailyReport(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            items=items,
            total_fetched=len(self.repo.query_by_status(Status.ARCHIVED))
            + len(candidates)
            + len(self.repo.query_by_status(Status.IGNORED)),
            total_candidates=len(candidates),
        )

        result = self.notifier.send_daily(report)
        if result.success:
            # 标记已通知
            for c in candidates:
                self.repo.transition(c["id"], Status.CANDIDATE, Status.NOTIFIED)
                self.repo.transition(c["id"], Status.NOTIFIED, Status.ARCHIVED)
            logger.info(f"推送成功: {len(items)} 条")
        else:
            logger.error(f"推送失败: {result.message}")

    def _notify_cookie_expired(self) -> None:
        """推送 Cookie 失效提醒"""
        report = DailyReport(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            items=[],
            total_fetched=0,
            total_candidates=0,
        )
        # 构建特殊提醒
        self.notifier._send_feishu("⚠️ Cookie 已过期,请手动更新 WECHAT_MP_COOKIE 和 WECHAT_MP_TOKEN!")
        self.notifier._send_dingtalk("⚠️ Cookie 已过期,请手动更新 WECHAT_MP_COOKIE 和 WECHAT_MP_TOKEN!")
