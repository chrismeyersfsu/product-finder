"""The backtest engine: pivot-sampled best-deal analysis, pure.

Owns pivot sampling, window evaluation, bootstrap CIs, and verdict
text. Never does I/O; observations arrive as dicts with site_slug,
price, observed_at (ISO), and optional score/hard_fails. Callers rely
on: run() is deterministic for a given seed, the interval per
(pivot, window) is half-open (pivot - window, pivot], windows the data
span cannot support are dropped into coverage["dropped_windows"]
instead of failing, and any window with fewer than MIN_PIVOTS
data-bearing pivots is flagged insufficient_data rather than given a
verdict.
"""

import random
from bisect import bisect_right
from datetime import UTC, datetime, timedelta
from statistics import fmean, median

DEFAULT_WINDOWS = [3, 7, 14, 28, 56, 112]  # days: 3d, 1w, 2w, 4w, 8w, 16w
MIN_PIVOTS = 30  # below this a window is reported, not interpreted
BOOTSTRAP_RESAMPLES = 1000


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _qualify(observations: list[dict], min_score: float) -> list[dict]:
    out = []
    for obs in observations:
        if not obs.get("price") or obs["price"] <= 0:
            continue
        if obs.get("hard_fails"):
            continue
        score = obs.get("score")
        if score is not None and score < min_score:
            continue
        out.append(obs)
    return out


def _sample_pivots(
    rng: random.Random, start: datetime, end: datetime, n_pivots: int
) -> list[datetime]:
    span = (end - start).total_seconds()
    if span < 0:
        return []
    return sorted(start + timedelta(seconds=rng.random() * span) for _ in range(n_pivots))


def _bootstrap_ci(rng: random.Random, values: list[float]) -> tuple[float, float]:
    means = []
    n = len(values)
    for _ in range(BOOTSTRAP_RESAMPLES):
        means.append(fmean(rng.choice(values) for _ in range(n)))
    means.sort()
    return (means[int(0.025 * len(means))], means[int(0.975 * len(means))])


