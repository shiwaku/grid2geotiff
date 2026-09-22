"""仕様適合の点検。

条項ごとに、満たす GeoTIFF と外れる GeoTIFF を組み立てて判定を確かめる。
"""

from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from grid2geotiff.spec import check_file

#: 1/500 図郭 `08LE2134`（X 5600..6000、Y -37200..-36900）の北西角。
LEFT = 5_600.0
TOP = -36_900.0
RES = 0.5


def write_tif(path, **over):
    """仕様を満たす GeoTIFF を書き、`over` で1項目だけ崩す。"""
    opts = {
        "width": 80,
        "height": 60,
        "dtype": "float32",
        "nodata": -9999.0,
        "crs": "EPSG:6676",
        "transform": from_origin(LEFT, TOP, RES, RES),
        "compress": "deflate",
        "count": 1,
    }
    data = over.pop("data", None)
    opts.update(over)
    if data is None:
        # 1cm 刻みの標高。実データ（山梨県 DEM）に合わせる。
        rng = np.random.default_rng(0)
        data = np.round(100 + rng.random((opts["height"], opts["width"])) * 10, 2)
    with rasterio.open(path, "w", driver="GTiff", **opts) as ds:
        for band in range(1, opts["count"] + 1):
            ds.write(data.astype(opts["dtype"]), band)
    return path


def levels(result) -> dict[str, str]:
    return {c.item: c.level for c in result.checks}


def test_仕様を満たす出力は全項目が適合(tmp_path):
    result = check_file(write_tif(tmp_path / "08LE2134.tif"))
    assert result.ok
    assert set(levels(result).values()) == {"OK"}


def test_NoDataが統一されていなければ不適合(tmp_path):
    """マップタイル作成マニュアルは「ラスタ値は -9999 に統一」と定める。

    山梨県の配布 GeoTIFF は float32 の最大値に近い値を使っており、混ざると
    透過処理が一部だけ効かなくなる。
    """
    result = check_file(write_tif(tmp_path / "08LE2134.tif", nodata=3.4e38))
    assert not result.ok
    assert levels(result)["NoData"] == "NG"


def test_NoDataが無ければ不適合(tmp_path):
    result = check_file(write_tif(tmp_path / "08LE2134.tif", nodata=None))
    assert levels(result)["NoData"] == "NG"


def test_格子間隔が1mを超えれば不適合(tmp_path):
    """標準仕様書 Ver3.1 は「ピクセルサイズは 1m 以下とする」と言い切る。"""
    result = check_file(
        write_tif(tmp_path / "08LE2134.tif", transform=from_origin(LEFT, TOP, 2.0, 2.0))
    )
    assert levels(result)["格子間隔"] == "NG"


def test_縦横で格子間隔が違えば不適合(tmp_path):
    result = check_file(
        write_tif(tmp_path / "08LE2134.tif", transform=from_origin(LEFT, TOP, 0.5, 1.0))
    )
    assert levels(result)["格子間隔"] == "NG"


def test_1m格子は注意にとどめる(tmp_path):
    """オープンデータ標準仕様書は 0.5m を求めるが、Ver3.1 は 1m 以下を認める。"""
    result = check_file(
        write_tif(tmp_path / "08LE2134.tif", transform=from_origin(LEFT, TOP, 1.0, 1.0))
    )
    assert levels(result)["格子間隔"] == "注意"
    assert result.ok  # 失敗にはしない


def test_半セルずれた原点は図郭で捕まる(tmp_path):
    """セル中心をそのまま原点にすると 0.25m ずれる。本ツールが最初に直した誤り。"""
    shifted = from_origin(LEFT + RES / 2, TOP - RES / 2, RES, RES)
    result = check_file(write_tif(tmp_path / "08LE2134.tif", transform=shifted))
    assert levels(result)["図郭"] == "NG"


def test_図郭番号でないファイル名は照合しない(tmp_path):
    result = check_file(write_tif(tmp_path / "dsm_area1.tif"))
    assert levels(result)["図郭"] == "注意"
    assert result.ok


def test_バンドが複数あれば不適合(tmp_path):
    result = check_file(write_tif(tmp_path / "08LE2134.tif", count=3))
    assert levels(result)["バンド"] == "NG"


def test_32bitでなければ注意(tmp_path):
    """「32bit を基本とする」なので、外れても失敗にはしない。"""
    data = np.full((60, 80), 100, dtype="int16")
    result = check_file(
        write_tif(tmp_path / "08LE2134.tif", dtype="int16", nodata=-9999.0, data=data)
    )
    assert levels(result)["バンド"] == "注意"
    assert result.ok


def test_非圧縮も適合(tmp_path):
    result = check_file(write_tif(tmp_path / "08LE2134.tif", compress=None))
    assert levels(result)["圧縮"] == "OK"


def test_平面直角座標系でなければ注意(tmp_path):
    result = check_file(write_tif(tmp_path / "08LE2134.tif", crs="EPSG:3857"))
    assert levels(result)["座標系"] == "注意"


def test_欠測率が1割を超えれば注意(tmp_path):
    """地理院マニュアルは「国土基本図図郭単位で 10％以下を標準とする」。"""
    data = np.full((60, 80), 100.0, dtype="float32")
    data[:20, :] = -9999.0  # 3分の1を欠測にする
    result = check_file(write_tif(tmp_path / "08LE2134.tif", data=data))
    assert levels(result)["欠測率"] == "注意"
    assert result.ok


@pytest.mark.parametrize("step", [0.1, 0.01])
def test_標高値の刻みを見分ける(tmp_path, step):
    rng = np.random.default_rng(1)
    data = np.round(rng.random((60, 80)) * 100 / step) * step
    result = check_file(write_tif(tmp_path / "08LE2134.tif", data=data))
    detail = next(c.detail for c in result.checks if c.item == "標高値の刻み")
    assert detail == f"{step:g}m 刻み"


def test_1cmより細かい値は注意(tmp_path):
    rng = np.random.default_rng(2)
    data = rng.random((60, 80)) * 100  # 丸めない
    result = check_file(write_tif(tmp_path / "08LE2134.tif", data=data))
    assert levels(result)["標高値の刻み"] == "注意"
    assert result.ok


def test_開けないファイルはエラーにする(tmp_path):
    broken = tmp_path / "08LE2134.tif"
    broken.write_bytes(b"not a tiff")
    result = check_file(broken)
    assert not result.ok
    assert "開けない" in result.error
