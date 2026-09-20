"""点の並びから格子構造を復元する。

XYZ テキストや LAS は「格子状に並んだ点」であって格子そのものではない。
値を壊さずにラスタ化するには、内挿ではなくインデックス計算で直接配置する
必要がある。そのために格子間隔と原点を推定し、本当に格子かどうかを検証する。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class NotAGridError(ValueError):
    """点が規則格子に載っていない（＝不規則点群である）。"""


@dataclass(frozen=True)
class GridSpec:
    """セル中心の座標で表した格子の定義。"""

    xmin: float  # 左端セルの中心 X
    ymin: float  # 下端セルの中心 Y
    res_x: float
    res_y: float
    width: int
    height: int

    @property
    def xmax(self) -> float:
        """右端セルの中心 X。"""
        return self.xmin + (self.width - 1) * self.res_x

    @property
    def ymax(self) -> float:
        """上端セルの中心 Y。"""
        return self.ymin + (self.height - 1) * self.res_y

    def transform(self):
        """rasterio のアフィン変換を返す。

        セル中心を半セルずらして「面（Area）」規約の外接矩形にする。GDAL の
        XYZ ドライバと同じ流儀で、QGIS などセル中心規約を解釈しないソフトでも
        位置がずれない。
        """
        from rasterio.transform import Affine

        return Affine.translation(
            self.xmin - self.res_x / 2.0, self.ymax + self.res_y / 2.0
        ) * Affine.scale(self.res_x, -self.res_y)

    def indices(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """座標配列を (row, col) の整数インデックスに変換する。"""
        col = np.rint((x - self.xmin) / self.res_x).astype(np.int64)
        row = np.rint((self.ymax - y) / self.res_y).astype(np.int64)
        return row, col


def _infer_step(values: np.ndarray, decimals: int) -> float:
    """1軸分の座標から格子間隔を推定する。

    ソート済みユニーク値の差分の最頻値を採る。欠損セルがあると差分に間隔の
    整数倍が混ざるため、平均や最小値ではなく最頻値を使う。
    """
    uniq = np.unique(np.round(values, decimals))
    if uniq.size < 2:
        raise NotAGridError(
            f"座標が1種類しかないため格子間隔を推定できない（{uniq.size} 通り）"
        )
    diffs = np.round(np.diff(uniq), decimals)
    diffs = diffs[diffs > 0]
    if diffs.size == 0:
        raise NotAGridError("有効な座標差分がない")
    vals, counts = np.unique(diffs, return_counts=True)
    return float(vals[np.argmax(counts)])


def infer_grid(
    x: np.ndarray,
    y: np.ndarray,
    *,
    res: tuple[float, float] | None = None,
    decimals: int = 6,
    tolerance_ratio: float = 0.01,
) -> GridSpec:
    """点群から GridSpec を推定し、格子に載っていることを検証する。

    Args:
        x, y: 点の座標。
        res: 格子間隔を明示する場合の (res_x, res_y)。省略時は推定する。
        decimals: 座標の丸め桁数。浮動小数の表記ゆれを吸収する。
        tolerance_ratio: 格子からのずれの許容量を格子間隔に対する比で指定する。

    Raises:
        NotAGridError: 点が規則格子に載っていないとき。
    """
    if x.size == 0:
        raise NotAGridError("点が1つもない")

    if res is None:
        res_x = _infer_step(x, decimals)
        res_y = _infer_step(y, decimals)
    else:
        res_x, res_y = float(res[0]), float(res[1])
        if res_x <= 0 or res_y <= 0:
            raise ValueError(f"格子間隔は正の値である必要がある: {res}")

    xmin, xmax = float(x.min()), float(x.max())
    ymin, ymax = float(y.min()), float(y.max())

    # 格子からの残差を見て、本当に格子かどうかを判定する。ここを省くと
    # 不規則点群を黙って最近隣で丸め込んでしまう。
    for axis, vals, origin, step in (("X", x, xmin, res_x), ("Y", y, ymin, res_y)):
        offset = (vals - origin) / step
        residual = np.abs(offset - np.rint(offset)) * step
        worst = float(residual.max())
        tol = step * tolerance_ratio
        if worst > tol:
            raise NotAGridError(
                f"{axis} 軸が格子に載っていない"
                f"（最大ずれ {worst:.6g} m > 許容 {tol:.6g} m、推定間隔 {step:.6g} m）。"
                f"不規則点群の可能性が高く、ラスタ化するには内挿補間が必要"
            )

    width = round((xmax - xmin) / res_x) + 1
    height = round((ymax - ymin) / res_y) + 1
    return GridSpec(
        xmin=xmin, ymin=ymin, res_x=res_x, res_y=res_y, width=width, height=height
    )


def rasterize(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    spec: GridSpec,
    *,
    nodata: float = -9999.0,
    dtype: str = "float32",
) -> np.ndarray:
    """格子上の点を 2 次元配列に直接配置する（内挿は行わない）。"""
    arr = np.full((spec.height, spec.width), nodata, dtype=dtype)
    row, col = spec.indices(x, y)
    inside = (row >= 0) & (row < spec.height) & (col >= 0) & (col < spec.width)
    arr[row[inside], col[inside]] = z[inside]
    return arr
