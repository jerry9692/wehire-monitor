"""jobs 表 Repository 测试"""
import json
from wehire_monitor.modules.storage.repository import Repository
from wehire_monitor.domain.models import Job, Deadline
from wehire_monitor.domain.status import Status


def test_upsert_and_query_jobs(tmp_db_path):
    repo = Repository(tmp_db_path)
    repo.init_db()

    # 先入库一篇文章
    repo.upsert_article(
        article_id="art1", account_name="号A", title="标题",
        url="http://a", publish_time="2026-06-28T10:00:00",
        status=Status.EXTRACTED,
    )

    # 入库岗位
    jobs = [
        Job(
            company_name="德邦证券", job_name="数据分析师", location="上海",
            apply_channel="hr@example.com", email="hr@example.com",
            email_chars=["h","r","@","x",".","c","o","m"],
            deadline=Deadline(date="2026-07-31", inferred=False),
            source_evidence={"company_name": "德邦证券"},
            confidence=85,
        ),
        Job(
            company_name="中金公司", job_name="风控经理", location="北京",
            apply_channel=None, email=None, email_chars=[],
            deadline=Deadline(date=None, inferred=False),
            source_evidence={}, confidence=70,
        ),
    ]
    repo.upsert_jobs("art1", jobs)

    # 查询
    queried = repo.query_jobs_by_article("art1")
    assert len(queried) == 2
    assert queried[0]["company_name"] == "德邦证券"
    assert queried[0]["confidence"] == 85
    repo.close()


def test_upsert_jobs_dedup(tmp_db_path):
    """相同 company+job+location+deadline 不重复插入"""
    repo = Repository(tmp_db_path)
    repo.init_db()
    repo.upsert_article(
        article_id="art1", account_name="号A", title="标题",
        url="http://a", publish_time="2026-06-28T10:00:00",
        status=Status.EXTRACTED,
    )
    job = Job(
        company_name="德邦证券", job_name="数据分析师", location="上海",
        apply_channel="hr@example.com", email="hr@example.com",
        email_chars=["h"], deadline=Deadline(date="2026-07-31"),
        source_evidence={}, confidence=85,
    )
    repo.upsert_jobs("art1", [job])
    repo.upsert_jobs("art1", [job])  # 重复
    queried = repo.query_jobs_by_article("art1")
    assert len(queried) == 1  # 去重
    repo.close()


def test_query_jobs_for_notify(tmp_db_path):
    """查询 match_score >= 阈值的岗位"""
    repo = Repository(tmp_db_path)
    repo.init_db()
    repo.upsert_article(
        article_id="art1", account_name="号A", title="标题",
        url="http://a", publish_time="2026-06-28T10:00:00",
        status=Status.MATCHED,
    )
    jobs = [
        Job(
            company_name="德邦证券", job_name="数据分析师", location="上海",
            apply_channel=None, email=None, email_chars=[],
            deadline=Deadline(date=None), source_evidence={}, confidence=85,
        ),
    ]
    repo.upsert_jobs("art1", jobs)
    # 手动设置 match_score
    repo.conn.execute(
        "UPDATE jobs SET match_score = 80 WHERE article_id = ?", ("art1",)
    )
    repo.conn.commit()

    # 查询 match_score >= 70
    results = repo.query_jobs_for_notify(min_score=70)
    assert len(results) == 1
    assert results[0]["match_score"] == 80
    repo.close()
