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

from .mecab_stage import add_ngrams, clean_kana, hira_to_kata

# ---------------------------------------------------------------------------
# 「繋ぎの語」抽出ルール(大岡俊彦氏)。token.dep_ / pos_ / lemma_ で判定する。
# ---------------------------------------------------------------------------

# 1. deprel ベース(これだけで大部分を網羅)
TSUNAGI_DEPRELS = frozenset({
    "case",       # 格助詞: に・を・で・から・まで・より
    "mark",       # 接続助詞・引用: て・ば・と・ながら・ので・のに・けれど
    "aux",        # 助動詞: た・ない・れる・られる・せる・ます・でしょう
    "cop",        # コピュラ: だ・です・である
    "cc",         # 等位接続: と・や・か・および・または
    "discourse",  # 間投詞・終助詞: まあ・ねえ・よ・ね・な・さ・ぞ
    "fixed",      # 複合辞の非主要部: 「にもかかわらず」の「も」「かかわら」等
})

# 2. 指示代名詞(deprel では nsubj/obl 等になり漏れる)— コソアド体系
DEMONSTRATIVE_LEMMAS = frozenset({
    "これ", "それ", "あれ", "どれ",
    "この", "その", "あの", "どの",
    "ここ", "そこ", "あそこ", "どこ",
    "こう", "そう", "ああ", "どう",
    "こんな", "そんな", "あんな", "どんな",
    "こちら", "そちら", "あちら", "どちら",
})

# 3. 形式名詞(実質的意味が希薄で文法機能を担う名詞)
FORMAL_NOUN_LEMMAS = frozenset({
    "こと", "もの", "ところ", "わけ", "はず",
    "よう", "ため", "せい", "おかげ", "まま",
    "うち", "ほう", "とおり", "かわり", "つもり",
    "の",   # 準体助詞用法(「行くのが好き」の「の」)
})

# 4. 接続副詞(deprel=advmod になり内容副詞と区別不能 → lemma リスト)
CONJUNCTIVE_ADVERB_LEMMAS = frozenset({
    "しかし", "だから", "したがって", "ところが", "ところで",
    "それで", "そこで", "すると", "つまり", "すなわち",
    "また", "なお", "ただし", "もっとも", "むしろ",
    "一方", "逆に", "要するに", "結局", "ちなみに",
    "それでも", "けれども", "だが", "でも", "しかも",
})

# 5. 補助動詞(本動詞に後接して文法的機能を担う)。
#    deprel=advcl/compound の主要部側に立つため deprel だけでは漏れる。
#    直前トークンが「て」「で」「に」の場合のみ繋ぎと判定する。
#    なる・するは本動詞用法が多く誤判定を招くためこのルールから除外する。
AUXILIARY_VERB_LEMMAS = frozenset({
    "しまう", "いく", "くる", "おく", "みる", "いる", "ある",
    "もらう", "くれる", "あげる", "くださる", "いただく",
})
_AUX_PREV_LEMMAS = frozenset({"て", "で", "に"})

# 6. 指示代名詞に直接後接する「いう」「した」(こういう・そうした 等)。
#    「した」は し(VERB, lemma する) + た(AUX) に分割されるため、
#    lemma が「する」の場合を対象にする。
_DEMONSTRATIVE_FOLLOW_VERB_LEMMAS = frozenset({"いう", "する"})

# 句読点・記号・空白はどちらのチャンクにも属さず、チャンク境界として働く
_CHUNK_BOUNDARY_POS = frozenset({"PUNCT", "SYM", "SPACE"})


