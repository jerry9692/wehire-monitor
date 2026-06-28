"""配置加载与校验"""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger

from wehire_monitor.config.schemas import (
    AccountConfig,
    RulesConfig,
    KeywordsConfig,
)

# 默认路径
_DEFAULT_CONFIG_DIR = Path("config")


class ConfigLoader:
    """加载 accounts.yaml / rules.yaml / keywords.yaml / .env"""

    def __init__(
        self,
        config_dir: Path | str | None = None,
        accounts_path: str | None = None,
        rules_path: str | None = None,
        keywords_path: str | None = None,
    ):
        self.config_dir = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
        self.accounts_path = accounts_path or str(self.config_dir / "accounts.yaml")
        self.rules_path = rules_path or str(self.config_dir / "rules.yaml")
        self.keywords_path = keywords_path or str(self.config_dir / "keywords.yaml")
        # 加载 .env
        load_dotenv(self.config_dir / ".env")

    def load_accounts(self) -> list[AccountConfig]:
        with open(self.accounts_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or "accounts" not in data:
            return []
        return [AccountConfig(**a) for a in data["accounts"]]

    def load_rules(self) -> RulesConfig:
        with open(self.rules_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data:
            return RulesConfig()
        return RulesConfig(**data)

    def load_keywords(self) -> KeywordsConfig:
        path = self.keywords_path
        if not Path(path).exists():
            return KeywordsConfig()
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data:
            return KeywordsConfig()
        return KeywordsConfig(**data)

    def is_cookie_stale(self, max_age_hours: float = 24.0) -> bool:
        """检测 Cookie 是否过期(超过 max_age_hours 小时)"""
        updated_str = os.environ.get("COOKIE_UPDATED_AT")
        if not updated_str:
            logger.warning("COOKIE_UPDATED_AT 未设置,视为过期")
            return True
        try:
            updated = datetime.strptime(updated_str, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            logger.warning(f"COOKIE_UPDATED_AT 格式无效: {updated_str}")
            return True
        age = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
        if age > max_age_hours:
            logger.warning(f"Cookie 已过期 {age:.1f}h (上限 {max_age_hours}h)")
            return True
        return False

    def get_cookie(self) -> str:
        return os.environ.get("WECHAT_MP_COOKIE", "")

    def get_token(self) -> str:
        return os.environ.get("WECHAT_MP_TOKEN", "")

    def get_user_agent(self) -> str:
        return os.environ.get(
            "WECHAT_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
