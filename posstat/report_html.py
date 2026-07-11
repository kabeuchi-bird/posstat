"""自己完結型 HTML レポート生成。

- テンプレート: 標準 string.Template(jinja2 不要)
- 表: 素の JS 数十行でクリックソート + テキストフィルタ。min_count で切らず全量掲載
- heatmap: matplotlib(必須依存)→ PNG → base64 埋め込み(config で off 可)
"""

from __future__ import annotations

import html
import sys
from collections import Counter
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

from .mecab_stage import MecabStats

if TYPE_CHECKING:
    from .ginza_stage import GinzaStats


def _esc(v) -> str:
    return html.escape(str(v))


def _table(headers: Sequence[str], rows: Sequence[Sequence], table_id: str) -> str:
    """ソート・フィルタ付きテーブルの HTML を返す。"""
    th = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = []
    for row in rows:
        tds = []
        for v in row:
            cls = ' class="num"' if isinstance(v, (int, float)) else ""
            if isinstance(v, float):
                v = f"{v:.4f}" if abs(v) < 1000 else f"{v:.1f}"
            tds.append(f"<td{cls}>{_esc(v)}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return (
        f'<input class="filter" type="text" placeholder="フィルタ..." data-table="{table_id}">'
        f'<div class="tablewrap"><table class="sortable" id="{table_id}">'
        f"<thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
        f"<p class=\"rowcount\">{len(rows)} 行</p>"
    )


def _counter_rows(counter: Counter, limit: Optional[int] = None) -> List[List]:
    total = sum(counter.values()) or 1
    rows = []
    for key, count in counter.most_common(limit):
        cols = list(key) if isinstance(key, tuple) else [key]
        cols.append(count)
        cols.append(count / total)
        rows.append(cols)
    return rows


# キー空間が広い表(3-gram・チャンク頻度)は全量掲載すると HTML が肥大するため
# 上位のみ載せる。全量は stats.json 側にある
_TABLE_ROW_LIMIT = 3000

_STAGE2_NOTE = "<p class=\"note\">Stage 2(GiNZA)が実行されていません。</p>"


def _limit_note(counter: Counter) -> str:
    if len(counter) <= _TABLE_ROW_LIMIT:
        return ""
    return (f"<p class=\"note\">上位 {_TABLE_ROW_LIMIT} 件のみ表示"
            f"(全 {len(counter)} 種)。全量は stats.json を参照。</p>")


def _trigram_table(counter: Counter, table_id: str) -> str:
    return _limit_note(counter) + _table(["カナ1", "カナ2", "カナ3", "頻度", "比率"],
                                         _counter_rows(counter, limit=_TABLE_ROW_LIMIT),
                                         table_id)


def _matrix_table(matrix: Dict[str, Dict[str, float]], table_id: str, limit: int = 40) -> str:
    """行方向正規化済み遷移行列を表にする。列数が多い場合は頻度上位に絞る。"""
    col_weight: Counter = Counter()
    for row in matrix.values():
        for b, p in row.items():
            col_weight[b] += p
    cols = [c for c, _ in col_weight.most_common(limit)]
    headers = [""] + cols
    rows = []
    for a in sorted(matrix, key=lambda k: -sum(matrix[k].values())):
        rows.append([a] + [round(matrix[a].get(c, 0.0), 4) for c in cols])
    note = ""
    if len(col_weight) > limit:
        note = f"<p class=\"note\">列は上位 {limit} 件のみ表示(全 {len(col_weight)} 列)。全量は stats.json を参照。</p>"
    return note + _table(headers, rows, table_id)


