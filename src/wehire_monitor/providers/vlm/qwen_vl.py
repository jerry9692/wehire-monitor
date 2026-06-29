"""Qwen-VL Provider 实现(DashScope OpenAI 兼容接口)

通过 DashScope 的 OpenAI 兼容模式调用 qwen-vl-max,逐切片识别招聘长图。
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

import httpx
from loguru import logger

from wehire_monitor.domain.models import Job, Deadline, ImageSlice
from wehire_monitor.providers.vlm.base import VLMResponse

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "vlm.txt"
_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
_COST_PER_SLICE = 0.03
_MAX_ATTEMPTS = 3
# Markdown 代码块正则(支持前后有其他文字)
_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


class QwenVLProvider:
    """Qwen-VL API 实现(qwen-vl-max)"""

    name = "qwen_vl"

    def __init__(self, api_key: str, model: str = "qwen-vl-max"):
        self.model = model
        self._api_key = api_key
        self._client = httpx.Client(
            timeout=120.0,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._prompt_template = self._load_prompt()

    def _load_prompt(self) -> str:
        if _PROMPT_PATH.exists():
            return _PROMPT_PATH.read_text(encoding="utf-8")
        logger.warning(f"VLM Prompt 模板不存在: {_PROMPT_PATH}")
        return ""

    def extract_jobs_from_slices(
        self,
        slices: list,
        title: str,
        publish_time: str,
    ) -> VLMResponse:
        """从图片切片中提取岗位信息(每切片调用一次 VLM)

        对每个切片独立调用 VLM 并带重试,汇总全部岗位与告警。
        只要有一个切片提取成功即视为整体成功;全部失败时返回失败。
        成本仅在实际发起 API 请求时累加。
        """
        if not slices:
            return VLMResponse(success=False, error="no slices provided")

        all_jobs: list[Job] = []
        all_warnings: list[str] = []
        article_type = "unknown"
        total_cost = 0.0
        success_count = 0
        last_error = ""

        for sl in slices:
            meta = self._slice_meta(sl)
            response = self._extract_single_slice(
                sl,
                title,
                meta["image_index"],
                meta["slice_index"],
                meta["y_start"],
                meta["y_end"],
                meta["is_bottom"],
                publish_time,
            )

            # 仅在实际调用 API 时累加成本
            # - 成功提取 → 计费
            # - 失败但非图片读取错误(API 已调用)→ 计费
            # - 图片无法读取(未调用 API)→ 不计费
            called_api = response.success or (
                response.error and "cannot read image" not in response.error
            )
            if called_api:
                total_cost += _COST_PER_SLICE

            if response.success:
                success_count += 1
                all_jobs.extend(response.jobs)
                all_warnings.extend(response.warnings)
                # article_type 取首个非 unknown 的值,后续切片有更具体类型时覆盖
                if response.article_type and response.article_type != "unknown":
                    if article_type == "unknown" or response.article_type != "unknown":
                        article_type = response.article_type
            else:
                last_error = response.error or "unknown error"
                all_warnings.append(
                    f"slice {meta['image_index']}-{meta['slice_index']} 提取失败: {last_error}"
                )

        return VLMResponse(
            success=success_count > 0,
            article_type=article_type,
            jobs=all_jobs,
            warnings=all_warnings,
            cost_estimate=total_cost,
            error="" if success_count > 0 else last_error,
        )

    def _extract_single_slice(
        self,
        slice_obj: ImageSlice,
        title: str,
        image_index: int,
        slice_index: int,
        y_start: int,
        y_end: int,
        is_bottom: bool,
        publish_time: str,
    ) -> VLMResponse:
        """对单个切片调用 VLM,支持 HTTP/格式错误重试(最多3次,指数退避)"""
        user_text = self._render_prompt(
            title, image_index, slice_index, y_start, y_end, is_bottom, publish_time,
        )
        image_url = self._get_image_url(slice_obj)
        if not image_url:
            local_path = getattr(slice_obj, "local_path", "")
            return VLMResponse(
                success=False,
                error=f"cannot read image: {local_path}",
            )

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ]

        last_error = ""
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = self._client.post(_API_URL, json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.1,
                })
                resp.raise_for_status()
                data = resp.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                if content is None:
                    content = ""
                response = self._parse_response(content)
                if response.success:
                    return response
                last_error = response.error or "JSON parse error"
                logger.warning(
                    f"Qwen-VL 响应解析失败 (attempt {attempt + 1}/{_MAX_ATTEMPTS}): {last_error}"
                )
            except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.HTTPError) as e:
                last_error = f"API error: {e}"
                logger.warning(
                    f"Qwen-VL HTTP 错误 (attempt {attempt + 1}/{_MAX_ATTEMPTS}): {e}"
                )
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                last_error = f"response format error: {e}"
                logger.warning(
                    f"Qwen-VL 响应格式错误 (attempt {attempt + 1}/{_MAX_ATTEMPTS}): {e}"
                )

            # 退避等待(最后一次不等待)
            if attempt < _MAX_ATTEMPTS - 1:
                wait = 2 ** attempt
                time.sleep(wait)

        return VLMResponse(
            success=False, error=f"max retries exceeded: {last_error}"
        )

    def _render_prompt(
        self,
        title: str,
        image_index: int,
        slice_index: int,
        y_start: int,
        y_end: int,
        is_bottom: bool,
        publish_time: str,
    ) -> str:
        """渲染 prompt 模板,替换占位符"""
        template = self._prompt_template
        if not template:
            # 兜底:内联 Prompt
            template = (
                "请识别图片中的招聘信息,返回严格 JSON。\n"
                "article_title={{title}}\n"
                "publish_time={{publish_time}}\n"
                "image_index={{image_index}}\n"
                "slice_index={{slice_index}}\n"
                "y_range={{y_start}}-{{y_end}}\n"
                "is_bottom={{is_bottom}}\n"
            )
        return (
            template
            .replace("{{title}}", str(title))
            .replace("{{publish_time}}", str(publish_time or ""))
            .replace("{{image_index}}", str(image_index))
            .replace("{{slice_index}}", str(slice_index))
            .replace("{{y_start}}", str(y_start))
            .replace("{{y_end}}", str(y_end))
            .replace("{{is_bottom}}", "true" if is_bottom else "false")
        )

    def _parse_response(self, content: str) -> VLMResponse:
        """解析 VLM 返回的 JSON,处理 ```json...``` markdown 包裹和前后冗余文字"""
        stripped = content.strip()

        # 优先用正则提取 ```json...``` 或 ```...``` 代码块
        m = _CODE_BLOCK_RE.search(stripped)
        if m:
            stripped = m.group(1).strip()
        elif stripped.startswith("```"):
            # 兼容未匹配到闭合 ``` 的情况
            lines = stripped.split("\n")
            if lines and lines[-1].startswith("```"):
                stripped = "\n".join(lines[1:-1])
            else:
                stripped = "\n".join(lines[1:])

        # 尝试找到 JSON 对象的起止位置
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
                    logger.warning(f"VLM 返回 JSON 解析失败: {e}")
                    return VLMResponse(
                        success=False, error=f"JSON parse error: {e}"
                    )
            else:
                logger.warning("VLM 返回中未找到 JSON 对象")
                return VLMResponse(
                    success=False, error="no JSON object found in response"
                )

        jobs: list[Job] = []
        jobs_raw = data.get("jobs", [])
        if not isinstance(jobs_raw, list):
            jobs_raw = []

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
                # 0-1 浮点 → 0-100
                if 0 < conf_val <= 1:
                    confidence = int(conf_val * 100)
                else:
                    confidence = int(conf_val)
                confidence = max(0, min(100, confidence))
            except (TypeError, ValueError):
                confidence = 0

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
                source_evidence=j.get("source_evidence") if isinstance(j.get("source_evidence"), dict) else {},
                confidence=confidence,
            ))

        article_type = data.get("article_type", "unknown")
        if article_type not in (
            "social_recruitment", "campus_recruitment",
            "internship", "non_recruitment", "unknown",
        ):
            article_type = "unknown"

        warnings_raw = data.get("warnings")
        warnings = [str(w) for w in warnings_raw] if isinstance(warnings_raw, list) else []

        return VLMResponse(
            success=True,
            article_type=article_type,
            jobs=jobs,
            warnings=warnings,
            cost_estimate=0.0,
        )

    def _get_image_url(self, slice_obj: ImageSlice) -> str:
        """将本地切片文件转为 base64 data URL(使用 with 确保文件关闭)"""
        local_path = getattr(slice_obj, "local_path", "")
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

    @staticmethod
    def _slice_meta(slice_obj) -> dict:
        """从切片对象提取元信息"""
        meta_obj = getattr(slice_obj, "meta", None)
        if meta_obj is not None:
            return {
                "image_index": getattr(meta_obj, "image_index", 0),
                "slice_index": getattr(meta_obj, "slice_index", 0),
                "y_start": getattr(meta_obj, "y_start", 0),
                "y_end": getattr(meta_obj, "y_end", 0),
                "is_bottom": getattr(meta_obj, "is_bottom", False),
                "local_path": getattr(slice_obj, "local_path", ""),
            }
        if isinstance(slice_obj, dict):
            return {
                "image_index": slice_obj.get("image_index", 0),
                "slice_index": slice_obj.get("slice_index", 0),
                "y_start": slice_obj.get("y_start", 0),
                "y_end": slice_obj.get("y_end", 0),
                "is_bottom": slice_obj.get("is_bottom", False),
                "local_path": slice_obj.get("local_path", ""),
            }
        return {
            "image_index": getattr(slice_obj, "image_index", 0),
            "slice_index": getattr(slice_obj, "slice_index", 0),
            "y_start": getattr(slice_obj, "y_start", 0),
            "y_end": getattr(slice_obj, "y_end", 0),
            "is_bottom": getattr(slice_obj, "is_bottom", False),
            "local_path": getattr(slice_obj, "local_path", ""),
        }

    def close(self) -> None:
        """释放 httpx.Client"""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
