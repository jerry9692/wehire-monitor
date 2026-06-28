"""SQLite 仓库"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from wehire_monitor.domain.status import Status

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Repository:
    """SQLite 数据访问层"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

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
        """状态迁移(带 from 校验)"""
        article = self.get_article(article_id)
        if article is None:
            raise ValueError(f"文章不存在: {article_id}")
        if article["status"] != from_status.value:
            raise ValueError(
                f"状态迁移不匹配: 期望 {from_status.value}, 实际 {article['status']}"
            )
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE articles SET status = ?, updated_at = ? WHERE id = ?",
            (to_status.value, now, article_id),
        )
        self.conn.commit()
        logger.debug(f"文章 {article_id} 状态: {from_status.value} → {to_status.value}")

    def query_by_status(self, status: Status) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT * FROM articles WHERE status = ? ORDER BY created_at",
            (status.value,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def log_run(self, run_id: str, started_at: str) -> None:
        self.conn.execute(
            "INSERT INTO run_logs (run_id, started_at) VALUES (?, ?)",
            (run_id, started_at),
        )
        self.conn.commit()

    def update_run(
        self,
        run_id: str,
        ended_at: str | None = None,
        fetched_count: int | None = None,
        candidate_count: int | None = None,
        error_summary: str | None = None,
    ) -> None:
        sets = []
        params: list[Any] = []
        if ended_at is not None:
            sets.append("ended_at = ?")
            params.append(ended_at)
        if fetched_count is not None:
            sets.append("fetched_count = ?")
            params.append(fetched_count)
        if candidate_count is not None:
            sets.append("candidate_count = ?")
            params.append(candidate_count)
        if error_summary is not None:
            sets.append("error_summary = ?")
            params.append(error_summary)
        if not sets:
            return
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
            self._conn.close()
            self._conn = None
