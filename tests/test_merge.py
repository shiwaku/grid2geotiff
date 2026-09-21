"""図郭マージのテスト。

実データ（山梨県の 1/500 図郭）では、結合後の有効セル数が図郭ごとの有効セル数の
合計と完全に一致することを確認してある。ここでは同じ性質を持つ合成ラスタで、
隙間・重複が出ないことと、揃っていない入力を弾くことを検証する。
"""

from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from grid2geotiff.merge import (
    MergeError,
    build_vrt,
    check_compatible,
    merge_files,
    read_sources,
)

NODATA = -9999.0
RES = 0.5
SIZE = 20  # 20 x 20 セル = 10m x 10m


@pytest.fixture
def make_tif(tmp_path):
    """諸元を指定して小さな GeoTIFF を書くファクトリ。

    左上（0, 0）を基準に、隣り合う図郭を `left` で並べる。1セルだけ NoData を
    入れて、欠損セルが結合をまたいでも保たれることを見る。
    """

    def _make(
        name: str,
        *,
        left: float = 0.0,
        top: float = 0.0,
        res: float = RES,
        crs: str = "EPSG:6676",
        dtype: str = "float32",
        nodata: float = NODATA,
        start: int = 0,
    ):
        array = np.arange(start, start + SIZE * SIZE, dtype=dtype).reshape(SIZE, SIZE)
        array[0, 0] = nodata
        path = tmp_path / name
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=SIZE,
            height=SIZE,
            count=1,
            dtype=dtype,
            crs=crs,
            nodata=nodata,
            transform=Affine.translation(left, top) * Affine.scale(res, -res),
            tiled=True,
            blockxsize=16,
            blockysize=16,
        ) as ds:
            ds.write(array, 1)
        return path

    return _make


@pytest.fixture
def pair(make_tif):
    """東西に隣り合う2枚。境界はぴったり接する。"""
    return [make_tif("west.tif"), make_tif("east.tif", left=SIZE * RES, start=10_000)]


# --- 結合 --------------------------------------------------------------------


def test_隣り合う図郭を結合できる(pair, tmp_path):
    out = tmp_path / "merged.tif"
    result = merge_files(pair, out)
    assert (result.width, result.height) == (SIZE * 2, SIZE)
    assert result.coverage == pytest.approx(1.0)

    with rasterio.open(out) as ds:
        assert ds.crs.to_epsg() == 6676
        assert ds.nodata == NODATA
        assert ds.bounds.left == 0.0
        assert ds.bounds.right == SIZE * 2 * RES


def test_結合しても有効セルは増えも減りもしない(pair, tmp_path):
    """隙間があれば減り、重複があれば減る。合計が一致すれば両方ないと言える。"""
    expected = 0
    for p in pair:
        with rasterio.open(p) as ds:
            expected += int((ds.read(1) != NODATA).sum())

    out = tmp_path / "merged.tif"
    merge_files(pair, out)
    with rasterio.open(out) as ds:
        assert int((ds.read(1) != NODATA).sum()) == expected


def test_値がそのまま運ばれる(pair, tmp_path):
    out = tmp_path / "merged.tif"
    merge_files(pair, out)
    with rasterio.open(out) as ds:
        merged = ds.read(1)
    with rasterio.open(pair[1]) as ds:
        east = ds.read(1)
    np.testing.assert_array_equal(merged[:, SIZE:], east)


def test_nodataが不揃いでも統一される(make_tif, tmp_path):
    sources = [make_tif("a.tif"), make_tif("b.tif", left=SIZE * RES, nodata=-32768.0)]
    out = tmp_path / "merged.tif"
    merge_files(sources, out, nodata=NODATA)
    with rasterio.open(out) as ds:
        array = ds.read(1)
        assert ds.nodata == NODATA
        assert not (array == -32768.0).any()
        assert int((array == NODATA).sum()) == 2  # 各図郭の1セルずつ


# --- 揃っていない入力を弾く --------------------------------------------------


def test_半セルずれた図郭は結合しない(make_tif):
    """このツールが最初に取り組んだ半セルずれを、結合で作り直すわけにいかない。"""
    sources = read_sources(
        [make_tif("a.tif"), make_tif("b.tif", left=SIZE * RES + RES / 2)]
    )
    with pytest.raises(MergeError, match="噛み合わない"):
        check_compatible(sources)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"crs": "EPSG:6677"}, "座標系が揃っていない"),
        ({"res": 1.0}, "格子間隔が揃っていない"),
        ({"dtype": "int32", "nodata": -9999}, "データ型が揃っていない"),
    ],
)
def test_諸元が食い違えば結合しない(make_tif, kwargs, message):
    sources = read_sources(
        [make_tif("a.tif"), make_tif("b.tif", left=SIZE * RES, **kwargs)]
    )
    with pytest.raises(MergeError, match=message):
        check_compatible(sources)


def test_離れた区画は被覆率で弾く(make_tif, tmp_path):
    """1/500 図郭が 10km 離れていれば、間を埋めるだけの巨大な空ラスタになる。"""
    sources = [make_tif("a.tif"), make_tif("far.tif", left=10_000.0)]
    with pytest.raises(MergeError, match="入力が疎すぎる"):
        merge_files(sources, tmp_path / "merged.tif")


