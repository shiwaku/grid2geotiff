"""国土基本図の図郭番号から座標系と範囲を求める。

図郭番号は先頭2桁が平面直角座標系の系番号になっている（`08LE2134` なら第8系）。
JGD2011 の平面直角座標系は EPSG:6669（第1系）から連番なので、系番号 N に対して
``EPSG = 6668 + N`` で求まる。これを使えば利用者が毎回 EPSG コードを調べずに済む。

ただし**推測が外れると成果物の位置が丸ごとずれる**うえ、CRS はファイルに埋め込まれて
しまうので後から気づきにくい。そこで図郭番号から範囲も計算し、実際の座標がその図郭に
収まることを確かめてから採用する。

この検算で分かるのは「ファイル名が本当に図郭番号か」であって、**系番号の正しさは検証
できない**。図郭の範囲は各系の原点からの相対位置で決まるため、系を取り違えても範囲は
まったく同じになるからである（`08LE2134` と `09LE2134` は各系で同じ位置を指す）。

それでも検算には意味がある。`12AB3456` のような業務番号がたまたま図郭番号の形に一致する
ことはありうるが、その場合に座標が 400m × 300m の図郭にぴったり収まることはまずない。
つまりこの検算は「名前の形が偶然一致しただけのファイルに、でたらめな CRS を埋め込む」
事故を防ぐ。系番号そのものは検証しようがないので、**判定結果は必ずログに出す**。

図郭の構成:

- 1/50000 図郭 …… 系原点を中心に南北 ±300km・東西 ±160km の区域を縦 20・横 8 に分割
  （南北 30km × 東西 40km）。左上を A として南北・東西の順に記号を付す。
- 1/5000 図郭 …… 1/50000 を縦横 10 等分（3km × 4km）。2桁の数字（行, 列）。
- 1/2500 図郭 …… 1/5000 を 4 等分（1.5km × 2km）。1桁の数字（1〜4）。
- 1/1000 図郭 …… 1/5000 を縦横 5 等分（600m × 800m）。2桁の数字（行, 列、各 0〜4）。
- 1/500 図郭 …… 1/5000 を縦横 10 等分（300m × 400m）。2桁の数字（行, 列）。

1/50000 の割り方と 1/5000 の行列の読み方は「森林情報に関するオープンデータ標準仕様書
Ver2.1」参考3（20m メッシュ ID の付与規則）で確認した。1/500 は山梨県の実データ4枚で
検算済み。

## 受け付ける範囲

**1/5000 より細かい図郭に限る。** XYZ テキストの点数は図郭サイズ ÷ 格子間隔で決まり、
1/5000（4km × 3km）を 0.5m 格子で表すと 4,800 万点・約 1.2GB になる。実務で見るのは
1/2500・1/1000・1/500 で、それより粗い単位のグリッドデータは出回らない。

1/50000 図郭（記号のみの4文字）は受け付けない。`12AB` のような文字列はいくらでもあり、
40km × 30km の範囲に収まるかを見ても偶然の一致を落としきれないため。

1/50000 を 4 分割した図郭（南北 15km × 東西 20km、`04HE2` のような5文字）も受け付けない。
これはオープンデータ標準仕様書がラスタや GeoPackage の配布単位として定めたもので、XYZ
テキストの単位ではない（0.5m 格子なら 12 億点になる）。

## 1/1000 と 1/500 の見分け方

どちらも 1/5000 コードの後ろに数字2桁が付くため、`08LE2134` はどちらとも読める。そこで
両方を候補として範囲を計算し、**実際の座標が収まる方を採る**。系番号は先頭2桁からしか
決まらないので、どちらに転んでも CRS は同じであり、判定を外しても実害がない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 図郭割りの基準点（系原点からの相対位置）。北端の northing と西端の easting。
_NORTH_EDGE = 300_000.0
_WEST_EDGE = -160_000.0

#: 1/50000 図郭の大きさ。南北 30km × 東西 40km。
_BLOCK_NS = 30_000.0
_BLOCK_EW = 40_000.0

#: 南北の記号は A〜T（20区分 × 30km = 600km）、東西は A〜H（8区分 × 40km = 320km）。
_ROW_LETTERS = "ABCDEFGHIJKLMNOPQRST"
_COL_LETTERS = "ABCDEFGH"

#: 系番号 N に対する EPSG コードのオフセット。第1系が先頭になる。
_DATUM_BASE = {
    "jgd2011": 6668,  # EPSG:6669〜6687
    "jgd2000": 2442,  # EPSG:2443〜2461
}

#: 平面直角座標系は第1系から第19系まで。
_MAX_SYSTEM = 19

#: 系番号2桁 + 記号2文字 + 細分の数字 2〜4桁。末尾に `_dsm` のような注記が付く例がある。
#: 数字 2桁未満（1/50000・4分割図郭）は受け付けない。
#: 理由はモジュール冒頭の「受け付ける範囲」を参照。
_PATTERN = re.compile(r"^(\d{2})([A-Z])([A-Z])(\d{2,4})(?:[_\-.].*)?$", re.ASCII)


class ZukakuError(ValueError):
    """図郭番号として解釈できない。"""


def _rect(
    top: float, left: float, ns: float, ew: float
) -> tuple[float, float, float, float]:
    """北西角と大きさから (xmin, ymin, xmax, ymax) を作る。"""
    return (left, top - ns, left + ew, top)


@dataclass(frozen=True)
class Figure:
    """図郭番号が指しうる1つの解釈。"""

    level: int  # 地図情報レベル（5000 / 2500 / 1000 / 500）
    extent: tuple[float, float, float, float]

    #: 検算に使う範囲。通常は `extent` と同じだが、番号の並びを確認できていない
    #: 階層では親の図郭を入れて、推測違いで正しいデータを弾かないようにする。
    check_extent: tuple[float, float, float, float]

    def contains(
        self, extent: tuple[float, float, float, float], *, slack: float
    ) -> bool:
        """座標範囲が図郭に収まるか。`slack` は境界の判定に許す余裕 [m]。"""
        xmin, ymin, xmax, ymax = extent
        bxmin, bymin, bxmax, bymax = self.check_extent
        return (
            xmin >= bxmin - slack
            and ymin >= bymin - slack
            and xmax <= bxmax + slack
            and ymax <= bymax + slack
        )


@dataclass(frozen=True)
class Zukaku:
    """解釈できた図郭番号。"""

    code: str
    system: int  # 平面直角座標系の系番号（1〜19）

    #: 番号が指しうる図郭。細かい方から並ぶ。1/1000 と 1/500 のように名前の形が
    #: 同じ階層があるため、ひとつに決めるには座標の範囲と突き合わせる必要がある。
    candidates: tuple[Figure, ...]

    def epsg(self, datum: str = "jgd2011") -> str:
        """測地系を指定して EPSG コードを返す。"""
        try:
            base = _DATUM_BASE[datum]
        except KeyError:
            raise ValueError(f"未知の測地系: {datum!r}") from None
        return f"EPSG:{base + self.system}"

    def match(
        self, extent: tuple[float, float, float, float], *, slack: float
    ) -> Figure | None:
        """座標範囲が収まる図郭を細かい方から探す。見つからなければ None。"""
        for figure in self.candidates:
            if figure.contains(extent, slack=slack):
                return figure
        return None


def parse(name: str) -> Zukaku:
    """ファイル名（拡張子なし）を図郭番号として解釈する。

    Raises:
        ZukakuError: 図郭番号の形式でないとき。
    """
    m = _PATTERN.match(name.upper())
    if m is None:
        raise ZukakuError(f"図郭番号の形式でない: {name!r}")

    system = int(m.group(1))
    row_letter, col_letter, digits = m.group(2), m.group(3), m.group(4)

    if not 1 <= system <= _MAX_SYSTEM:
        raise ZukakuError(f"系番号が範囲外: {system}（1〜{_MAX_SYSTEM}）")
    if row_letter not in _ROW_LETTERS or col_letter not in _COL_LETTERS:
        raise ZukakuError(f"1/50000 図郭の記号が範囲外: {row_letter}{col_letter}")

    # 1/50000 図郭。北西角から南東へ向かって記号が進む。
    top = _NORTH_EDGE - _BLOCK_NS * _ROW_LETTERS.index(row_letter)
    left = _WEST_EDGE + _BLOCK_EW * _COL_LETTERS.index(col_letter)

    # 1/5000 図郭。縦横 10 等分して 3km × 4km。
    ns, ew = _BLOCK_NS / 10, _BLOCK_EW / 10
    top -= ns * int(digits[0])
    left += ew * int(digits[1])
    base = _rect(top, left, ns, ew)

    rest = digits[2:]
    candidates: list[Figure] = []

    if not rest:
        candidates.append(Figure(5_000, base, base))

    elif len(rest) == 1:  # 1/2500。1/5000 を 4 等分。
        quadrant = int(rest)
        if not 1 <= quadrant <= 4:
            raise ZukakuError(f"1/2500 図郭の番号が範囲外: {quadrant}（1〜4）")
        qns, qew = ns / 2, ew / 2
        extent = _rect(
            top - qns * ((quadrant - 1) // 2), left + qew * ((quadrant - 1) % 2), qns, qew
        )
        # 1〜4 の並び（1=北西, 2=北東, 3=南西, 4=南東）は仕様書の図版でしか示されて
        # おらず裏が取れていないため、検算は親の 1/5000 図郭で行う。並びを誤って
        # 推測しても、正しいデータを弾かずに済む。
        candidates.append(Figure(2_500, extent, base))

    else:  # 数字2桁。1/500 と 1/1000 のどちらもありうる。
        row, col = int(rest[0]), int(rest[1])
        sns, sew = ns / 10, ew / 10  # 1/500: 300m × 400m
        extent = _rect(top - sns * row, left + sew * col, sns, sew)
        candidates.append(Figure(500, extent, extent))
        if row <= 4 and col <= 4:  # 1/1000 は縦横 5 等分なので各桁 0〜4
            kns, kew = ns / 5, ew / 5  # 600m × 800m
            extent = _rect(top - kns * row, left + kew * col, kns, kew)
            candidates.append(Figure(1_000, extent, extent))

    return Zukaku(code=m.group(0), system=system, candidates=tuple(candidates))
