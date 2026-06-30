"""预算管理器测试"""
from wehire_monitor.modules.extractor.budget import BudgetManager


def test_budget_initial():
    """初始预算正确"""
    bm = BudgetManager(daily_budget_cny=5.0, max_slices_per_article=8)
    assert bm.daily_budget == 5.0
    assert bm.max_slices_per_article == 8
    assert bm.remaining == 5.0
    assert bm.total_model_calls == 0
    assert bm.total_slices == 0
    assert bm.is_exhausted() is False
    assert bm.slice_limit_reached() is False


def test_budget_consume():
    """消费后余额减少"""
    bm = BudgetManager(daily_budget_cny=5.0, max_slices_per_article=8)
    bm.consume(1.0, slices=2)
    assert bm.remaining == 4.0
    assert bm.total_model_calls == 1
    assert bm.total_slices == 2

    bm.consume(0.5, slices=1)
    assert bm.remaining == 3.5
    assert bm.total_model_calls == 2
    assert bm.total_slices == 3


def test_budget_exhausted():
    """预算耗尽"""
    bm = BudgetManager(daily_budget_cny=5.0, max_slices_per_article=8)
    bm.consume(5.0)
    assert bm.is_exhausted() is True
    assert bm.remaining == 0.0
    assert bm.can_afford(0.01) is False
    assert bm.can_afford(0.0) is True

    # 超额消费后剩余钳制为 0
    bm.consume(1.0)
    assert bm.remaining == 0.0
    assert bm.is_exhausted() is True


def test_budget_can_afford():
    """can_afford 判断"""
    bm = BudgetManager(daily_budget_cny=5.0, max_slices_per_article=8)
    assert bm.can_afford(5.0) is True
    assert bm.can_afford(5.01) is False
    assert bm.can_afford(2.5) is True

    bm.consume(3.0)
    assert bm.can_afford(2.0) is True
    assert bm.can_afford(2.1) is False


def test_budget_max_slices():
    """切片数上限"""
    bm = BudgetManager(daily_budget_cny=100.0, max_slices_per_article=3)
    assert bm.slice_limit_reached() is False
    bm.consume(0.1, slices=1)
    bm.consume(0.1, slices=1)
    assert bm.slice_limit_reached() is False
    bm.consume(0.1, slices=1)
    assert bm.slice_limit_reached() is True
    assert bm.total_slices == 3


def test_budget_reset():
    """重置后恢复"""
    bm = BudgetManager(daily_budget_cny=5.0, max_slices_per_article=8)
    bm.consume(2.0, slices=2)
    assert bm.remaining == 3.0
    assert bm.total_model_calls == 1
    assert bm.total_slices == 2

    bm.reset()
    assert bm.remaining == 5.0
    assert bm.total_model_calls == 0
    assert bm.total_slices == 0
    assert bm.is_exhausted() is False
    assert bm.slice_limit_reached() is False


def test_budget_summary():
    """摘要正确"""
    bm = BudgetManager(daily_budget_cny=5.0, max_slices_per_article=8)
    bm.consume(1.234, slices=2)
    summary = bm.summary()
    assert summary["daily_budget"] == 5.0
    assert summary["spent"] == 1.234
    assert summary["remaining"] == round(5.0 - 1.234, 4)
    assert summary["model_calls"] == 1
    assert summary["total_slices"] == 2
