"""格子構造の推定と検証のテスト。"""

from __future__ import annotations

import numpy as np
import pytest
from tests.conftest import HEIGHT, ORIGIN_X, ORIGIN_Y, RES, WIDTH

from grid2geotiff.gridspec import NotAGridError, infer_grid, rasterize


def test_格子間隔と大きさを推定する(grid_points):
    x, y, _ = grid_points
    spec = infer_grid(x, y)
    assert (spec.res_x, spec.res_y) == (RES, RES)
    assert (spec.width, spec.height) == (WIDTH, HEIGHT)


def test_変換行列が半セルずれて図郭境界に載る(grid_points):
    x, y, _ = grid_points
    spec = infer_grid(x, y)
    transform = spec.transform()
    # セル中心が .25/.75 でも、左上隅は図郭の切りの良い座標になる
    assert transform.c == pytest.approx(ORIGIN_X)
    assert transform.f == pytest.approx(ORIGIN_Y + HEIGHT * RES)
    assert transform.a == pytest.approx(RES)
    assert transform.e == pytest.approx(-RES)


def test_欠損セルがあっても格子の大きさは変わらない(grid_points):
    x, y, _ = grid_points
    keep = np.ones(x.size, dtype=bool)
    keep[[0, 5, 17]] = False
    spec = infer_grid(x[keep], y[keep])
    assert (spec.width, spec.height) == (WIDTH, HEIGHT)


def test_不規則点群は充填率で拒否する(grid_points):
    """散らばった点は「細かすぎる格子」に載ってしまうので、疎さで見分ける。

    差分の最頻値はテキストの桁数による量子化ステップを拾いやすく、そうなると
    全点がその格子にぴったり載るため残差検定が素通りする。セルの埋まり具合なら
    桁違いに差が出る（実データは 98% 以上、不規則点群は 1% 未満）。
    """
    x, y, _ = grid_points
    rng = np.random.default_rng(0)
    with pytest.raises(NotAGridError, match="格子が疎すぎる"):
        infer_grid(x + rng.normal(0, 0.1, x.size), y + rng.normal(0, 0.1, y.size))


def test_1点だけ格子から外れていても拒否する(grid_points):
    """格子間隔が正しく推定できる場合は、残差検定の側で捕まえる。"""
    x, y, _ = grid_points
    x = x.copy()
    x[0] += 0.1  # 0.5m 格子に対して 0.1m のずれ
    with pytest.raises(NotAGridError, match="格子に載っていない"):
        infer_grid(x, y)


def test_充填率の下限は緩められる(grid_points):
    """意図的に疎な格子のために --min-fill-ratio を開けてある。

    緩めても残差検定は残るので、不規則点群はそちらで捕まる。0 にすると充填率の
    検定そのものが止まり、巨大な配列を確保しにいくので既定では勧めない。
    """
    x, y, _ = grid_points
    rng = np.random.default_rng(0)
    jx = x + rng.normal(0, 0.1, x.size)
    jy = y + rng.normal(0, 0.1, y.size)
    with pytest.raises(NotAGridError, match="格子に載っていない"):
        infer_grid(jx, jy, min_fill_ratio=1e-6)


def test_欠損が多くても実データ並みなら通る(grid_points):
    """山梨県の実データは欠損 1.76% の図郭がある。その程度では弾かない。"""
    x, y, _ = grid_points
    rng = np.random.default_rng(0)
    keep = rng.random(x.size) > 0.3  # 30% 欠損
    spec = infer_grid(x[keep], y[keep])
    assert (spec.width, spec.height) == (WIDTH, HEIGHT)


def test_格子間隔を明示できる(grid_points):
    x, y, _ = grid_points
    spec = infer_grid(x, y, res=(RES, RES))
    assert (spec.width, spec.height) == (WIDTH, HEIGHT)


def test_格子間隔の明示値が誤っていれば拒否する(grid_points):
    x, y, _ = grid_points
    with pytest.raises(NotAGridError):
        infer_grid(x, y, res=(0.3, 0.3))


def test_点が1つもなければ拒否する():
    with pytest.raises(NotAGridError, match="点が1つもない"):
        infer_grid(np.array([]), np.array([]))


def test_配置は内挿せず元の値を保つ(grid_points):
    x, y, z = grid_points
    spec = infer_grid(x, y)
    arr = rasterize(x, y, z, spec, nodata=-9999.0)
    assert arr.shape == (HEIGHT, WIDTH)
    row, col = spec.indices(x, y)
    assert np.allclose(arr[row, col], z, atol=1e-4)


def test_欠損セルにNoDataが入る(grid_points):
    x, y, z = grid_points
    keep = np.ones(x.size, dtype=bool)
    keep[[1, 2, 3]] = False
    spec = infer_grid(x[keep], y[keep])
    arr = rasterize(x[keep], y[keep], z[keep], spec, nodata=-9999.0)
    assert int((arr == -9999.0).sum()) == 3
