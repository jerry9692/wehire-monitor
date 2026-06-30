"""SQLite 仓库"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from wehire_monitor.domain.status import Status

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# 允许更新的 run_logs 列(白名单,防止 SQL 注入)
_RUN_UPDATE_COLUMNS = {
    "ended_at", "fetched_count", "candidate_count",
    "model_count",
    "cost_estimate", "error_summary",
}


class Repository:
    """SQLite 数据访问层(支持上下文管理器)"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        # 确保父目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            # 启用外键约束 + WAL 模式 + 超时
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.execute("PRAGMA synchronous = NORMAL")
        return self._conn

    def __enter__(self) -> "Repository":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def init_db(self) -> None:
        """初始化数据库(建表 + 迁移)"""
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        self.conn.executescript(schema_sql)
        self.conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """数据库 schema 迁移(v0.3 多模态统一)

        迁移内容:
        1. run_logs: ocr_count/llm_count/vlm_count → model_count
        2. articles: ocr_done → candidate, error_ocr → error_llm
        """
        # --- 1. run_logs: 检查 model_count 列是否存在 ---
        cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(run_logs)").fetchall()
        }
        if "model_count" not in cols:
            logger.info("迁移: run_logs 添加 model_count 列")
            self.conn.execute(
                "ALTER TABLE run_logs ADD COLUMN model_count INTEGER DEFAULT 0"
            )
            # 从旧列迁移数据(三路计数之和)
            old_sum_expr = " + ".join(
                f"COALESCE({c}, 0)"
                for c in ("ocr_count", "llm_count", "vlm_count")
                if c in cols
            )
            if old_sum_expr:
                self.conn.execute(
                    f"UPDATE run_logs SET model_count = {old_sum_expr}"
                )
            self.conn.commit()

        # --- 2. articles: 迁移已废弃的状态值 ---
        migrated = False
        # ocr_done → candidate(重新走单路提取)
        cursor = self.conn.execute(
            "UPDATE articles SET status = 'candidate' WHERE status = 'ocr_done'"
        )
        if cursor.rowcount > 0:
            logger.info(f"迁移: {cursor.rowcount} 篇 ocr_done → candidate")
            migrated = True
        # error_ocr → error_llm(统一错误状态)
        cursor = self.conn.execute(
            "UPDATE articles SET status = 'error_llm' WHERE status = 'error_ocr'"
        )
        if cursor.rowcount > 0:
            logger.info(f"迁移: {cursor.rowcount} 篇 error_ocr → error_llm")
            migrated = True
        if migrated:
            self.conn.commit()

    def list_tables(self) -> list[str]:
        cursor = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [row["name"] for row in cursor.fetchall()]

    def upsert_article(
        self,
        article_id: str,
        account_name: str,
        title: str,
        url: str,
        publish_time: str,
        status: Status,
        content_hash: str | None = None,
        image_hashes: str | None = None,
        prefilter_score: int | None = None,
        prefilter_reasons: str | None = None,
        article_type: str | None = None,
        raw_html_path: str | None = None,
        markdown_path: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO articles (id, account_name, title, url, publish_time, status,
                                   content_hash, image_hashes, prefilter_score,
                                   prefilter_reasons, article_type, raw_html_path,
                                   markdown_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                account_name=excluded.account_name,
                title=excluded.title,
                status=excluded.status,
                content_hash=excluded.content_hash,
                image_hashes=excluded.image_hashes,
                prefilter_score=excluded.prefilter_score,
                prefilter_reasons=excluded.prefilter_reasons,
                article_type=excluded.article_type,
                raw_html_path=excluded.raw_html_path,
                markdown_path=excluded.markdown_path,
                updated_at=excluded.updated_at
            """,
            (article_id, account_name, title, url, publish_time, status.value,
             content_hash, image_hashes, prefilter_score, prefilter_reasons,
             article_type, raw_html_path, markdown_path, now, now),
        )
        self.conn.commit()

    def get_article(self, article_id: str) -> dict[str, Any] | None:
        cursor = self.conn.execute(
            "SELECT * FROM articles WHERE id = ?", (article_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def is_url_seen(self, url_hash: str) -> bool:
        cursor = self.conn.execute(
            "SELECT 1 FROM articles WHERE id = ?", (url_hash,)
        )
        return cursor.fetchone() is not None

    def transition(
        self, article_id: str, from_status: Status, to_status: Status
    ) -> None:
        """原子状态迁移(WHERE 同时校验 id 和 from_status,避免 TOCTOU)"""
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            "UPDATE articles SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
            (to_status.value, now, article_id, from_status.value),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            # 查询当前状态以给出更准确的错误
            article = self.get_article(article_id)
            current = article["status"] if article else "NOT_FOUND"
            raise ValueError(
                f"状态迁移失败: 文章 {article_id[:8]} 当前状态={current}, "
                f"期望 from={from_status.value}, 目标 to={to_status.value}"
            )
        logger.debug(f"文章 {article_id[:8]} 状态: {from_status.value} → {to_status.value}")

    def force_status(self, article_id: str, to_status: Status) -> None:
        """强制设置状态(不校验 from_status,用于错误处理)"""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE articles SET status = ?, updated_at = ? WHERE id = ?",
            (to_status.value, now, article_id),
        )
        self.conn.commit()
        logger.debug(f"文章 {article_id[:8]} 强制状态 → {to_status.value}")

    def query_by_status(self, status: Status) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT * FROM articles WHERE status = ? ORDER BY created_at",
            (status.value,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_pending_articles(self) -> list[dict[str, Any]]:
        """查询所有处于待处理状态的文章(支持断点续跑和重试)

        注意:不包含 EXTRACTED/MATCHED/NOTIFIED/ARCHIVED 等终态或已完成提取的状态,
        避免 parse 等命令将已提取的文章错误降级重处理。
        """
        pending_statuses = (
            Status.DISCOVERED.value,
            Status.FETCHED.value,
            Status.PARSED.value,
            Status.ERROR_FETCH.value,
            Status.ERROR_PARSE.value,
            Status.ERROR_LLM.value,
            # CANDIDATE 也应包含(已预过滤待提取)
            Status.CANDIDATE.value,
        )
        placeholders = ",".join("?" * len(pending_statuses))
        cursor = self.conn.execute(
            f"SELECT * FROM articles WHERE status IN ({placeholders}) ORDER BY created_at",
            pending_statuses,
        )
        return [dict(row) for row in cursor.fetchall()]

    def log_run(self, run_id: str, started_at: str) -> None:
        self.conn.execute(
            "INSERT INTO run_logs (run_id, started_at) VALUES (?, ?)",
            (run_id, started_at),
        )
        self.conn.commit()

    def update_run(self, run_id: str, **kwargs: Any) -> None:
        """更新 run_logs,仅允许白名单列"""
        if not kwargs:
            return
        valid_items = [(k, v) for k, v in kwargs.items() if k in _RUN_UPDATE_COLUMNS]
        if not valid_items:
            return
        sets = [f"{k} = ?" for k, _ in valid_items]
        params = [v for _, v in valid_items]
        params.append(run_id)
        self.conn.execute(
            f"UPDATE run_logs SET {', '.join(sets)} WHERE run_id = ?",
            params,
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        cursor = self.conn.execute(
            "SELECT * FROM run_logs WHERE run_id = ?", (run_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        """查询最近 N 条运行日志(按开始时间倒序)"""
        cursor = self.conn.execute(
            """SELECT run_id, started_at, ended_at, fetched_count, candidate_count,
                      model_count, cost_estimate, error_summary
               FROM run_logs
               ORDER BY started_at DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def upsert_jobs(self, article_id: str, jobs: list) -> list[str]:
        """批量写入岗位(jobs 表),自动去重(UNIQUE 约束)。
        在单个事务中完成,返回实际写入/更新的 job_id 列表。
        异常时回滚并返回空列表。
        """
        now = datetime.now(timezone.utc).isoformat()
        inserted_ids: list[str] = []
        try:
            for job in jobs:
                # job hash: company + job_name + location + deadline
                job_key = "|".join([
                    (job.company_name or ""),
                    (job.job_name or ""),
                    (job.location or ""),
                    (job.deadline.date or ""),
                ])
                job_id = hashlib.sha256(job_key.encode()).hexdigest()[:16]

                self.conn.execute(
                    """INSERT INTO jobs (id, article_id, company_name, job_name, location,
                       apply_channel, email, email_chars, deadline_date, deadline_inferred,
                       confidence, match_score, source_evidence, warnings, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         confidence=excluded.confidence,
                         source_evidence=excluded.source_evidence,
                         warnings=excluded.warnings,
                         apply_channel=excluded.apply_channel,
                         email=excluded.email,
                         email_chars=excluded.email_chars,
                         deadline_date=excluded.deadline_date,
                         deadline_inferred=excluded.deadline_inferred,
                         updated_at=excluded.updated_at""",
                    (
                        job_id, article_id,
                        job.company_name or "", job.job_name or "", job.location or "",
                        job.apply_channel, job.email,
                        json.dumps(job.email_chars, ensure_ascii=False),
                        job.deadline.date or "", int(job.deadline.inferred),
                        job.confidence, 0,
                        json.dumps(job.source_evidence, ensure_ascii=False),
                        json.dumps(job.source_evidence.get("_warnings", []), ensure_ascii=False),
                        now, now,
                    ),
                )
                inserted_ids.append(job_id)
            self.conn.commit()
        except sqlite3.IntegrityError as e:
            logger.warning(f"岗位写入异常: {e}")
            self.conn.rollback()
            inserted_ids.clear()  # 回滚后返回空列表,避免返回已撤销的 id
        return inserted_ids

    def query_jobs_by_article(self, article_id: str) -> list[dict[str, Any]]:
        """查询某篇文章的所有岗位"""
        cursor = self.conn.execute(
            "SELECT * FROM jobs WHERE article_id = ? ORDER BY confidence DESC",
            (article_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_jobs_for_notify(self, min_score: int = 70) -> list[dict[str, Any]]:
        """查询匹配分达标的岗位(未通知,文章状态为 matched)"""
        cursor = self.conn.execute(
            """SELECT j.*, a.title as article_title, a.url as article_url,
                      a.account_name as account_name
               FROM jobs j JOIN articles a ON j.article_id = a.id
               WHERE j.match_score >= ? AND j.notified_at IS NULL
                 AND a.status = ?
               ORDER BY j.match_score DESC""",
            (min_score, Status.MATCHED.value),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_jobs_notified(self, job_ids: list[str]) -> None:
        """标记岗位已通知"""
        now = datetime.now(timezone.utc).isoformat()
        for jid in job_ids:
            self.conn.execute(
                "UPDATE jobs SET notified_at = ? WHERE id = ?",
                (now, jid),
            )
        self.conn.commit()

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
