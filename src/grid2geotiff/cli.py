"""grid2geotiff コマンドライン。"""

from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import click

from grid2geotiff import __version__
from grid2geotiff.convert import ConvertOptions, ConvertResult, convert_file, inspect_file
from grid2geotiff.merge import MergeError, merge_files, read_clip_bounds
from grid2geotiff.spec import SpecResult, check_file

#: ZIP も含めて入力として受け付ける拡張子。
INPUT_SUFFIXES = (".txt", ".csv", ".xyz", ".dat", ".zip")

#: merge の入力として受け付ける拡張子。
RASTER_SUFFIXES = (".tif", ".tiff")


def _expand_inputs(
    paths: tuple[str, ...], suffixes: tuple[str, ...] = INPUT_SUFFIXES
) -> list[Path]:
    """ディレクトリを展開し、入力ファイルの一覧を作る。"""
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found.extend(q for q in sorted(p.rglob("*")) if q.suffix.lower() in suffixes)
        else:
            found.append(p)
    # 同じ図郭を .txt と .zip の両方で拾わないよう、ステム単位で txt を優先する。
    by_stem: dict[str, Path] = {}
    for p in found:
        if p.stem not in by_stem or p.suffix.lower() != ".zip":
            by_stem[p.stem] = p
    return sorted(by_stem.values())


def _parse_res(value: str | None) -> tuple[float, float] | None:
    if value is None:
        return None
    parts = [v for v in value.replace("x", ",").split(",") if v.strip()]
    if len(parts) == 1:
        r = float(parts[0])
        return (r, r)
    if len(parts) == 2:
        return (float(parts[0]), float(parts[1]))
    raise click.BadParameter(f"--res は 0.5 か 0.5,0.5 の形式で指定する: {value!r}")


def _parse_columns(value: str) -> tuple[int, int, int]:
    parts = [int(v) for v in value.split(",")]
    if len(parts) != 3:
        raise click.BadParameter(f"--columns は3つの列番号を指定する: {value!r}")
    return (parts[0], parts[1], parts[2])


def _run(
    func, inputs: list[Path], opts: ConvertOptions, jobs: int
) -> list[ConvertResult]:
    """逐次または並列で処理し、結果を入力順に返す。"""
    if jobs <= 1 or len(inputs) <= 1:
        return [func(p, opts) for p in inputs]

    results: dict[Path, ConvertResult] = {}
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(func, p, opts): p for p in inputs}
        for future in as_completed(futures):
            src = futures[future]
            results[src] = future.result()
    return [results[p] for p in inputs]


def _report(results: list[ConvertResult], *, verb: str) -> int:
    """結果を表示し、終了コードを返す。"""
    ok = [r for r in results if r.ok]
    ng = [r for r in results if not r.ok]

    for r in results:
        if r.ok:
            ratio = r.filled / r.cells * 100 if r.cells else 0.0
            click.echo(
                f"  OK   {r.source.name}  {r.width}x{r.height} @ {r.res_x:g}m  "
                f"点 {r.points:,}  欠損 {r.filled:,} ({ratio:.2f}%)"
                + (
                    f"  CRS {r.crs}（{r.zukaku_name} 図郭番号から推定）"
                    if r.crs_inferred and r.output
                    else ""
                )
                + (f"  -> {r.output.name}" if r.output else f"  {r.message}")
            )
        else:
            click.secho(f"  NG   {r.source.name}  {r.message}", fg="red", err=True)

    click.echo(f"\n{verb}: 成功 {len(ok)} / 失敗 {len(ng)} / 計 {len(results)}")
    return 1 if ng else 0


