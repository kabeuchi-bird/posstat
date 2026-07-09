"""stats.json 出力。

Rust 側(tsuki_optimizer / MzKana)から serde で読む前提の構造:

{
  "meta": {"chars": ..., "sentences": ..., "generated": "..."},
  "pos_transition": {"名詞": {"助詞": 0.42, ...}, ...},
  "kana_bigram_within_pos": {...},
  "kana_bigram_cross_boundary": {...},
  "forbidden_pairs": [{"a": "ヲ", "b": "ヲ", "pmi": -8.2}, ...],
  "bunsetsu_head_kana": {...},
  "bunsetsu_tail_kana": {...},
  "kana_bigram_within_bunsetsu": {"ア": {"イ": 0.42, ...}, ...},
  "kana_bigram_cross_bunsetsu": {...},
  "kana_trigram_within_bunsetsu": {"ア": {"イ": {"ウ": 0.03, ...}, ...}, ...},
  "kana_trigram_cross_bunsetsu": {...},
  "tsunagi_chunk_freq": {"テイル": 0.02, ...},
  "kana_bigram_within_tsunagi": {...},
  "kana_trigram_within_tsunagi": {...},
  "kana_bigram_within_content": {...},
  "kana_trigram_within_content": {...},
  "kana_bigram_cross_chunk": {...},
  "kana_trigram_cross_chunk": {...}
}

- 2-gram 系は {先行: {後続: P(後続|先行)}}、3-gram 系は {a: {b: {c: P(c|a,b)}}}
- tsunagi 系は「繋ぎの語」チャンク(連続する繋ぎ語を膠着させた1塊)内の連接、
  content 系はそれ以外の内容チャンク内、cross_chunk はチャンク境界跨ぎ
- forbidden_pairs の pmi は観測ゼロのとき null(Rust 側は Option<f64>)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

from . import aggregate
from .mecab_stage import MecabStats

if TYPE_CHECKING:
    from .ginza_stage import GinzaStats


def build_stats(
    mecab: MecabStats,
    ginza: Optional[GinzaStats],
    total_chars: int,
    n_sentences: int,
    pmi_threshold: float = -3.0,
    min_count: float = 10.0,
) -> Dict:
    """エクスポート用の辞書を組み立てる。"""
    # 「後には来ない」判定は隣接カナ全体(品詞内 + 境界跨ぎ)で行う
    adjacency = mecab.kana_bigram_within + mecab.kana_bigram_cross
    stats = {
        "meta": {
            "chars": total_chars,
            "sentences": n_sentences,
            "tokens": mecab.n_tokens,
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "pos_transition": aggregate.row_normalize(mecab.pos_bigram),
        "kana_bigram_within_pos": aggregate.row_normalize(mecab.kana_bigram_within),
        "kana_bigram_cross_boundary": aggregate.row_normalize(mecab.kana_bigram_cross),
        "forbidden_pairs": aggregate.forbidden_pairs(adjacency, pmi_threshold, min_count),
        "bunsetsu_head_kana": {},
        "bunsetsu_tail_kana": {},
        "kana_bigram_within_bunsetsu": {},
        "kana_bigram_cross_bunsetsu": {},
        "kana_trigram_within_bunsetsu": {},
        "kana_trigram_cross_bunsetsu": {},
        "tsunagi_chunk_freq": {},
        "kana_bigram_within_tsunagi": {},
        "kana_trigram_within_tsunagi": {},
        "kana_bigram_within_content": {},
        "kana_trigram_within_content": {},
        "kana_bigram_cross_chunk": {},
        "kana_trigram_cross_chunk": {},
    }
    if ginza is not None:
        stats["bunsetsu_head_kana"] = aggregate.distribution(ginza.bunsetsu_head_kana)
        stats["bunsetsu_tail_kana"] = aggregate.distribution(ginza.bunsetsu_tail_kana)
        stats["kana_bigram_within_bunsetsu"] = aggregate.row_normalize(
            ginza.kana_bigram_within_bunsetsu)
        stats["kana_bigram_cross_bunsetsu"] = aggregate.row_normalize(
            ginza.kana_bigram_cross_bunsetsu)
        stats["kana_trigram_within_bunsetsu"] = aggregate.row_normalize_trigram(
            ginza.kana_trigram_within_bunsetsu)
        stats["kana_trigram_cross_bunsetsu"] = aggregate.row_normalize_trigram(
            ginza.kana_trigram_cross_bunsetsu)
        stats["tsunagi_chunk_freq"] = aggregate.distribution(ginza.tsunagi_chunk_freq)
        stats["kana_bigram_within_tsunagi"] = aggregate.row_normalize(
            ginza.kana_bigram_within_tsunagi)
        stats["kana_trigram_within_tsunagi"] = aggregate.row_normalize_trigram(
            ginza.kana_trigram_within_tsunagi)
        stats["kana_bigram_within_content"] = aggregate.row_normalize(
            ginza.kana_bigram_within_content)
        stats["kana_trigram_within_content"] = aggregate.row_normalize_trigram(
            ginza.kana_trigram_within_content)
        stats["kana_bigram_cross_chunk"] = aggregate.row_normalize(
            ginza.kana_bigram_cross_chunk)
        stats["kana_trigram_cross_chunk"] = aggregate.row_normalize_trigram(
            ginza.kana_trigram_cross_chunk)
    return stats


def write_json(stats: Dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "stats.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    return path
