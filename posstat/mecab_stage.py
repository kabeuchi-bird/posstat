"""Stage 1: fugashi(MeCab + unidic-lite)による全量形態素解析。

単一プロセス(600万字で10秒前後のため並列化しない)。
文ごとに (品詞大分類, 細分類, 活用形, 書字形, 仮名) 列を作り Counter に積む。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

# 統計対象の記号(前後の品詞・隣接カナを集計する)
SYMBOLS = set("\uff01\uff1f\u2026\u300c\u300d\u300e\u300f")  # ！？…「」『』

def hira_to_kata(s: str) -> str:
    """ひらがな→カタカナ変換(それ以外は素通し)。"""
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def _is_kana(c: str) -> bool:
    return "ァ" <= c <= "ヶ" or c == "ー"


def clean_kana(s: str) -> str:
    """カタカナ(と長音)以外を落とす。"""
    return "".join(c for c in s if _is_kana(c))


def add_ngrams(seq: Sequence, bigram: Counter, trigram: Optional[Counter] = None) -> None:
    """列の隣接2-gram(trigram 指定時は3-gramも)を Counter に積む。"""
    for a, b in zip(seq, seq[1:]):
        bigram[(a, b)] += 1
    if trigram is not None:
        for a, b, c in zip(seq, seq[1:], seq[2:]):
            trigram[(a, b, c)] += 1


def token_reading(feature, surface: str) -> str:
    """トークンの仮名(カタカナ)を得る。

    kana は unidic(フル版)用。unidic-lite では存在しないため pron / lForm にフォールバックする。
    どれも無ければ表層のかなをカタカナ化して使う。
    """
    for attr in ("kana", "pron", "lForm"):
        v = getattr(feature, attr, None)
        if v and v != "*":
            kana = clean_kana(str(v))
            if kana:
                return kana
    return clean_kana(hira_to_kata(surface))


@dataclass
class MecabStats:
    """Stage 1 の集計結果。キーはすべて文字列またはタプル。"""

    n_tokens: int = 0
    # 1. 品詞1-gram(大分類 / 細分類)
    pos1_unigram: Counter = field(default_factory=Counter)
    pos2_unigram: Counter = field(default_factory=Counter)  # "名詞-普通名詞" 形式
    # 2. 品詞2/3-gram(大分類)
    pos_bigram: Counter = field(default_factory=Counter)  # (pos, pos)
    pos_trigram: Counter = field(default_factory=Counter)  # (pos, pos, pos)
    # 3. 活用形分布(動詞・形容詞別)
    cform_by_pos: Counter = field(default_factory=Counter)  # (pos1, cForm)
    # 4. 品詞ごとの頭カナ / 尻カナ 1-gram
    head_kana_by_pos: Counter = field(default_factory=Counter)  # (pos1, kana)
    tail_kana_by_pos: Counter = field(default_factory=Counter)
    # 5. 品詞内カナ2-gram / 品詞境界跨ぎカナ2-gram(区別して集計)
    kana_bigram_within: Counter = field(default_factory=Counter)  # (k1, k2)
    kana_bigram_cross: Counter = field(default_factory=Counter)
    # 6. 記号前後の品詞と隣接カナ
    symbol_prev_pos: Counter = field(default_factory=Counter)  # (記号, pos1)
    symbol_next_pos: Counter = field(default_factory=Counter)
    symbol_prev_kana: Counter = field(default_factory=Counter)  # (記号, 尻カナ)
    symbol_next_kana: Counter = field(default_factory=Counter)  # (記号, 頭カナ)


Token = Tuple[str, str, str, str, str]  # (pos1, pos2, cForm, 書字形, 仮名)


def _tokenize(tagger, sentence: str) -> List[Token]:
    tokens: List[Token] = []
    for word in tagger(sentence):
        f = word.feature
        pos1 = getattr(f, "pos1", "*") or "*"
        pos2 = getattr(f, "pos2", "*") or "*"
        cform = getattr(f, "cForm", "*") or "*"
        surface = word.surface
        kana = token_reading(f, surface)
        tokens.append((pos1, pos2, cform, surface, kana))
    return tokens


def _accumulate(stats: MecabStats, tokens: Sequence[Token]) -> None:
    n = len(tokens)
    stats.n_tokens += n
    for i, (pos1, pos2, cform, surface, kana) in enumerate(tokens):
        stats.pos1_unigram[pos1] += 1
        stats.pos2_unigram[f"{pos1}-{pos2}"] += 1
        if pos1 in ("動詞", "形容詞") and cform != "*":
            stats.cform_by_pos[(pos1, cform)] += 1
        if kana:
            stats.head_kana_by_pos[(pos1, kana[0])] += 1
            stats.tail_kana_by_pos[(pos1, kana[-1])] += 1
            add_ngrams(kana, stats.kana_bigram_within)
        # 記号前後の統計(先頭1文字の判定で大多数のトークンの set 生成を回避)
        if surface and surface[0] in SYMBOLS and set(surface) <= SYMBOLS:
            sym = surface[0]
            if i > 0:
                p_pos, _, _, _, p_kana = tokens[i - 1]
                stats.symbol_prev_pos[(sym, p_pos)] += 1
                if p_kana:
                    stats.symbol_prev_kana[(sym, p_kana[-1])] += 1
            if i + 1 < n:
                n_pos, _, _, _, n_kana = tokens[i + 1]
                stats.symbol_next_pos[(sym, n_pos)] += 1
                if n_kana:
                    stats.symbol_next_kana[(sym, n_kana[0])] += 1
    # n-gram(大分類)と境界跨ぎカナ
    add_ngrams([t[0] for t in tokens], stats.pos_bigram, stats.pos_trigram)
    for t, u in zip(tokens, tokens[1:]):
        if t[4] and u[4]:
            stats.kana_bigram_cross[(t[4][-1], u[4][0])] += 1


def run(
    sentences: Sequence[str],
    on_progress: Optional[Callable[[int], None]] = None,
) -> MecabStats:
    """全文を解析して MecabStats を返す。

    on_progress は処理済み文数の増分を受け取る(1000文ごとに呼び、更新コストを抑える)。
    """
    from fugashi import Tagger

    tagger = Tagger()
    stats = MecabStats()
    done = 0
    for sent in sentences:
        _accumulate(stats, _tokenize(tagger, sent))
        done += 1
        if on_progress and done % 1000 == 0:
            on_progress(1000)
    if on_progress and done % 1000:
        on_progress(done % 1000)
    return stats