def test_被覆率の下限は緩められる(make_tif, tmp_path):
    sources = [make_tif("a.tif"), make_tif("far.tif", left=1_000.0)]
    result = merge_files(sources, tmp_path / "merged.tif", min_coverage=0.0)
    assert result.coverage < 0.05


def test_既存の出力は上書きしない(pair, tmp_path):
    out = tmp_path / "merged.tif"
    merge_files(pair, out)
    with pytest.raises(MergeError, match="--overwrite"):
        merge_files(pair, out)
    merge_files(pair, out, overwrite=True)


# --- クリップ ----------------------------------------------------------------


def test_boundsで切り出せる(pair, tmp_path):
    out = tmp_path / "clip.tif"
    result = merge_files(pair, out, bounds=(2.0, -8.0, 12.0, -2.0))
    assert (result.width, result.height) == (20, 12)
    with rasterio.open(out) as ds:
        assert (ds.bounds.left, ds.bounds.bottom) == (2.0, -8.0)
        assert (ds.bounds.right, ds.bounds.top) == (12.0, -2.0)


def test_格子に載らないboundsは外側へ丸める(pair, tmp_path):
    """半端な位置で切ると出力全体がずれるので、セル境界に丸める。"""
    out = tmp_path / "clip.tif"
    merge_files(pair, out, bounds=(2.1, -7.9, 11.9, -2.1))
    with rasterio.open(out) as ds:
        assert (ds.bounds.left, ds.bounds.bottom) == (2.0, -8.0)
        assert (ds.bounds.right, ds.bounds.top) == (12.0, -2.0)


def test_重ならないクリップ範囲はエラーにする(pair, tmp_path):
    with pytest.raises(MergeError, match="重ならない"):
        merge_files(pair, tmp_path / "clip.tif", bounds=(1e6, 1e6, 1e6 + 10, 1e6 + 10))


# --- VRT ---------------------------------------------------------------------


def test_vrtだけを出力できる(pair, tmp_path):
    out = tmp_path / "mosaic.vrt"
    merge_files(pair, out, vrt_only=True)
    xml = out.read_text(encoding="utf-8")
    assert xml.count("<ComplexSource>") == 2
    # VRT と同じ場所にあるソースは相対パスで書く（持ち運べるように）。
    assert 'relativeToVRT="1"' in xml
    with rasterio.open(out) as ds:
        assert (ds.width, ds.height) == (SIZE * 2, SIZE)


def test_範囲外の図郭はvrtに載せない(make_tif, tmp_path):
    sources = read_sources(
        [
            make_tif("a.tif"),
            make_tif("b.tif", left=SIZE * RES),
            make_tif("c.tif", left=SIZE * RES * 2),
        ]
    )
    xml = build_vrt(sources, (0.0, -10.0, 10.0, 0.0), nodata=NODATA)
    assert xml.count("<ComplexSource>") == 1


def test_入力が空ならエラー():
    with pytest.raises(MergeError, match="入力がない"):
        check_compatible([])


# --- ベクタでのクリップ（fiona は任意依存） ----------------------------------


def test_ベクタの範囲でクリップできる(pair, tmp_path):
    fiona = pytest.importorskip("fiona")

    from grid2geotiff.merge import read_clip_bounds

    poly = tmp_path / "clip.geojson"
    schema = {"geometry": "Polygon", "properties": {}}
    with fiona.open(poly, "w", driver="GeoJSON", schema=schema, crs="EPSG:6676") as dst:
        dst.write(
            {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            (2.0, -8.0),
                            (12.0, -8.0),
                            (12.0, -2.0),
                            (2.0, -2.0),
                            (2.0, -8.0),
                        ]
                    ],
                },
                "properties": {},
            }
        )

    assert read_clip_bounds(poly) == (2.0, -8.0, 12.0, -2.0)

    out = tmp_path / "clipped.tif"
    merge_files(pair, out, bounds=read_clip_bounds(poly))
    with rasterio.open(out) as ds:
        assert (ds.width, ds.height) == (20, 12)


# --- CLI ---------------------------------------------------------------------


def test_cliでマージできる(pair, tmp_path):
    from click.testing import CliRunner

    from grid2geotiff.cli import main

    out = tmp_path / "merged.tif"
    result = CliRunner().invoke(main, ["merge", *[str(p) for p in pair], "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "被覆率 100.00%" in result.output
    assert out.exists()


def test_cliは揃っていない入力を終了コード1で弾く(make_tif, tmp_path):
    from click.testing import CliRunner

    from grid2geotiff.cli import main

    sources = [make_tif("a.tif"), make_tif("b.tif", left=SIZE * RES + RES / 2)]
    result = CliRunner().invoke(
        main,
        ["merge", *[str(p) for p in sources], "-o", str(tmp_path / "x.tif")],
    )
    assert result.exit_code == 1
    assert "噛み合わない" in result.output
