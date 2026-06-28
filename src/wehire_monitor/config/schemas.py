"""配置 pydantic 模型"""
from pydantic import BaseModel, Field


class AccountConfig(BaseModel):
    """公众号订阅配置"""
    name: str
    alias: list[str] = Field(default_factory=list)
    priority: str = "medium"      # high | medium | low
    enabled: bool = True


class LocationRules(BaseModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class JobKeywordRules(BaseModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class CompanyRules(BaseModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class MatchRules(BaseModel):
    locations: LocationRules = Field(default_factory=LocationRules)
    job_keywords: JobKeywordRules = Field(default_factory=JobKeywordRules)
    companies: CompanyRules = Field(default_factory=CompanyRules)
    notify_min_score: int = 70


class NotifyConfig(BaseModel):
    max_per_run: int = 20
    push_when_empty: bool = False
    email_mask: bool = True


class ScheduleConfig(BaseModel):
    daily_at: list[str] = ["08:30", "20:30"]
    window_hours: int = 36
    max_articles_per_run: int = 80
    max_articles_per_account: int = 10


class RulesConfig(BaseModel):
    """rules.yaml 完整配置"""
    match_rules: MatchRules = Field(default_factory=MatchRules)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)


class KeywordsConfig(BaseModel):
    """keywords.yaml 预过滤词库"""
    strong_hit: list[str] = []
    strong_exclude: list[str] = []
