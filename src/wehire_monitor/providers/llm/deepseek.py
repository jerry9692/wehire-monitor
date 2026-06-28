"""DeepSeek LLM 实现(OpenAI 兼容 API)"""
from __future__ import annotations
import json
from pathlib import Path

import httpx
from loguru import logger

from wehire_monitor.domain.models import Job, Deadline
from wehire_monitor.providers.llm.base import LLMProvider, LLMResponse

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "text_llm.txt"
_API_URL = "https://api.deepseek.com/v1/chat/completions"


class DeepSeekProvider:
    """DeepSeek API 实现"""

    name = "deepseek"

    def __init__(self, api_key: str, model: str = "deepseek-chat"):
        self.model = model
        self._api_key = api_key
        self._client = httpx.Client(
            timeout=60.0,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._prompt_template = self._load_prompt()

    def _load_prompt(self) -> str:
        if _PROMPT_PATH.exists():
            return _PROMPT_PATH.read_text(encoding="utf-8")
        logger.warning(f"Prompt 模板不存在: {_PROMPT_PATH}")
        return ""

    def extract_jobs(
        self, text: str, title: str, publish_time: str
    ) -> LLMResponse:
        """调用 DeepSeek 提取岗位信息,JSON 解析失败重试 1 次"""
        user_content = self._render_prompt(text, title, publish_time)
        system_prompt = (
            "你是招聘信息结构化抽取助手。只从用户提供的正文中抽取明确出现的信息,"
            "不要猜测、不要补全、不要编造。如果字段不存在,输出 null。"
            "邮箱必须逐字符输出,并额外输出 email_chars 数组。"
            "返回严格 JSON,不要 Markdown。"
        )

        for attempt in range(2):
            try:
                resp = self._client.post(_API_URL, json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 4096,
                })
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                response = self._parse_response(content)
                # JSON 解析成功或已用完重试次数则返回;否则重试
                if response.success or attempt == 1:
                    return response
                logger.warning(
                    f"DeepSeek JSON 解析失败,准备重试 (attempt {attempt + 1}/2)"
                )
            except httpx.HTTPStatusError as e:
                logger.error(f"DeepSeek API 错误 (attempt {attempt + 1}): {e}")
                return LLMResponse(success=False, error=f"API error: {e}")
            except (KeyError, IndexError) as e:
                logger.error(f"DeepSeek 响应格式错误: {e}")
                return LLMResponse(success=False, error=f"response format error: {e}")
            except Exception as e:
                logger.error(f"DeepSeek 调用异常 (attempt {attempt + 1}): {e}")
                if attempt == 1:
                    return LLMResponse(success=False, error=str(e))

        return LLMResponse(success=False, error="max retries exceeded")

    def _render_prompt(self, text: str, title: str, publish_time: str) -> str:
        template = self._prompt_template
        if not template:
            # 兜底:内联 Prompt
            template = (
                "请从以下微信公众号招聘文章中提取招聘岗位信息。\n\n"
                "文章发布时间:{{publish_time}}\n文章标题:{{title}}\n正文:\n{{content}}"
            )
        return (
            template
            .replace("{{publish_time}}", publish_time)
            .replace("{{title}}", title)
            .replace("{{content}}", text[:4000])  # 截断防止超长
        )

    def _parse_response(self, content: str) -> LLMResponse:
        """解析 LLM 返回的 JSON"""
        # 去除可能的 Markdown 代码块包裹
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.split("\n")
            stripped = (
                "\n".join(lines[1:-1]) if lines[-1].startswith("```")
                else "\n".join(lines[1:])
            )

        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            logger.warning(f"LLM 返回 JSON 解析失败: {e}")
            return LLMResponse(
                success=False, error=f"JSON parse error: {e}", raw_content=content
            )

        jobs: list[Job] = []
        for j in data.get("jobs", []):
            deadline_data = j.get("deadline") or {}
            jobs.append(Job(
                company_name=j.get("company_name"),
                job_name=j.get("job_name"),
                location=j.get("location"),
                apply_channel=j.get("apply_channel"),
                email=j.get("email"),
                email_chars=j.get("email_chars") or [],
                deadline=Deadline(
                    date=deadline_data.get("date"),
                    inferred=deadline_data.get("inferred", False),
                ),
                source_evidence=j.get("source_evidence") or {},
                confidence=int(j.get("confidence", 0)),
            ))

        return LLMResponse(
            success=True,
            article_type=data.get("article_type", "unknown"),
            jobs=jobs,
            warnings=data.get("warnings") or [],
            raw_content=content,
        )

    def close(self) -> None:
        self._client.close()
