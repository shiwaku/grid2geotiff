"""1ファイル分の変換処理。CLI と並列実行の両方から呼ぶ。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grid2geotiff.gridspec import GridSpec, NotAGridError, infer_grid, rasterize
from grid2geotiff.writer import write_geotiff
from grid2geotiff.xyz import XyzReadError, read_xyz
from grid2geotiff.zukaku import ZukakuError
from grid2geotiff.zukaku import parse as parse_zukaku


@dataclass(frozen=True)
class ConvertOptions:
    """変換の設定。プロセス間で渡すので dataclass にしておく。"""

    #: 入力座標の参照系。None ならファイル名の図郭番号から判定する。
    crs: str | None
    out_dir: Path
    res: tuple[float, float] | None = None
    nodata: float = -9999.0
    dtype: str = "float32"
    compress: str = "deflate"
    blocksize: int = 256
    delimiter: str | None = None
    columns: tuple[int, int, int] = (0, 1, 2)
    input_nodata: tuple[float, ...] = ()
    tolerance_ratio: float = 0.01
    #: セル数に対する点数の下限。下回れば格子とみなさない。
    min_fill_ratio: float = 0.01
    overwrite: bool = False
    #: 図郭番号から CRS を判定するときの測地系。
    datum: str = "jgd2011"


@dataclass(frozen=True)
class ConvertResult:
    """1ファイルの変換結果。失敗しても例外にせずここに載せる。"""

    source: Path
    output: Path | None
    ok: bool
    message: str
    points: int = 0
    width: int = 0
    height: int = 0
    res_x: float = 0.0
    res_y: float = 0.0
    filled: int = 0
    crs: str = ""
    #: CRS をファイル名の図郭番号から判定した（＝利用者が明示していない）。
    crs_inferred: bool = False
    #: 判定に使った図郭の地図情報レベル（5000 / 2500 / 1000 / 500）。
    zukaku_level: int = 0

    @property
    def cells(self) -> int:
        return self.width * self.height


class CrsResolveError(ValueError):
    """CRS を決められない。"""


def _resolve_crs(
    path: Path, spec: GridSpec, opts: ConvertOptions
) -> tuple[str, bool, int]:
    """使う CRS、図郭番号から判定したか、判定に使った図郭のレベルを返す。

    `--crs` が明示されていればそれを優先する。判定に頼る場合は、図郭番号から
    計算した範囲に実際の座標が収まることを確かめてから採用する。これは名前の形が
    たまたま図郭番号に一致しただけのファイルを弾くための検算で、系番号そのものは
    検証できない（図郭の範囲は各系の原点からの相対位置なので系によらず同じ）。

    1/1000 と 1/500 は名前の形が同じなので、座標が収まる方を選ぶ。
    """
    if opts.crs is not None:
        return opts.crs, False, 0

    try:
        zukaku = parse_zukaku(path.stem)
    except ZukakuError as exc:
        raise CrsResolveError(
            f"ファイル名から CRS を判定できない（{exc}）。--crs で明示すること"
        ) from None

    crs = zukaku.epsg(opts.datum)

    # セル中心ではなく外接矩形で比べる。図郭の範囲とは本来ぴったり一致する。
    extent = (
        spec.xmin - spec.res_x / 2,
        spec.ymin - spec.res_y / 2,
        spec.xmax + spec.res_x / 2,
        spec.ymax + spec.res_y / 2,
    )
    figure = zukaku.match(extent, slack=max(spec.res_x, spec.res_y))
    if figure is None:
        candidates = "、".join(
            f"1/{f.level} は X {f.check_extent[0]:.2f}..{f.check_extent[2]:.2f} "
            f"Y {f.check_extent[1]:.2f}..{f.check_extent[3]:.2f}"
            for f in zukaku.candidates
        )
        raise CrsResolveError(
            f"図郭番号 {zukaku.code} から {crs} と判定したが、座標範囲 "
            f"X {extent[0]:.2f}..{extent[2]:.2f} Y {extent[1]:.2f}..{extent[3]:.2f} が"
            f"図郭の範囲に収まらない（{candidates}）。"
            f"ファイル名が図郭番号でない可能性がある。--crs で明示すること"
        )
    return crs, True, figure.level


def convert_file(path: Path, opts: ConvertOptions) -> ConvertResult:
    """XYZ テキスト1件を GeoTIFF に変換する。"""
    path = Path(path)
    out_path = opts.out_dir / f"{path.stem}.tif"

    if out_path.exists() and not opts.overwrite:
        return ConvertResult(
            path, out_path, False, "出力が既にある（--overwrite で上書き）"
        )

    try:
        data = read_xyz(
            path,
            delimiter=opts.delimiter,
            columns=opts.columns,
            input_nodata=opts.input_nodata,
        )
    except XyzReadError as exc:
        return ConvertResult(path, None, False, str(exc))

    try:
        spec = infer_grid(
            data.x,
            data.y,
            res=opts.res,
            tolerance_ratio=opts.tolerance_ratio,
            min_fill_ratio=opts.min_fill_ratio,
        )
    except NotAGridError as exc:
        return ConvertResult(path, None, False, str(exc), points=len(data))

    try:
        crs, crs_inferred, level = _resolve_crs(path, spec, opts)
    except CrsResolveError as exc:
        return ConvertResult(path, None, False, str(exc), points=len(data))

    array = rasterize(data.x, data.y, data.z, spec, nodata=opts.nodata, dtype=opts.dtype)

    write_geotiff(
        out_path,
        array,
        spec,
        crs,
        nodata=opts.nodata,
        compress=opts.compress,
        blocksize=opts.blocksize,
        description="elevation",
        extra_tags={"SOURCE_FILE": path.name},
    )

    return ConvertResult(
        source=path,
        output=out_path,
        ok=True,
        message="ok",
        points=len(data),
        width=spec.width,
        height=spec.height,
        res_x=spec.res_x,
        res_y=spec.res_y,
        filled=spec.width * spec.height - len(data),
        crs=crs,
        crs_inferred=crs_inferred,
        zukaku_level=level,
    )


def inspect_file(path: Path, opts: ConvertOptions) -> ConvertResult:
    """変換せずに格子構造だけを点検する。"""
    path = Path(path)
    try:
        data = read_xyz(
            path,
            delimiter=opts.delimiter,
            columns=opts.columns,
            input_nodata=opts.input_nodata,
        )
    except XyzReadError as exc:
        return ConvertResult(path, None, False, str(exc))

    try:
        spec = infer_grid(
            data.x,
            data.y,
            res=opts.res,
            tolerance_ratio=opts.tolerance_ratio,
            min_fill_ratio=opts.min_fill_ratio,
        )
    except NotAGridError as exc:
        return ConvertResult(path, None, False, str(exc), points=len(data))

    # 点検では CRS を決められなくても失敗にせず、理由を添えて報告する。
    try:
        crs, crs_inferred, level = _resolve_crs(path, spec, opts)
        crs_note = f"CRS {crs}" + (
            f"（1/{level} 図郭番号から推定）" if crs_inferred else ""
        )
    except CrsResolveError as exc:
        crs, crs_inferred, level = "", False, 0
        crs_note = f"CRS 不明（{exc}）"

    return ConvertResult(
        source=path,
        output=None,
        ok=True,
        message=(
            f"X {spec.xmin - spec.res_x / 2:.2f}..{spec.xmax + spec.res_x / 2:.2f} "
            f"Y {spec.ymin - spec.res_y / 2:.2f}..{spec.ymax + spec.res_y / 2:.2f}  "
            f"{crs_note}"
        ),
        points=len(data),
        width=spec.width,
        height=spec.height,
        res_x=spec.res_x,
        res_y=spec.res_y,
        filled=spec.width * spec.height - len(data),
        crs=crs,
        crs_inferred=crs_inferred,
        zukaku_level=level,
    )
