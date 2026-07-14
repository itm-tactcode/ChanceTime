"""Path C paper strategy — tweet hybrid Up/Down loop (no live CLOB).

Canonical 5 steps (research thesis):

1. At window start, record external asset price; keep streaming spot.
2. Evaluate direction, volatility, time remaining, Polymarket liquidity.
3. Form own P(Up) / P(Down) (simple calibrated heuristic — not free alpha).
4. Buy undervalued side on mispricing; add opposite if Up+Down asks < 1.
5. Near expiry, lean into the clear favorite (sniping) with inventory caps.

Fail-closed: missing spot, missing BBO, wide spread → no fill.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Literal

from chancetime.crypto_updown.models import UpDownMarket
from chancetime.crypto_updown.paper import CryptoPaperBook
from chancetime.modules.signals import (
    ImpliedDirectionSignal,
    build_direction_from_book,
)

Side = Literal["up", "down"]


@dataclass
class TweetStrategyConfig:
    """Knobs for the hybrid Path C strategy (paper)."""

    # Mispricing (step 4)
    min_edge: float = 0.06  # model_p - market mid (or reverse)
    size_usd: float = 5.0
    complete_set_max_sum: float = 0.995  # after asking both; leave fee buffer
    complete_set_size_usd: float = 5.0
    # Liquidity gates (step 2)
    max_spread: float = 0.12  # ask - bid on favored side
    min_ask_size: float = 0.0  # if known; 0 = ignore
    # Own P model (step 3)
    vol_lookback: int = 20  # recent spot samples per asset
    vol_floor: float = 0.0005  # min relative vol per sample
    # Sniping (step 5)
    snipe_seconds: float = 90.0
    snipe_min_p: float = 0.62  # clear favorite threshold on model or market
    snipe_size_usd: float = 5.0
    # Inventory
    max_usd_per_market_side: float = 25.0
    # Signals for Path D
    signal_edge_threshold: float = 0.08


@dataclass
class StrategyResult:
    signals: list[ImpliedDirectionSignal]
    actions: list[dict[str, Any]]
    evaluations: list[dict[str, Any]] = field(default_factory=list)


def scan_implied_direction(
    markets: list[UpDownMarket],
    spots: dict[str, float],
    *,
    references: dict[str, float] | None = None,
    edge_threshold: float = 0.08,
) -> list[ImpliedDirectionSignal]:
    """Emit one signal per market with usable books (fail closed if no price)."""
    refs = references or {}
    out: list[ImpliedDirectionSignal] = []
    for m in markets:
        up_mid = m.up.mid if m.up else None
        down_mid = m.down.mid if m.down else None
        up_ask = m.up.best_ask if m.up else None
        down_ask = m.down.best_ask if m.down else None
        if up_mid is None and (up_ask is None or down_ask is None):
            continue
        sec = m.seconds_remaining()
        end_ts = m.window_end.timestamp() if m.window_end else None
        sig = build_direction_from_book(
            asset=m.asset,
            slug=m.slug,
            up_mid=up_mid,
            down_mid=down_mid,
            up_ask=up_ask,
            down_ask=down_ask,
            spot=spots.get(m.asset),
            seconds_remaining=sec,
            window_end_ts=end_ts,
            complete_set_sum=m.complete_set_ask_sum(),
            reference_price=refs.get(m.slug),
            edge_threshold=edge_threshold,
        )
        if sig is not None:
            out.append(sig)
    return out


def paper_buy_favored_side(
    book: CryptoPaperBook,
    market: UpDownMarket,
    sig: ImpliedDirectionSignal,
    *,
    size_usd: float,
    min_confidence: float = 0.65,
    require_spot: bool = True,
) -> dict[str, Any]:
    """Legacy helper: if signal strong, paper-buy favored side only."""
    if sig.direction == "flat" or sig.confidence < min_confidence:
        return {"slug": market.slug, "action": "skip", "reason": "weak_signal"}
    if require_spot and sig.spot is None:
        return {"slug": market.slug, "action": "skip", "reason": "no_spot"}
    side: Side = "up" if sig.direction == "up" else "down"
    err = book.try_buy(market, side=side, size_usd=size_usd)
    if err:
        return {"slug": market.slug, "action": "skip", "reason": err, "side": side}
    return {
        "slug": market.slug,
        "action": "paper_buy",
        "side": side,
        "size_usd": size_usd,
        "confidence": sig.confidence,
        "p_up": sig.p_up,
    }


def _rel_vol(samples: deque[tuple[float, float]], floor: float) -> float:
    """Rough realized vol from successive relative returns."""
    if len(samples) < 3:
        return max(floor, 0.002)
    rets: list[float] = []
    prev = samples[0][1]
    for _, px in list(samples)[1:]:
        if prev > 0 and px > 0:
            rets.append((px - prev) / prev)
        prev = px
    if not rets:
        return max(floor, 0.002)
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    return max(floor, math.sqrt(var))


def model_p_up(
    *,
    spot: float,
    ref: float,
    vol: float,
    seconds_remaining: float | None,
    window_seconds: float | None = None,
) -> float:
    """Heuristic P(Up | spot vs open). Not a claim of true probability.

    Up ≈ spot finishes >= open/ref. Early in window stay closer to 0.5;
    late in window push harder toward 0/1 from signed move / vol.
    """
    if spot <= 0 or ref <= 0:
        return 0.5
    move = (spot - ref) / ref
    # Time fraction remaining in window (assume 5m if unknown)
    total = window_seconds if window_seconds and window_seconds > 0 else 300.0
    sec = seconds_remaining if seconds_remaining is not None else total * 0.5
    sec = max(0.0, min(total, sec))
    # elapsed fraction 0→1
    elapsed = 1.0 - (sec / total)
    # Scale: larger |move|/vol and later in window → more extreme p
    z = move / max(vol, 1e-6)
    # logistic on z * (0.35 + 1.2 * elapsed^1.5)
    scale = 0.35 + 1.2 * (elapsed**1.5)
    x = z * scale * 2.5
    # stable sigmoid
    if x >= 20:
        p = 1.0
    elif x <= -20:
        p = 0.0
    else:
        p = 1.0 / (1.0 + math.exp(-x))
    return max(0.02, min(0.98, p))


def _side_spread(market: UpDownMarket, side: Side) -> float | None:
    book = market.up if side == "up" else market.down
    if book is None or book.best_ask is None or book.best_bid is None:
        return None
    return float(book.best_ask) - float(book.best_bid)


def _market_p_up(market: UpDownMarket) -> float | None:
    if market.up and market.up.mid is not None and 0 < market.up.mid < 1:
        return float(market.up.mid)
    if market.up and market.up.best_ask is not None and market.down and market.down.best_ask is not None:
        total = market.up.best_ask + market.down.best_ask
        if total > 0:
            # inverse cost proxy
            return max(0.02, min(0.98, 1.0 - market.up.best_ask / total))
    return None


def _position_usd(book: CryptoPaperBook, slug: str, side: Side) -> float:
    pos = book.positions.get((slug, side))
    return float(pos.size_usd) if pos else 0.0


class TweetHybridStrategy:
    """Stateful paper strategy implementing the 5-step tweet loop."""

    name = "tweet_hybrid_updown"

    def __init__(self, cfg: TweetStrategyConfig | None = None) -> None:
        self.cfg = cfg or TweetStrategyConfig()
        # asset → (ts, price) samples for vol
        self._spot_hist: dict[str, deque[tuple[float, float]]] = defaultdict(
            lambda: deque(maxlen=self.cfg.vol_lookback)
        )

    def note_spot(self, asset: str, price: float, ts: float | None = None) -> None:
        if price <= 0:
            return
        self._spot_hist[asset.upper()].append((ts or time.time(), price))

    def evaluate_market(
        self,
        market: UpDownMarket,
        *,
        spot: float | None,
        ref: float | None,
    ) -> dict[str, Any]:
        """Steps 2–3: features + own P(Up)."""
        sec = market.seconds_remaining()
        window_sec = None
        if market.window_start and market.window_end:
            window_sec = (
                market.window_end.timestamp() - market.window_start.timestamp()
            )
        vol = _rel_vol(self._spot_hist[market.asset], self.cfg.vol_floor)
        direction = "flat"
        if spot is not None and ref is not None and ref > 0:
            if spot > ref * 1.00005:
                direction = "up"
            elif spot < ref * 0.99995:
                direction = "down"
        mkt_p = _market_p_up(market)
        model_p = (
            model_p_up(
                spot=spot,
                ref=ref,
                vol=vol,
                seconds_remaining=sec,
                window_seconds=window_sec,
            )
            if spot is not None and ref is not None
            else None
        )
        csum = market.complete_set_ask_sum()
        up_spread = _side_spread(market, "up")
        down_spread = _side_spread(market, "down")
        return {
            "slug": market.slug,
            "asset": market.asset,
            "spot": spot,
            "reference_price": ref,
            "direction_spot": direction,
            "vol": round(vol, 6),
            "seconds_remaining": None if sec is None else round(sec, 2),
            "window_seconds": window_sec,
            "market_p_up": None if mkt_p is None else round(mkt_p, 4),
            "model_p_up": None if model_p is None else round(model_p, 4),
            "complete_set_sum": csum,
            "up_spread": up_spread,
            "down_spread": down_spread,
            "up_ask": market.up.best_ask if market.up else None,
            "down_ask": market.down.best_ask if market.down else None,
            "has_bbo": bool(
                market.up
                and market.up.has_bbo
                and market.down
                and market.down.has_bbo
            ),
        }

    def decide_actions(
        self,
        book: CryptoPaperBook,
        market: UpDownMarket,
        evaluation: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Steps 4–5: mispricing, complete-set, sniping. Fail closed."""
        actions: list[dict[str, Any]] = []
        slug = market.slug
        model_p = evaluation.get("model_p_up")
        mkt_p = evaluation.get("market_p_up")
        spot = evaluation.get("spot")
        sec = evaluation.get("seconds_remaining")
        cfg = self.cfg

        if spot is None:
            return [{"slug": slug, "action": "skip", "reason": "no_spot"}]
        if not evaluation.get("has_bbo"):
            return [{"slug": slug, "action": "skip", "reason": "missing_bbo"}]
        if model_p is None or mkt_p is None:
            return [{"slug": slug, "action": "skip", "reason": "no_probs"}]

        # --- Step 4a: complete-set when both asks cheap ---
        csum = evaluation.get("complete_set_sum")
        if csum is not None and csum < cfg.complete_set_max_sum:
            half = cfg.complete_set_size_usd / 2.0
            for side in ("up", "down"):
                if _position_usd(book, slug, side) + half > cfg.max_usd_per_market_side:
                    actions.append(
                        {
                            "slug": slug,
                            "action": "skip",
                            "reason": "inventory_cap",
                            "side": side,
                            "phase": "complete_set",
                        }
                    )
                    continue
                err = book.try_buy(market, side=side, size_usd=half)
                actions.append(
                    {
                        "slug": slug,
                        "action": "paper_buy" if err is None else "skip",
                        "reason": err,
                        "side": side,
                        "size_usd": half,
                        "phase": "complete_set",
                        "complete_set_sum": csum,
                    }
                )

        # --- Step 4b: mispricing — buy undervalued outcome ---
        edge_up = float(model_p) - float(mkt_p)  # model higher than market → buy up
        # liquidity gate
        def liq_ok(side: Side) -> str | None:
            sp = _side_spread(market, side)
            if sp is None:
                return "no_spread"
            if sp > cfg.max_spread:
                return "wide_spread"
            return None

        if edge_up >= cfg.min_edge:
            side: Side = "up"
            bad = liq_ok(side)
            if bad:
                actions.append(
                    {"slug": slug, "action": "skip", "reason": bad, "phase": "mispricing", "side": side}
                )
            elif _position_usd(book, slug, side) + cfg.size_usd > cfg.max_usd_per_market_side:
                actions.append(
                    {
                        "slug": slug,
                        "action": "skip",
                        "reason": "inventory_cap",
                        "phase": "mispricing",
                        "side": side,
                    }
                )
            else:
                err = book.try_buy(market, side=side, size_usd=cfg.size_usd)
                actions.append(
                    {
                        "slug": slug,
                        "action": "paper_buy" if err is None else "skip",
                        "reason": err,
                        "side": side,
                        "size_usd": cfg.size_usd,
                        "phase": "mispricing",
                        "edge": round(edge_up, 4),
                        "model_p_up": model_p,
                        "market_p_up": mkt_p,
                    }
                )
        elif edge_up <= -cfg.min_edge:
            side = "down"
            bad = liq_ok(side)
            if bad:
                actions.append(
                    {"slug": slug, "action": "skip", "reason": bad, "phase": "mispricing", "side": side}
                )
            elif _position_usd(book, slug, side) + cfg.size_usd > cfg.max_usd_per_market_side:
                actions.append(
                    {
                        "slug": slug,
                        "action": "skip",
                        "reason": "inventory_cap",
                        "phase": "mispricing",
                        "side": side,
                    }
                )
            else:
                err = book.try_buy(market, side=side, size_usd=cfg.size_usd)
                actions.append(
                    {
                        "slug": slug,
                        "action": "paper_buy" if err is None else "skip",
                        "reason": err,
                        "side": side,
                        "size_usd": cfg.size_usd,
                        "phase": "mispricing",
                        "edge": round(-edge_up, 4),
                        "model_p_up": model_p,
                        "market_p_up": mkt_p,
                    }
                )

        # --- Step 5: sniping near end ---
        if sec is not None and 0 < float(sec) <= cfg.snipe_seconds:
            # clear favorite: model or market
            fav_model = "up" if float(model_p) >= 0.5 else "down"
            fav_p = max(float(model_p), 1.0 - float(model_p))
            mkt_fav_p = max(float(mkt_p), 1.0 - float(mkt_p))
            clear = fav_p >= cfg.snipe_min_p or mkt_fav_p >= cfg.snipe_min_p
            if clear:
                # prefer agreement of model + market
                mkt_fav = "up" if float(mkt_p) >= 0.5 else "down"
                side = fav_model if fav_model == mkt_fav else (
                    fav_model if fav_p >= mkt_fav_p else mkt_fav
                )
                bad = liq_ok(side)  # type: ignore[arg-type]
                if bad:
                    actions.append(
                        {
                            "slug": slug,
                            "action": "skip",
                            "reason": bad,
                            "phase": "snipe",
                            "side": side,
                        }
                    )
                elif _position_usd(book, slug, side) + cfg.snipe_size_usd > cfg.max_usd_per_market_side:
                    actions.append(
                        {
                            "slug": slug,
                            "action": "skip",
                            "reason": "inventory_cap",
                            "phase": "snipe",
                            "side": side,
                        }
                    )
                else:
                    err = book.try_buy(market, side=side, size_usd=cfg.snipe_size_usd)
                    actions.append(
                        {
                            "slug": slug,
                            "action": "paper_buy" if err is None else "skip",
                            "reason": err,
                            "side": side,
                            "size_usd": cfg.snipe_size_usd,
                            "phase": "snipe",
                            "model_p_up": model_p,
                            "market_p_up": mkt_p,
                            "seconds_remaining": sec,
                        }
                    )

        if not actions:
            actions.append({"slug": slug, "action": "hold", "reason": "no_edge"})
        return actions

    def run_poll(
        self,
        book: CryptoPaperBook,
        markets: list[UpDownMarket],
        spots: dict[str, float],
        references: dict[str, float],
        *,
        execute: bool = False,
    ) -> StrategyResult:
        """Full poll: update vol, evaluate, optional paper execute, emit signals."""
        now = time.time()
        for asset, px in spots.items():
            self.note_spot(asset, px, now)

        evaluations: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        for m in markets:
            ev = self.evaluate_market(
                m, spot=spots.get(m.asset), ref=references.get(m.slug)
            )
            evaluations.append(ev)
            if execute:
                actions.extend(self.decide_actions(book, m, ev))
            else:
                # Shadow: still compute intents without filling? decide would mutate.
                # Shadow only logs evaluation; no decide_actions without execute.
                actions.append(
                    {
                        "slug": m.slug,
                        "action": "shadow",
                        "model_p_up": ev.get("model_p_up"),
                        "market_p_up": ev.get("market_p_up"),
                        "phase": "eval_only",
                    }
                )

        signals = scan_implied_direction(
            markets,
            spots,
            references=references,
            edge_threshold=self.cfg.signal_edge_threshold,
        )
        # Enrich signals with model_p when available
        by_slug = {e["slug"]: e for e in evaluations}
        enriched: list[ImpliedDirectionSignal] = []
        for s in signals:
            ev = by_slug.get(s.slug or "")
            if ev and ev.get("model_p_up") is not None:
                mp = float(ev["model_p_up"])
                direction = "flat"
                conf = s.confidence
                if mp >= 0.5 + self.cfg.signal_edge_threshold:
                    direction = "up"
                    conf = max(conf, min(1.0, (mp - 0.5) / 0.45))
                elif mp <= 0.5 - self.cfg.signal_edge_threshold:
                    direction = "down"
                    conf = max(conf, min(1.0, (0.5 - mp) / 0.45))
                enriched.append(
                    s.model_copy(
                        update={
                            "direction": direction,
                            "p_up": mp,
                            "confidence": round(conf, 4),
                            "note": (s.note or "") + "+model_p",
                            "reference_price": ev.get("reference_price"),
                            "spot": ev.get("spot"),
                        }
                    )
                )
            else:
                enriched.append(s)

        return StrategyResult(
            signals=enriched,
            actions=actions,
            evaluations=evaluations,
        )
