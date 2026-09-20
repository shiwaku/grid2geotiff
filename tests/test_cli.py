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


def test_crsは必須(tmp_path, xyz_file):
    src = xyz_file()
    result = CliRunner().invoke(main, ["convert", str(src), "-o", str(tmp_path / "o")])
    assert result.exit_code != 0
    assert "--crs" in result.output
