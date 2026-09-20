"""XYZ テキスト読み込みのテスト。"""

from __future__ import annotations

import zipfile

import numpy as np
import pytest

from grid2geotiff.xyz import XyzReadError, read_xyz, sniff_delimiter


def test_空白区切りCRLFを読める(xyz_file):
    path = xyz_file(drop=0)
    data = read_xyz(path)
    assert len(data) == 16 * 12
    assert data.x.min() == pytest.approx(5600.25)


@pytest.mark.parametrize(
    ("delimiter", "expected"),
    [(" ", r"\s+"), (",", ","), ("\t", "\t"), (";", ";")],
)
def test_区切り文字を判定できる(xyz_file, delimiter, expected):
    path = xyz_file(name=f"d{ord(delimiter)}.txt", delimiter=delimiter)
    assert sniff_delimiter(path) == expected
    assert len(read_xyz(path)) > 0


def test_ZIPをそのまま読める(tmp_path, xyz_file):
    src = xyz_file(drop=0)
    zip_path = tmp_path / "08LE2134.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(src, arcname=src.name)
    assert len(read_xyz(zip_path)) == 16 * 12


def test_ZIPに複数テキストがあれば拒否する(tmp_path, xyz_file):
    a = xyz_file(name="a.txt")
    b = xyz_file(name="b.txt")
    zip_path = tmp_path / "multi.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(a, arcname="a.txt")
        z.write(b, arcname="b.txt")
    with pytest.raises(XyzReadError, match="複数ある"):
        read_xyz(zip_path)


def test_入力側の欠測値を読み捨てる(tmp_path):
    path = tmp_path / "s.txt"
    path.write_text("0.25 0.25 10.0\n0.75 0.25 -9999\n0.25 0.75 11.0\n0.75 0.75 12.0\n")
    data = read_xyz(path, input_nodata=(-9999,))
    assert len(data) == 3
    assert -9999 not in data.z


def test_コメント行と空行を飛ばす(tmp_path):
    path = tmp_path / "c.txt"
    path.write_text("# comment\n0.25 0.25 10.0\n\n0.75 0.25 11.0\n")
    assert len(read_xyz(path)) == 2


def test_列順を指定できる(tmp_path):
    path = tmp_path / "yxz.txt"
    # Y X Z の順に並んだファイル
    path.write_text("0.25 5.25 10.0\n0.25 5.75 11.0\n")
    data = read_xyz(path, columns=(1, 0, 2))
    assert np.allclose(data.x, [5.25, 5.75])
    assert np.allclose(data.y, [0.25, 0.25])


def test_全点が欠測値なら拒否する(tmp_path):
    path = tmp_path / "e.txt"
    path.write_text("0.25 0.25 -9999\n0.75 0.25 -9999\n")
    with pytest.raises(XyzReadError, match="点が残らない"):
        read_xyz(path, input_nodata=(-9999,))
