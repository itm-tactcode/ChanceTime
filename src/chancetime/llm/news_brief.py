"""Daily news brief: rare tool pulls + long cache (not per-poll search).

Philosophy: tool/search is expensive (100k–400k tokens). Pull world context
only a few times per day, cache the short summary, and feed it into cheap
no-tools calibration. Minute-to-minute market mids do not need live search.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from chancetime.llm.client import DailyBudgetExceeded, GrokClient
from chancetime.utils.logging import get_logger
from chancetime.utils.paths import project_root

log = get_logger(__name__)

DEFAULT_BRIEF_PATH = "data/llm_news_brief.json"


@dataclass
class NewsBriefState:
    day: str
    text: str
    pulls_today: int
    last_pull_ts: float
    source: str = "cache"


class DailyNewsBrief:
    """At most ``max_pulls_per_day`` tool-using pulls; otherwise return cache."""

    def __init__(
        self,
        llm: GrokClient,
        *,
        max_pulls_per_day: int = 4,
        min_hours_between_pulls: float = 4.0,
        cache_path: Path | None = None,
        topics: str = (
            "Fed rates, major crypto moves, top US sports finals, elections, "
            "markets-moving macro headlines"
        ),
    ) -> None:
        self.llm = llm
        self.max_pulls_per_day = max(0, int(max_pulls_per_day))
        self.min_hours_between_pulls = max(0.0, float(min_hours_between_pulls))
        self.cache_path = cache_path or (project_root() / DEFAULT_BRIEF_PATH)
        self.topics = topics
        self._state = self._load()

    def current_text(self) -> str:
        """Cached brief for injection into no-tools prompts (may be empty)."""
        self._roll_day()
        return (self._state.text or "").strip()

    def pulls_remaining(self) -> int:
        self._roll_day()
        return max(0, self.max_pulls_per_day - self._state.pulls_today)

    async def maybe_refresh(self, *, force: bool = False) -> NewsBriefState:
        """Pull with tools only if under daily cap and interval elapsed."""
        self._roll_day()
        now = time.time()
        hours_since = (
            (now - self._state.last_pull_ts) / 3600.0
            if self._state.last_pull_ts > 0
            else 1e9
        )

        if not force:
            if self._state.pulls_today >= self.max_pulls_per_day:
                log.info(
                    "news_brief_skip",
                    reason="daily_pull_cap",
                    pulls=self._state.pulls_today,
                    cap=self.max_pulls_per_day,
                )
                self._state.source = "cache_cap"
                return self._state
            if self._state.text and hours_since < self.min_hours_between_pulls:
                log.info(
                    "news_brief_skip",
                    reason="interval",
                    hours_since=round(hours_since, 2),
                    min_hours=self.min_hours_between_pulls,
                )
                self._state.source = "cache_fresh"
                return self._state
            if self.llm.tracker.remaining() <= 0.15:
                log.warning("news_brief_skip", reason="budget_low")
                self._state.source = "cache_budget"
                return self._state

        # Only use tools if settings allow; else skip pull
        if not self.llm._tools_active(force_tools=True):
            log.info(
                "news_brief_skip",
                reason="tools_disabled",
                msg="Enable tools only for rare daily briefs; calibrate stays no-tools",
            )
            self._state.source = "cache_no_tools"
            return self._state

        # Hard tool-call ledger on the client
        if not self.llm.allow_tool_call():
            log.warning("news_brief_skip", reason="tool_call_cap")
            self._state.source = "cache_tool_cap"
            return self._state

        prompt = (
            "Write a tight prediction-market news brief (max 400 words, bullets).\n"
            f"Focus: {self.topics}.\n"
            "Use web_search and/or x_search for the last ~6–12 hours only.\n"
            "Include: event, direction of impact, rough confidence, 1 source each.\n"
            "No trading advice. No filler. Prefer precision over coverage."
        )
        try:
            self.llm.note_tool_call()  # count before call so concurrent polls share cap
            text = await self.llm.chat(
                [{"role": "user", "content": prompt}],
                use_cache=False,
                use_tools=True,
                max_tokens=min(600, int(self.llm.settings.max_tokens or 512) + 200),
                prompt_summary="news_brief:daily",
            )
        except DailyBudgetExceeded:
            log.warning("news_brief_budget_exceeded")
            self._state.source = "cache_budget"
            return self._state
        except Exception:
            log.exception("news_brief_failed")
            self._state.source = "cache_error"
            return self._state

        text = (text or "").strip()
        if not text:
            self._state.source = "cache_empty"
            return self._state

        self._state.text = text[:4000]
        self._state.pulls_today += 1
        self._state.last_pull_ts = now
        self._state.source = "fresh_pull"
        self._save()
        log.info(
            "news_brief_refreshed",
            pulls_today=self._state.pulls_today,
            cap=self.max_pulls_per_day,
            chars=len(self._state.text),
        )
        return self._state

    def _roll_day(self) -> None:
        today = str(date.today())
        if self._state.day != today:
            self._state = NewsBriefState(
                day=today,
                text="",
                pulls_today=0,
                last_pull_ts=0.0,
                source="new_day",
            )
            self._save()

    def _load(self) -> NewsBriefState:
        if not self.cache_path.is_file():
            return NewsBriefState(day=str(date.today()), text="", pulls_today=0, last_pull_ts=0.0)
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            day = str(data.get("day") or "")
            if day != str(date.today()):
                return NewsBriefState(day=str(date.today()), text="", pulls_today=0, last_pull_ts=0.0)
            return NewsBriefState(
                day=day,
                text=str(data.get("text") or ""),
                pulls_today=int(data.get("pulls_today") or 0),
                last_pull_ts=float(data.get("last_pull_ts") or 0),
                source="disk",
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return NewsBriefState(day=str(date.today()), text="", pulls_today=0, last_pull_ts=0.0)

    def _save(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "day": self._state.day,
                "text": self._state.text,
                "pulls_today": self._state.pulls_today,
                "last_pull_ts": self._state.last_pull_ts,
                "updated_ts": time.time(),
            }
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.cache_path)
        except OSError as exc:
            log.warning("news_brief_save_failed", error=str(exc))
