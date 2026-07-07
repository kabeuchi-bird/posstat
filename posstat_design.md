# posstat 設計書 v2 (Python / クロスプラットフォームCLI)

## 前提

- コーパス: 日本語 .txt、最大600万字(約20万文)
- Stage 1 = fugashi(MeCab+unidic-lite)全量、Stage 2 = GiNZA全量(サンプリング廃止)
- 出力: 自己完結型HTML 1ファイル + JSONエクスポート
- 対応OS: Windows / macOS / Linux。OS依存コードなし(pathlib、fugashi/GiNZAともwheel配布あり)

## ディレクトリ構成

```
posstat/
├── pyproject.toml
├── config.toml
├── posstat/
│   ├── __main__.py      # CLI エントリ
│   ├── reader.py        # 走査・正規化・文分割
│   ├── mecab_stage.py
│   ├── ginza_stage.py
│   ├── aggregate.py     # 確率化・PMI計算
│   ├── export.py        # JSON出力
│   └── report_html.py   # HTML生成
└── output/
    ├── report.html
    └── stats.json
```

## CLI

```
python -m posstat CORPUS_PATH [-c config.toml] [-o output/]
```

CORPUS_PATH はファイルまたはディレクトリ。exit code: 0=成功, 1=入力エラー, 2=解析エラー。

## 実行中表示 (rich)

`rich.progress` を採用。理由: 純Python・3OS対応(Windowsコンソール含む)、
複数プログレスバー同時表示、パーセンテージ/ETA/スループット内蔵。

```python
from rich.progress import Progress, BarColumn, TaskProgressColumn, TimeRemainingColumn

with Progress(
    "[bold]{task.description}", BarColumn(),
    TaskProgressColumn(), TimeRemainingColumn(),
) as progress:
    t1 = progress.add_task("Stage 0: 読込・文分割", total=total_bytes)
    t2 = progress.add_task("Stage 1: 形態素解析",   total=n_sentences, start=False)
    t3 = progress.add_task("Stage 2: 文節・係り受け", total=n_sentences, start=False)
    t4 = progress.add_task("集計・レポート生成",     total=4, start=False)
```

表示イメージ:

```
Stage 0: 読込・文分割     ━━━━━━━━━━ 100% 0:00:00
Stage 1: 形態素解析       ━━━━━━━━━━ 100% 0:00:00
Stage 2: 文節・係り受け   ━━━━━━╺━━━  62% 0:07:12
集計・レポート生成        ╺━━━━━━━━━   0% -:--:--
```

進捗の粒度:
- Stage 0: 読込バイト数
- Stage 1: 処理済み文数(1000文ごとに update、更新コスト抑制)
- Stage 2: `nlp.pipe()` はイテレータなので、消費側ループで文数カウントしそのまま update
- 総所要の9割はStage 2。ETAが実質ここの残り時間になる

非TTY環境(リダイレクト・CI)では rich が自動でバー描画を抑制し、
代わりに `--log-interval 30` 秒ごとの行ログを標準エラーに出す。

## Stage 0: reader

- エンコーディング: UTF-8既定、失敗時 charset-normalizer で判定、判定不能は警告してスキップ
- NFKC正規化なし(！？…『』を保持)。BOM除去のみ
- 文分割: 正規表現(。！？…+閉じ括弧処理)、ジェネレータ
- 600万字は全文リスト保持でも数十MB。文リストをメモリに載せてStage 1/2で共用(2回読み回避)

## Stage 1: fugashi

単一プロセス。600万字で10秒前後のため並列化しない。

文ごとに `(品詞大分類, 細分類, 活用形, 書字形, 仮名)` 列を作り Counter に積む:

1. 品詞1-gram(大分類/細分類)
2. 品詞2/3-gram
3. 活用形分布(動詞・形容詞別)
4. 品詞ごとの頭カナ/尻カナ1-gram
5. 品詞内カナ2-gram / 品詞境界跨ぎカナ2-gram(区別して集計)
6. 記号(！？…「」『』)前後の品詞と隣接カナ

## Stage 2: GiNZA

- モデル: `ja_ginza`(config で `ja_ginza_electra` 切替可)
- 全20万文を `nlp.pipe(sentences, batch_size=128, n_process=N)`
  - `n_process` 既定 = `max(1, cpu_count - 1)`。Windows の spawn 対策として
    エントリポイントに `if __name__ == "__main__":` ガード必須
- 目安: 4プロセスで15〜30分
- 集計項目:
  1. 文節境界統計: 文節頭/尻カナ1-gram、文節長分布
  2. 文節境界跨ぎカナ2-gram vs 文節内2-gram
  3. depラベル × (係り元品詞, 係り先品詞)
  4. 文節先頭品詞の遷移

## aggregate

- Counter → 行方向正規化で遷移確率行列
- PMI = log2(P(xy) / P(x)P(y))。観測ゼロまたはPMI ≤ 閾値(既定 -3.0)のペアを
  「後には来ない」候補として列挙。信頼性のため P(x)P(y)×総数 ≥ 10 のみ対象

## export.py (stats.json)

Rust側(tsuki_optimizer / MzKana)から serde で読む前提の構造:

```json
{
  "meta": {"chars": 6000000, "sentences": 200000, "generated": "..."},
  "pos_transition": {"名詞": {"助詞": 0.42, ...}, ...},
  "kana_bigram_within_pos": {...},
  "kana_bigram_cross_boundary": {...},
  "forbidden_pairs": [{"a": "を", "b": "を", "pmi": -8.2}, ...],
  "bunsetsu_head_kana": {...},
  "bunsetsu_tail_kana": {...}
}
```

## report_html.py

- テンプレート: 標準 string.Template で単一HTML生成(jinja2不要)
- 表: 素のJS数十行でクリックソート + テキストフィルタ。min_countで切らず全量掲載
- heatmap: matplotlib → PNG → base64埋め込み(config で off 可)
- 構成:
  1. コーパス概要
  2. 品詞頻度(大/細分類)
  3. 品詞遷移確率行列(heatmap + 表)
  4. 品詞3-gram上位
  5. 活用形分布
  6. 品詞ごとの頭尾カナ
  7. 記号前後統計
  8. 文節統計(境界カナ、文節長、文節頭品詞遷移)
  9. 係り受けラベル頻度
  10. 「絶対来ない」ペア(PMI下位)

## config.toml

```toml
[input]
encoding_fallback = true

[ginza]
model = "ja_ginza"
batch_size = 128
n_process = 0        # 0 = cpu_count - 1

[analysis]
min_count = 10
pmi_threshold = -3.0

[report]
heatmap = true

[progress]
log_interval = 30    # 非TTY時の秒間隔
```

## 依存

```
fugashi[unidic-lite]
ja_ginza  (spacy 同時導入)
rich
charset-normalizer
matplotlib   # optional (heatmap)
```

全依存にWin/mac/Linuxのwheelあり。導入は `pip install -e .` のみ。

## 性能見積(600万字)

| 段 | 時間 |
|---|---|
| Stage 0 | 数秒 |
| Stage 1 | 約10秒 |
| Stage 2 | 15〜30分(4プロセス) |
| 集計+HTML | 数秒 |
