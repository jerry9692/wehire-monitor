"""Typer CLI 主入口"""
import typer
from loguru import logger

app = typer.Typer(
    name="wehire-monitor",
    help="微信公众号招聘情报监控管道",
    no_args_is_help=True,
)


@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run", help="只读模式,不推不写"),
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径"),
):
    """执行完整管道:抓取→解析→过滤→入库→推送"""
    from wehire_monitor.pipeline.runner import PipelineRunner

    logger.info(f"启动管道 (dry_run={dry_run})")
    runner = PipelineRunner(db_path=db, dry_run=dry_run)
    runner.run()


@app.command()
def fetch(
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径"),
):
    """仅执行抓取阶段"""
    from wehire_monitor.pipeline.runner import PipelineRunner

    runner = PipelineRunner(db_path=db, dry_run=True)
    runner.run()
    typer.echo("抓取阶段完成(dry-run)")


@app.command()
def parse(
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径"),
):
    """仅执行解析阶段"""
    typer.echo("parse — 单模块运行待 v0.2 完善")


@app.command()
def prefilter(
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径"),
):
    """仅执行预过滤阶段"""
    typer.echo("prefilter — 单模块运行待 v0.2 完善")


@app.command()
def notify(
    db: str = typer.Option("data/job_intel.sqlite", help="SQLite 路径"),
):
    """仅执行推送阶段"""
    typer.echo("notify — 单模块运行待 v0.2 完善")


@app.command()
def schedule():
    """启动定时调度(每日 08:30/20:30)"""
    from wehire_monitor.pipeline.scheduler import Scheduler
    from wehire_monitor.config.loader import ConfigLoader

    loader = ConfigLoader()
    rules = loader.load_rules()
    sched = Scheduler(daily_at=rules.schedule.daily_at)
    sched.start()


if __name__ == "__main__":
    app()
