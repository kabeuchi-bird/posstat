# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## コマンド

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .                    # PEP 668 環境では venv 必須

python -m posstat CORPUS_PATH [-c config.toml] [-o output/]
python -m posstat corpus.txt --long-bunsetsu 20      # 最長文節の診断
python -m posstat corpus.txt --dump-tsunagi-text     # 繋ぎ語判定の目視確認
```

テストスイート・lint 設定は無い。変更の検証は小さな .txt を作って実走させ、
`output/stats.json` と `output/report.html` を見る:

```bash
printf '猫がいる。犬もいた。\n' > /tmp/t.txt && python -m posstat /tmp/t.txt -o /tmp/out
```

Stage 2 (GiNZA) が全所要時間の 9 割。反復検証では 10〜100 文程度の入力を使うこと。
`config.toml` の `[ginza] n_process = 1` にすると並列起因の問題を切り分けられる。

## アーキテクチャ

4 段のパイプライン。`__main__.run()` が全段を順に呼び、`progress.Reporter`
(TTY は rich バー / 非TTY は行ログ)に進捗を流す。exit code: 0=成功, 1=入力エラー, 2=解析エラー。

```
reader.load_corpus  →  mecab_stage.run  →  ginza_stage.run  →  export.build_stats
   文リスト(全保持)      MecabStats          GinzaStats          ↘ export.write_json
                                                                 ↘ report_html.render
```

- **文リストはメモリに全保持**し Stage 1/2 で共用する(ファイル 2 回読みの回避)。600万字で数十MB。
- 各 Stage は **タプルキーの `Counter` だけを貯める**(`MecabStats` / `GinzaStats` の dataclass フィールド)。
  確率化は最後にまとめて `aggregate.row_normalize` (2-gram) / `row_normalize_trigram` (3-gram) /
  `distribution` (1-gram) で行う。Stage 内で割り算をしないのが原則。
- カナ関連の共有ヘルパ(`hira_to_kata` / `clean_kana` / `add_ngrams`)は `mecab_stage` にあり
  `ginza_stage` がそれを import する。**全カナ統計はカタカナ表記**で、カナ以外の文字は
  `clean_kana` で落ちる(英数字・記号はカナ列に入らない)。

### stats.json は外部 API

Rust 側(tsuki_optimizer / MzKana)が serde で読む。スキーマを変えるときは
`export.py` のモジュール docstring と `README.md` の 2 箇所を揃える。
`forbidden_pairs[].pmi` は観測ゼロのとき `null`(Rust 側 `Option<f64>`)。

### GiNZA 統計を 1 項目足すときの 4 箇所

1. `ginza_stage.GinzaStats` に Counter フィールドを追加
2. `ginza_stage._accumulate` で積む
3. `export._GINZA_EXPORTS` に「キー名 → 正規化関数」を登録(キー名はフィールド名と同一)
4. `report_html.render` の `sections` に表を追加

### 「繋ぎの語」チャンク (`ginza_stage`)

`is_tsunagi(token)` が deprel / lemma / pos の 5 ルール(定数は同ファイル冒頭)で判定し、
`_sent_chunks` が連続する同種トークンを膠着させて 1 チャンクにする。PUNCT/SYM/SPACE は
どちらのチャンクにも属さず境界として働く。ルールを触ったら `--dump-tsunagi-text` で
`output/tsunagi_masked.txt` を出して目視確認する。

## 落とし穴

- **NFKC 正規化はしない**(！？…『』を保持)。BOM 除去のみ。
- **『』→「」の正規化は Stage 2 入力だけ**。GiNZA の文節境界が二重鉤括弧で壊れて
  述語まで巻き込んだ長大文節になるため。原文は変えないので Stage 1 の記号統計は無影響。
- **文節長は 2 系統**。表層文字数は空白・記号を除いた文字数(トークン内部の空白も落とす)、
  カナ文字数は読みの長さ。英字は表層が実態より長く出る(`programming` 11 / `プログラミング` 7)。
- **`n_process > 1` は fork 前提**。Python 3.14 の Linux で既定が forkserver になり
  EOFError で落ちるため `ensure_fork_start_method` が fork に戻す。呼び出し元が明示設定済みなら尊重する。
- **Windows spawn 対策**で、組み込み利用も `if __name__ == "__main__":` ガード配下から呼ぶこと。
- HTML は自己完結 1 ファイルで**画像を一切使わない**。heatmap は遷移行列表のセル背景色
  (`_heat_cell`)で表現する。テンプレートは標準 `string.Template`、表の JS は素の数十行。
  jinja2・matplotlib の類は入れない。
- 大きい表は HTML 側だけ上位 `_TABLE_ROW_LIMIT`(3000)件に切り、stats.json には全量を出す。

## コード規約

コメント・docstring・CLI メッセージ・コミットメッセージはすべて日本語。
CodeRabbit が日本語でレビューする(`.coderabbit.yaml` に注目点の指示あり)。
新規依存は原則追加しない(3 OS の wheel があることが条件)。