_common_options = [
    click.option(
        "--crs",
        default=None,
        help=(
            "入力座標の参照系（例 EPSG:6676 = JGD2011 平面直角座標系第8系）。"
            "省略時はファイル名の図郭番号から判定する。"
        ),
    ),
    click.option(
        "--datum",
        type=click.Choice(["jgd2011", "jgd2000"]),
        default="jgd2011",
        show_default=True,
        help="図郭番号から CRS を判定するときの測地系。",
    ),
    click.option(
        "--res",
        default=None,
        help="格子間隔 [m]。`0.5` か `0.5,0.5`。省略時は座標から推定する。",
    ),
    click.option(
        "--delimiter",
        type=click.Choice(["space", "comma", "tab", "semicolon"]),
        default=None,
        help="区切り文字。省略時は先頭行から判定する。",
    ),
    click.option(
        "--columns",
        default="0,1,2",
        help="X,Y,Z の列番号（0 始まり）。既定は 0,1,2。",
    ),
    click.option(
        "--input-nodata",
        multiple=True,
        type=float,
        help="入力側の欠測値。該当する点を読み捨てる。複数指定可（例 -9999、-32768）。",
    ),
    click.option(
        "--tolerance-ratio",
        default=0.01,
        show_default=True,
        help="格子からのずれの許容量（格子間隔に対する比）。",
    ),
    click.option(
        "--min-fill-ratio",
        default=0.01,
        show_default=True,
        help=(
            "セル数に対する点数の下限。下回れば格子とみなさずエラーにする。"
            "0 にすると検定そのものを止めるため、巨大な配列の確保を防げなくなる。"
        ),
    ),
    click.option("--jobs", "-j", default=1, show_default=True, help="並列処理数。"),
]


def _add_options(options):
    def decorator(func):
        for option in reversed(options):
            func = option(func)
        return func

    return decorator


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="grid2geotiff")
def main() -> None:
    """航空レーザ計測のグリッドデータ（XYZ座標値テキスト）を GeoTIFF に変換する。

    セル中心で記録された座標を半セルずらして配置するため、図郭境界にぴったり
    合った GeoTIFF になる。格子に載っていない点群は内挿せずエラーにする。
    """


@main.command()
@click.argument("inputs", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "--out-dir",
    "-o",
    required=True,
    type=click.Path(file_okay=False),
    help="GeoTIFF の出力先ディレクトリ。",
)
@click.option("--nodata", default=-9999.0, show_default=True, help="出力の NoData 値。")
@click.option(
    "--dtype",
    type=click.Choice(["float32", "float64", "int16", "int32"]),
    default="float32",
    show_default=True,
    help="出力のデータ型。",
)
@click.option(
    "--compress",
    type=click.Choice(["deflate", "lzw", "zstd", "none"]),
    default="deflate",
    show_default=True,
    help="圧縮方式。",
)
@click.option(
    "--blocksize", default=256, show_default=True, help="タイル化のブロックサイズ。"
)
@click.option("--overwrite", is_flag=True, help="既存の出力を上書きする。")
@_add_options(_common_options)
def convert(
    inputs, out_dir, nodata, dtype, compress, blocksize, overwrite, **common
) -> None:
    """XYZ テキストを GeoTIFF に変換する。

    INPUTS にはファイルのほかディレクトリも指定できる（再帰的に探索する）。
    """
    files = _expand_inputs(inputs)
    if not files:
        raise click.ClickException("入力ファイルが見つからない")

    opts = ConvertOptions(
        crs=common["crs"],
        out_dir=Path(out_dir),
        res=_parse_res(common["res"]),
        nodata=nodata,
        dtype=dtype,
        compress=compress,
        blocksize=blocksize,
        delimiter=common["delimiter"],
        columns=_parse_columns(common["columns"]),
        input_nodata=tuple(common["input_nodata"]),
        tolerance_ratio=common["tolerance_ratio"],
        min_fill_ratio=common["min_fill_ratio"],
        overwrite=overwrite,
        datum=common["datum"],
    )

    crs_note = (
        f"CRS {common['crs']}"
        if common["crs"]
        else f"CRS は図郭番号から判定 / {common['datum'].upper()}"
    )
    click.echo(f"変換 {len(files)} ファイル -> {out_dir}  ({crs_note})")
    results = _run(convert_file, files, opts, common["jobs"])
    sys.exit(_report(results, verb="変換"))


