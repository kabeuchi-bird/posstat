# posstat

日本語コーパス(.txt)の品詞・カナ・文節統計を集計し、自己完結型の HTML レポートと
Rust 側(tsuki_optimizer / MzKana)向け JSON を出力するクロスプラットフォーム CLI です。
設計の詳細は [posstat_design.md](posstat_design.md) を参照してください。

- **Stage 0**: 走査・デコード(UTF-8 既定、失敗時 charset-normalizer)・正規表現による文分割
- **Stage 1**: fugashi(MeCab + unidic-lite)による全量形態素解析
- **Stage 2**: GiNZA による全量の文節・係り受け解析
- **集計**: 遷移確率行列(行方向正規化)、PMI による「後には来ない」ペア抽出
- **出力**: `output/report.html`(1ファイル完結)+ `output/stats.json`

対応 OS: Windows / macOS / Linux(OS 依存コードなし。全依存に 3 OS の wheel あり)。

## インストール

Python 3.9 以上。venv の作成を推奨します。

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

これで fugashi(unidic-lite 同梱)、ja_ginza(spacy 同時導入)、rich、
charset-normalizer、matplotlib がすべて入ります。追加の選択は不要です。
heatmap が不要な場合は config.toml の `[report] heatmap = false` で無効化できます。

### 使用ライブラリ

- **fugashi** — MeCab の Python バインディング。Stage 1 の形態素解析に使用
- **unidic-lite** — fugashi 用の軽量 UniDic 辞書。pip だけで入り追加ダウンロード不要
- **ja-ginza (GiNZA)** — spaCy ベースの日本語 NLP。Stage 2 の文節・係り受け・「繋ぎの語」判定に使用
- **spacy** — GiNZA の土台となる NLP フレームワーク(ja-ginza と同時に導入される)
- **rich** — ターミナルのプログレスバー表示(非TTY では行ログに自動フォールバック)
- **charset-normalizer** — UTF-8 でデコードできないファイルのエンコーディング推定
- **matplotlib** — 品詞遷移行列 heatmap の PNG 描画(HTML に base64 埋め込み)
- **tomli** — Python 3.10 以前での config.toml 読込(3.11+ は標準 tomllib を使用)

> **注意**: Arch Linux など PEP 668 準拠の環境ではシステム Python への直接インストールが
> 拒否されます。必ず venv を作成してからインストールしてください。

## 使い方

```bash
python -m posstat CORPUS_PATH [-c config.toml] [-o output/]
```

- `CORPUS_PATH`: `.txt` ファイル 1 つ、またはディレクトリ(再帰的に `*.txt` を走査)
- `-c/--config`: 設定ファイル(省略時は既定値で動作)
- `-o/--output`: 出力ディレクトリ(既定: `output/`)
- `--log-interval N`: 非TTY時の行ログ間隔(秒)

exit code: `0` = 成功, `1` = 入力エラー, `2` = 解析エラー。

実行中は rich による複数プログレスバー(パーセンテージ / ETA 付き)を表示します。
リダイレクトや CI などの非TTY環境ではバーを抑制し、代わりに `log_interval` 秒ごとの
行ログを標準エラーに出します。

```text
Stage 0: 読込・文分割     ━━━━━━━━━━ 100% 0:00:00
Stage 1: 形態素解析       ━━━━━━━━━━ 100% 0:00:00
Stage 2: 文節・係り受け   ━━━━━━╺━━━  62% 0:07:12
集計・レポート生成        ╺━━━━━━━━━   0% -:--:--
```

## 設定 (config.toml)

```toml
[input]
encoding_fallback = true   # UTF-8 失敗時に charset-normalizer で判定

[ginza]
model = "ja_ginza"         # "ja_ginza_electra" に切替可
batch_size = 128
n_process = 0              # 0 = cpu_count - 1

[analysis]
min_count = 10             # PMI 判定の信頼性下限: P(x)P(y)×総数 >= min_count
pmi_threshold = -3.0       # これ以下を「後には来ない」候補に

[report]
heatmap = true             # false で品詞遷移行列の heatmap を省略

[progress]
log_interval = 30          # 非TTY時の行ログ間隔(秒)
```

## 出力

### report.html

