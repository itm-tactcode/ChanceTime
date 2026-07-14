"""Load environment variables and YAML configuration.

Secrets always come from the environment (via python-dotenv). Non-secret
parameters live in config YAML and may be overridden by env vars.

RSA private keys (Kalshi + Polymarket US) are loaded from file paths under
secrets/ — never commit PEMs or put PEM bodies in .env.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from chancetime.utils.paths import project_root, resolve_private_key_path


class RiskSettings(BaseModel):
    max_position_usd: float = 50.0
    max_daily_loss_usd: float = 25.0
    max_open_positions: int = 10
    max_consecutive_errors: int = 5
    # Optional exit rules (fraction of position size_usd)
    take_profit_pct: float | None = 0.30
    stop_loss_pct: float | None = 0.25
    # Phase 8: per-family exposure (sports / macro / crypto / other)
    max_family_exposure_usd: float = 100.0
    # Phase 19: tighter same-event cluster (series/ticker) — 0 = off
    max_cluster_exposure_usd: float = 0.0
    # Phase 19: max fraction of cash_basis in open positions (0 = off)
    max_deploy_pct: float = 0.0
    # Phase 19: time-to-event (hours/days); 0 = off
    min_hours_to_close: float = 0.0  # skip if resolves too soon
    max_days_to_close: float = 0.0  # skip if far horizon (dead long capital); 0 = off
    # Phase 8: auto-disable cold strategies (0 = off)
    cold_min_fills: int = 5
    cold_max_realized_pnl: float = -10.0
    # Cash ledger: never approve more notional than free cash (paper mirrors live reject)
    enforce_cash: bool = True
    min_order_usd: float = 1.0
    # Skip ultra-longshot / near-certain mids at risk layer (0 = off)
    min_yes_mid: float = 0.03
    max_yes_mid: float = 0.97
    # Cost-aware filter: require |edge| - half_spread - fee >= min_net_edge
    min_net_edge: float = 0.02
    assumed_half_spread: float = 0.005  # matches paper 50 bps absolute points
    assumed_fee: float = 0.0
    # Default concurrent open cap per strategy (0 = unlimited).
    # Overridden by strategies.<name>.max_open when that field is set.
    max_open_per_strategy: int = 8
    # Reject when BBO spread (ask-bid) exceeds this (0 = off). Synced with execution.max_spread.
    max_spread: float = 0.06
    # Weight floor: strategies with weight <= 0 are skipped


class LLMSettings(BaseModel):
    enabled: bool = True
    # Prefer fast tier for continuous paper bots; escalate model only when needed
    model: str = "grok-4.5"
    daily_budget_usd: float = 5.0
    max_tokens: int = 512
    call_on_every_poll: bool = False
    # MUST match xAI list prices for the chosen model (grok-4.5 ≈ $2 / $6 per 1M)
    # Wrong rates = fake "under budget" while real $ burns (Jul 2026 incident).
    price_input_per_1m: float = 2.0
    price_output_per_1m: float = 6.0
    cache_ttl_seconds: float = 3600.0
    cache_dir: str = "llm_cache"
    # Run Grok post-trade review once when the bot stops (if any fills)
    post_trade_review: bool = True
    # Bust calibration cache if |Δ mid| exceeds this
    price_move_bust: float = 0.05
    # Optional free-text / file path for news context (if path exists, file is read)
    news_context: str = ""
    news_context_file: str = ""
    # Phase 18: xAI server-side tools (Responses API) for live context
    # Default OFF for continuous bots — tools inflate to 100k–400k tokens/call.
    tools_enabled: bool = False
    web_search: bool = False
    x_search: bool = False
    # When tools run, skip disk cache (fresh world); set true only for cost tests
    cache_when_tools: bool = False
    # Prefer tool-using path for calibration (still falls back to chat if tools fail)
    # Keep FALSE — calibrate uses cached daily news brief, not per-market search.
    calibrate_with_tools: bool = False
    # Pre-flight: reserve this many input tokens when tools_on (cost estimate)
    tools_reserve_input_tokens: int = 80_000
    # Soft hard-cap for prompt size estimate (0 = off)
    max_input_tokens_per_call: int = 100_000
    # Hard cap: tool/search pulls per calendar day (all strategies share this)
    max_tool_calls_per_day: int = 4
    # Daily news brief: rare tools pull, long cache, inject into no-tools calibrations
    news_brief_enabled: bool = True
    news_brief_max_pulls_per_day: int = 4
    news_brief_min_hours_between: float = 4.0


class UniverseProfileSettings(BaseModel):
    """One named market universe (shared fetch, strategy-specific filters)."""

    max_markets: int = 100
    # Soft prefer (drop_beyond=false) or hard window (drop_beyond=true); 0 = no time filter
    prefer_closing_within_hours: float = 0.0
    drop_beyond_prefer: bool = False
    keep_unknown_close: bool = True
    queries: list[str] = Field(default_factory=list)
    search_limit_per_query: int = 40
    # Dual-venue discovery merge (used by dual_list profile / arb_cross)
    deep_discovery: bool = False
    discovery_every_polls: int = 5
    discovery_limit: int = 150


def _default_data_profiles() -> dict[str, UniverseProfileSettings]:
    from chancetime.data_layer.profiles import default_profile_specs

    return {
        name: UniverseProfileSettings(**spec) for name, spec in default_profile_specs().items()
    }


class DataSettings(BaseModel):
    source: str = "mock"  # mock | kalshi | polymarket | both
    # Legacy global list size (also default for profiles that omit max_markets)
    max_markets: int = 20
    # Legacy dual-venue discovery (used if profiles.dual_list not customized)
    discovery_every_polls: int = 5
    discovery_limit: int = 150
    # Legacy short-horizon knobs (applied to short_bbo profile defaults if present)
    prefer_closing_within_hours: float = 48.0
    short_horizon_queries: list[str] = Field(
        default_factory=lambda: [
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "crypto",
            "up or down",
        ]
    )
    short_horizon_search_limit: int = 40
    # When source is not mock, drop Market.synthetic fixtures if they ever appear
    reject_synthetic: bool = True
    # Named universes — strategies set strategies.<name>.universe to pick one
    profiles: dict[str, UniverseProfileSettings] = Field(
        default_factory=_default_data_profiles
    )


class HistorySettings(BaseModel):
    """Phase 10: append market/BBO snapshots for replay."""

    enabled: bool = False
    directory: str = "data/history"
    # If empty, use markets-YYYYMMDD.jsonl
    filename: str = ""


class SimpleEdgeSettings(BaseModel):
    enabled: bool = True
    # Market universe profile name (data.profiles.*)
    universe: str = "broad"
    edge_threshold: float = 0.08
    min_liquidity_usd: float = 100.0
    default_fair_prob: float = 0.5
    # static | trailing_mean | blend — prefer blend/trailing for production (Phase 18)
    prior_mode: str = "blend"
    blend_alpha: float = 0.5
    history_window: int = 5
    min_history: int = 3
    weight: float = 1.0
    # Skip lottery / near-certain mids (static 0.5 prior is nonsense outside this band)
    min_yes_price: float = 0.05
    max_yes_price: float = 0.95
    # Concurrent open cap for this strategy (None = risk.max_open_per_strategy; 0 = unlimited)
    max_open: int | None = None
    # Optional per-strategy size budget (None = use execution.default_order_size_usd)
    max_size_usd: float | None = None


class LLMCalibratedSettings(BaseModel):
    enabled: bool = False  # opt-in; costs tokens
    universe: str = "llm_screen"
    edge_threshold: float = 0.10  # Phase 18: higher bar after costs
    min_liquidity_usd: float = 100.0
    min_confidence: float = 0.45
    screen_threshold: float = 0.05
    max_llm_calls_per_poll: int = 2
    weight: float = 1.0
    max_open: int | None = None
    max_size_usd: float | None = None
    # Require model confidence even higher when tools are off
    min_confidence_no_tools: float = 0.55


class ArbCrossSettings(BaseModel):
    enabled: bool = False
    universe: str = "dual_list"
    min_spread: float = 0.04
    fee_buffer: float = 0.03  # Phase 18: slightly higher cost buffer
    min_match_score: float = 0.72
    min_liquidity_usd: float = 100.0
    emit_hedge_legs: bool = True
    weight: float = 1.0
    # Optional canonical_key / id aliases: kalshi_key -> pm_key
    aliases: dict[str, str] = Field(default_factory=dict)
    # Mid-band LLM adjudication: fuzzy auto-accepts >= min_match_score;
    # candidates in [llm_match_band_low, min_match_score) get a tiny yes/no call.
    use_llm_match: bool = False
    llm_match_band_low: float = 0.40
    llm_match_min_confidence: float = 0.75
    llm_match_max_each: int = 24
    # Heavier full-list Grok match only if band is empty (usually leave false)
    llm_bulk_fallback: bool = False
    # Executable arb: require pair BBO; size by min book depth
    require_bbo: bool = True  # Phase 18: prefer real BBO pairs
    use_executable_prices: bool = True
    size_by_depth: bool = True
    max_leg_usd: float = 25.0
    max_pair_usd: float = 40.0
    min_depth_usd: float = 5.0
    max_open: int | None = None
    max_size_usd: float | None = None
    # Dual-venue discovery (series + search) so real same-event pairs appear
    deep_discovery: bool = True


class ComplementArbSettings(BaseModel):
    """Same-market YES ask + NO ask < 1 (no LLM; pure BBO)."""

    enabled: bool = False
    universe: str = "short_bbo"
    min_edge: float = 0.01
    fee_buffer: float = 0.02
    require_bbo: bool = True
    min_depth_usd: float = 5.0
    max_leg_usd: float = 20.0
    max_pair_usd: float = 40.0
    min_liquidity_usd: float = 0.0
    size_by_depth: bool = True
    # Drop mock fixtures (always true when live data also present)
    reject_synthetic: bool = True
    # 0 = any horizon; e.g. 6 = only markets closing within 6h
    max_hours_to_close: float = 0.0
    weight: float = 1.0
    max_open: int | None = None
    max_size_usd: float | None = None


class MeanRevertSettings(BaseModel):
    enabled: bool = False
    universe: str = "broad"
    move_threshold: float = 0.06
    min_liquidity_usd: float = 100.0
    history_window: int = 8
    min_history: int = 3
    weight: float = 1.0
    max_open: int | None = None
    max_size_usd: float | None = None


class NewsImpulseSettings(BaseModel):
    enabled: bool = False
    universe: str = "llm_screen"
    edge_threshold: float = 0.06
    min_liquidity_usd: float = 100.0
    min_confidence: float = 0.4
    max_llm_calls_per_poll: int = 2
    news_context: str = ""  # optional override; else llm.news_context
    weight: float = 1.0
    max_open: int | None = None
    max_size_usd: float | None = None


class MLEdgeSettings(BaseModel):
    enabled: bool = False
    universe: str = "broad"
    model_path: str = "models/ml_edge.joblib"
    edge_threshold: float = 0.05
    min_liquidity_usd: float = 100.0
    weight: float = 1.0
    max_open: int | None = None
    max_size_usd: float | None = None


class PairGapTrackerSettings(BaseModel):
    """Log-only dual-list edge time series (no fills)."""

    enabled: bool = True
    universe: str = "dual_list"
    min_match_score: float = 0.72
    fee_buffer: float = 0.03
    top_n: int = 40
    log_name: str = "pair_gap"
    weight: float = 0.0


class TteBucketsSettings(BaseModel):
    enabled: bool = True
    universe: str = "short_bbo"
    max_rows: int = 200
    log_name: str = "tte_buckets"
    weight: float = 0.0


class PriceBucketsSettings(BaseModel):
    enabled: bool = True
    universe: str = "broad"
    max_rows: int = 250
    log_name: str = "price_buckets"
    weight: float = 0.0


class MatchQualitySettings(BaseModel):
    enabled: bool = True
    universe: str = "dual_list"
    min_match_score: float = 0.55
    long_tte_hours: float = 720.0
    top_n: int = 60
    log_name: str = "match_quality"
    weight: float = 0.0


class StrategiesSettings(BaseModel):
    simple_edge: SimpleEdgeSettings = Field(default_factory=SimpleEdgeSettings)
    llm_calibrated: LLMCalibratedSettings = Field(default_factory=LLMCalibratedSettings)
    arb_cross: ArbCrossSettings = Field(default_factory=ArbCrossSettings)
    complement_arb: ComplementArbSettings = Field(default_factory=ComplementArbSettings)
    mean_revert: MeanRevertSettings = Field(default_factory=MeanRevertSettings)
    news_impulse: NewsImpulseSettings = Field(default_factory=NewsImpulseSettings)
    ml_edge: MLEdgeSettings = Field(default_factory=MLEdgeSettings)
    pair_gap_tracker: PairGapTrackerSettings = Field(default_factory=PairGapTrackerSettings)
    tte_buckets: TteBucketsSettings = Field(default_factory=TteBucketsSettings)
    price_buckets: PriceBucketsSettings = Field(default_factory=PriceBucketsSettings)
    match_quality: MatchQualitySettings = Field(default_factory=MatchQualitySettings)


class ExecutionSettings(BaseModel):
    paper_slippage_bps: float = 50.0
    default_order_size_usd: float = 10.0
    # Phase 17: paper realism
    use_bbo_paper: bool = True  # pay ask / hit bid when BBO present
    paper_fee_bps: float = 70.0  # entry fee on notional (venue-ish default)
    paper_fee_venue: str = "default"  # default|kalshi|polymarket → fee schedule override
    max_spread: float = 0.06  # reject if yes_ask-yes_bid wider (when BBO); 0 = off
    size_by_depth: bool = True
    min_depth_usd: float = 5.0
    liquidity_participation: float = 0.25  # max fraction of depth/liquidity per fill
    min_fill_ratio: float = 0.25  # drop if clipped size < this × requested
    # Dual-leg paper arb hard caps (both legs or neither)
    max_arb_pairs_per_poll: int = 3
    max_arb_pair_usd: float = 40.0
    max_arb_notional_per_poll: float = 100.0
    max_leg_usd: float = 25.0
    max_position_usd_hard: float = 50.0
    require_both_arb_legs: bool = True
    # Phase 6 live micro-caps (ignored in paper mode)
    live_enabled: bool = False
    max_live_order_usd: float = 5.0
    min_live_order_usd: float = 1.0
    max_live_orders_session: int = 4
    max_live_notional_session: float = 20.0
    live_tif_kalshi: str = "immediate_or_cancel"
    live_tif_polymarket: str = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"
    # Phase 6 complete: allow dual-leg live when True (still needs risk ack)
    dual_leg_live_enabled: bool = True


class AlertsSettings(BaseModel):
    telegram_enabled: bool = False


class PersistenceSettings(BaseModel):
    """Local SQLite book of record (portfolio, fills, equity).

    Paper and live should use **different files** so the monitor can toggle books.
    Default process book is paper (``data/paper.db``). Live configs use ``data/live.db``.
    """

    enabled: bool = True
    # Active book for this process (bot writes here)
    db_path: str = "data/paper.db"
    cash_basis_usd: float = 1000.0


class DashboardSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    # Monitor shows both; independent of active bot db_path
    paper_db_path: str = "data/paper.db"
    live_db_path: str = "data/live.db"


class BotSettings(BaseModel):
    name: str = "chance-time"
    paper_mode: bool = True
    poll_interval_seconds: float = 30.0
    # Phase 9: generate signals + risk filter but place no paper/live orders
    shadow_mode: bool = False
    # Phase 19: re-read risk (+ strategy caps/weights) from YAML each poll
    hot_reload_risk: bool = False


class LoggingSettings(BaseModel):
    level: str = "INFO"
    json_logs: bool = False


class CryptoUpDownSettings(BaseModel):
    """Path C — global Polymarket crypto Up/Down (paper-first).

    **Sensitive knobs** (edge, sizes, snipe thresholds) belong in gitignored
    ``config/user.yaml`` under ``crypto_updown:``, not committed defaults you
    consider proprietary after paper discovery.
    """

    poll_interval_seconds: float = 15.0
    max_markets: int = 20
    bbo_limit: int = 12
    # Default shadow: evaluate/log only. Enable paper fills in user.yaml or CLI.
    paper_strategy: bool = False
    paper_complete_set: bool = False
    paper_direction: bool = False
    size_usd: float = 5.0
    min_edge: float = 0.06
    complete_set_size_usd: float = 5.0
    complete_set_max_sum: float = 0.995
    max_spread: float = 0.12
    snipe_seconds: float = 90.0
    snipe_min_p: float = 0.62
    snipe_size_usd: float = 5.0
    max_usd_per_market_side: float = 25.0
    signal_edge_threshold: float = 0.08
    fee_bps: float = 50.0
    max_daily_loss_usd: float = 50.0
    max_spot_age_sec: float = 90.0
    use_ws: bool = False
    publish_signals: bool = True
    db_path: str = "data/crypto_paper.db"
    starting_cash: float = 1000.0


class CryptoExchangeSettings(BaseModel):
    """Path D — US crypto exchange spot paper + optional C→D signals.

    Size/confidence and risk caps: put personal values in ``user.yaml``.
    """

    poll_interval_seconds: float = 20.0
    venue: str = "coinbase"
    trade_signals: bool = False
    signal_size_usd: float = 25.0
    min_signal_confidence: float = 0.65
    max_signal_age_sec: float = 180.0
    max_positions: int = 4
    max_notional_per_asset: float = 100.0
    max_signal_fills_per_poll: int = 2
    fee_bps: float = 30.0
    db_path: str = "data/crypto_exchange_paper.db"
    starting_cash: float = 1000.0
    consume_signals: bool = True


class AppConfig(BaseModel):
    """Merged application configuration."""

    bot: BotSettings = Field(default_factory=BotSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    history: HistorySettings = Field(default_factory=HistorySettings)
    strategies: StrategiesSettings = Field(default_factory=StrategiesSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    alerts: AlertsSettings = Field(default_factory=AlertsSettings)
    persistence: PersistenceSettings = Field(default_factory=PersistenceSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    crypto_updown: CryptoUpDownSettings = Field(default_factory=CryptoUpDownSettings)
    crypto_exchange: CryptoExchangeSettings = Field(
        default_factory=CryptoExchangeSettings
    )

    # Secrets / env-only (never from YAML)
    xai_api_key: str | None = None
    kalshi_api_key: str | None = None  # Kalshi API Key ID (UUID)
    # Path to RSA private key PEM (e.g. ./secrets/kalshi.key) — not the PEM itself
    kalshi_private_key_path: Path | None = None
    kalshi_env: str = "demo"
    # Polymarket US (docs.polymarket.us / polymarket.us/developer) — same shape as Kalshi
    polymarket_api_key: str | None = None  # API Key ID (UUID)
    polymarket_private_key_path: Path | None = None  # e.g. ./secrets/polymarket.key
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    @property
    def paper_mode(self) -> bool:
        return self.bot.paper_mode

    @property
    def kalshi_credentials_configured(self) -> bool:
        """True when key id is set and private key file exists."""
        if not self.kalshi_api_key or not self.kalshi_private_key_path:
            return False
        return self.kalshi_private_key_path.is_file()

    @property
    def polymarket_credentials_configured(self) -> bool:
        """True when Polymarket US key id is set and private key file exists."""
        if not self.polymarket_api_key or not self.polymarket_private_key_path:
            return False
        return self.polymarket_private_key_path.is_file()


class EnvSettings(BaseSettings):
    """Environment-only overrides and secrets."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    paper_mode: bool | None = None
    xai_api_key: str | None = None
    grok_model: str | None = None
    llm_daily_budget_usd: float | None = None
    llm_price_input_per_1m: float | None = None
    llm_price_output_per_1m: float | None = None
    kalshi_api_key: str | None = None
    # Preferred: path to PEM file
    kalshi_private_key_path: str | None = None
    # Legacy alias: treated as a file path (not inline PEM)
    kalshi_api_secret: str | None = None
    kalshi_env: str | None = None
    polymarket_api_key: str | None = None
    polymarket_private_key_path: str | None = None
    # Legacy alias: treated as a file path (not inline PEM)
    polymarket_api_secret: str | None = None
    poll_interval_seconds: float | None = None
    config_path: str = "config/default.yaml"
    log_level: str | None = None
    max_position_usd: float | None = None
    max_daily_loss_usd: float | None = None
    max_open_positions: int | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    @field_validator("paper_mode", mode="before")
    @classmethod
    def parse_bool(cls, v: Any) -> bool | None:
        if v is None or v == "":
            return None
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overlay`` into ``base`` (overlay wins on leaf keys)."""
    out: dict[str, Any] = dict(base)
    for key, val in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def user_config_path(*, root: Path | None = None) -> Path:
    """Path for gitignored user overrides (dashboard / CLI writes)."""
    return (root or project_root()) / "config" / "user.yaml"


def save_user_config(overrides: dict[str, Any], *, root: Path | None = None) -> Path:
    """Merge ``overrides`` into ``config/user.yaml`` and write. Returns path."""
    r = root or project_root()
    path = user_config_path(root=r)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_yaml(path)
    merged = deep_merge(existing, overrides)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, default_flow_style=False, sort_keys=False)
    return path


def load_config(
    config_path: str | Path | None = None,
    *,
    env_file: str | Path | None = ".env",
    user_config: str | Path | bool | None = True,
) -> AppConfig:
    """Load YAML + optional user overlay + secrets from env.

    Priority (highest last for non-secrets)::

        config/default.yaml or --config path
        < config/user.yaml (if present; dashboard/CLI overrides)
        < secrets & PAPER_MODE from .env / process env

    Ops knobs (poll interval, risk sizes, strategy flags) belong in YAML /
    ``user.yaml``, not ``.env``. Env still accepts legacy knobs if set, but
    prefer editing ``config/user.yaml`` or the dashboard when that lands.
    """
    root = project_root()
    if env_file is not None:
        env_path = Path(env_file)
        if not env_path.is_absolute():
            env_path = root / env_path
        load_dotenv(env_path, override=False)
        env = EnvSettings()
    else:
        # Tests / isolated loads: process env only, no .env file.
        # _env_file is a BaseSettings init kwarg; not in the typed signature.
        env = EnvSettings(_env_file=None)  # type: ignore[call-arg]

    path = Path(config_path or env.config_path or "config/default.yaml")
    if not path.is_absolute():
        path = root / path

    raw = _load_yaml(path)

    # Overlay user.yaml (non-secret knobs; never put API keys here)
    if user_config is not False:
        if user_config is True or user_config is None:
            upath = user_config_path(root=root)
        else:
            upath = Path(user_config)
            if not upath.is_absolute():
                upath = root / upath
        user_raw = _load_yaml(upath)
        if user_raw:
            raw = deep_merge(raw, user_raw)

    cfg = AppConfig.model_validate(raw)

    # Secrets + hard safety only (ops knobs: use YAML / user.yaml)
    if env.paper_mode is not None:
        cfg.bot.paper_mode = env.paper_mode
    # Legacy env knobs still work if set (optional); prefer user.yaml
    if env.poll_interval_seconds is not None:
        cfg.bot.poll_interval_seconds = env.poll_interval_seconds
    if env.grok_model:
        cfg.llm.model = env.grok_model
    if env.llm_daily_budget_usd is not None:
        cfg.llm.daily_budget_usd = env.llm_daily_budget_usd
    if env.llm_price_input_per_1m is not None:
        cfg.llm.price_input_per_1m = env.llm_price_input_per_1m
    if env.llm_price_output_per_1m is not None:
        cfg.llm.price_output_per_1m = env.llm_price_output_per_1m
    if env.log_level:
        cfg.logging.level = env.log_level
    if env.max_position_usd is not None:
        cfg.risk.max_position_usd = env.max_position_usd
    if env.max_daily_loss_usd is not None:
        cfg.risk.max_daily_loss_usd = env.max_daily_loss_usd
    if env.max_open_positions is not None:
        cfg.risk.max_open_positions = env.max_open_positions

    cfg.xai_api_key = env.xai_api_key or os.getenv("XAI_API_KEY")
    cfg.kalshi_api_key = env.kalshi_api_key or os.getenv("KALSHI_API_KEY")

    # Kalshi private key: file path only (preferred env + legacy secret-as-path)
    cfg.kalshi_private_key_path = resolve_private_key_path(
        env.kalshi_private_key_path
        or os.getenv("KALSHI_PRIVATE_KEY_PATH")
        or env.kalshi_api_secret
        or os.getenv("KALSHI_API_SECRET"),
        root=root,
        venue="Kalshi",
        example_path="./secrets/kalshi.key",
        preferred_env="KALSHI_PRIVATE_KEY_PATH",
    )

    if env.kalshi_env:
        cfg.kalshi_env = env.kalshi_env

    # Polymarket US: same schema as Kalshi (UUID + RSA PEM file path)
    cfg.polymarket_api_key = env.polymarket_api_key or os.getenv("POLYMARKET_API_KEY")
    cfg.polymarket_private_key_path = resolve_private_key_path(
        env.polymarket_private_key_path
        or os.getenv("POLYMARKET_PRIVATE_KEY_PATH")
        or env.polymarket_api_secret
        or os.getenv("POLYMARKET_API_SECRET"),
        root=root,
        venue="Polymarket US",
        example_path="./secrets/polymarket.key",
        preferred_env="POLYMARKET_PRIVATE_KEY_PATH",
    )

    cfg.telegram_bot_token = env.telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN") or None
    cfg.telegram_chat_id = env.telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID") or None
    if cfg.telegram_bot_token and cfg.telegram_chat_id:
        cfg.alerts.telegram_enabled = True

    # Load news context file if configured
    if cfg.llm.news_context_file:
        npath = Path(cfg.llm.news_context_file)
        if not npath.is_absolute():
            npath = root / npath
        if npath.is_file():
            try:
                file_ctx = npath.read_text(encoding="utf-8").strip()
                if file_ctx:
                    cfg.llm.news_context = (
                        f"{cfg.llm.news_context}\n{file_ctx}".strip()
                        if cfg.llm.news_context
                        else file_ctx
                    )
            except OSError:
                pass

    return cfg
