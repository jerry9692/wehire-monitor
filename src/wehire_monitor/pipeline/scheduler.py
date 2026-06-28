"""定时调度器

每日 08:30 / 20:30 触发管道运行。
跨平台兼容(Windows/Mac/Linux)。
"""
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger


class Scheduler:
    """APScheduler 定时调度"""

    def __init__(self, daily_at: list[str] | None = None):
        self.daily_at = daily_at or ["08:30", "20:30"]
        self.scheduler = BlockingScheduler()

    def start(self) -> None:
        """启动调度器(阻塞)"""
        for time_str in self.daily_at:
            hour, minute = time_str.split(":")
            self.scheduler.add_job(
                self._run_pipeline,
                CronTrigger(hour=int(hour), minute=int(minute)),
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
        """执行管道"""
        from wehire_monitor.pipeline.runner import PipelineRunner

        logger.info("定时任务触发")
        runner = PipelineRunner()
        runner.run()
