"""SQLite 仓库"""
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
    "ocr_count", "llm_count", "vlm_count",
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
        """初始化数据库(建表)"""
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        self.conn.executescript(schema_sql)
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
        """查询所有处于中间状态的文章(支持断点续跑)"""
        pending_statuses = (
            Status.DISCOVERED.value,
            Status.FETCHED.value,
            Status.PARSED.value,
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

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