def _heatmap_png(matrix: Dict[str, Dict[str, float]], title: str) -> Optional[str]:
    """遷移行列の heatmap を base64 PNG で返す。

    matplotlib は必須依存だが、万一インポートできない環境でも
    解析結果(表・JSON)を失わないよう heatmap だけスキップして続行する。
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("警告: 必須依存 matplotlib を読み込めません。heatmap を省略します。"
              "`pip install -e .` でインストールを修復してください", file=sys.stderr)
        return None
    import base64
    import io
    import warnings

    from matplotlib import font_manager

    # 日本語グリフを持つフォントがあれば使う。無ければ豆腐警告だけ抑制して続行
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Noto Sans CJK JP", "Noto Sans JP", "IPAexGothic", "IPAGothic",
                 "Hiragino Sans", "Yu Gothic", "Meiryo", "MS Gothic",
                 "TakaoGothic", "VL Gothic"):
        if name in available:
            matplotlib.rcParams["font.family"] = name
            break
    else:
        print("警告: 日本語フォント未検出。heatmap のラベルが欠ける場合があります", file=sys.stderr)
    warnings.filterwarnings("ignore", message="Glyph .* missing from font")

    labels = sorted(matrix)
    data = [[matrix.get(a, {}).get(b, 0.0) for b in labels] for a in labels]
    fig, ax = plt.subplots(figsize=(max(6, len(labels) * 0.5), max(5, len(labels) * 0.45)))
    im = ax.imshow(data, cmap="viridis", aspect="auto")
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=90, fontsize=8)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_title(title)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f'<img alt="{_esc(title)}" src="data:image/png;base64,{b64}">'


_PAGE = Template("""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>posstat report</title>
<style>
body { font-family: "Hiragino Sans", "Noto Sans CJK JP", "Yu Gothic", sans-serif;
       margin: 2rem auto; max-width: 72rem; padding: 0 1rem; color: #222; }
h1 { border-bottom: 3px solid #4a6; padding-bottom: .3rem; }
h2 { border-left: 6px solid #4a6; padding-left: .5rem; margin-top: 2.5rem; }
table { border-collapse: collapse; font-size: .85rem; }
th, td { border: 1px solid #ccc; padding: .2rem .5rem; white-space: nowrap; }
th { background: #eef5ee; cursor: pointer; position: sticky; top: 0; }
th:after { content: " ↕"; color: #aaa; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
tr:nth-child(even) { background: #fafafa; }
.tablewrap { max-height: 30rem; overflow: auto; border: 1px solid #ddd; }
.filter { margin: .4rem 0; padding: .2rem .4rem; width: 16rem; }
.rowcount, .note { color: #888; font-size: .8rem; }
img { max-width: 100%; }
dl.meta { display: grid; grid-template-columns: 12rem 1fr; gap: .2rem .8rem; }
dl.meta dt { font-weight: bold; }
</style>
</head>
<body>
<h1>posstat レポート</h1>
$body
<script>
(function () {
  function cellVal(tr, i) {
    var t = tr.children[i].textContent.trim();
    var n = parseFloat(t.replace(/,/g, ""));
    return isNaN(n) || String(n) !== t && !/^[-+0-9.eE]+$/.test(t) ? t : n;
  }
  document.querySelectorAll("table.sortable th").forEach(function (th) {
    th.addEventListener("click", function () {
      var table = th.closest("table");
      var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
      var asc = th.dataset.asc !== "1";
      th.parentNode.querySelectorAll("th").forEach(function (h) { delete h.dataset.asc; });
      th.dataset.asc = asc ? "1" : "0";
      var rows = Array.prototype.slice.call(table.tBodies[0].rows);
      rows.sort(function (a, b) {
        var x = cellVal(a, idx), y = cellVal(b, idx);
        if (typeof x === "number" && typeof y === "number") return asc ? x - y : y - x;
        return asc ? String(x).localeCompare(String(y), "ja")
                   : String(y).localeCompare(String(x), "ja");
      });
      rows.forEach(function (r) { table.tBodies[0].appendChild(r); });
    });
  });
  document.querySelectorAll("input.filter").forEach(function (input) {
    input.addEventListener("input", function () {
      var q = input.value.toLowerCase();
      var table = document.getElementById(input.dataset.table);
      Array.prototype.forEach.call(table.tBodies[0].rows, function (tr) {
        tr.style.display = tr.textContent.toLowerCase().indexOf(q) >= 0 ? "" : "none";
      });
    });
  });
})();
</script>
</body>
</html>
""")


def render(
    mecab: MecabStats,
    ginza: Optional[GinzaStats],
    stats_json: Dict,
    heatmap: bool = True,
) -> str:
    """レポート HTML 全体を組み立てて返す。"""
    meta = stats_json["meta"]
    parts: List[str] = []

    # 1. コーパス概要
    parts.append("<h2>1. コーパス概要</h2><dl class=\"meta\">")
    labels = {
        "chars": "総文字数", "sentences": "総文数", "tokens": "総トークン数",
        "files": "読込ファイル数", "generated": "生成日時", "model": "GiNZA モデル",
    }
    for k, v in meta.items():
        parts.append(f"<dt>{_esc(labels.get(k, k))}</dt><dd>{_esc(v)}</dd>")
    if ginza is not None:
        parts.append(f"<dt>総文節数</dt><dd>{ginza.n_bunsetsu}</dd>")
    parts.append("</dl>")

    # 2. 品詞頻度(大/細分類)
    parts.append("<h2>2. 品詞頻度</h2><h3>大分類</h3>")
    parts.append(_table(["品詞", "頻度", "比率"], _counter_rows(mecab.pos1_unigram), "t-pos1"))
    parts.append("<h3>細分類</h3>")
    parts.append(_table(["品詞(大-細)", "頻度", "比率"], _counter_rows(mecab.pos2_unigram), "t-pos2"))

    # 3. 品詞遷移確率行列
    parts.append("<h2>3. 品詞遷移確率行列</h2>")
    pos_matrix = stats_json["pos_transition"]
    if heatmap:
        img = _heatmap_png(pos_matrix, "POS transition P(next | prev)")
        if img:
            parts.append(img)
    parts.append(_matrix_table(pos_matrix, "t-postrans"))

    # 4. 品詞3-gram上位
    parts.append("<h2>4. 品詞3-gram</h2>")
    parts.append(_table(["1", "2", "3", "頻度", "比率"], _counter_rows(mecab.pos_trigram), "t-pos3"))

    # 5. 活用形分布(動詞・形容詞別)
    parts.append("<h2>5. 活用形分布</h2>")
    parts.append(_table(["品詞", "活用形", "頻度", "比率"], _counter_rows(mecab.cform_by_pos), "t-cform"))

    # 6. 品詞ごとの頭尾カナ
    parts.append("<h2>6. 品詞ごとの頭尾カナ</h2><h3>頭カナ</h3>")
    parts.append(_table(["品詞", "カナ", "頻度", "比率"], _counter_rows(mecab.head_kana_by_pos), "t-headkana"))
    parts.append("<h3>尻カナ</h3>")
    parts.append(_table(["品詞", "カナ", "頻度", "比率"], _counter_rows(mecab.tail_kana_by_pos), "t-tailkana"))

    # 7. 記号前後統計
    parts.append("<h2>7. 記号前後統計</h2><h3>直前の品詞</h3>")
    parts.append(_table(["記号", "品詞", "頻度", "比率"], _counter_rows(mecab.symbol_prev_pos), "t-symppos"))
    parts.append("<h3>直後の品詞</h3>")
    parts.append(_table(["記号", "品詞", "頻度", "比率"], _counter_rows(mecab.symbol_next_pos), "t-symnpos"))
    parts.append("<h3>直前の隣接カナ(尻)</h3>")
    prev_rows = [[k, s, c, r] for (s, k), c, r in
                 ((key, cnt, cnt / (sum(mecab.symbol_prev_kana.values()) or 1))
                  for key, cnt in mecab.symbol_prev_kana.most_common())]
    parts.append(_table(["カナ", "記号", "頻度", "比率"], prev_rows, "t-sympkana"))
    parts.append("<h3>直後の隣接カナ(頭)</h3>")
    parts.append(_table(["記号", "カナ", "頻度", "比率"], _counter_rows(mecab.symbol_next_kana), "t-symnkana"))

    # 8. 文節統計
    parts.append("<h2>8. 文節統計</h2>")
    if ginza is None:
        parts.append(_STAGE2_NOTE)
    else:
        parts.append("<h3>文節頭カナ</h3>")
        parts.append(_table(["カナ", "頻度", "比率"], _counter_rows(ginza.bunsetsu_head_kana), "t-bhead"))
        parts.append("<h3>文節尻カナ</h3>")
        parts.append(_table(["カナ", "頻度", "比率"], _counter_rows(ginza.bunsetsu_tail_kana), "t-btail"))
        parts.append("<h3>文節長分布(表層文字数)</h3>")
        len_rows = sorted(_counter_rows(ginza.bunsetsu_len_dist), key=lambda r: r[0])
        parts.append(_table(["文字数", "頻度", "比率"], len_rows, "t-blen"))
        parts.append("<h3>文節内カナ2-gram(上位)</h3>")
        parts.append(_table(["カナ1", "カナ2", "頻度", "比率"],
                            _counter_rows(ginza.kana_bigram_within_bunsetsu), "t-bwithin"))
        parts.append("<h3>文節境界跨ぎカナ2-gram(上位)</h3>")
        parts.append(_table(["尻カナ", "頭カナ", "頻度", "比率"],
                            _counter_rows(ginza.kana_bigram_cross_bunsetsu), "t-bcross"))
        parts.append("<h3>文節内カナ3-gram(上位)</h3>")
        parts.append(_trigram_table(ginza.kana_trigram_within_bunsetsu, "t-bwithin3"))
        parts.append("<h3>文節境界跨ぎカナ3-gram(上位)</h3>")
        parts.append("<p class=\"note\">境界を跨ぐ3-gramは、前文節尻2カナ+後文節頭1カナ、"
                     "および前文節尻1カナ+後文節頭2カナの両方を数える。</p>")
        parts.append(_trigram_table(ginza.kana_trigram_cross_bunsetsu, "t-bcross3"))
        parts.append("<h3>文節先頭品詞の遷移</h3>")
        parts.append(_matrix_table(stats_json["bunsetsu_head_pos_transition"], "t-bpos"))

    # 9. 係り受けラベル頻度
    parts.append("<h2>9. 係り受けラベル頻度</h2>")
    if ginza is None:
        parts.append(_STAGE2_NOTE)
    else:
        parts.append(_table(["depラベル", "係り元品詞", "係り先品詞", "頻度", "比率"],
                            _counter_rows(ginza.dep_pos_pairs), "t-dep"))

    # 10. 「絶対来ない」ペア(PMI下位)
    parts.append("<h2>10. 「絶対来ない」ペア(PMI下位)</h2>")
    parts.append("<p class=\"note\">隣接カナ全体(品詞内+境界跨ぎ)で期待頻度が基準以上なのに"
                 "観測ゼロまたは低PMIのペア。pmi 空欄は観測ゼロ。</p>")
    fp_rows = [[p["a"], p["b"], "" if p["pmi"] is None else p["pmi"], p["expected"]]
               for p in stats_json["forbidden_pairs"]]
    parts.append(_table(["先行カナ", "後続カナ", "PMI", "期待頻度"], fp_rows, "t-forbidden"))

    # 11. 「繋ぎの語」チャンク分析
    parts.append("<h2>11. 「繋ぎの語」チャンク分析</h2>")
    if ginza is None:
        parts.append(_STAGE2_NOTE)
    else:
        parts.append(
            "<p class=\"note\">抽出ルール(deprel: case/mark/aux/cop/cc/"
            "discourse/fixed、指示代名詞、形式名詞、接続副詞、補助動詞)で各トークンを"
            "「繋ぎの語」か判定し、連続する繋ぎ語を膠着させて1塊(チャンク)として扱う。"
            "塊内のカナ連接と、塊境界を跨ぐ連接を集計する。句読点・記号はチャンク境界。</p>")
        parts.append("<h3>繋ぎチャンク頻度(連結カナ)</h3>")
        parts.append(_limit_note(ginza.tsunagi_chunk_freq))
        tsunagi_rows = [[k, len(k), c, r] for [k, c, r]
                        in _counter_rows(ginza.tsunagi_chunk_freq, limit=_TABLE_ROW_LIMIT)]
        parts.append(_table(["チャンク(カナ)", "文字数", "頻度", "比率"],
                            tsunagi_rows, "t-tsunagi-freq"))
        parts.append("<h3>繋ぎチャンク内カナ2-gram</h3>")
        parts.append(_table(["カナ1", "カナ2", "頻度", "比率"],
                            _counter_rows(ginza.kana_bigram_within_tsunagi), "t-tsunagi-bi"))
        parts.append("<h3>繋ぎチャンク内カナ3-gram</h3>")
        parts.append(_trigram_table(ginza.kana_trigram_within_tsunagi, "t-tsunagi-tri"))
        parts.append("<h3>内容チャンク内カナ2-gram</h3>")
        parts.append(_table(["カナ1", "カナ2", "頻度", "比率"],
                            _counter_rows(ginza.kana_bigram_within_content), "t-content-bi"))
        parts.append("<h3>内容チャンク内カナ3-gram</h3>")
        parts.append(_trigram_table(ginza.kana_trigram_within_content, "t-content-tri"))
        parts.append("<h3>チャンク境界跨ぎカナ2-gram</h3>")
        parts.append(_table(["尻カナ", "頭カナ", "頻度", "比率"],
                            _counter_rows(ginza.kana_bigram_cross_chunk), "t-chunk-cross-bi"))
        parts.append("<h3>チャンク境界跨ぎカナ3-gram</h3>")
        parts.append(_trigram_table(ginza.kana_trigram_cross_chunk, "t-chunk-cross-tri"))

    return _PAGE.safe_substitute(body="\n".join(parts))


def write_html(html_text: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.html"
    path.write_text(html_text, encoding="utf-8")
    return path
