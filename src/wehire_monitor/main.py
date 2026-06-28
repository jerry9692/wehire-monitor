"""Typer CLI 主入口"""
import typer

app = typer.Typer(
    name="wehire-monitor",
    help="微信公众号招聘情报监控管道",
    no_args_is_help=True,
)


@app.command()
def run(dry_run: bool = typer.Option(False, "--dry-run", help="只读模式,不推不写")):
    """执行完整管道:抓取→解析→过滤→入库→推送"""
    typer.echo(f"run (dry_run={dry_run}) — 待实现")


@app.command()
def fetch():
    """仅执行抓取阶段"""
    typer.echo("fetch — 待实现")


@app.command()
def parse():
    """仅执行解析阶段"""
    typer.echo("parse — 待实现")


@app.command()
def prefilter():
    """仅执行预过滤阶段"""
    typer.echo("prefilter — 待实现")


@app.command()
def notify():
    """仅执行推送阶段"""
    typer.echo("notify — 待实现")


if __name__ == "__main__":
    app()
