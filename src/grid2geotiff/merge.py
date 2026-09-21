"""図郭単位の GeoTIFF を1枚に結合する。

「マップタイル作成マニュアル 第1.0版」が指摘するとおり、航空レーザ解析の成果は
国土基本図図郭を単位に作られるため大量のファイルになる。1/500 図郭（400m × 300m）
なら県域で万単位になり、QGIS はファイル数が 100 を超えると重くなる。

結合には VRT を経由する。VRT は実データを持たず「どのファイルのどこを、出力の
どこに置くか」だけを記した XML なので、何千枚を並べてもメモリに載らない。GDAL の
Python バインディングに依存しない方針を保つため、XML は自前で組み立てる。

結合前に入力の整合性を検証する。座標系・格子間隔・データ型が食い違うものが混ざって
いればエラーにする。**格子の位相（原点が互いに格子間隔の整数倍だけずれているか）も
確かめる**。ここを見ないと、半セルずれた図郭を VRT が黙って最近隣で丸め込み、値が
セル1つ分ずれたまま結合されてしまう。半セルずれはこのツールが最初に取り組んだ問題
そのものなので、結合で作り直すわけにはいかない。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import rasterio

#: numpy のデータ型名から VRT の dataType への対応。
_VRT_DTYPES = {
    "uint8": "Byte",
    "uint16": "UInt16",
    "int16": "Int16",
    "uint32": "UInt32",
    "int32": "Int32",
    "float32": "Float32",
    "float64": "Float64",
}

#: 格子の位相ずれをこのセル数まで許す。浮動小数の表記ゆれを吸収するための値で、
#: 実際のずれ（半セル = 0.5）とは桁がまったく違う。
_ALIGN_TOLERANCE = 1e-6


class MergeError(ValueError):
    """結合できない入力である。"""


@dataclass(frozen=True)
class Source:
    """結合元1枚の諸元。画素は持たない。"""

    path: Path
    width: int
    height: int
    res_x: float
    res_y: float
    left: float
    top: float
    crs: object
    dtype: str
    nodata: float | None
    blockx: int
    blocky: int

    @property
    def right(self) -> float:
        return self.left + self.width * self.res_x

    @property
    def bottom(self) -> float:
        return self.top - self.height * self.res_y


def read_sources(paths: list[Path]) -> list[Source]:
    """結合元の諸元を読む。画素は読まない。"""
    sources: list[Source] = []
    for p in paths:
        with rasterio.open(p) as ds:
            t = ds.transform
            if t.b or t.d:
                raise MergeError(f"回転を含む変換行列は扱えない: {Path(p).name}")
            blocky, blockx = ds.block_shapes[0]
            sources.append(
                Source(
                    path=Path(p),
                    width=ds.width,
                    height=ds.height,
                    res_x=abs(t.a),
                    res_y=abs(t.e),
                    left=t.c,
                    top=t.f,
                    crs=ds.crs,
                    dtype=ds.dtypes[0],
                    nodata=ds.nodata,
                    blockx=blockx,
                    blocky=blocky,
                )
            )
    return sources


def check_compatible(sources: list[Source]) -> None:
    """結合できる組み合わせかを検証する。

    Raises:
        MergeError: 座標系・格子間隔・データ型・格子の位相が揃っていないとき。
    """
    if not sources:
        raise MergeError("結合する入力がない")

    ref = sources[0]
    if ref.dtype not in _VRT_DTYPES:
        raise MergeError(f"VRT が扱えないデータ型: {ref.dtype}")

    for s in sources[1:]:
        if s.crs != ref.crs:
            raise MergeError(
                f"座標系が揃っていない: {ref.path.name} は {ref.crs}、"
                f"{s.path.name} は {s.crs}"
            )
        if not (
            math.isclose(s.res_x, ref.res_x, rel_tol=1e-9)
            and math.isclose(s.res_y, ref.res_y, rel_tol=1e-9)
        ):
            raise MergeError(
                f"格子間隔が揃っていない: {ref.path.name} は "
                f"{ref.res_x:g} x {ref.res_y:g} m、"
                f"{s.path.name} は {s.res_x:g} x {s.res_y:g} m"
            )
        if s.dtype != ref.dtype:
            raise MergeError(
                f"データ型が揃っていない: {ref.path.name} は {ref.dtype}、"
                f"{s.path.name} は {s.dtype}"
            )

        # 格子の位相。ここを見ないと半セルずれた図郭が黙って丸め込まれる。
        for axis, offset, step in (
            ("X", (s.left - ref.left) / ref.res_x, ref.res_x),
            ("Y", (s.top - ref.top) / ref.res_y, ref.res_y),
        ):
            if abs(offset - round(offset)) > _ALIGN_TOLERANCE:
                shift = abs(offset - round(offset)) * step
                raise MergeError(
                    f"{s.path.name} の格子が {ref.path.name} と噛み合わない"
                    f"（{axis} 方向に {shift:.6g} m ずれている）。"
                    f"そのまま結合すると値がセル単位でずれる"
                )


def union_bounds(sources: list[Source]) -> tuple[float, float, float, float]:
    """全体を覆う範囲を返す。"""
    return (
        min(s.left for s in sources),
        min(s.bottom for s in sources),
        max(s.right for s in sources),
        max(s.top for s in sources),
    )


def snap_bounds(
    bounds: tuple[float, float, float, float], ref: Source
) -> tuple[float, float, float, float]:
    """範囲を基準となる図郭の格子に合わせて外側へ広げる。

    クリップ範囲を格子に載せずに切ると、出力全体が半端にずれる。切る位置を
    セル境界に丸めることで、結合してもクリップしても格子が保たれる。
    """
    xmin, ymin, xmax, ymax = bounds
    return (
        ref.left + math.floor((xmin - ref.left) / ref.res_x) * ref.res_x,
        ref.top - math.ceil((ref.top - ymin) / ref.res_y) * ref.res_y,
        ref.left + math.ceil((xmax - ref.left) / ref.res_x) * ref.res_x,
        ref.top - math.floor((ref.top - ymax) / ref.res_y) * ref.res_y,
    )


def build_vrt(
    sources: list[Source],
    bounds: tuple[float, float, float, float],
    *,
    nodata: float,
    vrt_dir: Path | None = None,
) -> str:
    """結合の VRT XML を組み立てる。

    各ソースを `ComplexSource` として並べ、そのソース自身の NoData を `NODATA`
    に書く。読み出し時にその値の画素は飛ばされるので、図郭ごとに NoData が
    不揃いでも出力側の1つの値に統一できる。

    Args:
        sources: 結合元。`check_compatible` を通したもの。
        bounds: 出力範囲。格子に載っていること。
        nodata: 出力の NoData 値。
        vrt_dir: VRT を置くディレクトリ。渡すとソースを相対パスで書く。
    """
    ref = sources[0]
    left, bottom, right, top = bounds
    width = round((right - left) / ref.res_x)
    height = round((top - bottom) / ref.res_y)
    if width <= 0 or height <= 0:
        raise MergeError(f"出力範囲が空になる: {bounds}")
    vrt_dtype = _VRT_DTYPES[ref.dtype]

    entries: list[str] = []
    for s in sources:
        xoff = round((s.left - left) / ref.res_x)
        yoff = round((top - s.top) / ref.res_y)

        # 出力範囲からはみ出す分は読まない。クリップしたときに効く。
        src_x0, src_y0 = max(0, -xoff), max(0, -yoff)
        dst_x0, dst_y0 = max(xoff, 0), max(yoff, 0)
        x_size = min(s.width - src_x0, width - dst_x0)
        y_size = min(s.height - src_y0, height - dst_y0)
        if x_size <= 0 or y_size <= 0:
            continue  # 範囲外の図郭

        filename = str(s.path.resolve())
        relative = "0"
        if vrt_dir is not None:
            try:
                candidate = os.path.relpath(s.path.resolve(), vrt_dir.resolve())
            except ValueError:  # 別ドライブなど相対化できない
                candidate = None
            # 相対パスは VRT を持ち運べるようにするためのもの。共通の親が遠いと
            # `..` が延々と並んで絶対パスより長くなるので、そのときは素直に
            # 絶対パスにする。
            if candidate is not None and len(candidate) <= len(filename):
                filename, relative = candidate, "1"

        nodata_tag = (
            f"\n      <NODATA>{s.nodata:.17g}</NODATA>" if s.nodata is not None else ""
        )
        entries.append(
            "    <ComplexSource>\n"
            f'      <SourceFilename relativeToVRT="{relative}">'
            f"{escape(filename)}</SourceFilename>\n"
            "      <SourceBand>1</SourceBand>\n"
            f'      <SourceProperties RasterXSize="{s.width}" RasterYSize="{s.height}"'
            f' DataType="{vrt_dtype}" BlockXSize="{s.blockx}"'
            f' BlockYSize="{s.blocky}"/>\n'
            f'      <SrcRect xOff="{src_x0}" yOff="{src_y0}"'
            f' xSize="{x_size}" ySize="{y_size}"/>\n'
            f'      <DstRect xOff="{dst_x0}" yOff="{dst_y0}"'
            f' xSize="{x_size}" ySize="{y_size}"/>'
            f"{nodata_tag}\n"
            "    </ComplexSource>"
        )

    if not entries:
        raise MergeError("指定した範囲に重なる入力がない")

    srs = escape(ref.crs.to_wkt()) if ref.crs else ""
    geotransform = (
        f"{left:.17g}, {ref.res_x:.17g}, 0.0, {top:.17g}, 0.0, {-ref.res_y:.17g}"
    )
    body = "\n".join(entries)
    return (
        f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}">\n'
        f"  <SRS>{srs}</SRS>\n"
        f"  <GeoTransform>{geotransform}</GeoTransform>\n"
        f'  <VRTRasterBand dataType="{vrt_dtype}" band="1">\n'
        f"    <NoDataValue>{nodata:.17g}</NoDataValue>\n"
        f"    <ColorInterp>Gray</ColorInterp>\n"
        f"{body}\n"
        f"  </VRTRasterBand>\n"
        f"</VRTDataset>\n"
    )


def read_clip_bounds(path: Path) -> tuple[float, float, float, float]:
    """クリップ用ベクタの範囲を読む。

    ベクタの読み込みだけは fiona に頼る。任意の依存にしてあるので、無ければ
    その旨を伝えて終わる。
    """
    try:
        import fiona
    except ImportError:  # pragma: no cover - 任意依存
        raise MergeError(
            "ポリゴンでのクリップには fiona が必要"
            "（pip install 'grid-geotiff-converter[clip]'）"
        ) from None

    with fiona.open(path) as src:
        if len(src) == 0:
            raise MergeError(f"フィーチャが1つも入っていない: {Path(path).name}")
        return tuple(src.bounds)  # type: ignore[return-value]


@dataclass(frozen=True)
class MergeResult:
    """結合の結果。"""

    output: Path
    sources: int
    width: int
    height: int
    res_x: float
    res_y: float
    nodata: float
    covered: int  # 入力が載るセル数
    vrt: Path | None = None  # 残した VRT。残していなければ None

    @property
    def cells(self) -> int:
        return self.width * self.height

    @property
    def coverage(self) -> float:
        return self.covered / self.cells if self.cells else 0.0


def _covered_cells(
    sources: list[Source], bounds: tuple[float, float, float, float]
) -> int:
    """出力範囲のうち、入力のいずれかが載るセル数を数える。

    図郭同士は重ならない前提なので単純な足し合わせでよい。重なりがあっても
    過大評価になるだけで、被覆率の下限判定は安全側に働く。
    """
    ref = sources[0]
    left, bottom, right, top = bounds
    total = 0
    for s in sources:
        w = min(s.right, right) - max(s.left, left)
        h = min(s.top, top) - max(s.bottom, bottom)
        if w > 0 and h > 0:
            total += round(w / ref.res_x) * round(h / ref.res_y)
    return total


def merge_files(
    paths: list[Path],
    out_path: Path,
    *,
    nodata: float = -9999.0,
    compress: str = "deflate",
    blocksize: int = 256,
    bounds: tuple[float, float, float, float] | None = None,
    min_coverage: float = 0.05,
    overwrite: bool = False,
    vrt_only: bool = False,
    keep_vrt: bool = False,
) -> MergeResult:
    """複数の GeoTIFF を1枚に結合する。

    VRT を介してブロック単位で読み書きするので、出力が何 GB になってもメモリには
    1ブロック分しか載らない。

    Args:
        vrt_only: GeoTIFF を書かず、`out_path` に VRT だけを書く。
        keep_vrt: GeoTIFF に加えて、同じ場所に `.vrt` も残す。どの図郭をどう
            並べた結合なのかがファイルとして残り、QGIS や GDAL から直接開ける。

    Raises:
        MergeError: 入力が揃っていない、または被覆率が下限を下回るとき。
    """
    out_path = Path(out_path)
    # 残す VRT は GeoTIFF と同じ場所・同じ名前で拡張子だけ変える。
    side_vrt = out_path.with_suffix(".vrt") if keep_vrt and not vrt_only else None

    if not overwrite:
        for p in (out_path, side_vrt):
            if p is not None and p.exists():
                raise MergeError(f"出力が既にある（--overwrite で上書き）: {p}")

    sources = read_sources(paths)
    check_compatible(sources)
    ref = sources[0]

    extent = union_bounds(sources)
    if bounds is not None:
        clipped = (
            max(extent[0], bounds[0]),
            max(extent[1], bounds[1]),
            min(extent[2], bounds[2]),
            min(extent[3], bounds[3]),
        )
        if clipped[0] >= clipped[2] or clipped[1] >= clipped[3]:
            raise MergeError(
                f"クリップ範囲が入力と重ならない"
                f"（入力 X {extent[0]:.2f}..{extent[2]:.2f} "
                f"Y {extent[1]:.2f}..{extent[3]:.2f}）"
            )
        extent = snap_bounds(clipped, ref)

    width = round((extent[2] - extent[0]) / ref.res_x)
    height = round((extent[3] - extent[1]) / ref.res_y)
    covered = _covered_cells(sources, extent)
    coverage = covered / (width * height) if width * height else 0.0

    # 離れた区画をまとめて指定すると、間を埋めるだけの巨大な空ラスタができる。
    # 1/500 図郭が 10km 離れていれば 20000x20000 セルになり、中身は 0.5% しかない。
    if coverage < min_coverage:
        raise MergeError(
            f"入力が疎すぎる（{width:,}x{height:,} = {width * height:,} セルに対して"
            f"入力は {covered:,} セル、被覆率 {coverage:.2%} "
            f"< 許容 {min_coverage:.2%}）。"
            f"離れた区画が混ざっていないか確認する"
            f"（意図した結合なら --min-coverage で緩める）"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 残す VRT はソースを相対パスで書く。GeoTIFF と一緒に持ち運べるように。
    # 一時的に使うだけなら絶対パスでよい。
    kept_vrt = out_path if vrt_only else side_vrt
    vrt_xml = build_vrt(
        sources,
        extent,
        nodata=nodata,
        vrt_dir=kept_vrt.parent if kept_vrt is not None else None,
    )

    if vrt_only:
        out_path.write_text(vrt_xml, encoding="utf-8")
    else:
        _write_merged(vrt_xml, out_path, side_vrt, ref, nodata, compress, blocksize)

    return MergeResult(
        output=out_path,
        sources=len(sources),
        width=width,
        height=height,
        res_x=ref.res_x,
        res_y=ref.res_y,
        nodata=nodata,
        covered=covered,
        vrt=kept_vrt,
    )


def _write_merged(
    vrt_xml: str,
    out_path: Path,
    side_vrt: Path | None,
    ref: Source,
    nodata: float,
    compress: str,
    blocksize: int,
) -> None:
    """VRT をブロック単位で読み出し、GeoTIFF に書き出す。

    `side_vrt` を渡すとそこに VRT を残し、読み出しにもそれを使う。渡さなければ
    一時ファイルに置いて最後に消す。
    """
    from grid2geotiff.writer import COMPRESS_OPTIONS

    if compress not in COMPRESS_OPTIONS:
        raise MergeError(
            f"未知の圧縮方式 {compress!r}（{', '.join(COMPRESS_OPTIONS)} のいずれか）"
        )

    tmp = side_vrt or out_path.with_suffix(out_path.suffix + ".tmp.vrt")
    tmp.write_text(vrt_xml, encoding="utf-8")
    try:
        with rasterio.open(tmp) as src:
            profile = {
                "driver": "GTiff",
                "height": src.height,
                "width": src.width,
                "count": 1,
                "dtype": ref.dtype,
                "crs": src.crs,
                "transform": src.transform,
                "nodata": nodata,
                "tiled": True,
                "blockxsize": blocksize,
                "blockysize": blocksize,
                "BIGTIFF": "IF_SAFER",
            }
            profile.update(COMPRESS_OPTIONS[compress])
            with rasterio.open(out_path, "w", **profile) as dst:
                # ブロック単位で流す。出力が何 GB でもメモリには1ブロックしか載らない。
                for _, window in dst.block_windows(1):
                    dst.write(src.read(1, window=window), 1, window=window)
                dst.set_band_description(1, "elevation")
                dst.update_tags(
                    AREA_OR_POINT="Area",
                    GRID_CONVENTION=(
                        "cell-center samples, transform shifted by half a cell"
                    ),
                    GRID_RESOLUTION=f"{ref.res_x} x {ref.res_y}",
                )
    finally:
        if side_vrt is None:
            tmp.unlink(missing_ok=True)
