"""集計: Counter の確率化と PMI 計算。

- ペア Counter → 行方向正規化で遷移確率行列
- PMI = log2(P(xy) / (P(x)P(y)))。観測ゼロまたは PMI <= 閾値のペアを
  「後には来ない」候補として列挙。信頼性のため P(x)P(y) × 総数 >= min_count
  のペアのみ対象とする。
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Optional, Tuple


def row_normalize(pair_counter: Counter) -> Dict[str, Dict[str, float]]:
    """(a, b) -> count の Counter を {a: {b: P(b|a)}} に正規化する。"""
    rows: Dict[str, Counter] = {}
    for (a, b), c in pair_counter.items():
        rows.setdefault(a, Counter())[b] += c
    result: Dict[str, Dict[str, float]] = {}
    for a, row in rows.items():
        total = sum(row.values())
        result[a] = {b: c / total for b, c in sorted(row.items(), key=lambda kv: -kv[1])}
    return result


def row_normalize_trigram(trigram_counter: Counter) -> Dict[str, Dict[str, Dict[str, float]]]:
    """(a, b, c) -> count の Counter を {a: {b: {c: P(c|a,b)}}} に正規化する。"""
    groups: Dict[Tuple[str, str], Counter] = {}
    for (a, b, c), cnt in trigram_counter.items():
        groups.setdefault((a, b), Counter())[c] += cnt
    result: Dict[str, Dict[str, Dict[str, float]]] = {}
    for (a, b), row in groups.items():
        total = sum(row.values())
        inner = {c: cnt / total for c, cnt in sorted(row.items(), key=lambda kv: -kv[1])}
        result.setdefault(a, {})[b] = inner
    return result


def distribution(counter: Counter) -> Dict[str, float]:
    """単純な Counter を確率分布に正規化する。"""
    total = sum(counter.values())
    if total == 0:
        return {}
    return {str(k): v / total for k, v in sorted(counter.items(), key=lambda kv: -kv[1])}


def forbidden_pairs(
    pair_counter: Counter,
    pmi_threshold: float = -3.0,
    min_expected: float = 10.0,
) -> List[Dict[str, object]]:
    """PMI 下位の「後には来ない」候補ペアを列挙する。

    全ての (先行 x, 後続 y) 組合せのうち、期待頻度 P(x)P(y)×N >= min_expected
    を満たすものだけを判定対象とし、
    - 観測ゼロ → pmi は None(JSON では null。Rust 側は Option<f64> で受ける)
    - PMI <= pmi_threshold → その値
    を {"a": x, "b": y, "pmi": ..., "expected": ...} で返す(PMI 昇順)。
    """
    n_total = sum(pair_counter.values())
    if n_total == 0:
        return []
    first: Counter = Counter()
    second: Counter = Counter()
    for (a, b), c in pair_counter.items():
        first[a] += c
        second[b] += c

    results: List[Tuple[Optional[float], float, str, str]] = []
    for a, ca in first.items():
        for b, cb in second.items():
            expected = ca * cb / n_total  # P(a)P(b) × N
            if expected < min_expected:
                continue
            observed = pair_counter.get((a, b), 0)
            if observed == 0:
                results.append((None, expected, a, b))
                continue
            pmi = math.log2(observed / expected)
            if pmi <= pmi_threshold:
                results.append((pmi, expected, a, b))

    # 観測ゼロ(pmi=None)を先頭に、以降 PMI 昇順。同順位は期待頻度の大きい順
    results.sort(key=lambda r: (r[0] is not None, r[0] if r[0] is not None else 0.0, -r[1]))
    return [
        {"a": a, "b": b, "pmi": None if pmi is None else round(pmi, 3), "expected": round(exp, 1)}
        for pmi, exp, a, b in results
    ]
