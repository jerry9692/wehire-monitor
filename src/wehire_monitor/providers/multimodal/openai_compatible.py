"""OpenAI 兼容多模态 Provider 基类

封装通过 httpx.Client 调用 OpenAI 兼容 ``/chat/completions`` 接口的公共逻辑:

- HTTP 调用与重试(最多 3 次,指数退避 1s/2s/4s)
- Markdown 代码块去除(```json ... ``` 包裹与前后冗余文字)
- JSON 解析与 ``Job`` 列表构建(带字段容错)
- 图片 base64 编码(data URI 形式注入 messages content 数组)
- 成本估算(input/output token 数 × 单价)

子类只需提供类属性 ``base_url`` / ``model`` / ``input_price`` / ``output_price``,
并通过 ``__init__`` 传入 ``api_key`` 即可获得完整能力。
若需逐切片调用(如 Qwen-VL),可覆写 :meth:`extract_jobs` 并复用本类的
``_call_api`` / ``_parse_jobs_json`` / ``_estimate_cost`` / ``_encode_image`` 等方法。

参考实现: ``providers/llm/deepseek.py``(重试与 JSON 解析)、
``providers/vlm/qwen_vl.py``(图片处理)。
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from wehire_monitor.domain.models import Deadline, Job
from wehire_monitor.providers.multimodal.base import (
    MultimodalProvider,
    MultimodalResponse,
)

# Prompt 模板路径: providers/prompts/multimodal.txt
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "multimodal.txt"

_MAX_ATTEMPTS = 3          # 最大重试次数
_HTTP_TIMEOUT = 120.0      # 图片上传较慢,统一 120s 超时
_MAX_TEXT_CHARS = 8000     # 正文截断长度,防止超长

# Markdown 代码块正则(支持前后有其他文字)
_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)

_VALID_ARTICLE_TYPES = (
    "social_recruitment",
    "campus_recruitment",
    "internship",
    "non_recruitment",
    "unknown",
)

# 兜底 prompt(当 providers/prompts/multimodal.txt 不存在时使用)
_FALLBACK_PROMPT = """请从以下微信公众号招聘文章中提取招聘岗位信息。文章可能以纯文本、纯图片或文本+图片形式提供。

抽取字段：
- company_name: 公司名称
- job_name: 岗位名称
- location: 工作地点
- apply_channel: 投递邮箱、报名链接、二维码说明或其他投递方式
- email: 邮箱;必须逐字符准确输出
- email_chars: 邮箱字符数组,例如 ["h","r","@","x",".","c","o","m"]
- deadline: 截止日期,统一 YYYY-MM-DD;无明确年份时结合文章发布时间推断,并标记 inferred=true
- source_evidence: 每个字段对应的原文短证据
- confidence: 0-100

要求：
1. 一篇文章可能包含多个岗位,输出 jobs 数组。
2. 不抽取校招、实习、宣讲会、培训类信息。
3. 如果是报名链接或二维码,没有邮箱,也要保留 apply_channel。
4. 邮箱必须逐字符准确识别;无法确认时 email=null,但在 apply_channel 中保留原始片段。
5. 不要根据常识补全公司名、地点或截止日期;只抽取明确出现的信息。
6. 返回严格 JSON,不要 Markdown。

JSON Schema:
{
  "article_type": "social_recruitment | campus_recruitment | internship | non_recruitment | unknown",
  "jobs": [
    {
      "company_name": "string|null",
      "job_name": "string|null",
      "location": "string|null",
      "apply_channel": "string|null",
      "email": "string|null",
      "email_chars": ["string"],
      "deadline": {"date": "YYYY-MM-DD|null", "inferred": false},
      "source_evidence": {},
      "confidence": 0
    }
  ],
  "warnings": []
}

