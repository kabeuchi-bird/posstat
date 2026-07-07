"""Stage 2: GiNZA による文節・係り受け解析(全量)。

- モデル: ja_ginza(config で ja_ginza_electra 切替可)
- nlp.pipe(sentences, batch_size, n_process) で全文処理。
  Windows の spawn 対策としてエントリポイント側に
  `if __name__ == "__main__":` ガードが必須(__main__.py 参照)。
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from .mecab_stage import clean_kana, hira_to_kata


@dataclass
class GinzaStats:
    """Stage 2 の集計結果。"""

    n_sentences: int = 0
    n_bunsetsu: int = 0
    # 1. 文節境界統計: 文節頭 / 尻カナ 1-gram、文節長分布
    bunsetsu_head_kana: Counter = field(default_factory=Counter)  # kana
    bunsetsu_tail_kana: Counter = field(default_factory=Counter)
    bunsetsu_len_dist: Counter = field(default_factory=Counter)  # 表層文字数
    # 2. 文節境界跨ぎカナ2-gram vs 文節内2-gram
    kana_bigram_within_bunsetsu: Counter = field(default_factory=Counter)  # (k1, k2)
    kana_bigram_cross_bunsetsu: Counter = field(default_factory=Counter)
    # 3. depラベル × (係り元品詞, 係り先品詞)
    dep_pos_pairs: Counter = field(default_factory=Counter)  # (dep, 元pos, 先pos)
    # 4. 文節先頭品詞の遷移
    bunsetsu_head_pos_transition: Counter = field(default_factory=Counter)  # (pos, pos)


def default_n_process(configured: int) -> int:
    """config の n_process(0 = cpu_count - 1)を実値に解決する。"""
    if configured and configured > 0:
        return configured
    return max(1, (os.cpu_count() or 2) - 1)


def _token_kana(token) -> str:
    """GiNZA トークンの読み(カタカナ)。morph の Reading を優先。"""
    reading = token.morph.get("Reading")
    if reading:
        kana = clean_kana(reading[0])
        if kana:
            return kana
    return clean_kana(hira_to_kata(token.text))


def _accumulate(stats: GinzaStats, doc) -> None:
    import ginza

    for sent in doc.sents:
        stats.n_sentences += 1
        for token in sent:
            head = token.head
            stats.dep_pos_pairs[(token.dep_, token.pos_, head.pos_)] += 1
        spans = ginza.bunsetu_spans(sent)
        stats.n_bunsetsu += len(spans)
        readings = []
        head_pos_seq = []
        for span in spans:
            stats.bunsetsu_len_dist[len(span.text)] += 1
            head_pos_seq.append(span[0].pos_)
            kana = "".join(_token_kana(t) for t in span)
            readings.append(kana)
            if kana:
                stats.bunsetsu_head_kana[kana[0]] += 1
                stats.bunsetsu_tail_kana[kana[-1]] += 1
                for a, b in zip(kana, kana[1:]):
                    stats.kana_bigram_within_bunsetsu[(a, b)] += 1
        for r, s in zip(readings, readings[1:]):
            if r and s:
                stats.kana_bigram_cross_bunsetsu[(r[-1], s[0])] += 1
        for a, b in zip(head_pos_seq, head_pos_seq[1:]):
            stats.bunsetsu_head_pos_transition[(a, b)] += 1


def run(
    sentences: Sequence[str],
    model: str = "ja_ginza",
    batch_size: int = 128,
    n_process: int = 0,
    on_progress: Optional[Callable[[int], None]] = None,
) -> GinzaStats:
    """全文を GiNZA で解析して GinzaStats を返す。

    nlp.pipe() はイテレータなので、消費側ループで文数をカウントして
    そのまま進捗を更新する。
    """
    import spacy

    try:
        nlp = spacy.load(model)
    except Exception as e:  # noqa: BLE001
        # ja_ginza の config は compound_splitter の split_mode を null で持つが、
        # 新しめの confection/pydantic はこれを str として厳格に検証して弾く。
        # その場合のみ GiNZA 既定の分割モード "C" を明示して再ロードする。
        if "split_mode" not in str(e):
            raise
        nlp = spacy.load(
            model,
            config={"components": {"compound_splitter": {"split_mode": "C"}}},
        )
    nproc = default_n_process(n_process)
    stats = GinzaStats()
    for doc in nlp.pipe(sentences, batch_size=batch_size, n_process=nproc):
        _accumulate(stats, doc)
        if on_progress:
            on_progress(1)
    return stats