def is_tsunagi(token) -> bool:
    """GiNZA の Token が「繋ぎの語」なら True。"""
    if token.dep_ in TSUNAGI_DEPRELS:
        return True
    if token.lemma_ in DEMONSTRATIVE_LEMMAS:
        return True
    if token.pos_ == "NOUN" and token.lemma_ in FORMAL_NOUN_LEMMAS:
        return True
    if token.pos_ == "ADV" and token.lemma_ in CONJUNCTIVE_ADVERB_LEMMAS:
        return True
    if token.pos_ == "VERB" and token.lemma_ in AUXILIARY_VERB_LEMMAS:
        if token.i > token.sent.start and token.nbor(-1).lemma_ in _AUX_PREV_LEMMAS:
            return True
    if token.pos_ == "VERB" and token.lemma_ in _DEMONSTRATIVE_FOLLOW_VERB_LEMMAS:
        if token.i > token.sent.start and token.nbor(-1).lemma_ in DEMONSTRATIVE_LEMMAS:
            return True
    return False


@dataclass
class GinzaStats:
    """Stage 2 の集計結果。"""

    n_sentences: int = 0
    n_bunsetsu: int = 0
    # 1. 文節境界統計: 文節頭 / 尻カナ 1-gram、文節長分布
    bunsetsu_head_kana: Counter = field(default_factory=Counter)  # kana
    bunsetsu_tail_kana: Counter = field(default_factory=Counter)
    bunsetsu_len_dist: Counter = field(default_factory=Counter)  # 表層文字数
    # 2. 文節境界跨ぎカナ2/3-gram vs 文節内2/3-gram
    kana_bigram_within_bunsetsu: Counter = field(default_factory=Counter)  # (k1, k2)
    kana_bigram_cross_bunsetsu: Counter = field(default_factory=Counter)
    kana_trigram_within_bunsetsu: Counter = field(default_factory=Counter)  # (k1, k2, k3)
    kana_trigram_cross_bunsetsu: Counter = field(default_factory=Counter)
    # 3. depラベル × (係り元品詞, 係り先品詞)
    dep_pos_pairs: Counter = field(default_factory=Counter)  # (dep, 元pos, 先pos)
    # 4. 文節先頭品詞の遷移
    bunsetsu_head_pos_transition: Counter = field(default_factory=Counter)  # (pos, pos)
    # 5. 「繋ぎの語」チャンク統計。連続する繋ぎ語を膠着させて1塊とし、
    #    塊内のカナ2/3-gramを集計する。繋ぎ以外(内容チャンク)と境界跨ぎも対で持つ
    tsunagi_chunk_freq: Counter = field(default_factory=Counter)  # 連結カナ
    kana_bigram_within_tsunagi: Counter = field(default_factory=Counter)  # (k1, k2)
    kana_trigram_within_tsunagi: Counter = field(default_factory=Counter)  # (k1, k2, k3)
    kana_bigram_within_content: Counter = field(default_factory=Counter)
    kana_trigram_within_content: Counter = field(default_factory=Counter)
    kana_bigram_cross_chunk: Counter = field(default_factory=Counter)
    kana_trigram_cross_chunk: Counter = field(default_factory=Counter)


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


def _sent_chunks(sent, kanas: Sequence[str]) -> list:
    """文を「繋ぎ」/「内容」チャンクの列に分割する。

    kanas は sent の各トークンの読み(文先頭からの位置で対応)。
    連続する同種トークンを膠着させて1塊とし、(種別, 連結カナ) のリストを返す。
    句読点・記号・空白はチャンク境界として働き、どちらの塊にも含めない。
    """
    chunks = []
    cur_type = None
    cur_kana = ""
    for token in sent:
        if token.pos_ in _CHUNK_BOUNDARY_POS:
            if cur_type is not None and cur_kana:
                chunks.append((cur_type, cur_kana))
            cur_type, cur_kana = None, ""
            continue
        typ = "tsunagi" if is_tsunagi(token) else "content"
        if typ != cur_type:
            if cur_type is not None and cur_kana:
                chunks.append((cur_type, cur_kana))
            cur_type, cur_kana = typ, ""
        cur_kana += kanas[token.i - sent.start]
    if cur_type is not None and cur_kana:
        chunks.append((cur_type, cur_kana))
    return chunks


