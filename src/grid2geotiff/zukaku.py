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

図郭の構成（作業規程の準則）:

- 1/50000 図郭 …… 系原点を基準に北端 +300km・西端 -160km から、南北 30km × 東西 40km
  で区切る。南北は北から A〜T（20区分）、東西は西から A〜H（8区分）の記号を付す。
- 1/5000 図郭 …… 1/50000 を縦横 10 等分（3km × 4km）。2桁の数字（行, 列）。
- 1/2500 図郭 …… 1/5000 を 4 等分（1.5km × 2km）。1桁の数字
  （1=北西, 2=北東, 3=南西, 4=南東）。
- 1/500 図郭 …… 1/5000 を縦横 10 等分（300m × 400m）。2桁の数字（行, 列）。
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

#: 図郭番号は系番号2桁 + 記号2文字 + 細分の数字。
#: 末尾に `_dsm` のような注記が付く例がある。
_PATTERN = re.compile(r"^(\d{2})([A-Z])([A-Z])(\d*)(?:[_\-.].*)?$", re.ASCII)


class ZukakuError(ValueError):
    """図郭番号として解釈できない。"""


#: 細分の数字の桁数と地図情報レベルの対応。
_LEVEL_BY_DIGITS = {0: 50_000, 2: 5_000, 3: 2_500, 4: 500}


@dataclass(frozen=True)
class Zukaku:
    """解釈できた図郭番号。"""

    code: str
    system: int  # 平面直角座標系の系番号（1〜19）
    level: int  # 地図情報レベル（50000 / 5000 / 2500 / 500）

    #: 図郭の範囲（easting/northing、外接矩形）。細分の数字まで反映した最も細かい範囲。
    extent: tuple[float, float, float, float]

    def epsg(self, datum: str = "jgd2011") -> str:
        """測地系を指定して EPSG コードを返す。"""
        try:
            base = _DATUM_BASE[datum]
        except KeyError:
            raise ValueError(f"未知の測地系: {datum!r}") from None
        return f"EPSG:{base + self.system}"

    def contains(
        self, extent: tuple[float, float, float, float], *, slack: float
    ) -> bool:
        """座標範囲が図郭に収まるか。`slack` は境界の判定に許す余裕 [m]。"""
        xmin, ymin, xmax, ymax = extent
        bxmin, bymin, bxmax, bymax = self.extent
        return (
            xmin >= bxmin - slack
            and ymin >= bymin - slack
            and xmax <= bxmax + slack
            and ymax <= bymax + slack
        )


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
    if len(digits) not in _LEVEL_BY_DIGITS:
        raise ZukakuError(f"細分の桁数が図郭番号として不正: {digits!r}")

    # 1/50000 図郭。北西角から南東へ向かって記号が進む。
    top = _NORTH_EDGE - _BLOCK_NS * _ROW_LETTERS.index(row_letter)
    left = _WEST_EDGE + _BLOCK_EW * _COL_LETTERS.index(col_letter)
    ns, ew = _BLOCK_NS, _BLOCK_EW

    if len(digits) >= 2:  # 1/5000（縦横 10 等分）
        ns, ew = ns / 10, ew / 10
        top -= ns * int(digits[0])
        left += ew * int(digits[1])

    if len(digits) == 3:  # 1/2500（1=北西 2=北東 3=南西 4=南東）
        quadrant = int(digits[2])
        if not 1 <= quadrant <= 4:
            raise ZukakuError(f"1/2500 図郭の番号が範囲外: {quadrant}（1〜4）")
        ns, ew = ns / 2, ew / 2
        top -= ns * ((quadrant - 1) // 2)
        left += ew * ((quadrant - 1) % 2)
    elif len(digits) == 4:  # 1/500（1/5000 をさらに縦横 10 等分）
        ns, ew = ns / 10, ew / 10
        top -= ns * int(digits[2])
        left += ew * int(digits[3])

    return Zukaku(
        code=m.group(0),
        system=system,
        level=_LEVEL_BY_DIGITS[len(digits)],
        extent=(left, top - ns, left + ew, top),
    )
