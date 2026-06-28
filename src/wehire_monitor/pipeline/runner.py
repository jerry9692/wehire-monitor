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
from wehire_monitor.domain.models import ArticleMeta, ParsedArticle
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
        self.stages = stages or {"fetch", "parse", "prefilter", "notify"}
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

            # ========== 阶段3: 推送 ==========
            if "notify" in self.stages:
                self._notify(candidate_count, fetched_count)

        # 更新运行日志
        self.repo.update_run(
            self.run_id,
            ended_at=_now_iso(),
            fetched_count=fetched_count,
            candidate_count=candidate_count,
            error_summary=error_summary,
        )
        logger.info(
            f"=== 运行结束 {self.run_id}: fetched={fetched_count}, candidates={candidate_count} ==="
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

    def _notify(self, candidate_count: int, fetched_count: int) -> None:
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
            date=_today_str(),
            items=items,
            total_fetched=fetched_count,
            total_candidates=candidate_count,
        )

        result: NotifyResult = self.notifier.send_daily(report)
        if result.success and result.pushed_count > 0:
            pushed = candidates[: result.pushed_count]
            for c in pushed:
                try:
                    self.repo.transition(c["id"], Status.CANDIDATE, Status.NOTIFIED)
                    self.repo.transition(c["id"], Status.NOTIFIED, Status.ARCHIVED)
                except ValueError as e:
                    logger.warning(f"归档失败: {c['title']} — {e}")
            remaining = len(candidates) - result.pushed_count
            logger.info(
                f"推送成功: 展示 {result.pushed_count} 条, 剩余 {remaining} 条留待下次"
            )
        elif result.success and result.pushed_count == 0 and not items:
            logger.info("无候选文章,跳过推送")
        else:
            logger.error(f"推送失败: {result.message}")

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
