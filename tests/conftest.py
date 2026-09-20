"""テスト共通のフィクスチャ。

実データは配布ライセンスと容量の都合でリポジトリに含めないため、
同じ性質（セル中心座標・欠損セル・CRLF）を持つ合成データを組み立てる。
"""

from __future__ import annotations

import numpy as np
import pytest

#: 山梨県の 1/500 図郭データに合わせた諸元（縮小版）。
RES = 0.5
ORIGIN_X = 5600.0
ORIGIN_Y = -37200.0
WIDTH = 16
HEIGHT = 12


@pytest.fixture
def grid_points() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """セル中心に並んだ完全な格子の点を返す。"""
    cx = ORIGIN_X + (np.arange(WIDTH) + 0.5) * RES
    cy = ORIGIN_Y + (np.arange(HEIGHT) + 0.5) * RES
    xx, yy = np.meshgrid(cx, cy)
    x = xx.ravel()
    y = yy.ravel()
    z = 100.0 + (x - ORIGIN_X) * 0.01 + (y - ORIGIN_Y) * 0.02
    return x, y, np.round(z, 2)


@pytest.fixture
def xyz_file(tmp_path, grid_points):
    """CRLF・空白区切りの XYZ テキストを書き出すファクトリ。

    実データと同じく北→南・西→東の順で、一部のセルを欠損させる。
    """

    def _write(name: str = "08LE2134.txt", *, drop: int = 3, delimiter: str = " "):
        x, y, z = grid_points
        order = np.lexsort((x, -y))
        x, y, z = x[order], y[order], z[order]
        if drop:
            keep = np.ones(x.size, dtype=bool)
            keep[np.linspace(0, x.size - 1, drop, dtype=int)] = False
            x, y, z = x[keep], y[keep], z[keep]
        lines = [
            f"{xi:.2f}{delimiter}{yi:.2f}{delimiter}{zi:.2f}"
            for xi, yi, zi in zip(x, y, z, strict=True)
        ]
        path = tmp_path / name
        path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
        return path

    return _write
