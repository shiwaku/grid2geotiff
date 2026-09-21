"""CLI のテスト。"""

from __future__ import annotations

import rasterio
from click.testing import CliRunner

from grid2geotiff.cli import main


def test_convertで変換できる(tmp_path, xyz_file):
    src = xyz_file(drop=0)
    out = tmp_path / "out"
    result = CliRunner().invoke(
        main, ["convert", str(src), "-o", str(out), "--crs", "EPSG:6676"]
    )
    assert result.exit_code == 0, result.output
    with rasterio.open(out / f"{src.stem}.tif") as ds:
        assert ds.crs.to_epsg() == 6676


def test_ディレクトリを再帰的に展開する(tmp_path, xyz_file):
    xyz_file(name="08LE2134.txt")
    xyz_file(name="08LE2135.txt")
    src_dir = tmp_path
    out = tmp_path / "out"
    result = CliRunner().invoke(
        main, ["convert", str(src_dir), "-o", str(out), "--crs", "EPSG:6676"]
    )
    assert result.exit_code == 0, result.output
    assert {p.name for p in out.glob("*.tif")} == {"08LE2134.tif", "08LE2135.tif"}


def test_inspectは出力を作らない(tmp_path, xyz_file):
    src = xyz_file(drop=2)
    result = CliRunner().invoke(main, ["inspect", str(src)])
    assert result.exit_code == 0, result.output
    assert "16x12" in result.output
    assert not list(tmp_path.glob("*.tif"))


def test_不規則点群は終了コード1で失敗する(tmp_path, grid_points):
    import numpy as np

    x, y, z = grid_points
    rng = np.random.default_rng(0)
    src = tmp_path / "scatter.txt"
    src.write_text(
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
    result = CliRunner().invoke(
        main, ["convert", str(src), "-o", str(tmp_path / "out"), "--crs", "EPSG:6676"]
    )
    assert result.exit_code == 1


def test_図郭番号のファイル名ならcrsを省略できる(tmp_path, xyz_file):
    src = xyz_file(drop=0)  # 08LE2134 -> 第8系
    out = tmp_path / "out"
    result = CliRunner().invoke(main, ["convert", str(src), "-o", str(out)])
    assert result.exit_code == 0, result.output
    with rasterio.open(out / f"{src.stem}.tif") as ds:
        assert ds.crs.to_epsg() == 6676
    # 黙って推測せず、判定した CRS とレベルを必ず出す。
    assert "EPSG:6676" in result.output
    assert "1/500 図郭番号から推定" in result.output


def test_datumでEPSGが変わる(tmp_path, xyz_file):
    src = xyz_file(drop=0)
    out = tmp_path / "out"
    result = CliRunner().invoke(
        main, ["convert", str(src), "-o", str(out), "--datum", "jgd2000"]
    )
    assert result.exit_code == 0, result.output
    with rasterio.open(out / f"{src.stem}.tif") as ds:
        assert ds.crs.to_epsg() == 2450


def test_明示したcrsが図郭番号より優先される(tmp_path, xyz_file):
    src = xyz_file(drop=0)  # 名前は第8系だが、第9系を明示する
    out = tmp_path / "out"
    result = CliRunner().invoke(
        main, ["convert", str(src), "-o", str(out), "--crs", "EPSG:6677"]
    )
    assert result.exit_code == 0, result.output
    with rasterio.open(out / f"{src.stem}.tif") as ds:
        assert ds.crs.to_epsg() == 6677


def test_図郭番号でない名前はcrsが必要(tmp_path, xyz_file):
    src = xyz_file(drop=0, name="dsm_area1.txt")
    result = CliRunner().invoke(main, ["convert", str(src), "-o", str(tmp_path / "o")])
    assert result.exit_code == 1
    assert "--crs" in result.output

    ok = CliRunner().invoke(
        main, ["convert", str(src), "-o", str(tmp_path / "o"), "--crs", "EPSG:6676"]
    )
    assert ok.exit_code == 0, ok.output


def test_座標範囲と矛盾する図郭番号は採用しない(tmp_path, xyz_file):
    """名前は 08AA（原点の北西端）だが、座標は 08LE のもの。"""
    src = xyz_file(drop=0, name="08AA0000.txt")
    result = CliRunner().invoke(main, ["convert", str(src), "-o", str(tmp_path / "o")])
    assert result.exit_code == 1
    assert "収まらない" in result.output


def test_inspectも判定したcrsを表示する(tmp_path, xyz_file):
    src = xyz_file(drop=0)
    result = CliRunner().invoke(main, ["inspect", str(src)])
    assert result.exit_code == 0, result.output
    assert "EPSG:6676" in result.output