def _cross_ngrams(stats_bigram: Counter, stats_trigram: Counter, r: str, s: str) -> None:
    """隣接する読み r, s の境界を跨ぐ2/3-gramを積む(r, s は非空)。"""
    stats_bigram[(r[-1], s[0])] += 1
    if len(r) >= 2:
        stats_trigram[(r[-2], r[-1], s[0])] += 1
    if len(s) >= 2:
        stats_trigram[(r[-1], s[0], s[1])] += 1


def _accumulate(stats: GinzaStats, doc, bunsetu_spans) -> None:
    for sent in doc.sents:
        stats.n_sentences += 1
        for token in sent:
            head = token.head
            stats.dep_pos_pairs[(token.dep_, token.pos_, head.pos_)] += 1
        kanas = [_token_kana(t) for t in sent]  # 読みの導出は1トークン1回
        spans = bunsetu_spans(sent)
        stats.n_bunsetsu += len(spans)
        readings = []
        head_pos_seq = []
        for span in spans:
            stats.bunsetsu_len_dist[len(span.text)] += 1
            head_pos_seq.append(span[0].pos_)
            kana = "".join(kanas[t.i - sent.start] for t in span)
            readings.append(kana)
            if kana:
                stats.bunsetsu_head_kana[kana[0]] += 1
                stats.bunsetsu_tail_kana[kana[-1]] += 1
                add_ngrams(kana, stats.kana_bigram_within_bunsetsu,
                           stats.kana_trigram_within_bunsetsu)
        for r, s in zip(readings, readings[1:]):
            if r and s:
                _cross_ngrams(stats.kana_bigram_cross_bunsetsu,
                              stats.kana_trigram_cross_bunsetsu, r, s)
        add_ngrams(head_pos_seq, stats.bunsetsu_head_pos_transition)

        # 「繋ぎの語」チャンク統計
        chunks = _sent_chunks(sent, kanas)
        for typ, kana in chunks:
            if typ == "tsunagi":
                stats.tsunagi_chunk_freq[kana] += 1
                add_ngrams(kana, stats.kana_bigram_within_tsunagi,
                           stats.kana_trigram_within_tsunagi)
            else:
                add_ngrams(kana, stats.kana_bigram_within_content,
                           stats.kana_trigram_within_content)
        for (_, r), (_, s) in zip(chunks, chunks[1:]):
            _cross_ngrams(stats.kana_bigram_cross_chunk,
                          stats.kana_trigram_cross_chunk, r, s)


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
    try:
        import spacy
    except ImportError as e:
        raise ImportError(
            f"spacy を読み込めません: {e}\n"
            "  pip install ja-ginza  で spacy と関連パッケージを導入してください。\n"
            "  click が無い場合は  pip install click  も実行してください。"
        ) from e

    try:
        nlp = spacy.load(model)
    except Exception as e:  # noqa: BLE001
        try:
            from confection._errors import ConfigValidationError
        except ImportError:
            raise e from None
        if not isinstance(e, ConfigValidationError) or "split_mode" not in str(e):
            raise
        # ja_ginza の config は compound_splitter の split_mode を null で持つが、
        # 新しめの confection/pydantic はこれを str として厳格に検証して弾く。
        # その場合のみ GiNZA 既定の分割モード "C" を明示して再ロードする。
        nlp = spacy.load(
            model,
            config={"components": {"compound_splitter": {"split_mode": "C"}}},
        )
    from ginza import bunsetu_spans  # 重量級のため Stage 2 実行時まで遅延

    nproc = default_n_process(n_process)
    stats = GinzaStats()
    try:
        for doc in nlp.pipe(sentences, batch_size=batch_size, n_process=nproc):
            _accumulate(stats, doc, bunsetu_spans)
            if on_progress:
                on_progress(1)
    except BrokenPipeError:
        raise RuntimeError(
            f"GiNZA の子プロセスが異常終了しました (n_process={nproc})。\n"
            "  config.toml で [ginza] n_process = 1 にして再実行してください。"
        )
    return stats
