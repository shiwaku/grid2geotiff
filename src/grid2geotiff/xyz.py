"""XYZ 座標値テキストの読み込み。

航空レーザ計測のグリッドデータは `X Y Z` を1行1点で並べたテキストで配布される
ことが多い。区切り文字・改行コード・列順は業務によって揺れるため、読み込み側で
吸収する。配布形態がそのまま ZIP のことも多いので ZIP も直接受け付ける。
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

#: ZIP の中から本体とみなす拡張子。
TEXT_SUFFIXES = (".txt", ".csv", ".xyz", ".dat", ".asc")

#: 区切り文字の候補。いずれも pandas の C エンジンで扱える形にしてある
#: （``\s+`` は C エンジンが空白区切りとして特別扱いする）。
_DELIMITERS = {
    "space": r"\s+",
    "comma": ",",
    "tab": "\t",
    "semicolon": ";",
}


class XyzReadError(ValueError):
    """XYZ テキストを読み取れない。"""


@dataclass(frozen=True)
class XyzData:
    """読み込んだ点の座標。"""

    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    source: Path

    def __len__(self) -> int:
        return int(self.x.size)


@contextmanager
def open_xyz_stream(path: Path) -> Iterator[io.BufferedIOBase]:
    """テキスト本体のバイナリストリームを開く。ZIP なら中の1ファイルを取り出す。"""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [
                n
                for n in archive.namelist()
                if not n.endswith("/") and n.lower().endswith(TEXT_SUFFIXES)
            ]
            if not names:
                raise XyzReadError(
                    f"{path.name}: ZIP 内にテキストファイルがない"
                    f"（対象拡張子 {', '.join(TEXT_SUFFIXES)}）"
                )
            if len(names) > 1:
                raise XyzReadError(
                    f"{path.name}: ZIP 内にテキストファイルが複数ある: {names}"
                )
            with archive.open(names[0]) as stream:
                yield stream
    else:
        with path.open("rb") as stream:
            yield stream


def sniff_delimiter(path: Path) -> str:
    """先頭の有効行から区切り文字を判定し、pandas 用の正規表現を返す。"""
    with open_xyz_stream(path) as stream:
        for raw in stream:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line or line.startswith(("#", "//")):
                continue
            if "," in line:
                return _DELIMITERS["comma"]
            if ";" in line:
                return _DELIMITERS["semicolon"]
            if "\t" in line:
                return _DELIMITERS["tab"]
            return _DELIMITERS["space"]
    raise XyzReadError(f"{path.name}: 有効な行が1つもない")


def read_xyz(
    path: str | Path,
    *,
    delimiter: str | None = None,
    columns: Sequence[int] = (0, 1, 2),
    input_nodata: Sequence[float] = (),
) -> XyzData:
    """XYZ テキストを読み込む。

    Args:
        path: .txt/.csv/.xyz または、それを1つだけ含む .zip。
        delimiter: `space`/`comma`/`tab`/`semicolon`。省略時は先頭行から判定する。
        columns: X, Y, Z が入っている列番号（0 始まり）。
        input_nodata: 入力側で欠測を表す値。該当する点は読み捨てる。
            業務によって -9999 や -32768、±3.4e38 などが混在するため。

    Raises:
        XyzReadError: 読み取れない、または有効な点が残らないとき。
    """
    path = Path(path)
    if len(columns) != 3:
        raise ValueError(f"columns は3つ指定する: {columns}")

    sep = _DELIMITERS[delimiter] if delimiter else sniff_delimiter(path)
    usecols = sorted(set(columns))
    if len(usecols) != 3:
        raise ValueError(f"columns が重複している: {columns}")

    with open_xyz_stream(path) as stream:
        try:
            frame = pd.read_csv(
                stream,
                sep=sep,
                header=None,
                usecols=usecols,
                names=[f"c{i}" for i in range(max(columns) + 1)],
                dtype="float64",
                comment="#",
                skip_blank_lines=True,
                skipinitialspace=True,
                engine="c",
            )
        except Exception as exc:  # pandas は多様な例外を投げる
            raise XyzReadError(f"{path.name}: 読み込みに失敗した: {exc}") from exc

    if frame.empty:
        raise XyzReadError(f"{path.name}: 点が1つもない")

    x = frame[f"c{columns[0]}"].to_numpy(dtype="float64", copy=False)
    y = frame[f"c{columns[1]}"].to_numpy(dtype="float64", copy=False)
    z = frame[f"c{columns[2]}"].to_numpy(dtype="float64", copy=False)

    keep = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    for sentinel in input_nodata:
        keep &= ~np.isclose(z, sentinel, rtol=0, atol=1e-6)
    if not keep.all():
        x, y, z = x[keep], y[keep], z[keep]

    if x.size == 0:
        raise XyzReadError(f"{path.name}: 欠測値を除くと点が残らない")

    return XyzData(x=x, y=y, z=z, source=path)
