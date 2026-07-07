"""Stage 0: コーパス走査・デコード・文分割。

- UTF-8 既定、失敗時は charset-normalizer で判定(config で無効化可)
- NFKC 正規化は行わない(！？…『』を保持)。BOM 除去のみ
- 文分割は正規表現(。！？… + 閉じ括弧の後処理)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Tuple

# 文末: 。！？…(連続可)+ 直後の閉じ括弧類まで含めて 1 文とする
_SENT_END = re.compile(r"[。！？…]+[」』）〉》】”’\]\)]*")


class InputError(Exception):
    """入力(パス・ファイル)に起因するエラー。exit code 1 に対応。"""


def collect_files(corpus_path: str) -> List[Path]:
    """CORPUS_PATH(ファイルまたはディレクトリ)から .txt 一覧を返す。"""
    p = Path(corpus_path)
    if p.is_file():
        return [p]
    if p.is_dir():
        files = sorted(p.rglob("*.txt"))
        if not files:
            raise InputError(f"ディレクトリに .txt ファイルがありません: {p}")
        return files
    raise InputError(f"入力パスが存在しません: {corpus_path}")


def decode_file(path: Path, encoding_fallback: bool = True) -> Optional[str]:
    """ファイルをデコードして返す。判定不能や I/O 失敗なら警告して None(スキップ)。"""
    try:
        raw = path.read_bytes()
    except OSError as e:
        print(f"警告: ファイルを読めずスキップ: {path} ({e})", file=sys.stderr)
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        if not encoding_fallback:
            print(f"警告: UTF-8 でデコードできずスキップ: {path}", file=sys.stderr)
            return None
        from charset_normalizer import from_bytes

        best = from_bytes(raw).best()
        if best is None:
            print(f"警告: エンコーディング判定不能のためスキップ: {path}", file=sys.stderr)
            return None
        text = str(best)
    # BOM 除去のみ(NFKC 正規化なし)
    return text.lstrip("\ufeff")


def split_sentences(text: str) -> Iterator[str]:
    """テキストを文に分割するジェネレータ。改行は常に文境界とみなす。"""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        pos = 0
        for m in _SENT_END.finditer(line):
            sent = line[pos : m.end()].strip()
            if sent:
                yield sent
            pos = m.end()
        rest = line[pos:].strip()
        if rest:
            yield rest


def load_corpus(
    files: List[Path],
    encoding_fallback: bool = True,
    on_bytes: Optional[Callable[[int], None]] = None,
) -> Tuple[List[str], int, int]:
    """全ファイルを読み、(文リスト, 総文字数, 読込ファイル数) を返す。

    600万字なら文リスト全保持でも数十MB のため、メモリに載せて
    Stage 1/2 で共用する(ファイル 2 回読みの回避)。
    on_bytes は進捗用コールバック(処理済みバイト数を都度渡す)。
    """
    sentences: List[str] = []
    total_chars = 0
    n_read = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError as e:
            print(f"警告: ファイル情報を取得できずスキップ: {path} ({e})", file=sys.stderr)
            continue
        text = decode_file(path, encoding_fallback)
        if text is not None:
            n_read += 1
            total_chars += len(text)
            sentences.extend(split_sentences(text))
        if on_bytes:
            on_bytes(size)
    return sentences, total_chars, n_read