@main.command()
@click.argument("inputs", nargs=-1, required=True, type=click.Path(exists=True))
@_add_options(_common_options)
def inspect(inputs, **common) -> None:
    """変換せずに格子構造・範囲・欠損を点検する。

    大量の図郭を変換する前に、格子間隔の食い違いや不規則点群の混入を洗い出す。
    """
    files = _expand_inputs(inputs)
    if not files:
        raise click.ClickException("入力ファイルが見つからない")

    opts = ConvertOptions(
        crs=common["crs"],
        out_dir=Path("."),  # 点検では使わない
        res=_parse_res(common["res"]),
        delimiter=common["delimiter"],
        columns=_parse_columns(common["columns"]),
        input_nodata=tuple(common["input_nodata"]),
        tolerance_ratio=common["tolerance_ratio"],
        min_fill_ratio=common["min_fill_ratio"],
        datum=common["datum"],
    )

    click.echo(f"点検 {len(files)} ファイル")
    results = _run(inspect_file, files, opts, common["jobs"])
    sys.exit(_report(results, verb="点検"))


def _parse_bounds(value: str | None) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    parts = [v for v in value.replace(" ", "").split(",") if v]
    if len(parts) != 4:
        raise click.BadParameter(
            f"--bounds は xmin,ymin,xmax,ymax の4つを指定する: {value!r}"
        )
    xmin, ymin, xmax, ymax = (float(v) for v in parts)
    if xmin >= xmax or ymin >= ymax:
        raise click.BadParameter(f"--bounds の順序が不正: {value!r}")
    return (xmin, ymin, xmax, ymax)


@main.command()
@click.argument("inputs", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "--out",
    "-o",
    "out_path",
    required=True,
    type=click.Path(dir_okay=False),
    help="結合結果の出力先ファイル。",
)
@click.option("--nodata", default=-9999.0, show_default=True, help="出力の NoData 値。")
@click.option(
    "--compress",
    type=click.Choice(["deflate", "lzw", "zstd", "none"]),
    default="deflate",
    show_default=True,
    help="圧縮方式。",
)
@click.option(
    "--blocksize", default=256, show_default=True, help="タイル化のブロックサイズ。"
)
@click.option(
    "--bounds",
    default=None,
    help="切り出す範囲 `xmin,ymin,xmax,ymax`。格子に合わせて外側へ丸める。",
)
@click.option(
    "--clip",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="切り出す範囲をベクタファイルの範囲から取る（fiona が必要）。",
)
@click.option(
    "--min-coverage",
    default=0.05,
    show_default=True,
    help="出力セル数に対する入力の被覆率の下限。下回れば結合せずエラーにする。",
)
@click.option(
    "--vrt-only",
    is_flag=True,
    help="GeoTIFF を書かず VRT だけを出力する。QGIS はこれを直接開ける。",
)
@click.option(
    "--keep-vrt",
    is_flag=True,
    help="GeoTIFF に加えて、同じ名前の .vrt も残す（どう結合したかの記録になる）。",
)
@click.option("--overwrite", is_flag=True, help="既存の出力を上書きする。")
def merge(
    inputs,
    out_path,
    nodata,
    compress,
    blocksize,
    bounds,
    clip,
    min_coverage,
    vrt_only,
    keep_vrt,
    overwrite,
) -> None:
    """図郭ごとの GeoTIFF を1枚に結合する。

    VRT を介してブロック単位で読み書きするので、出力が何 GB になってもメモリには
    1ブロック分しか載らない。座標系・格子間隔・データ型・格子の位相が食い違う
    入力が混ざっていれば、結合せずエラーにする。
    """
    files = _expand_inputs(inputs, RASTER_SUFFIXES)
    if not files:
        raise click.ClickException("入力ファイルが見つからない")

    try:
        clip_bounds = _parse_bounds(bounds)
        if clip is not None:
            vector_bounds = read_clip_bounds(Path(clip))
            clip_bounds = (
                vector_bounds
                if clip_bounds is None
                else (
                    max(clip_bounds[0], vector_bounds[0]),
                    max(clip_bounds[1], vector_bounds[1]),
                    min(clip_bounds[2], vector_bounds[2]),
                    min(clip_bounds[3], vector_bounds[3]),
                )
            )

        click.echo(f"結合 {len(files)} ファイル -> {out_path}")
        result = merge_files(
            files,
            Path(out_path),
            nodata=nodata,
            compress=compress,
            blocksize=blocksize,
            bounds=clip_bounds,
            min_coverage=min_coverage,
            overwrite=overwrite,
            vrt_only=vrt_only,
            keep_vrt=keep_vrt,
        )
    except MergeError as exc:
        click.secho(f"  NG   {exc}", fg="red", err=True)
        sys.exit(1)

    outputs = result.output.name
    if result.vrt is not None and result.vrt != result.output:
        outputs += f" + {result.vrt.name}"
    click.echo(
        f"  OK   {result.width:,}x{result.height:,} @ {result.res_x:g}m  "
        f"被覆率 {result.coverage:.2%}  -> {outputs}"
    )


