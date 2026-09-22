"""図郭番号の解釈のテスト。

1/500 の期待値は山梨県の実データ4枚の座標範囲から採った。図郭番号から計算した
範囲と実データの外接矩形が完全に一致することを確認してある。1/50000 の割り方と
1/5000 の行列の読み方は「森林情報に関するオープンデータ標準仕様書 Ver2.1」参考3
で確認した。
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
    figure = z.match(extent, slack=0.5)
    assert figure is not None
    assert figure.name == "1/500"
    assert figure.extent == extent


def test_系番号からEPSGを導く():
    assert parse("01AA00").epsg() == "EPSG:6669"  # 第1系
    assert parse("19AA00").epsg() == "EPSG:6687"  # 第19系


def test_測地系でEPSGが変わる():
    z = parse("08LE2134")
    assert z.epsg("jgd2011") == "EPSG:6676"
    assert z.epsg("jgd2000") == "EPSG:2450"
    with pytest.raises(ValueError, match="未知の測地系"):
        z.epsg("tokyo")


def test_1_5000図郭():
    (figure,) = parse("08LE21").candidates
    assert figure.name == "1/5000"
    assert figure.extent == (4_000.0, -39_000.0, 8_000.0, -36_000.0)


@pytest.mark.parametrize(
    ("code", "extent"),
    [
        ("08LE211", (4_000.0, -37_500.0, 6_000.0, -36_000.0)),  # 1=北西
        ("08LE214", (6_000.0, -39_000.0, 8_000.0, -37_500.0)),  # 4=南東
    ],
)
def test_1_2500図郭(code, extent):
    (figure,) = parse(code).candidates
    assert figure.name == "1/2500"
    assert figure.extent == extent


def test_1_2500は自分の範囲で検算する():
    """1〜4 の並びは広島県の実データ 300 図郭で確かめてある。

    かつては並びの裏が取れず親の 1/5000 図郭で検算していたが、実データで確認できた
    ので自分の範囲で見る。別の象限に載るデータは弾く。
    """
    (figure,) = parse("08LE211").candidates
    assert figure.check_extent == figure.extent
    assert not figure.contains((6_100.0, -38_900.0, 6_200.0, -38_800.0), slack=0.5)


# --- 数字2桁の3通りの読み方 --------------------------------------------------


def test_数字2桁は1_500と1_1000の両方を候補にする():
    names = [f.name for f in parse("08LE2100").candidates]
    assert names == ["1/500", "1/1000"]  # 細かい方が先


def test_各桁が1から4なら1_2500の4分割も候補になる():
    """林野庁のマップタイル作成マニュアルが実務単位として挙げる 750m × 1000m。"""
    names = [f.name for f in parse("08LE2122").candidates]
    assert names == ["1/500", "1/1000", "1/2500 の4分割"]  # 細かい方が先


def test_0を含む数字2桁は1_2500の4分割になりえない():
    """1/2500 とその4分割の番号は 1〜4 で、0 は使わない。"""
    names = [f.name for f in parse("08LE2100").candidates]
    assert "1/2500 の4分割" not in names


def test_各桁が5以上なら1_1000はありえない():
    """1/1000 は 1/5000 を縦横 5 等分するので、各桁は 0〜4 にしかならない。"""
    names = [f.name for f in parse("08LE2155").candidates]
    assert names == ["1/500"]


@pytest.mark.parametrize(
    ("code", "extent", "name"),
    [
        ("08LE2100", (4_000.0, -36_300.0, 4_400.0, -36_000.0), "1/500"),  # 400m × 300m
        ("08LE2100", (4_000.0, -36_600.0, 4_800.0, -36_000.0), "1/1000"),  # 800m × 600m
        # 750m × 1000m。1/500 にも 1/1000 にも収まらないので4分割と判る。
        ("08LE2122", (7_000.0, -36_750.0, 8_000.0, -36_000.0), "1/2500 の4分割"),
    ],
)
def test_座標が収まる図郭を選ぶ(code, extent, name):
    """数字2桁は読み方が3通りあるので、範囲で見分ける。"""
    figure = parse(code).match(extent, slack=0.5)
    assert figure is not None
    assert figure.name == name
    assert figure.extent == extent


def test_広島県の実データの図郭番号から範囲を再現できる():
    """1/2500 の4分割で配布されている実例。

    ファイル名は `03od7922_14_05mcsv` で、末尾は整備年度と格子間隔。座標範囲は
    grid2geotiff inspect の実測値（2000x1483 @0.5m）。
    """
    z = parse("03OD7922")
    assert z.epsg() == "EPSG:6671"  # 第3系
    figure = z.match((-1_000.0, -141_750.0, 0.0, -141_008.5), slack=0.5)
    assert figure is not None
    assert figure.name == "1/2500 の4分割"
    assert figure.extent == (-1_000.0, -141_750.0, 0.0, -141_000.0)


def test_どちらの候補にも収まらなければNone():
    assert parse("08LE2100").match((0.0, 0.0, 1.0, 1.0), slack=0.5) is None


# --- 解釈しない形式 ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("08LE", "1/50000 図郭。誤一致しやすく、グリッドデータの単位でもない"),
        ("04HE2", "1/50000 を 4 分割した図郭。ラスタの配布単位であって XYZ の単位でない"),
        ("scatter", "図郭番号の形式でない"),
        ("08LE21345", "細分の桁数が多すぎる"),
        ("00LE2134", "系番号が範囲外"),
        ("20LE2134", "系番号が範囲外"),
        ("08UE2134", "南北の記号が範囲外（A〜T）"),
        ("08LJ2134", "東西の記号が範囲外（A〜H）"),
        ("08LE215", "1/2500 の番号が範囲外（1〜4）"),
        ("08LE210", "1/2500 の番号が範囲外（1〜4）"),
    ],
)
def test_解釈しない名前はエラーにする(name, reason):
    with pytest.raises(ZukakuError):
        parse(name)


def test_末尾の注記と小文字を許容する():
    for name in ("08le2134_dsm", "08LE2134-1"):
        figure = parse(name).match(REAL_TILES["08LE2134"], slack=0.5)
        assert figure is not None
        assert figure.extent == REAL_TILES["08LE2134"]


def test_系番号が違っても図郭の範囲は同じ():
    """図郭の範囲は各系の原点からの相対位置なので、系を取り違えても一致する。

    したがって範囲の検算では系番号の正しさを検証できない。判定結果を必ずログに
    出しているのはこのため。
    """
    assert parse("08LE2134").candidates == parse("09LE2134").candidates
    assert parse("08LE2134").epsg() != parse("09LE2134").epsg()
