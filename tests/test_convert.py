"""変換の通しテスト。"""

from __future__ import annotations

import numpy as np
import pytest
import rasterio
from tests.conftest import HEIGHT, ORIGIN_X, ORIGIN_Y, RES, WIDTH

from grid2geotiff.convert import ConvertOptions, convert_file, inspect_file
from grid2geotiff.xyz import read_xyz


@pytest.fixture
def opts(tmp_path):
    return ConvertOptions(crs="EPSG:6676", out_dir=tmp_path / "out")


def test_GeoTIFFの諸元が正しい(xyz_file, opts):
    result = convert_file(xyz_file(drop=0), opts)
    assert result.ok, result.message

    with rasterio.open(result.output) as ds:
        assert ds.crs.to_epsg() == 6676
        assert (ds.width, ds.height) == (WIDTH, HEIGHT)
        assert ds.res == (RES, RES)
        assert ds.nodata == -9999.0
        assert ds.dtypes[0] == "float32"
        # セル中心座標のまま置かず、半セルずらして図郭境界に合わせている
        assert ds.bounds.left == pytest.approx(ORIGIN_X)
        assert ds.bounds.bottom == pytest.approx(ORIGIN_Y)
        assert ds.bounds.right == pytest.approx(ORIGIN_X + WIDTH * RES)
        assert ds.bounds.top == pytest.approx(ORIGIN_Y + HEIGHT * RES)
        assert ds.tags()["AREA_OR_POINT"] == "Area"


def test_元の座標で引くと元の標高が返る(xyz_file, opts):
    path = xyz_file(drop=0)
    result = convert_file(path, opts)
    data = read_xyz(path)

    with rasterio.open(result.output) as ds:
        coords = zip(data.x, data.y, strict=True)
        got = np.array([v[0] for v in ds.sample(coords)])
    assert np.allclose(got, data.z, atol=1e-4)


def test_欠損セルがNoDataになる(xyz_file, opts):
    result = convert_file(xyz_file(drop=3), opts)
    assert result.filled == 3
    with rasterio.open(result.output) as ds:
        assert ds.read(1, masked=True).mask.sum() == 3


def test_既存の出力は上書きしない(xyz_file, opts):
    path = xyz_file()
    assert convert_file(path, opts).ok
    again = convert_file(path, opts)
    assert not again.ok
    assert "既にある" in again.message


def test_overwriteで上書きする(xyz_file, tmp_path):
    path = xyz_file()
    opts = ConvertOptions(crs="EPSG:6676", out_dir=tmp_path / "out")
    assert convert_file(path, opts).ok
    forced = ConvertOptions(crs="EPSG:6676", out_dir=tmp_path / "out", overwrite=True)
    assert convert_file(path, forced).ok


def test_不規則点群は変換せず失敗を返す(tmp_path, grid_points, opts):
    x, y, z = grid_points
    rng = np.random.default_rng(0)
    path = tmp_path / "scatter.txt"
    path.write_text(
        "\n".join(
            f"{xi:.3f} {yi:.3f} {zi:.2f}"
            for xi, yi, zi in zip(
                x + rng.normal(0, 0.1, x.size),
                y + rng.normal(0, 0.1, y.size),
                z,
                strict=True,
            )
        )
    )
    result = convert_file(path, opts)
    assert not result.ok
    assert "格子に載っていない" in result.message
    assert not (opts.out_dir / "scatter.tif").exists()


def test_点検は出力を作らない(xyz_file, opts):
    result = inspect_file(xyz_file(drop=2), opts)
    assert result.ok
    assert result.output is None
    assert (result.width, result.height) == (WIDTH, HEIGHT)
    assert result.filled == 2
    assert not opts.out_dir.exists()


@pytest.mark.parametrize("compress", ["deflate", "lzw", "zstd", "none"])
def test_圧縮方式を選べる(xyz_file, tmp_path, compress):
    opts = ConvertOptions(crs="EPSG:6676", out_dir=tmp_path / compress, compress=compress)
    result = convert_file(xyz_file(drop=0), opts)
    assert result.ok, result.message
    with rasterio.open(result.output) as ds:
        assert ds.width == WIDTH
