"""Typer CLI 主入口"""
import sys
from pathlib import Path

import typer
from loguru import logger

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 配置日志(文件轮转 + 控制台)
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    level="INFO",
    colorize=True,
)
logs_dir = _PROJECT_ROOT / "logs"
logs_dir.mkdir(exist_ok=True)
logger.add(
    logs_dir / "wehire_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="30 days",
    level="DEBUG",
    encoding="utf-8",
)

app = typer.Typer(
    name="wehire-monitor",
    help="微信公众号招聘情报监控管道 (v0.4)",
    no_args_is_help=True,
)


def _default(val, default):
    """CLI 选项默认值:如果为 None 则使用项目根目录下的路径"""
    return val if val else str(_PROJECT_ROOT / default)


@app.command()
def run(
    stats: bool = typer.Option(False, "--stats", help="查看最近运行统计"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只读模式,不写库不推送"),
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径(相对路径基于项目根目录)"),
    config_dir: str = typer.Option("config", help="配置目录路径"),
    data_dir: str = typer.Option("data", help="数据目录路径"),
):
    """执行完整管道:抓取→解析→过滤→入库→推送"""
    from wehire_monitor.pipeline.runner import PipelineRunner

    if stats:
        logger.info("查看最近运行统计")
        with PipelineRunner(
            db_path=db,
            config_dir=config_dir,
            data_dir=data_dir,
            dry_run=True,
            stages=set(),
        ) as runner:
            runs = runner.repo.get_recent_runs(limit=10)
            if not runs:
                typer.echo("暂无运行记录")
                return
            typer.echo(f"最近 {len(runs)} 条运行记录:")
            typer.echo("-" * 80)
            for r in runs:
                error = r.get("error_summary") or "-"
                model_count = r.get("model_count", 0) or 0
                cost = r.get("cost_estimate") or 0
                typer.echo(
                    f"{r['run_id'][:16]:<16} "
                    f"fetched={r.get('fetched_count', 0) or 0} "
                    f"cand={r.get('candidate_count', 0) or 0} | "
                    f"模型调用: {model_count} 次, 成本: {cost:.4f} 元 | {error}"
                )
        return

    logger.info(f"启动完整管道 (dry_run={dry_run}, db={db})")
    with PipelineRunner(
        db_path=db,
        config_dir=config_dir,
        data_dir=data_dir,
        dry_run=dry_run,
    ) as runner:
        runner.run()


@app.command()
def fetch(
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径"),
    config_dir: str = typer.Option("config", help="配置目录路径"),
    data_dir: str = typer.Option("data", help="数据目录路径"),
):
    """执行抓取+解析+预过滤(不推送)"""
    from wehire_monitor.pipeline.runner import PipelineRunner

    logger.info(f"启动抓取+解析+预过滤 (db={db})")
    with PipelineRunner(
        db_path=db,
        config_dir=config_dir,
        data_dir=data_dir,
        dry_run=False,
        stages={"fetch", "parse", "prefilter"},
    ) as runner:
        runner.run()


@app.command()
def notify(
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径"),
    config_dir: str = typer.Option("config", help="配置目录路径"),
):
    """仅执行推送阶段(推送当前 CANDIDATE 状态文章)"""
    from wehire_monitor.pipeline.runner import PipelineRunner

    logger.info(f"启动推送 (db={db})")
    with PipelineRunner(
        db_path=db,
        config_dir=config_dir,
        dry_run=False,
        stages={"notify"},
    ) as runner:
        runner.run()


@app.command()
def check_cookie(
    config_dir: str = typer.Option("config", help="配置目录路径"),
):
    """检查 Cookie 有效性(调用微信 API 验证)"""
    from wehire_monitor.pipeline.runner import PipelineRunner

    logger.info("检查 Cookie 有效性...")
    with PipelineRunner(
        db_path=str(_PROJECT_ROOT / "data" / "job_intel.sqlite"),
        config_dir=config_dir,
        dry_run=True,
    ) as runner:
        ok = runner.check_cookie()
        if ok:
            typer.echo("✅ Cookie 有效")
            raise typer.Exit(code=0)
        else:
            typer.echo("❌ Cookie 无效或已过期,请更新 WECHAT_MP_COOKIE 和 WECHAT_MP_TOKEN", err=True)
            raise typer.Exit(code=1)


@app.command()
def login(
    config_dir: str = typer.Option("config", help="配置目录路径"),
    data_dir: str = typer.Option("data", help="数据目录路径"),
):
    """扫码登录微信公众号，自动获取 Cookie/Token（v0.4）"""
    from wehire_monitor.modules.fetcher.wechat_login import WeChatLogin
    from wehire_monitor.config.loader import ConfigLoader

    logger.info("启动扫码登录...")

    # 确定数据目录绝对路径
    p = Path(data_dir)
    cookie_file = str(p if p.is_absolute() else _PROJECT_ROOT / data_dir) + "/wechat_cookie.json"

    wechat_login = WeChatLogin(cookie_file=cookie_file)

    try:
        # 先检测已有 Cookie 是否有效
        if wechat_login.is_cookie_valid():
            typer.echo("✅ 已有 Cookie 有效，无需扫码登录")
            # 同步写入 .env（确保 .env 与持久化文件一致）
            loader = ConfigLoader(config_dir=config_dir)
            loader.update_env_cookie(
                cookie=wechat_login._cookie,
                token=wechat_login._token,
            )
            raise typer.Exit(code=0)

        # 执行扫码登录
        result = wechat_login.login()

        if result.success:
            # 写入 .env
            loader = ConfigLoader(config_dir=config_dir)
            loader.update_env_cookie(
                cookie=result.cookie,
                token=result.token,
            )
            typer.echo(f"✅ 登录成功，Cookie/Token 已写入 .env (token={result.token})")
        else:
            typer.echo(f"❌ 登录失败: {result.error}", err=True)
            raise typer.Exit(code=1)
    finally:
        wechat_login.close()


@app.command()
def parse(
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径"),
    config_dir: str = typer.Option("config", help="配置目录路径"),
):
    """仅执行解析阶段(对已入库待处理文章)"""
    from wehire_monitor.pipeline.runner import PipelineRunner

    logger.info("执行解析阶段(对已入库文章)")
    with PipelineRunner(
        db_path=db,
        config_dir=config_dir,
        dry_run=False,
        stages={"parse", "prefilter"},
    ) as runner:
        runner.run()


@app.command()
def prefilter(
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径"),
    config_dir: str = typer.Option("config", help="配置目录路径"),
):
    """仅执行预过滤阶段(对已入库待处理文章重新解析+过滤)"""
    from wehire_monitor.pipeline.runner import PipelineRunner

    logger.info("执行预过滤阶段(对已入库文章)")
    with PipelineRunner(
        db_path=db,
        config_dir=config_dir,
        dry_run=False,
        stages={"parse", "prefilter"},
    ) as runner:
        runner.run()


@app.command()
def extract(
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径"),
    config_dir: str = typer.Option("config", help="配置目录路径"),
):
    """仅执行提取阶段(对 CANDIDATE 状态文章调用多模态模型提取岗位)"""
    from wehire_monitor.pipeline.runner import PipelineRunner

    logger.info("执行提取阶段")
    with PipelineRunner(
        db_path=db, config_dir=config_dir, dry_run=False,
        stages={"extract"},
    ) as runner:
        runner.run()


@app.command()
def match(
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径"),
    config_dir: str = typer.Option("config", help="配置目录路径"),
):
    """仅执行匹配阶段(对已提取岗位计算匹配分)"""
    from wehire_monitor.pipeline.runner import PipelineRunner

    logger.info("执行匹配阶段")
    with PipelineRunner(
        db_path=db, config_dir=config_dir, dry_run=False,
        stages={"match"},
    ) as runner:
        runner.run()


@app.command()
def schedule(
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径"),
    config_dir: str = typer.Option("config", help="配置目录路径"),
    data_dir: str = typer.Option("data", help="数据目录路径"),
):
    """启动定时调度(每日 08:30/20:30)"""
    from wehire_monitor.pipeline.scheduler import Scheduler
    from wehire_monitor.config.loader import ConfigLoader

    loader = ConfigLoader(config_dir=config_dir)
    rules = loader.load_rules()
    sched = Scheduler(
        daily_at=rules.schedule.daily_at,
        db_path=db,
        config_dir=config_dir,
        data_dir=data_dir,
    )
    sched.start()


@app.command()
def review(
    list: bool = typer.Option(False, "--list", help="列出待复核文章"),
    approve: str = typer.Option(None, "--approve", help="复核通过,重新提取(迁回 CANDIDATE 触发重新处理)"),
    reject: str = typer.Option(None, "--reject", help="复核拒绝,归档"),
):
    """人工复核队列管理(v0.3)"""
    from wehire_monitor.pipeline.runner import PipelineRunner
    from wehire_monitor.domain.status import Status

    if not list and not approve and not reject:
        typer.echo("用法: wehire review --list | --approve <id> | --reject <id>")
        raise typer.Exit(code=0)

    with PipelineRunner(
        dry_run=True,
        stages=set(),
    ) as runner:
        if list:
            articles = runner.repo.query_by_status(Status.NEED_REVIEW)
            if not articles:
                typer.echo("暂无待复核文章")
                return
            typer.echo(f"待复核文章 ({len(articles)} 篇):")
            typer.echo(f"{'ID':<16} {'标题':<30} {'公众号':<15} URL")
            typer.echo("-" * 80)
            for a in articles:
                title = (a.get("title") or "")[:30]
                account = (a.get("account_name") or "")[:15]
                typer.echo(
                    f"{a.get('id', '')[:16]:<16} {title:<30} {account:<15} {a.get('url', '')}"
                )
        elif approve:
            # approve 后迁回 CANDIDATE,下次 run 时重新提取(而非直接到 EXTRACTED 导致无 jobs 被归档)
            runner.repo.force_status(approve, Status.CANDIDATE)
            typer.echo(f"已通过复核,文章 {approve[:16]} 迁回 CANDIDATE 状态,下次 run 将重新提取")
        elif reject:
            runner.repo.force_status(reject, Status.ARCHIVED)
            typer.echo(f"已拒绝,文章 {reject[:16]} 归档")


if __name__ == "__main__":
    app()
