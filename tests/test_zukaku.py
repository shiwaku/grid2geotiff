"""図郭番号の解釈のテスト。

期待値は山梨県の実データ4枚（1/500 図郭）の座標範囲から採った。図郭番号から
計算した範囲と実データの外接矩形が完全に一致することを確認してある。
"""

from __future__ import annotations

import pytest

from grid2geotiff.zukaku import ZukakuError, parse

#: 実データで検算済みの 1/500 図郭。code -> (xmin, ymin, xmax, ymax)
REAL_TILES = {
    "08LE2134": (5600.0, -37200.0, 6000.0, -36900.0),
    "08LE2135": (6000.0, -37200.0, 6400.0, -36900.0),
    "08LE2144": (5600.0, -37500.0, 6000.0, -37200.0),
    "08LE2145": (6000.0, -37500.0, 6400.0, -37200.0),
}


@pytest.mark.parametrize(("code", "extent"), REAL_TILES.items())
def test_実データの図郭番号から範囲を再現できる(code, extent):
    z = parse(code)
    assert z.epsg() == "EPSG:6676"
    assert z.level == 500
    assert z.extent == extent


def test_系番号からEPSGを導く():
    assert parse("01AA").epsg() == "EPSG:6669"  # 第1系
    assert parse("19AA").epsg() == "EPSG:6687"  # 第19系


def test_測地系でEPSGが変わる():
    z = parse("08LE2134")
    assert z.epsg("jgd2011") == "EPSG:6676"
    assert z.epsg("jgd2000") == "EPSG:2450"
    with pytest.raises(ValueError, match="未知の測地系"):
        z.epsg("tokyo")


@pytest.mark.parametrize(
    ("code", "level", "extent"),
    [
        ("08LE", 50_000, (0.0, -60_000.0, 40_000.0, -30_000.0)),
        ("08LE21", 5_000, (4_000.0, -39_000.0, 8_000.0, -36_000.0)),
        ("08LE211", 2_500, (4_000.0, -37_500.0, 6_000.0, -36_000.0)),  # 1=北西
        ("08LE214", 2_500, (6_000.0, -39_000.0, 8_000.0, -37_500.0)),  # 4=南東
    ],
)
def test_細分の階層ごとに範囲を計算する(code, level, extent):
    z = parse(code)
    assert z.level == level
    assert z.extent == extent


def test_細分は親の図郭に含まれる():
    parent = parse("08LE21").extent
    child = parse("08LE2134").extent
    assert parent[0] <= child[0] and child[2] <= parent[2]
    assert parent[1] <= child[1] and child[3] <= parent[3]


def test_末尾の注記と小文字を許容する():
    assert parse("08le2134_dsm").extent == REAL_TILES["08LE2134"]
    assert parse("08LE2134-1").extent == REAL_TILES["08LE2134"]


@pytest.mark.parametrize(
    "name",
    [
        "scatter",  # 図郭番号の形式でない
        "08LE21341",  # 細分の桁数が不正
        "08LE213456",
        "00LE2134",  # 系番号が範囲外
        "20LE2134",
        "08UE2134",  # 南北の記号が範囲外（A〜T）
        "08LJ2134",  # 東西の記号が範囲外（A〜H）
        "08LE215",  # 1/2500 の番号が範囲外（1〜4）
        "08LE210",
    ],
)
def test_解釈できない名前はエラーにする(name):
    with pytest.raises(ZukakuError):
        parse(name)


def test_containsは図郭の範囲で判定する():
    z = parse("08LE2134")
    assert z.contains(z.extent, slack=0.5)  # 図郭ぴったり
    assert z.contains((5700.0, -37100.0, 5800.0, -37000.0), slack=0.5)  # 図郭の内側
    assert not z.contains((5600.0, -37200.0, 6400.0, -36900.0), slack=0.5)  # 東に1図郭分
    assert not z.contains((0.0, 0.0, 1.0, 1.0), slack=0.5)  # まるで別の場所


def test_系番号が違っても図郭の範囲は同じ():
    """図郭の範囲は各系の原点からの相対位置なので、系を取り違えても一致する。

    したがって範囲の検算では系番号の正しさを検証できない。判定結果を必ずログに
    出しているのはこのため。
    """
    assert parse("08LE2134").extent == parse("09LE2134").extent
    assert parse("08LE2134").epsg() != parse("09LE2134").epsg()
