"""Grok / xAI LLM client with cost tracking, caching, and structured outputs.

Primary path: OpenAI-compatible client against https://api.x.ai/v1
(Optional official xai_sdk can be wired later via extras.)

Safety: never places trades. LLM output is signals/ideas only.

Cost control (critical):
- Prices must match the **actual model** (grok-4.5 is ~$2/$6 per 1M, not $0.20/$0.50).
- Daily spend is **persisted to disk** so bot restarts cannot reset the cap.
- Pre-flight reserve refuses calls that would exceed remaining budget.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from chancetime.llm.cache import LLMCache
from chancetime.utils.config import AppConfig, LLMSettings
from chancetime.utils.logging import get_logger
from chancetime.utils.paths import project_root

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

# Default rates if YAML not updated — flagship grok-4.5 as of mid-2026
_DEFAULT_PRICE_IN = 2.0
_DEFAULT_PRICE_OUT = 6.0


class DailyBudgetExceeded(RuntimeError):
    """Raised when estimated daily LLM spend hits the configured cap."""


@dataclass
class LLMCallRecord:
    timestamp: float
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    prompt_summary: str
    cached: bool = False


@dataclass
class DailySpendTracker:
    """Daily spend tracker with optional durable file (survives restarts)."""

    budget_usd: float
    day: date = field(default_factory=date.today)
    spent_usd: float = 0.0
    tool_calls_today: int = 0
    max_tool_calls_per_day: int = 4
    calls: list[LLMCallRecord] = field(default_factory=list)
    persist_path: Path | None = None
    # Soft reserve: refuse call if remaining < this *after* estimated call cost
    min_remaining_usd: float = 0.0

    def __post_init__(self) -> None:
        self._load()

    def _roll_day(self) -> None:
        today = date.today()
        if today != self.day:
            self.day = today
            self.spent_usd = 0.0
            self.tool_calls_today = 0
            self.calls.clear()
            log.info("llm_spend_day_reset", day=str(today))
            self._save()

    def remaining(self) -> float:
        self._roll_day()
        return max(0.0, self.budget_usd - self.spent_usd)

    def check_budget(self, *, reserve_usd: float = 0.0) -> None:
        """Raise if spent >= budget, or if remaining cannot cover ``reserve_usd``."""
        self._roll_day()
        if self.spent_usd >= self.budget_usd:
            raise DailyBudgetExceeded(
                f"LLM daily budget ${self.budget_usd:.2f} exhausted "
                f"(spent ${self.spent_usd:.4f})"
            )
        need = max(0.0, float(reserve_usd))
        if self.remaining() < need + self.min_remaining_usd:
            raise DailyBudgetExceeded(
                f"LLM daily budget would exceed after next call: "
                f"remaining=${self.remaining():.4f} reserve=${need:.4f} "
                f"budget=${self.budget_usd:.2f} spent=${self.spent_usd:.4f}"
            )

    def record(self, rec: LLMCallRecord) -> None:
        self._roll_day()
        self.spent_usd += rec.estimated_cost_usd
        self.calls.append(rec)
        # Cap in-memory call log growth
        if len(self.calls) > 500:
            self.calls = self.calls[-250:]
        self._save()
        log.info(
            "llm_call",
            model=rec.model,
            input_tokens=rec.input_tokens,
            output_tokens=rec.output_tokens,
            estimated_cost_usd=round(rec.estimated_cost_usd, 6),
            spent_today_usd=round(self.spent_usd, 4),
            remaining_usd=round(self.remaining(), 4),
            budget_usd=round(self.budget_usd, 4),
            prompt_summary=rec.prompt_summary[:120],
            cached=rec.cached,
            durable=bool(self.persist_path),
        )

    def _load(self) -> None:
        if self.persist_path is None or not self.persist_path.is_file():
            return
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            day_s = str(data.get("day") or "")
            if day_s == str(date.today()):
                self.day = date.today()
                self.spent_usd = float(data.get("spent_usd") or 0.0)
                self.tool_calls_today = int(data.get("tool_calls_today") or 0)
                log.info(
                    "llm_spend_loaded",
                    path=str(self.persist_path),
                    spent_usd=round(self.spent_usd, 4),
                    budget_usd=round(self.budget_usd, 4),
                    tool_calls_today=self.tool_calls_today,
                )
            else:
                # Stale day file — start fresh
                self.day = date.today()
                self.spent_usd = 0.0
                self.tool_calls_today = 0
                self._save()
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning("llm_spend_load_failed", error=str(exc))

    def allow_tool_call(self) -> bool:
        self._roll_day()
        if self.max_tool_calls_per_day <= 0:
            return False
        return self.tool_calls_today < self.max_tool_calls_per_day

    def note_tool_call(self) -> None:
        self._roll_day()
        self.tool_calls_today += 1
        self._save()
        log.info(
            "llm_tool_call_counted",
            tool_calls_today=self.tool_calls_today,
            max_tool_calls_per_day=self.max_tool_calls_per_day,
        )

    def _save(self) -> None:
        if self.persist_path is None:
            return
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "day": str(self.day),
                "spent_usd": round(self.spent_usd, 6),
                "budget_usd": self.budget_usd,
                "tool_calls_today": self.tool_calls_today,
                "max_tool_calls_per_day": self.max_tool_calls_per_day,
                "updated_ts": time.time(),
            }
            tmp = self.persist_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.persist_path)
        except OSError as exc:
            log.warning("llm_spend_save_failed", error=str(exc))


class GrokClient:
    """Async Grok wrapper with cost control and optional caching.

    If ``XAI_API_KEY`` is missing, ``chat`` returns a deterministic mock so
    paper-mode development works without network/API access.
    """

    def __init__(
        self,
        settings: LLMSettings,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.x.ai/v1",
        cache: LLMCache | None = None,
        spend_path: Path | None = None,
    ) -> None:
        self.settings = settings
        self.api_key = api_key
        self.base_url = base_url
        self.cache = cache or LLMCache()
        # Durable spend file (process restarts must not reset the daily cap)
        path = spend_path
        if path is None:
            path = project_root() / "data" / "llm_spend.json"
        self.tracker = DailySpendTracker(
            budget_usd=float(settings.daily_budget_usd),
            persist_path=path,
            max_tool_calls_per_day=int(
                getattr(settings, "max_tool_calls_per_day", 4) or 0
            ),
        )
        self._client: Any | None = None
        # Warn once if prices look like fast-model rates on a flagship model name
        pin = float(getattr(settings, "price_input_per_1m", _DEFAULT_PRICE_IN) or _DEFAULT_PRICE_IN)
        if "4.5" in (settings.model or "") and pin < 1.0:
            log.error(
                "llm_price_mismatch",
                model=settings.model,
                price_input_per_1m=pin,
                msg=(
                    "grok-4.5 is ~$2/1M input — configured rate looks like fast-tier. "
                    "Budget will undercount real $ spend. Fix llm.price_* in YAML."
                ),
            )

    @classmethod
    def from_config(cls, cfg: AppConfig) -> GrokClient:
        cache_dir = project_root() / cfg.llm.cache_dir
        cache = LLMCache(
            ttl_seconds=cfg.llm.cache_ttl_seconds,
            disk_dir=cache_dir,
        )
        return cls(settings=cfg.llm, api_key=cfg.xai_api_key, cache=cache)

    def _price_in(self) -> float:
        v = float(getattr(self.settings, "price_input_per_1m", 0) or 0)
        return v if v > 0 else _DEFAULT_PRICE_IN

    def _price_out(self) -> float:
        v = float(getattr(self.settings, "price_output_per_1m", 0) or 0)
        return v if v > 0 else _DEFAULT_PRICE_OUT

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        inp = (input_tokens / 1_000_000.0) * self._price_in()
        out = (output_tokens / 1_000_000.0) * self._price_out()
        return inp + out

    def _preflight_reserve(self, messages: list[dict[str, str]], *, tools_on: bool) -> float:
        """Estimate worst-case cost before calling the API; refuse if over budget."""
        # Rough prompt size: chars/4 + tool inflation
        chars = sum(len(m.get("content") or "") for m in messages)
        est_in = max(200, chars // 4)
        if tools_on:
            # Tools/search often pull 50k–400k tokens of page content
            est_in = max(est_in, int(getattr(self.settings, "tools_reserve_input_tokens", 80_000)))
        max_in = int(getattr(self.settings, "max_input_tokens_per_call", 0) or 0)
        if max_in > 0 and est_in > max_in and tools_on:
            # Cap reserve estimate; actual call may still be large — prefer tools off
            est_in = max_in
        est_out = int(self.settings.max_tokens or 512)
        return self._estimate_cost(est_in, est_out)

    def _get_openai_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("openai package required for GrokClient") from exc

        if not self.api_key:
            raise RuntimeError("XAI_API_KEY not set")

        self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def _build_server_tools(self) -> list[dict[str, Any]]:
        """xAI server-side tools for Responses API (executed by xAI, not locally)."""
        tools: list[dict[str, Any]] = []
        if getattr(self.settings, "web_search", False):
            tools.append({"type": "web_search"})
        if getattr(self.settings, "x_search", False):
            tools.append({"type": "x_search"})
        return tools

    def _tools_active(self, *, force_tools: bool | None) -> bool:
        if force_tools is False:
            return False
        if force_tools is True:
            return bool(getattr(self.settings, "tools_enabled", False))
        return bool(
            getattr(self.settings, "tools_enabled", False)
            and (getattr(self.settings, "web_search", False) or getattr(self.settings, "x_search", False))
        )

    def allow_tool_call(self) -> bool:
        """Hard daily cap on tool/search pulls (independent of $ budget)."""
        return self.tracker.allow_tool_call()

    def note_tool_call(self) -> None:
        self.tracker.note_tool_call()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        use_cache: bool = True,
        prompt_summary: str | None = None,
        use_tools: bool | None = None,
    ) -> str:
        """Send a chat completion; returns assistant text content.

        When tools are enabled (web_search / x_search), uses xAI Responses API so
        server-side search runs automatically. Tools help with breaking news; they
        do not place trades.
        """
        if not self.settings.enabled:
            log.warning("llm_disabled")
            return ""

        model = model or self.settings.model
        max_tokens = max_tokens or self.settings.max_tokens
        summary = prompt_summary or (messages[-1]["content"][:80] if messages else "")
        tools_on = self._tools_active(force_tools=use_tools)
        # Fresh world when *real* search runs — mock path may still cache
        if (
            tools_on
            and self.api_key
            and not getattr(self.settings, "cache_when_tools", False)
        ):
            use_cache = False

        cache_key = LLMCache.make_key(
            model,
            messages,
            max_tokens=max_tokens,
            t=temperature,
            tools=int(tools_on),
        )
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self.tracker.record(
                    LLMCallRecord(
                        timestamp=time.time(),
                        model=model,
                        input_tokens=0,
                        output_tokens=0,
                        estimated_cost_usd=0.0,
                        prompt_summary=summary,
                        cached=True,
                    )
                )
                return str(cached)

        if tools_on and not self.allow_tool_call():
            log.warning(
                "llm_tools_blocked_daily_cap",
                tool_calls_today=self.tracker.tool_calls_today,
                max=self.tracker.max_tool_calls_per_day,
                msg="Falling back to no-tools chat",
            )
            tools_on = False

        reserve = self._preflight_reserve(messages, tools_on=tools_on)
        self.tracker.check_budget(reserve_usd=reserve)

        # Offline / paper-friendly mock when no key
        if not self.api_key:
            content = self._mock_response(messages)
            est_in, est_out = 50, 80
            cost = self._estimate_cost(est_in, est_out)
            self.tracker.record(
                LLMCallRecord(
                    timestamp=time.time(),
                    model=f"{model}(mock)",
                    input_tokens=est_in,
                    output_tokens=est_out,
                    estimated_cost_usd=cost,
                    prompt_summary=summary,
                )
            )
            if use_cache:
                self.cache.set(cache_key, content)
            return content

        client = self._get_openai_client()
        if tools_on:
            try:
                content, in_tok, out_tok = await self._chat_with_tools(
                    client,
                    messages,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except Exception as exc:
                log.warning(
                    "llm_tools_fallback_chat",
                    error=str(exc)[:200],
                    msg="Responses+tools failed; falling back to plain chat",
                )
                content, in_tok, out_tok = await self._chat_plain(
                    client,
                    messages,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
        else:
            content, in_tok, out_tok = await self._chat_plain(
                client,
                messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        cost = self._estimate_cost(in_tok, out_tok)
        self.tracker.record(
            LLMCallRecord(
                timestamp=time.time(),
                model=f"{model}+tools" if tools_on else model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                estimated_cost_usd=cost,
                prompt_summary=summary,
            )
        )
        if use_cache:
            self.cache.set(cache_key, content)
        return content

    async def _chat_plain(
        self,
        client: Any,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, int, int]:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
        out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
        return content, in_tok, out_tok

    async def _chat_with_tools(
        self,
        client: Any,
        messages: list[dict[str, str]],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, int, int]:
        """xAI Responses API with server-side web_search / x_search."""
        tools = self._build_server_tools()
        if not tools:
            return await self._chat_plain(
                client, messages, model=model, max_tokens=max_tokens, temperature=temperature
            )

        # Map chat messages → responses input
        input_items: list[dict[str, Any]] = []
        instructions = ""
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                instructions = (instructions + "\n" + content).strip() if instructions else content
            else:
                input_items.append({"role": role, "content": content})

        kwargs: dict[str, Any] = {
            "model": model,
            "input": input_items or [{"role": "user", "content": ""}],
            "tools": tools,
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if instructions:
            kwargs["instructions"] = instructions

        response = await client.responses.create(**kwargs)
        content = self._extract_responses_text(response)
        usage = getattr(response, "usage", None)
        # Responses API usage field names vary
        in_tok = int(
            getattr(usage, "input_tokens", None)
            or getattr(usage, "prompt_tokens", None)
            or 0
        )
        out_tok = int(
            getattr(usage, "output_tokens", None)
            or getattr(usage, "completion_tokens", None)
            or 0
        )
        # Tool usage may inflate cost; log server-side tool counts when present
        tool_usage = getattr(response, "server_side_tool_usage", None) or getattr(
            response, "tool_usage", None
        )
        if tool_usage:
            log.info("llm_server_tools_used", usage=str(tool_usage)[:300])
        citations = getattr(response, "citations", None)
        if citations:
            log.info("llm_citations", n=len(citations) if hasattr(citations, "__len__") else 1)
        return content, in_tok, out_tok

    @staticmethod
    def _extract_responses_text(response: Any) -> str:
        """Best-effort text extraction from Responses API objects."""
        text = getattr(response, "output_text", None)
        if isinstance(text, str) and text.strip():
            return text
        # Walk output items
        chunks: list[str] = []
        for item in getattr(response, "output", None) or []:
            item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
            if item_type == "message":
                content = getattr(item, "content", None) or (
                    item.get("content") if isinstance(item, dict) else None
                )
                for part in content or []:
                    ptype = getattr(part, "type", None) or (
                        part.get("type") if isinstance(part, dict) else None
                    )
                    if ptype in {"output_text", "text"}:
                        t = getattr(part, "text", None) or (
                            part.get("text") if isinstance(part, dict) else None
                        )
                        if t:
                            chunks.append(str(t))
        if chunks:
            return "\n".join(chunks)
        # Last resort
        return str(getattr(response, "content", "") or "")

    async def structured(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        **kwargs: Any,
    ) -> T:
        """Chat and parse response into a Pydantic model."""
        # Encourage JSON by appending schema hint
        schema_hint = (
            f"\n\nRespond with JSON only matching this schema: "
            f"{json.dumps(schema.model_json_schema())}"
        )
        msgs = list(messages)
        if msgs and msgs[-1]["role"] == "user":
            msgs[-1] = {**msgs[-1], "content": msgs[-1]["content"] + schema_hint}

        raw = await self.chat(msgs, **kwargs)
        text = raw.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        # Sometimes models add prose before JSON
        if text and not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            data = json.loads(text)
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            log.error("llm_structured_parse_failed", error=str(exc), raw=text[:200])
            raise

    @staticmethod
    def _mock_response(messages: list[dict[str, str]]) -> str:
        """Deterministic offline response for tests / paper mode without API key."""
        last = messages[-1]["content"] if messages else ""
        low = last.lower()
        # Mid-band venue adjudication schema
        if "same dual-listed" in low or "verdicts" in low or "same_event" in low:
            # Accept index 0 if present; reject others — cheap deterministic stub
            return json.dumps(
                {
                    "verdicts": [
                        {
                            "index": 0,
                            "same_event": True,
                            "confidence": 0.9,
                            "reason": "mock same-event",
                        }
                    ]
                }
            )
        if "venue_match" in low or '"pairs"' in low and "kalshi" in low:
            return json.dumps({"pairs": []})
        if "probability" in low or "market" in low:
            return json.dumps(
                {
                    "probability": 0.5,
                    "confidence": 0.3,
                    "reasoning": "Mock response (no XAI_API_KEY); default to neutral.",
                    "edge_vs_market": 0.0,
                    "used_tools": False,
                    "sources_note": "none (mock)",
                }
            )
        return json.dumps({"ok": True, "message": "mock llm response", "detail": last[:100]})

    def spend_summary(self) -> dict[str, Any]:
        return {
            "day": str(self.tracker.day),
            "spent_usd": round(self.tracker.spent_usd, 6),
            "budget_usd": self.tracker.budget_usd,
            "remaining_usd": round(self.tracker.remaining(), 6),
            "n_calls": len(self.tracker.calls),
        }
