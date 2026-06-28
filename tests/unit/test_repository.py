"""Repository 测试"""
from wehire_monitor.modules.storage.repository import Repository
from wehire_monitor.domain.status import Status


def test_init_db_creates_tables(tmp_db_path):
    repo = Repository(tmp_db_path)
    repo.init_db()
    # 表应存在
    tables = repo.list_tables()
    assert "articles" in tables
    assert "jobs" in tables
    assert "run_logs" in tables
    assert "images" in tables


def test_upsert_and_get_article(tmp_db_path):
    repo = Repository(tmp_db_path)
    repo.init_db()
    repo.upsert_article(
        article_id="hash001",
        account_name="上海国资招聘",
        title="测试标题",
        url="https://mp.weixin.qq.com/s/xxx",
        publish_time="2026-06-28T09:30:00+08:00",
        status=Status.DISCOVERED,
    )
    article = repo.get_article("hash001")
    assert article is not None
    assert article["title"] == "测试标题"
    assert article["status"] == "discovered"


def test_is_url_seen(tmp_db_path):
    repo = Repository(tmp_db_path)
    repo.init_db()
    assert repo.is_url_seen("hash001") is False
    repo.upsert_article(
        article_id="hash001",
        account_name="测试号",
        title="标题",
        url="https://example.com",
        publish_time="2026-06-28T09:30:00+08:00",
        status=Status.DISCOVERED,
    )
    assert repo.is_url_seen("hash001") is True


def test_transition_status(tmp_db_path):
    repo = Repository(tmp_db_path)
    repo.init_db()
    repo.upsert_article(
        article_id="hash001",
        account_name="测试号",
        title="标题",
        url="https://example.com",
        publish_time="2026-06-28T09:30:00+08:00",
        status=Status.DISCOVERED,
    )
    repo.transition("hash001", Status.DISCOVERED, Status.FETCHED)
    article = repo.get_article("hash001")
    assert article["status"] == "fetched"


def test_transition_wrong_from_raises(tmp_db_path):
    repo = Repository(tmp_db_path)
    repo.init_db()
    repo.upsert_article(
        article_id="hash001",
        account_name="测试号",
        title="标题",
        url="https://example.com",
        publish_time="2026-06-28T09:30:00+08:00",
        status=Status.DISCOVERED,
    )
    import pytest
    with pytest.raises(ValueError, match="状态迁移不匹配"):
        repo.transition("hash001", Status.FETCHED, Status.PARSED)


def test_log_run(tmp_db_path):
    repo = Repository(tmp_db_path)
    repo.init_db()
    repo.log_run(
        run_id="run-001",
        started_at="2026-06-28T08:30:00+08:00",
    )
    repo.update_run(
        run_id="run-001",
        ended_at="2026-06-28T08:45:00+08:00",
        fetched_count=30,
        candidate_count=5,
        error_summary=None,
    )
    run = repo.get_run("run-001")
    assert run["fetched_count"] == 30
    assert run["candidate_count"] == 5
    assert run["ended_at"] == "2026-06-28T08:45:00+08:00"


def test_query_by_status(tmp_db_path):
    repo = Repository(tmp_db_path)
    repo.init_db()
    for i in range(3):
        repo.upsert_article(
            article_id=f"hash{i}",
            account_name="测试号",
            title=f"标题{i}",
            url=f"https://example.com/{i}",
            publish_time="2026-06-28T09:30:00+08:00",
            status=Status.CANDIDATE if i < 2 else Status.IGNORED,
        )
    candidates = repo.query_by_status(Status.CANDIDATE)
    assert len(candidates) == 2
