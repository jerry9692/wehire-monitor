"""预算管理器(SRS §8.1 第2层门控 — 预算硬上限)

budget.daily_model_budget_cny 耗尽即停止模型调用,剩余转 need_review。
"""
from __future__ import annotations

from loguru import logger


class BudgetManager:
    """多模态模型预算管理器"""

    def __init__(
        self,
        daily_budget_cny: float = 5.0,
        max_slices_per_article: int = 8,
    ):
        self.daily_budget = daily_budget_cny
        self.max_slices_per_article = max_slices_per_article
        self._spent: float = 0.0
        self._model_calls: int = 0
        self._total_slices: int = 0

    @property
    def remaining(self) -> float:
        """剩余预算"""
        return max(0.0, self.daily_budget - self._spent)

    @property
    def total_model_calls(self) -> int:
        return self._model_calls

    @property
    def total_slices(self) -> int:
        return self._total_slices

    def is_exhausted(self) -> bool:
        """预算是否已耗尽"""
        return self.remaining <= 0

    def slice_limit_reached(self) -> bool:
        """切片数是否已达上限"""
        return self._total_slices >= self.max_slices_per_article

    def can_afford(self, cost: float) -> bool:
        """判断剩余预算是否足够支付"""
        return self.remaining >= cost

    def consume(self, cost: float, slices: int = 1, api_calls: int = 1) -> None:
        """消费预算

        Args:
            cost: 本次消费金额(元)
            slices: 本次处理的切片数
            api_calls: 本次 API 调用次数
        """
        self._spent += cost
        self._model_calls += api_calls
        self._total_slices += slices
        logger.info(
            f"模型预算消费: {cost:.4f} 元 ({slices} 切片), "
            f"累计 {self._spent:.4f}/{self.daily_budget:.2f} 元"
        )

    def reset(self) -> None:
        """重置预算(新的一天)"""
        self._spent = 0.0
        self._model_calls = 0
        self._total_slices = 0
        logger.info("模型预算已重置")

    def summary(self) -> dict:
        """获取预算摘要"""
        return {
            "daily_budget": self.daily_budget,
            "spent": round(self._spent, 4),
            "remaining": round(self.remaining, 4),
            "model_calls": self._model_calls,
            "total_slices": self._total_slices,
        }