@main.command()
@click.argument("inputs", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--verbose", "-v", is_flag=True, help="適合した条項も1行ずつ出す。")
@click.option("--jobs", "-j", default=1, show_default=True, help="並列処理数。")
def validate(inputs, verbose, jobs) -> None:
    """GeoTIFF が標準仕様に適合しているかを点検する。

    自分が変換したものだけでなく、受け取った GeoTIFF の点検にも使える。「貸与された
    データからいきなりマップタイルを作成すると…異常に事前に気づくことができず、出戻り
    作業が発生する」（マップタイル作成マニュアル 第1.0版）。

    仕様書が「〜とする」と定める条項に反すれば NG、「基本とする」「標準とする」の幅から
    外れるだけなら 注意 として報告する。
    """
    files = _expand_inputs(inputs, RASTER_SUFFIXES)
    if not files:
        raise click.ClickException("入力ファイルが見つからない")

    click.echo(f"仕様適合点検 {len(files)} ファイル")
    if jobs <= 1 or len(files) <= 1:
        results = [check_file(p) for p in files]
    else:
        done: dict[Path, SpecResult] = {}
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(check_file, p): p for p in files}
            for future in as_completed(futures):
                done[futures[future]] = future.result()
        results = [done[p] for p in files]

    ng_files = 0
    warn_files = 0
    for r in results:
        if r.error:
            ng_files += 1
            click.secho(f"  NG   {r.path.name}  {r.error}", fg="red", err=True)
            continue
        if r.ng:
            ng_files += 1
            click.secho(f"  NG   {r.path.name}", fg="red", err=True)
        elif r.warn:
            warn_files += 1
            click.secho(f"  注意 {r.path.name}", fg="yellow")
        else:
            click.echo(f"  OK   {r.path.name}")
        for c in r.checks:
            if c.level == "OK" and not verbose:
                continue
            mark = {"NG": "NG  ", "注意": "注意", "OK": "OK  "}[c.level]
            click.echo(f"       {mark} {c.item}: {c.detail}")
            if c.level != "OK":
                click.echo(f"            根拠 {c.source}")

    ok_files = len(results) - ng_files - warn_files
    click.echo(
        f"\n仕様適合点検: 適合 {ok_files} / 注意 {warn_files} / 不適合 {ng_files}"
        f" / 計 {len(results)}"
    )
    sys.exit(1 if ng_files else 0)


if __name__ == "__main__":
    main()
