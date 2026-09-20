"""GeoTIFF の書き出し。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS

from grid2geotiff.gridspec import GridSpec

#: 圧縮方式ごとの既定オプション。標高のような連続値は予測子2が効く。
_COMPRESS_OPTIONS = {
    "deflate": {"compress": "deflate", "predictor": 2, "zlevel": 6},
    "lzw": {"compress": "lzw", "predictor": 2},
    "zstd": {"compress": "zstd", "predictor": 2, "zstd_level": 9},
    "none": {},
}


def write_geotiff(
    path: str | Path,
    array: np.ndarray,
    spec: GridSpec,
    crs: str | CRS,
    *,
    nodata: float = -9999.0,
    compress: str = "deflate",
    tiled: bool = True,
    blocksize: int = 256,
    description: str | None = None,
    extra_tags: dict[str, str] | None = None,
) -> Path:
    """2次元配列を GeoTIFF として書き出す。

    セル中心座標を半セルずらした「面（Area）」規約の変換行列を書き込み、
    `AREA_OR_POINT=Area` を明示する。値自体は点標本だが、Point 規約は解釈が
    ソフトによって割れるため、どの GIS でも同じ位置に載る Area で統一する。
    元が点標本であることは `GRID_CONVENTION` タグに残す。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if array.ndim != 2:
        raise ValueError(f"2次元配列が必要: shape={array.shape}")
    if array.shape != (spec.height, spec.width):
        raise ValueError(
            f"配列の形と格子定義が食い違う: {array.shape} != {(spec.height, spec.width)}"
        )
    if compress not in _COMPRESS_OPTIONS:
        raise ValueError(
            f"未知の圧縮方式 {compress!r}（{', '.join(_COMPRESS_OPTIONS)} のいずれか）"
        )

    profile = {
        "driver": "GTiff",
        "height": spec.height,
        "width": spec.width,
        "count": 1,
        "dtype": array.dtype.name,
        "crs": CRS.from_user_input(crs),
        "transform": spec.transform(),
        "nodata": nodata,
        "tiled": tiled,
        "BIGTIFF": "IF_SAFER",
    }
    if tiled:
        profile["blockxsize"] = blocksize
        profile["blockysize"] = blocksize
    profile.update(_COMPRESS_OPTIONS[compress])

    tags = {
        "AREA_OR_POINT": "Area",
        "GRID_CONVENTION": "cell-center samples, transform shifted by half a cell",
        "GRID_RESOLUTION": f"{spec.res_x} x {spec.res_y}",
    }
    if extra_tags:
        tags.update(extra_tags)

    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array, 1)
        dst.update_tags(**tags)
        if description:
            dst.set_band_description(1, description)

    return path
