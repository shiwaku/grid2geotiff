"""出来上がった GeoTIFF が標準仕様に適合しているかを点検する。

変換が正しく動いたかどうかと、**成果物が仕様を満たしているかどうかは別の問いである**。
前者は入力と出力を突き合わせれば分かるが、後者は仕様書の条文と照らす必要がある。

この照合が要るのは自分の出力に対してだけではない。林野庁「マップタイル作成マニュアル
第1.0版」は作業の最初にこう置いている。

> 貸与されたデータからいきなりマップタイルを作成すると、貸与データに不足があること、
> Nodata が適切に設定されていない等の異常に事前に気づくことができず、出戻り作業が
> 発生することも少なくない

つまり**受け取った GeoTIFF を点検する**ことが実務の第一歩であり、自分が出力したものも
他人が受け取る側から見れば同じ立場にある。

## 何を根拠にしているか

- 森林情報に関するオープンデータ標準仕様書 Ver2.1（2.5.2 標高 DEM）
  …… 「非圧縮、32bit を基本とする。圧縮する場合は可逆性がある方式とし、圧縮形式は
  公開時の説明文に記載する」「ピクセルサイズ（X,Y）は 0.5,0.5 とする」
  「座標参照系：原則、最新の平面直角座標系」
- 森林資源データ解析・管理標準仕様書 Ver3.1 …… 「ピクセルサイズは 1m 以下とする」
- マップタイル作成マニュアル 第1.0版 …… Nodata は「ラスタ値は -9999 に統一」
- 航空レーザ測量による数値標高モデル（ＤＥＭ）作成マニュアル（案）（国土地理院）
  …… メッシュ値は「１cm 単位を四捨五入して 0.1m」、メッシュ間隔は 0.5/1.0/1.5/2.0/
  2.5/3.0/5.0m、欠測率は「国土基本図図郭単位で 10％以下を標準とする」

## 適合・注意・違反を分ける

仕様書の書き方は一様ではない。「〜とする」と言い切る条項もあれば「〜を基本とする」
「〜を標準とする」と幅を認める条項もある。**後者を違反として扱うと、正しいデータまで
弾いてしまう**。そこで結果を3段階に分ける。

- `OK` …… 条項を満たす
- `注意` …… 「基本とする」「標準とする」の幅から外れる。理由を添えて報告するが失敗にしない
- `NG` …… 「〜とする」と定める条項に反する、または仕様以前に壊れている
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio

from grid2geotiff.zukaku import ZukakuError
from grid2geotiff.zukaku import parse as parse_zukaku

#: 可逆圧縮とみなす方式。GeoTIFF で使えるもののうち、値が変わらないもの。
LOSSLESS = {
    "none",
    "deflate",
    "lzw",
    "zstd",
    "packbits",
    "lzma",
    "lerc_deflate",
    "lerc_zstd",
}

#: 非可逆。標高値に使えば値が変わる。
LOSSY = {"jpeg", "webp", "lerc"}

#: 地理院マニュアルが挙げるメッシュ間隔 [m]。
STANDARD_RES = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0)

#: 標高値が載りうる刻み [m]。粗い方から見る。
#:
#: 地理院マニュアルは「メッシュデータの最小単位は、１cm 単位を四捨五入して 0.1m」と
#: するが、**実データは 1cm 刻みのことが多い**（山梨県の DEM で確認）。0.1m を要求すると
#: 現に配布されているデータが軒並み外れるので、1cm までを仕様の範囲として扱う。
VALUE_STEPS = (0.1, 0.01)

#: 欠測率の標準的な上限。「国土基本図図郭単位で 10％以下を標準とする」。
MAX_MISSING_RATIO = 0.10

#: マップタイル作成マニュアルが統一を求める NoData 値。
NODATA = -9999.0

#: 平面直角座標系の EPSG。JGD2011 は 6669〜6687、JGD2000 は 2443〜2461。
PLANE_EPSG = set(range(6669, 6688)) | set(range(2443, 2462))


@dataclass
class Check:
    """条項1つの点検結果。"""

    item: str
    level: str  # "OK" / "注意" / "NG"
    detail: str
    source: str


@dataclass
class SpecResult:
    """ファイル1つの点検結果。"""

    path: Path
    checks: list[Check] = field(default_factory=list)
    error: str = ""

    @property
    def ng(self) -> list[Check]:
        return [c for c in self.checks if c.level == "NG"]

    @property
    def warn(self) -> list[Check]:
        return [c for c in self.checks if c.level == "注意"]

    @property
    def ok(self) -> bool:
        return not self.error and not self.ng


def _add(result: SpecResult, item: str, level: str, detail: str, source: str) -> None:
    result.checks.append(Check(item=item, level=level, detail=detail, source=source))


def _check_format(result: SpecResult, ds) -> None:
    """GeoTIFF 単体で位置情報を持つか。"""
    src = "オープンデータ標準仕様書 Ver2.1 2.5.2"
    if ds.driver != "GTiff":
        _add(result, "形式", "NG", f"GeoTIFF でない（{ds.driver}）", src)
        return
    if not ds.crs or ds.transform.is_identity:
        _add(
            result,
            "形式",
            "NG",
            "位置情報を持たない（tfw 併用の tif は GeoTIFF でない）",
            src,
        )
        return
    _add(result, "形式", "OK", "GeoTIFF（位置情報を内包）", src)


def _check_band(result: SpecResult, ds) -> None:
    """バンド数とビット長。"""
    src = "オープンデータ標準仕様書 Ver2.1 2.5.2「非圧縮、32bit を基本とする」"
    if ds.count != 1:
        _add(result, "バンド", "NG", f"標高は1バンドであるべき（{ds.count} バンド）", src)
        return
    dtype = ds.dtypes[0]
    bits = np.dtype(dtype).itemsize * 8
    level = "OK" if bits == 32 else "注意"
    _add(result, "バンド", level, f"1バンド {dtype}（{bits}bit）", src)


def _check_compress(result: SpecResult, ds) -> None:
    """圧縮方式が可逆か。"""
    src = "オープンデータ標準仕様書 Ver2.1 2.5.2「圧縮する場合は可逆性がある方式」"
    name = ds.profile.get("compress") or "none"
    name = str(name).lower()
    if name in LOSSY:
        _add(result, "圧縮", "NG", f"非可逆圧縮（{name}）。標高値が変わる", src)
    elif name in LOSSLESS:
        note = "非圧縮" if name == "none" else f"可逆圧縮（{name}）"
        _add(result, "圧縮", "OK", note, src)
    else:
        _add(result, "圧縮", "注意", f"可逆かどうか判断できない方式（{name}）", src)


def _check_res(result: SpecResult, ds) -> None:
    """ピクセルサイズ。"""
    src = (
        "標準仕様書 Ver3.1「ピクセルサイズは 1m 以下」/ オープンデータ Ver2.1「0.5,0.5」"
    )
    rx, ry = abs(ds.transform.a), abs(ds.transform.e)
    if abs(rx - ry) > 1e-9:
        _add(result, "格子間隔", "NG", f"X と Y が違う（{rx:g} x {ry:g}）", src)
        return
    if rx > 1.0:
        _add(result, "格子間隔", "NG", f"{rx:g}m は 1m を超える", src)
        return
    if any(abs(rx - v) < 1e-9 for v in STANDARD_RES):
        level = "OK" if abs(rx - 0.5) < 1e-9 else "注意"
        note = f"{rx:g}m" + (
            "" if level == "OK" else "（オープンデータは 0.5m を求める）"
        )
    else:
        level = "注意"
        note = f"{rx:g}m は地理院マニュアルの挙げる間隔にない"
    _add(result, "格子間隔", level, note, src)


def _check_crs(result: SpecResult, ds) -> None:
    """座標参照系が平面直角座標系か。"""
    src = "オープンデータ標準仕様書 Ver2.1「原則、最新の平面直角座標系」"
    epsg = ds.crs.to_epsg() if ds.crs else None
    if epsg is None:
        _add(result, "座標系", "注意", f"EPSG に解決できない（{ds.crs}）", src)
    elif epsg in PLANE_EPSG:
        _add(result, "座標系", "OK", f"平面直角座標系 EPSG:{epsg}", src)
    else:
        _add(result, "座標系", "注意", f"平面直角座標系でない EPSG:{epsg}", src)


def _check_nodata(result: SpecResult, ds) -> None:
    """NoData 値。"""
    src = "マップタイル作成マニュアル 第1.0版「ラスタ値は -9999 に統一」"
    if ds.nodata is None:
        _add(result, "NoData", "NG", "NoData が設定されていない", src)
    elif abs(ds.nodata - NODATA) < 1e-6:
        _add(result, "NoData", "OK", "-9999", src)
    else:
        _add(result, "NoData", "NG", f"{ds.nodata:g}（-9999 に統一すべき）", src)


def _check_zukaku(result: SpecResult, ds) -> None:
    """図郭境界に載っているか。ファイル名が図郭番号のときだけ見る。"""
    src = "地理院 DEM 作成マニュアル（案）第2条／第8節"
    try:
        zukaku = parse_zukaku(result.path.stem)
    except ZukakuError:
        _add(result, "図郭", "注意", "ファイル名が図郭番号でないので照合しない", src)
        return
    bounds = tuple(ds.bounds)
    slack = max(abs(ds.transform.a), abs(ds.transform.e))
    figure = zukaku.match(bounds, slack=slack)
    if figure is None:
        _add(result, "図郭", "NG", f"{zukaku.code} の範囲に収まらない", src)
        return
    fx0, _, _, fy1 = figure.extent
    res = abs(ds.transform.a)
    # 図郭の角からのずれは格子間隔の整数倍でなければならない。半セルずれていれば
    # ここに 0.25m のような半端が出る。セル中心をそのまま原点にした誤りが捕まる。
    offsets = [abs(bounds[0] - fx0) % res, abs(bounds[3] - fy1) % res]
    if max(min(o, res - o) for o in offsets) > 1e-6:
        _add(
            result, "図郭", "NG", f"{figure.name} の格子に載っていない（原点が半端）", src
        )
        return
    _add(result, "図郭", "OK", f"{figure.name} の格子に載る", src)


def _detect_step(values: np.ndarray) -> float | None:
    """標高値が載っている刻みを粗い方から探す。どれにも載らなければ None。

    許容差は値の大きさに応じて決める。float32 は 2,500m 付近で刻み幅が 0.0002m ほど
    あり、1cm 単位に丸めた値でも厳密には一致しないため、固定の許容差では判定できない。
    """
    v = values.astype("float64")
    tol = np.maximum(1e-4, np.abs(v) * 8 * float(np.finfo("float32").eps))
    for step in VALUE_STEPS:
        if bool((np.abs(v - np.round(v / step) * step) <= tol).all()):
            return step
    return None


def _check_values(result: SpecResult, ds, sample_rows: int) -> tuple[int, int]:
    """標高値の刻みと欠測率。戻り値は (有効セル数, 全セル数)。"""
    src_step = "地理院 DEM 作成マニュアル（案）第8節「１cm 単位を四捨五入して 0.1m」"
    src_miss = "同 第5節運用基準「欠測率は国土基本図図郭単位で 10％以下を標準」"

    data = ds.read(1)
    valid = data != ds.nodata if ds.nodata is not None else np.isfinite(data)
    total = int(data.size)
    filled = int(valid.sum())

    values = data[valid]
    if values.size:
        if values.size > sample_rows:
            values = values[:: values.size // sample_rows]
        step = _detect_step(values)
        if step is None:
            _add(result, "標高値の刻み", "注意", "1cm より細かい刻みの値がある", src_step)
        else:
            _add(result, "標高値の刻み", "OK", f"{step:g}m 刻み", src_step)

    missing = 1.0 - (filled / total if total else 0.0)
    level = "OK" if missing <= MAX_MISSING_RATIO else "注意"
    _add(result, "欠測率", level, f"{missing * 100:.2f}%", src_miss)
    return filled, total


def check_file(path: Path, *, sample_rows: int = 100_000) -> SpecResult:
    """GeoTIFF 1枚を仕様と照らす。"""
    path = Path(path)
    result = SpecResult(path=path)
    try:
        with rasterio.open(path) as ds:
            _check_format(result, ds)
            if result.ng:
                return result
            _check_band(result, ds)
            _check_compress(result, ds)
            _check_res(result, ds)
            _check_crs(result, ds)
            _check_nodata(result, ds)
            _check_zukaku(result, ds)
            _check_values(result, ds, sample_rows)
    except rasterio.RasterioIOError as exc:
        result.error = f"開けない（{exc}）"
    return result
