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
    help="微信公众号招聘情报监控管道 (v0.1 MVP)",
    no_args_is_help=True,
)


def _default(val, default):
    """CLI 选项默认值:如果为 None 则使用项目根目录下的路径"""
    return val if val else str(_PROJECT_ROOT / default)


@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run", help="只读模式,不写库不推送"),
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径(相对路径基于项目根目录)"),
    config_dir: str = typer.Option("config", help="配置目录路径"),
    data_dir: str = typer.Option("data", help="数据目录路径"),
):
    """执行完整管道:抓取→解析→过滤→入库→推送"""
    from wehire_monitor.pipeline.runner import PipelineRunner

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
def parse():
    """仅执行解析阶段(对已入库 DISCOVERED 状态文章) — v0.2 完善"""
    from wehire_monitor.pipeline.runner import PipelineRunner

    logger.info("执行解析阶段(对已入库文章)")
    with PipelineRunner(
        db_path=str(_PROJECT_ROOT / "data" / "job_intel.sqlite"),
        dry_run=False,
        stages={"parse", "prefilter"},
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


if __name__ == "__main__":
    app()
