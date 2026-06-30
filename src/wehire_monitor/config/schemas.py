"""配置 pydantic 模型"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AccountConfig(BaseModel):
    """公众号订阅配置"""
    model_config = ConfigDict(extra="forbid")

    name: str
    alias: list[str] = Field(default_factory=list)
    priority: Literal["high", "medium", "low"] = "medium"
    enabled: bool = True


class LocationRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class JobKeywordRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class CompanyRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class MatchRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locations: LocationRules = Field(default_factory=LocationRules)
    job_keywords: JobKeywordRules = Field(default_factory=JobKeywordRules)
    companies: CompanyRules = Field(default_factory=CompanyRules)
    notify_min_score: int = 70


class NotifyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_per_run: int = 20
    push_when_empty: bool = False
    email_mask: bool = True


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_at: list[str] = ["08:30", "20:30"]
    window_hours: int = 36
    max_articles_per_run: int = 80
    max_articles_per_account: int = 10


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_model_budget_cny: float = 5.0
    max_slices_per_article: int = 8


class RulesConfig(BaseModel):
    """rules.yaml 完整配置"""
    model_config = ConfigDict(extra="forbid")
    match_rules: MatchRules = Field(default_factory=MatchRules)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)


class KeywordsConfig(BaseModel):
    """keywords.yaml 预过滤词库"""
    model_config = ConfigDict(extra="forbid")
    strong_hit: list[str] = Field(default_factory=list)
    strong_exclude: list[str] = Field(default_factory=list)
