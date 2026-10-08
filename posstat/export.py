"""stats.json 出力。

Rust 側(tsuki_optimizer / MzKana)から serde で読む前提の構造:

{
  "meta": {"chars": ..., "sentences": ..., "tokens": ..., "files": ...,
           "model": "ja_ginza", "generated": "..."},
  "pos_transition": {"名詞": {"助詞": 0.42, ...}, ...},
  "kana_bigram_within_pos": {...},
  "kana_bigram_cross_boundary": {...},
  "forbidden_pairs": [{"a": "ヲ", "b": "ヲ", "pmi": -8.2}, ...],
  "bunsetsu_head_kana": {...},
  "bunsetsu_tail_kana": {...},
  "bunsetsu_head_pos_transition": {"NOUN": {"VERB": 0.31, ...}, ...},
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
  "kana_trigram_cross_chunk": {...},
  "bunsetsu_suffix_freq": {"ヲ": 0.08, ...}
}

- 2-gram 系は {先行: {後続: P(後続|先行)}}、3-gram 系は {a: {b: {c: P(c|a,b)}}}
- tsunagi 系は「繋ぎの語」チャンク(連続する繋ぎ語を膠着させた1塊)内の連接、
  content 系はそれ以外の内容チャンク内、cross_chunk はチャンク境界跨ぎ
- forbidden_pairs の pmi は観測ゼロのとき null(Rust 側は Option<f64>)
- bunsetsu_suffix_freq は文節付属部(文節末尾に連続する繋ぎ語。文節先頭トークンは除く)
  の連結カナ分布。付属部を持たない文節は分母に含まない

--bunsetsu-freq 指定時は別ファイルで文節そのものの頻度を出す(write_bunsetsu_freq):

- bunsetsu_kana_freq.tsv: 読み単位。列は 読み / 自立部 / 付属部 / 頻度
- bunsetsu_tokens.jsonl: 表層込みのトークン列単位。1行1文節
  {"count": 12, "reading": "...", "suffix_start": 1,
   "tokens": [["表層", "読み", false], ["を", "ヲ", true]]}
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Optional, Tuple

from . import aggregate
from .mecab_stage import MecabStats

if TYPE_CHECKING:
    from .ginza_stage import GinzaStats

# GiNZA 由来の出力キー(= GinzaStats の同名 Counter 属性)と正規化関数。
# Stage 2 未実行時は空 dict を出す(キー自体は常に存在させ、Rust 側の
# 構造体定義を安定させる)
_GINZA_EXPORTS: Dict[str, Callable] = {
    "bunsetsu_head_kana": aggregate.distribution,
    "bunsetsu_tail_kana": aggregate.distribution,
    "bunsetsu_kana_len_dist": aggregate.distribution,
    "bunsetsu_head_pos_transition": aggregate.row_normalize,
    "kana_bigram_within_bunsetsu": aggregate.row_normalize,
    "kana_bigram_cross_bunsetsu": aggregate.row_normalize,
    "kana_trigram_within_bunsetsu": aggregate.row_normalize_trigram,
    "kana_trigram_cross_bunsetsu": aggregate.row_normalize_trigram,
    "tsunagi_chunk_freq": aggregate.distribution,
    "kana_bigram_within_tsunagi": aggregate.row_normalize,
    "kana_trigram_within_tsunagi": aggregate.row_normalize_trigram,
    "kana_bigram_within_content": aggregate.row_normalize,
    "kana_trigram_within_content": aggregate.row_normalize_trigram,
    "kana_bigram_cross_chunk": aggregate.row_normalize,
    "kana_trigram_cross_chunk": aggregate.row_normalize_trigram,
    "bunsetsu_suffix_freq": aggregate.distribution,
}


def build_stats(
    mecab: MecabStats,
    ginza: Optional[GinzaStats],
    total_chars: int,
    n_sentences: int,
    n_files: int = 0,
    model: str = "",
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
            "files": n_files,
            "model": model,
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "pos_transition": aggregate.row_normalize(mecab.pos_bigram),
        "kana_bigram_within_pos": aggregate.row_normalize(mecab.kana_bigram_within),
        "kana_bigram_cross_boundary": aggregate.row_normalize(mecab.kana_bigram_cross),
        "forbidden_pairs": aggregate.forbidden_pairs(adjacency, pmi_threshold, min_count),
    }
    for key, normalize in _GINZA_EXPORTS.items():
        stats[key] = normalize(getattr(ginza, key)) if ginza is not None else {}
    return stats


def write_json(stats: Dict, out_dir: Path) -> Path:
    """stats を out_dir/stats.json に書き出し、そのパスを返す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "stats.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    return path


def write_bunsetsu_freq(
    bunsetsu_freq: Counter, out_dir: Path, min_count: int = 1,
) -> Tuple[Path, Path]:
    """文節頻度を bunsetsu_kana_freq.tsv / bunsetsu_tokens.jsonl に書き出す。

    bunsetsu_freq のキーは ((表層, 読み, 繋ぎ判定), ...)。
    - TSV は (自立部カナ, 付属部カナ) で集約してから min_count で切る。
      同じ読みでも自立部/付属部の切れ目が違えば別行になる。読みが空の文節は除く
    - JSONL はトークン列単位のまま min_count で切る
    どちらも頻度降順(同頻度はキー順)。(TSV パス, JSONL パス) を返す。
    """
    from .ginza_stage import suffix_start  # Stage 2 実行時のみ呼ばれる

    out_dir.mkdir(parents=True, exist_ok=True)

    kana_freq: Counter = Counter()
    for key, cnt in bunsetsu_freq.items():
        sfx = suffix_start([f for _, _, f in key])
        content = "".join(k for _, k, _ in key[:sfx])
        suffix = "".join(k for _, k, _ in key[sfx:])
        if content or suffix:
            kana_freq[(content, suffix)] += cnt

    tsv_path = out_dir / "bunsetsu_kana_freq.tsv"
    with tsv_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("reading\tcontent\tsuffix\tcount\n")
        for (content, suffix), cnt in sorted(kana_freq.items(), key=lambda kv: (-kv[1], kv[0])):
            if cnt < min_count:
                break
            f.write(f"{content}{suffix}\t{content}\t{suffix}\t{cnt}\n")

    jsonl_path = out_dir / "bunsetsu_tokens.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as f:
        for key, cnt in sorted(bunsetsu_freq.items(), key=lambda kv: (-kv[1], kv[0])):
            if cnt < min_count:
                break
            rec = {
                "count": cnt,
                "reading": "".join(k for _, k, _ in key),
                "suffix_start": suffix_start([fl for _, _, fl in key]),
                "tokens": [[s, k, fl] for s, k, fl in key],
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return tsv_path, jsonl_path