文章发布时间:{{publish_time}}
文章标题:{{title}}
"""

# 系统提示(所有具体 Provider 共用)
_DEFAULT_SYSTEM_PROMPT = (
    "你是招聘信息结构化抽取助手。只从用户提供的正文或图片中抽取明确出现的信息,"
    "不要猜测、不要补全、不要编造。如果字段不存在,输出 null。"
    "邮箱必须逐字符输出,并额外输出 email_chars 数组。"
    "返回严格 JSON,不要 Markdown。"
)


class OpenAICompatibleProvider(MultimodalProvider):
    """OpenAI 兼容多模态 Provider 基类

    子类需设置以下类属性:

    - ``name``:         Provider 名称(如 "mimo"、"qwen_vl")
    - ``base_url``:     OpenAI 兼容 ``/chat/completions`` 端点
    - ``model``:        默认模型名
    - ``input_price``:  输入单价(元/百万 token)
    - ``output_price``: 输出单价(元/百万 token)

    并通过 ``__init__(api_key, model=None)`` 传入 API Key,可选覆盖模型名。
    """

    name: str = "openai_compatible"
    base_url: str = ""
    model: str = ""
    input_price: float = 0.0
    output_price: float = 0.0

    # 子类可覆盖系统提示
    system_prompt: str = _DEFAULT_SYSTEM_PROMPT

    def __init__(self, api_key: str, model: str | None = None, base_url: str | None = None) -> None:
        if model:
            self.model = model
        if base_url:
            self.base_url = base_url
        # 自动补全 /chat/completions 后缀(兼容传入 base URL 和完整 endpoint 两种方式)
        if self.base_url and not self.base_url.rstrip("/").endswith("/chat/completions"):
            self.base_url = self.base_url.rstrip("/") + "/chat/completions"
        if not self.model:
            raise ValueError(f"{type(self).__name__} 未配置 model")
        if not self.base_url:
            raise ValueError(f"{type(self).__name__} 未配置 base_url")

        self._api_key = api_key
        self._client = httpx.Client(
            timeout=_HTTP_TIMEOUT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        self._prompt_template = self._load_prompt()

    # ------------------------------------------------------------------
    # Prompt 加载与渲染
    # ------------------------------------------------------------------
    @staticmethod
    def _load_prompt() -> str:
        """加载 prompt 模板,不存在则使用内联兜底 prompt"""
        if _PROMPT_PATH.exists():
            return _PROMPT_PATH.read_text(encoding="utf-8")
        logger.warning(f"多模态 Prompt 模板不存在: {_PROMPT_PATH}, 使用内联兜底 prompt")
        return _FALLBACK_PROMPT

    def _render_prompt(self, title: str, publish_time: str) -> str:
        """渲染 prompt 模板,替换 {{title}} / {{publish_time}} 占位符"""
        template = self._prompt_template or _FALLBACK_PROMPT
        return (
            template
            .replace("{{title}}", str(title))
            .replace("{{publish_time}}", str(publish_time or ""))
        )

    # ------------------------------------------------------------------
    # 图片处理
    # ------------------------------------------------------------------
    @staticmethod
    def _encode_image(image: Any) -> str:
        """将图片对象(ImageSlice / 含 local_path 的对象)转为 base64 data URI

        Returns:
            ``data:image/<mime>;base64,<b64>`` 字符串;文件不存在或读取失败时返回 ""。
        """
        local_path = getattr(image, "local_path", "")
        if not local_path or not Path(local_path).exists():
            logger.warning(f"切片文件不存在: {local_path}")
            return ""
        suffix = Path(local_path).suffix.lower().lstrip(".") or "png"
        if suffix in ("jpg", "jpeg"):
            mime = "jpeg"
        elif suffix in ("png", "gif", "webp", "bmp"):
            mime = suffix
        else:
            mime = "png"
        try:
            with open(local_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError as e:
            logger.warning(f"切片文件读取失败: {local_path}: {e}")
            return ""
        return f"data:image/{mime};base64,{b64}"

    # ------------------------------------------------------------------
    # 消息构建
    # ------------------------------------------------------------------
    def _wrap_messages(self, user_content: list[dict]) -> list[dict]:
        """用系统提示 + 用户 content 数组组装完整 messages"""
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]

    def _build_messages(
        self,
        text: str | None,
        images: list,
        title: str,
        publish_time: str,
    ) -> list[dict]:
        """构建 OpenAI 兼容 messages(content 为多模态数组)

        组装规则:
        - 纯文本文章(images 为空): content 仅含 prompt 文本 + 正文文本
        - 纯图片文章(text 为空): content 仅含 prompt 文本 + 图片
        - 文本 + 图片: prompt 文本在前,正文文本次之,图片最后
        """
        content: list[dict] = []
        # 1) 提取指令 prompt(含标题与发布时间)
        content.append({"type": "text", "text": self._render_prompt(title, publish_time)})

        # 2) 文章正文(若有)
        body_text = (text or "").strip()
        if body_text:
            content.append({
                "type": "text",
                "text": f"文章正文:\n{body_text[:_MAX_TEXT_CHARS]}",
            })

        # 3) 图片(若有),以 base64 data URI 形式注入
        for img in images:
            data_uri = self._encode_image(img)
            if data_uri:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                })

        return self._wrap_messages(content)

    # ------------------------------------------------------------------
    # HTTP 调用与重试
    # ------------------------------------------------------------------
    def _call_api(self, messages: list[dict]) -> tuple[str, dict]:
        """发起单次 chat/completions 请求(带重试)

        Args:
            messages: OpenAI 兼容 messages 数组

        Returns:
            ``(content, usage)`` 元组 —— content 为模型回复文本,
            usage 为 token 用量字典(含 prompt_tokens / completion_tokens)。

        Raises:
            RuntimeError: 重试耗尽仍失败。
        """
        last_error = ""
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = self._client.post(
                    self.base_url,
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.1,
                        "max_tokens": 4096,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                if content is None:
                    content = ""
                usage = data.get("usage", {}) or {}
                return content, usage
            except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.HTTPError) as e:
                last_error = f"API error: {e}"
                logger.warning(
                    f"[{self.name}] HTTP 错误 (attempt {attempt + 1}/{_MAX_ATTEMPTS}): {e}"
                )
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                last_error = f"response format error: {e}"
                logger.warning(
                    f"[{self.name}] 响应格式错误 (attempt {attempt + 1}/{_MAX_ATTEMPTS}): {e}"
                )

            # 指数退避(最后一次不等待): 1s / 2s / 4s
            if attempt < _MAX_ATTEMPTS - 1:
                wait = 2 ** attempt
                time.sleep(wait)

        raise RuntimeError(f"max retries exceeded: {last_error}")

    # ------------------------------------------------------------------
    # 成本估算
    # ------------------------------------------------------------------
    def _estimate_cost(self, usage: dict) -> float:
        """根据 input/output token 数与单价估算成本(元)

        成本 = prompt_tokens / 1e6 * input_price
             + completion_tokens / 1e6 * output_price
        """
        input_tokens = int(usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(usage.get("completion_tokens", 0) or 0)
        return (
            input_tokens / 1_000_000 * self.input_price
            + output_tokens / 1_000_000 * self.output_price
        )

    # ------------------------------------------------------------------
    # JSON 解析与 Job 构建
    # ------------------------------------------------------------------
    @staticmethod
    def _strip_code_blocks(content: str) -> str:
        """去除 Markdown 代码块包裹与前后冗余文字,返回纯 JSON 文本"""
        stripped = content.strip()

        # 优先用正则提取 ```json...``` 或 ```...``` 代码块
        m = _CODE_BLOCK_RE.search(stripped)
        if m:
            return m.group(1).strip()
        if stripped.startswith("```"):
            # 兼容未匹配到闭合 ``` 的情况
            lines = stripped.split("\n")
            if lines and lines[-1].startswith("```"):
                return "\n".join(lines[1:-1]).strip()
            return "\n".join(lines[1:]).strip()
        return stripped

    def _parse_jobs_json(self, content: str) -> MultimodalResponse:
        """解析模型返回的 JSON,构建 Job 列表(不计成本/调用次数)

        成本与 model_calls 由调用方(:meth:`_invoke` / :meth:`extract_jobs`)填充。
        """
        stripped = self._strip_code_blocks(content)

        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            # 可能前后有冗余文字,尝试提取第一个完整 JSON 对象
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(stripped[start:end + 1])
                except json.JSONDecodeError as e:
                    logger.warning(f"[{self.name}] JSON 解析失败: {e}")
                    return MultimodalResponse(
                        success=False, error=f"JSON parse error: {e}"
                    )
            else:
                logger.warning(f"[{self.name}] 返回中未找到 JSON 对象")
                return MultimodalResponse(
                    success=False, error="no JSON object found in response"
                )

        jobs: list[Job] = self._build_jobs(data.get("jobs", []))

        article_type = data.get("article_type", "unknown")
        if article_type not in _VALID_ARTICLE_TYPES:
            article_type = "unknown"

        warnings_raw = data.get("warnings")
        warnings = (
            [str(w) for w in warnings_raw]
            if isinstance(warnings_raw, list) else []
        )

        return MultimodalResponse(
            success=True,
            article_type=article_type,
            jobs=jobs,
            warnings=warnings,
        )

    @staticmethod
    def _build_jobs(jobs_raw: Any) -> list[Job]:
        """从原始 JSON 构建 Job 列表(对 deadline / email_chars / confidence 容错)"""
        if not isinstance(jobs_raw, list):
            return []

        jobs: list[Job] = []
        for j in jobs_raw:
            if not isinstance(j, dict):
                continue

            # deadline 容错: 可能是字符串或对象
            deadline_raw = j.get("deadline")
            if isinstance(deadline_raw, str):
                deadline_data = {"date": deadline_raw, "inferred": False}
            elif isinstance(deadline_raw, dict):
                deadline_data = deadline_raw
            else:
                deadline_data = {}

            # email_chars 容错: 可能是字符串
            email_chars_raw = j.get("email_chars")
            if isinstance(email_chars_raw, str):
                email_chars = list(email_chars_raw)
            elif isinstance(email_chars_raw, list):
                email_chars = [str(c) for c in email_chars_raw]
            else:
                email_chars = []

            # confidence 容错: 可能是 0-1 浮点
            conf_raw = j.get("confidence", 0)
            try:
                conf_val = float(conf_raw) if conf_raw is not None else 0
                if 0 < conf_val <= 1:
                    confidence = int(conf_val * 100)
                else:
                    confidence = int(conf_val)
                confidence = max(0, min(100, confidence))
            except (TypeError, ValueError):
                confidence = 0

            source_evidence = j.get("source_evidence")
            jobs.append(Job(
                company_name=j.get("company_name"),
                job_name=j.get("job_name"),
                location=j.get("location"),
                apply_channel=j.get("apply_channel"),
                email=j.get("email"),
                email_chars=email_chars,
                deadline=Deadline(
                    date=deadline_data.get("date"),
                    inferred=bool(deadline_data.get("inferred", False)),
                ),
                source_evidence=source_evidence if isinstance(source_evidence, dict) else {},
                confidence=confidence,
            ))
        return jobs

    # ------------------------------------------------------------------
    # 单次调用封装
    # ------------------------------------------------------------------
    def _invoke(self, messages: list[dict]) -> MultimodalResponse:
        """发起单次 API 调用并解析,返回带成本与调用次数的响应"""
        try:
            content, usage = self._call_api(messages)
        except RuntimeError as e:
            return MultimodalResponse(success=False, error=str(e))

        response = self._parse_jobs_json(content)
        if not response.success:
            return response

        response.cost_estimate = self._estimate_cost(usage)
        response.model_calls = 1
        return response

    # ------------------------------------------------------------------
    # 统一提取接口(单次调用:文本 + 图片同发)
    # ------------------------------------------------------------------
    def extract_jobs(
        self,
        text: str | None,
        images: list,
        title: str,
        publish_time: str,
    ) -> MultimodalResponse:
        """从文本和/或图片中提取岗位信息(单次调用,文本与图片同发)

        适用于支持多图单次调用的多模态模型(如 MiMo-V2.5)。
        逐切片调用的实现(如 Qwen-VL)请覆写本方法。
        """
        if not text and not images:
            return MultimodalResponse(
                success=False, error="no text and no images provided"
            )

        messages = self._build_messages(text, images, title, publish_time)
        return self._invoke(messages)

    # ------------------------------------------------------------------
    # 资源释放
    # ------------------------------------------------------------------
    def close(self) -> None:
        """关闭 httpx.Client,释放连接资源"""
        self._client.close()

    def __enter__(self) -> "OpenAICompatibleProvider":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
