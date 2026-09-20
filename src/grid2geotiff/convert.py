"""1ファイル分の変換処理。CLI と並列実行の両方から呼ぶ。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grid2geotiff.gridspec import NotAGridError, infer_grid, rasterize
from grid2geotiff.writer import write_geotiff
from grid2geotiff.xyz import XyzReadError, read_xyz


@dataclass(frozen=True)
class ConvertOptions:
    """変換の設定。プロセス間で渡すので dataclass にしておく。"""

    crs: str
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
    overwrite: bool = False


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

    @property
    def cells(self) -> int:
        return self.width * self.height


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
            data.x, data.y, res=opts.res, tolerance_ratio=opts.tolerance_ratio
        )
    except NotAGridError as exc:
        return ConvertResult(path, None, False, str(exc), points=len(data))

    array = rasterize(data.x, data.y, data.z, spec, nodata=opts.nodata, dtype=opts.dtype)

    write_geotiff(
        out_path,
        array,
        spec,
        opts.crs,
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
            data.x, data.y, res=opts.res, tolerance_ratio=opts.tolerance_ratio
        )
    except NotAGridError as exc:
        return ConvertResult(path, None, False, str(exc), points=len(data))

    return ConvertResult(
        source=path,
        output=None,
        ok=True,
        message=(
            f"X {spec.xmin - spec.res_x / 2:.2f}..{spec.xmax + spec.res_x / 2:.2f} "
            f"Y {spec.ymin - spec.res_y / 2:.2f}..{spec.ymax + spec.res_y / 2:.2f}"
        ),
        points=len(data),
        width=spec.width,
        height=spec.height,
        res_x=spec.res_x,
        res_y=spec.res_y,
        filled=spec.width * spec.height - len(data),
    )
