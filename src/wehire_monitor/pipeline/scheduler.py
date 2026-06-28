"""定时调度器

每日指定时间触发管道运行。
跨平台兼容(Windows/Mac/Linux)。
默认时区 Asia/Shanghai。
"""
from loguru import logger

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Asia/Shanghai")
except ImportError:
    _TZ = None  # type: ignore[assignment]

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger


class Scheduler:
    """APScheduler 定时调度"""

    def __init__(
        self,
        daily_at: list[str] | None = None,
        db_path: str = "data/job_intel.sqlite",
        config_dir: str = "config",
        data_dir: str = "data",
    ):
        self.daily_at = daily_at or ["08:30", "20:30"]
        self.db_path = db_path
        self.config_dir = config_dir
        self.data_dir = data_dir
        self.scheduler = BlockingScheduler(timezone=_TZ)

    def start(self) -> None:
        """启动调度器(阻塞)"""
        for time_str in self.daily_at:
            hour, minute = time_str.split(":")
            trigger_kwargs = {"hour": int(hour), "minute": int(minute)}
            if _TZ:
                trigger_kwargs["timezone"] = _TZ
            self.scheduler.add_job(
                self._run_pipeline,
                CronTrigger(**trigger_kwargs),
                id=f"daily_{time_str}",
                name=f"每日招聘监控 {time_str}",
            )
            logger.info(f"已注册定时任务: 每日 {time_str}")

        logger.info("调度器启动,按 Ctrl+C 退出")
        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("调度器已停止")

    def _run_pipeline(self) -> None:
        """执行管道(每次创建新实例,异常不中断调度器)"""
        from wehire_monitor.pipeline.runner import PipelineRunner

        logger.info("定时任务触发")
        try:
            with PipelineRunner(
                db_path=self.db_path,
                config_dir=self.config_dir,
                data_dir=self.data_dir,
                dry_run=False,
            ) as runner:
                runner.run()
        except Exception as e:
            logger.error(f"定时任务执行异常: {e}", exc_info=True)
