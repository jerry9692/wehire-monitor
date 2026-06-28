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

# 默认配置目录(基于项目根目录的绝对路径)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"


class ConfigLoader:
    """加载 accounts.yaml / rules.yaml / keywords.yaml / .env"""

    def __init__(
        self,
        config_dir: Path | str | None = None,
        accounts_path: str | None = None,
        rules_path: str | None = None,
        keywords_path: str | None = None,
    ):
        self.config_dir = Path(config_dir).resolve() if config_dir else _DEFAULT_CONFIG_DIR
        self.accounts_path = accounts_path or str(self.config_dir / "accounts.yaml")
        self.rules_path = rules_path or str(self.config_dir / "rules.yaml")
        self.keywords_path = keywords_path or str(self.config_dir / "keywords.yaml")
        self.env_path = self.config_dir / ".env"
        # 加载 .env(项目根目录也尝试加载)
        load_dotenv(self.env_path)
        load_dotenv(_PROJECT_ROOT / ".env", override=False)

    def load_accounts(self) -> list[AccountConfig]:
        with open(self.accounts_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or "accounts" not in data:
            return []
        return [AccountConfig(**a) for a in data["accounts"]]

    def load_rules(self) -> RulesConfig:
        if not Path(self.rules_path).exists():
            return RulesConfig()
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
        """检测 Cookie 是否过期(超过 max_age_hours 小时)

        COOKIE_UPDATED_AT 支持以下格式(均按 Asia/Shanghai 解释,除非显式带时区):
        - ISO8601 带时区: 2026-06-28T08:00:00+08:00
        - ISO8601 带Z: 2026-06-28T00:00:00Z
        - ISO8601 无时区: 2026-06-28T08:00:00 → 按 Asia/Shanghai
        - 本地时间格式: 2026-06-28 08:00:00 → 按 Asia/Shanghai
        """
        updated_str = os.environ.get("COOKIE_UPDATED_AT")
        if not updated_str:
            logger.warning("COOKIE_UPDATED_AT 未设置,视为过期")
            return True
        try:
            # 统一用 fromisoformat 解析(Python 3.11+ 支持 Z 后缀)
            normalized = updated_str.replace("Z", "+00:00")
            updated = datetime.fromisoformat(normalized)
            # 无时区信息 → 按 Asia/Shanghai 解释
            if updated.tzinfo is None:
                try:
                    from zoneinfo import ZoneInfo
                    updated = updated.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                except ImportError:
                    updated = updated.replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            logger.warning(f"COOKIE_UPDATED_AT 格式无效: {updated_str}")
            return True
        now = datetime.now(timezone.utc)
        age = (now - updated.astimezone(timezone.utc)).total_seconds() / 3600
        if age > max_age_hours:
            logger.warning(f"Cookie 已过期 {age:.1f}h (上限 {max_age_hours}h)")
            return True
        return False

    def validate_required_config(self) -> list[str]:
        """验证必要配置项是否存在,返回缺失项列表"""
        missing = []
        if not self.get_cookie():
            missing.append("WECHAT_MP_COOKIE")
        if not self.get_token():
            missing.append("WECHAT_MP_TOKEN")
        return missing

    def get_cookie(self) -> str:
        return os.environ.get("WECHAT_MP_COOKIE", "").strip()

    def get_token(self) -> str:
        return os.environ.get("WECHAT_MP_TOKEN", "").strip()

    def get_user_agent(self) -> str:
        return os.environ.get(
            "WECHAT_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        ).strip()

    def get_feishu_webhook(self) -> str:
        return os.environ.get("FEISHU_WEBHOOK", "").strip()

    def get_dingtalk_webhook(self) -> str:
        return os.environ.get("DINGTALK_WEBHOOK", "").strip()

    def get_llm_config(self) -> dict:
        return {
            "provider": os.environ.get("LLM_PROVIDER", ""),
            "api_key": os.environ.get("LLM_API_KEY", ""),
            "model": os.environ.get("LLM_MODEL", ""),
        }

    def get_vlm_config(self) -> dict:
        return {
            "provider": os.environ.get("VLM_PROVIDER", ""),
            "api_key": os.environ.get("VLM_API_KEY", ""),
            "model": os.environ.get("VLM_MODEL", ""),
        }

    def get_ocr_provider(self) -> str:
        return os.environ.get("OCR_PROVIDER", "").strip()