def run(
    observations: list[dict],
    windows: list[int] | None = None,
    n_pivots: int = 200,
    min_score: float = 0.5,
    seed: int = 0,
    now: datetime | None = None,
    pivots: list[str] | None = None,
) -> dict:
    """Backtest best-deal-found over lookback windows from sampled pivots.

    Returns a JSON-serializable dict: params, coverage, per-window
    stats (paired against the shortest window), per-site win rates,
    verdict strings, caveats.
    """
    windows = sorted(windows or DEFAULT_WINDOWS)
    now = now or datetime.now(UTC)
    rng = random.Random(seed)
    caveats = [
        "Pivot windows overlap and share observations, so confidence intervals are optimistic.",
        "Backtests only see prices this database observed; history accrues from repeated "
        "searches, eBay sold-listing backfills (~90 days deep), and manual imports.",
    ]

    qualifying = _qualify(observations, min_score)
    result = {
        "params": {
            "windows_days": windows,
            "n_pivots": n_pivots,
            "min_score": min_score,
            "seed": seed,
        },
        "coverage": {
            "n_observations": len(observations),
            "n_qualifying": len(qualifying),
            "dropped_windows": [],
        },
        "windows": {},
        "sites": {},
        "verdict": [],
        "caveats": caveats,
    }
    if not qualifying:
        result["verdict"] = ["No qualifying observations; run searches/backfills first."]
        return result

    qualifying.sort(key=lambda o: o["observed_at"])
    times = [_parse_dt(o["observed_at"]) for o in qualifying]
    earliest = max(times[0], now - timedelta(days=365))  # skip stale pre-inflation-window data
    latest = times[-1]
    kinds: dict[str, int] = {}
    for obs in qualifying:
        kinds[obs.get("kind", "seen")] = kinds.get(obs.get("kind", "seen"), 0) + 1
    result["coverage"].update(
        {
            "span_start": earliest.isoformat(),
            "span_end": latest.isoformat(),
            "span_days": round((latest - earliest).total_seconds() / 86400, 1),
            "kinds": kinds,
        }
    )

    span_days = (latest - earliest).total_seconds() / 86400
    usable = [w for w in windows if w <= span_days]
    result["coverage"]["dropped_windows"] = [w for w in windows if w not in usable]
    if not usable:
        result["verdict"] = [
            f"Data spans only {span_days:.1f} days; the smallest window ({windows[0]}d) "
            "needs more history. Keep collecting observations."
        ]
        return result

    if pivots is not None:
        pivot_dts = sorted(_parse_dt(p) for p in pivots)
    else:
        pivot_dts = _sample_pivots(rng, earliest + timedelta(days=max(usable)), latest, n_pivots)
        if not pivot_dts:  # range collapsed: fall back to the widest start we can afford
            pivot_dts = _sample_pivots(rng, earliest + timedelta(days=usable[0]), latest, n_pivots)
    result["coverage"]["n_pivots_sampled"] = len(pivot_dts)
    if pivot_dts:
        result["coverage"]["pivot_range"] = [pivot_dts[0].isoformat(), pivot_dts[-1].isoformat()]

    # best deal per (pivot, window): min price in (pivot - w, pivot]
    best: dict[int, dict[int, dict]] = {w: {} for w in usable}
    for i, pivot in enumerate(pivot_dts):
        hi = bisect_right(times, pivot)
        for w in usable:
            lo = bisect_right(times, pivot - timedelta(days=w))
            if hi <= lo:
                continue
            winner = min(qualifying[lo:hi], key=lambda o: o["price"])
            best[w][i] = winner

    base_w = usable[0]
    site_stats: dict[str, dict] = {}
    for w in usable:
        found = best[w]
        prices = [o["price"] for o in found.values()]
        stats: dict = {"n_pivots_with_data": len(found)}
        if prices:
            stats.update(
                {
                    "mean_best_price": round(fmean(prices), 2),
                    "median_best_price": round(median(prices), 2),
                    "min_best_price": round(min(prices), 2),
                }
            )
        if len(found) < MIN_PIVOTS:
            stats["insufficient_data"] = True
        if w != base_w:
            paired = [
                (best[base_w][i]["price"] - found[i]["price"]) for i in found if i in best[base_w]
            ]
            if len(paired) >= MIN_PIVOTS:
                lo_ci, hi_ci = _bootstrap_ci(rng, paired)
                base_mean = fmean(best[base_w][i]["price"] for i in found if i in best[base_w])
                stats["vs_shortest_window"] = {
                    "baseline_days": base_w,
                    "n_paired": len(paired),
                    "mean_improvement": round(fmean(paired), 2),
                    "mean_improvement_pct": round(fmean(paired) / base_mean * 100, 1),
                    "ci95": [round(lo_ci, 2), round(hi_ci, 2)],
                    "frac_pivots_improved": round(sum(1 for d in paired if d > 0) / len(paired), 3),
                }
        result["windows"][str(w)] = stats

        for winner in found.values():
            site = winner["site_slug"]
            entry = site_stats.setdefault(site, {"wins": 0, "prices": []})
            entry["wins"] += 1
            entry["prices"].append(winner["price"])

    total_wins = sum(s["wins"] for s in site_stats.values())
    for site, entry in sorted(site_stats.items(), key=lambda kv: -kv[1]["wins"]):
        result["sites"][site] = {
            "wins": entry["wins"],
            "win_rate": round(entry["wins"] / total_wins, 3) if total_wins else 0.0,
            "mean_winning_price": round(fmean(entry["prices"]), 2),
        }

    result["verdict"] = _verdict(result, base_w)
    return result


def _verdict(result: dict, base_w: int) -> list[str]:
    lines = []
    for w, stats in result["windows"].items():
        vs = stats.get("vs_shortest_window")
        if not vs:
            continue
        lo_ci, hi_ci = vs["ci95"]
        direction = "improves" if vs["mean_improvement"] > 0 else "worsens"
        significant = lo_ci > 0 or hi_ci < 0
        lines.append(
            f"Extending the search from {base_w}d to {w}d {direction} the expected best "
            f"price by ${abs(vs['mean_improvement']):.2f} ({vs['mean_improvement_pct']:+.1f}%, "
            f"95% CI ${lo_ci:.2f}..${hi_ci:.2f}"
            + (", significant)" if significant else ", NOT significant)")
        )
    insufficient = [w for w, s in result["windows"].items() if s.get("insufficient_data")]
    if insufficient:
        lines.append(
            "Insufficient data (<30 pivots) for windows: "
            + ", ".join(f"{w}d" for w in insufficient)
            + " — collect more history before trusting them."
        )
    if result["sites"]:
        top = next(iter(result["sites"].items()))
        lines.append(
            f"Best site for deals so far: {top[0]} "
            f"(supplies the best deal in {top[1]['win_rate']:.0%} of pivot-windows, "
            f"mean winning price ${top[1]['mean_winning_price']:.2f})."
        )
    return lines
