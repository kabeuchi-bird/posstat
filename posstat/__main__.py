"""CLI エントリポイント。

    python -m posstat CORPUS_PATH [-c config.toml] [-o output/]

exit code: 0=成功, 1=入力エラー, 2=解析エラー。

GiNZA の n_process > 1 は Windows で spawn を使うため、
`if __name__ == "__main__":` ガードを必ず経由すること。
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Dict

from . import __version__, export, mecab_stage, report_html
from .progress import Reporter
from .reader import InputError, collect_files, load_corpus

DEFAULT_CONFIG: Dict = {
    "input": {"encoding_fallback": True},
    "ginza": {"model": "ja_ginza", "batch_size": 128, "n_process": 0},
    "analysis": {"min_count": 10, "pmi_threshold": -3.0},
    "report": {"heatmap": True},
    "progress": {"log_interval": 30},
}


def load_config(path: str | None) -> Dict:
    """config.toml を読み、既定値にマージして返す。"""
    cfg = {section: dict(values) for section, values in DEFAULT_CONFIG.items()}
    if path is None:
        return cfg
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        import tomli as tomllib
    with open(path, "rb") as f:
        user = tomllib.load(f)
    for section, values in user.items():
        cfg.setdefault(section, {}).update(values)
    return cfg


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="posstat",
        description="日本語コーパスの品詞・カナ・文節統計を集計し、HTMLレポートとJSONを出力する",
    )
    ap.add_argument("corpus", help="コーパスの .txt ファイルまたはディレクトリ")
    ap.add_argument("-c", "--config", default=None, help="config.toml のパス")
    ap.add_argument("-o", "--output", default="output", help="出力ディレクトリ(既定: output/)")
    ap.add_argument("--log-interval", type=float, default=None,
                    help="非TTY時の行ログ間隔(秒)。config より優先")
    ap.add_argument("--version", action="version", version=f"posstat {__version__}")
    return ap.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    log_interval = args.log_interval or cfg["progress"]["log_interval"]
    out_dir = Path(args.output)

    # Stage 0: 読込・文分割 -----------------------------------------------
    try:
        files = collect_files(args.corpus)
    except InputError as e:
        print(f"入力エラー: {e}", file=sys.stderr)
        return 1

    with Reporter(log_interval=log_interval) as rep:
        try:
            total_bytes = sum(f.stat().st_size for f in files)
        except OSError as e:
            print(f"入力エラー: ファイル情報の取得に失敗: {e}", file=sys.stderr)
            return 1
        t0 = rep.add_task("Stage 0: 読込・文分割", total=total_bytes)
        t1 = rep.add_task("Stage 1: 形態素解析", total=None, start=False)
        t2 = rep.add_task("Stage 2: 文節・係り受け", total=None, start=False)
        t3 = rep.add_task("集計・レポート生成", total=4, start=False)

        sentences, total_chars, n_read = load_corpus(
            files,
            encoding_fallback=cfg["input"]["encoding_fallback"],
            on_bytes=lambda n: rep.advance(t0, n),
        )
        rep.finish(t0)
        if n_read == 0 or not sentences:
            print("入力エラー: 解析可能な文がありません", file=sys.stderr)
            return 1
        n_sentences = len(sentences)

        # Stage 1: fugashi ------------------------------------------------
        try:
            rep.start_task(t1, total=n_sentences)
            mecab = mecab_stage.run(sentences, on_progress=lambda n: rep.advance(t1, n))
            rep.finish(t1)
        except Exception:
            print("解析エラー (Stage 1 / fugashi):", file=sys.stderr)
            traceback.print_exc()
            return 2

        # Stage 2: GiNZA --------------------------------------------------
        try:
            from . import ginza_stage
        except ImportError:
            print(
                "解析エラー (Stage 2): ginza_stage モジュールの読み込みに失敗しました。\n"
                "  pip install ja-ginza  で GiNZA と依存パッケージを導入してください。",
                file=sys.stderr,
            )
            traceback.print_exc()
            return 2
        try:
            rep.start_task(t2, total=n_sentences)
            ginza = ginza_stage.run(
                sentences,
                model=cfg["ginza"]["model"],
                batch_size=cfg["ginza"]["batch_size"],
                n_process=cfg["ginza"]["n_process"],
                on_progress=lambda n: rep.advance(t2, n),
            )
            rep.finish(t2)
        except ImportError as e:
            print(
                f"解析エラー (Stage 2 / GiNZA): 依存パッケージが不足しています。\n  {e}\n"
                "  pip install ja-ginza click  を実行してください。",
                file=sys.stderr,
            )
            return 2
        except Exception:
            print("解析エラー (Stage 2 / GiNZA):", file=sys.stderr)
            traceback.print_exc()
            return 2

        # 集計・出力 ------------------------------------------------------
        rep.start_task(t3)
        stats = export.build_stats(
            mecab,
            ginza,
            total_chars=total_chars,
            n_sentences=n_sentences,
            n_files=n_read,
            model=cfg["ginza"]["model"],
            pmi_threshold=cfg["analysis"]["pmi_threshold"],
            min_count=cfg["analysis"]["min_count"],
        )
        rep.advance(t3)
        json_path = export.write_json(stats, out_dir)
        rep.advance(t3)
        html_text = report_html.render(mecab, ginza, stats, heatmap=cfg["report"]["heatmap"])
        rep.advance(t3)
        html_path = report_html.write_html(html_text, out_dir)
        rep.advance(t3)
        rep.finish(t3)

    print(f"完了: {html_path} / {json_path}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    # Windows spawn 対策として必須のガード(GiNZA の n_process 用)
    sys.exit(main())
