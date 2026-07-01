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

    def is_cookie_stale(self, max_age_hours: float = 48.0) -> bool:
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

    def get_multimodal_config(self) -> dict:
        """获取多模态模型配置"""
        return {
            "provider": os.environ.get("MULTIMODAL_PROVIDER", "mimo").strip(),
            "api_key": os.environ.get("MULTIMODAL_API_KEY", "").strip(),
            "model": os.environ.get("MULTIMODAL_MODEL", "").strip(),
            "base_url": os.environ.get("MULTIMODAL_BASE_URL", "").strip(),
        }

    def update_env_cookie(self, cookie: str, token: str) -> None:
        """将新的 Cookie/Token 写入 .env 文件

        同时更新 COOKIE_UPDATED_AT 为当前时间(Asia/Shanghai)。

        Args:
            cookie: 新的 WECHAT_MP_COOKIE 值
            token: 新的 WECHAT_MP_TOKEN 值
        """
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo("Asia/Shanghai")
        except ImportError:
            tz = timezone(timedelta(hours=8))
        now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        env_path = self.env_path
        lines: list[str] = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()

        updates = {
            "WECHAT_MP_COOKIE": cookie,
            "WECHAT_MP_TOKEN": token,
            "COOKIE_UPDATED_AT": now_str,
        }
        found_keys: set[str] = set()

        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                key = stripped.split("=", 1)[0].strip()
                if key in updates:
                    new_lines.append(f"{key}={updates[key]}")
                    found_keys.add(key)
                    continue
            new_lines.append(line)

        # 添加 .env 中缺失的 KEY
        for key, value in updates.items():
            if key not in found_keys:
                new_lines.append(f"{key}={value}")

        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        logger.info(f"Cookie/Token 已写入 {env_path}")

        # 同步更新当前进程的环境变量
        os.environ["WECHAT_MP_COOKIE"] = cookie
        os.environ["WECHAT_MP_TOKEN"] = token
        os.environ["COOKIE_UPDATED_AT"] = now_str