自己完結型 HTML 1 ファイル。表はクリックでソート、テキストフィルタ付き。
構成: 1. コーパス概要 / 2. 品詞頻度(大・細分類) / 3. 品詞遷移確率行列(heatmap + 表) /
4. 品詞3-gram / 5. 活用形分布 / 6. 品詞ごとの頭尾カナ / 7. 記号前後統計 /
8. 文節統計(境界カナ・文節長・文節内/境界跨ぎカナ2-gram/3-gram・文節頭品詞遷移) /
9. 係り受けラベル頻度 / 10. 「絶対来ない」ペア(PMI 下位) /
11. 「繋ぎの語」チャンク分析

セクション 11 は大岡俊彦氏の抽出ルール(deprel: case/mark/aux/cop/cc/discourse/fixed、
指示代名詞、形式名詞、接続副詞、補助動詞)で「繋ぎの語」を判定し、連続する繋ぎ語が
膠着した全体を 1 塊(チャンク)として、繋ぎ/内容それぞれのチャンク内カナ 2-gram・3-gram
とチャンク境界跨ぎの連接を集計します。3-gram 表は上位 3000 件のみ HTML に掲載し、
全量は stats.json に出力します。

### stats.json

Rust 側から serde で読む前提の構造:

```json
{
  "meta": {"chars": 6000000, "sentences": 200000, "generated": "..."},
  "pos_transition": {"名詞": {"助詞": 0.42}},
  "kana_bigram_within_pos": {},
  "kana_bigram_cross_boundary": {},
  "forbidden_pairs": [{"a": "ヲ", "b": "ヲ", "pmi": -8.2, "expected": 42.0}],
  "bunsetsu_head_kana": {},
  "bunsetsu_tail_kana": {},
  "bunsetsu_head_pos_transition": {"NOUN": {"VERB": 0.31}},
  "kana_bigram_within_bunsetsu": {"ア": {"イ": 0.42}},
  "kana_bigram_cross_bunsetsu": {},
  "kana_trigram_within_bunsetsu": {"ア": {"イ": {"ウ": 0.03}}},
  "kana_trigram_cross_bunsetsu": {},
  "tsunagi_chunk_freq": {"テイル": 0.02},
  "kana_bigram_within_tsunagi": {},
  "kana_trigram_within_tsunagi": {},
  "kana_bigram_within_content": {},
  "kana_trigram_within_content": {},
  "kana_bigram_cross_chunk": {},
  "kana_trigram_cross_chunk": {}
}
```

- 遷移系は行方向正規化した確率(`P(次 | 前)`)。3-gram 系は `{a: {b: {c: P(c|a,b)}}}`
- `tsunagi` 系は「繋ぎの語」チャンク内、`content` 系は内容チャンク内、
  `cross_chunk` はチャンク境界跨ぎの連接
- 境界跨ぎ 3-gram は「前尻2+後頭1」「前尻1+後頭2」の両方を数える
- `forbidden_pairs` の `pmi` は観測ゼロのとき `null`(Rust 側は `Option<f64>`)

## モジュール構成

```text
posstat/
├── __main__.py      # CLI エントリ(Windows spawn 対策の __main__ ガード)
├── reader.py        # Stage 0: 走査・正規化・文分割
├── mecab_stage.py   # Stage 1: fugashi
├── ginza_stage.py   # Stage 2: GiNZA
├── aggregate.py     # 確率化・PMI 計算
├── export.py        # stats.json 出力
├── report_html.py   # HTML レポート生成
└── progress.py      # rich プログレス / 非TTY 行ログ
```

## 性能見積(600万字・約20万文)

| 段 | 時間 |
|---|---|
| Stage 0 | 数秒 |
| Stage 1 | 約10秒 |
| Stage 2 | 15〜30分(4プロセス) |
| 集計 + HTML | 数秒 |

総所要の 9 割は Stage 2 で、ETA は実質 Stage 2 の残り時間です。

## 注意点

- NFKC 正規化は行いません(！？…『』を保持)。BOM 除去のみ行います。
- Windows では GiNZA の並列処理(spawn)のため、`python -m posstat` 以外から
  組み込む場合も `if __name__ == "__main__":` ガード配下で呼び出してください。
- エンコーディング判定不能なファイルは警告を出してスキップします。
