#!/usr/bin/env python3
"""山梨県オープンデータからグリッドデータ（DEM 0.50m txt）のサンプルを取得する。

G空間情報センターのリソース URL は `{z}/{x}/{y}.pbf` のベクトルタイルで、これは
実データではなく「ダウンロード用の索引」である。タイルのフィーチャ属性に図郭番号
（MESH_NO）と ZIP の URL が入っているので、そこから実ファイルを辿る。

依存: requests は使わず標準ライブラリのみ。ベクトルタイルの復号に
mapbox-vector-tile が要る（``pip install mapbox-vector-tile``）。

使い方::

    python scripts/fetch_yamanashi_sample.py --out testdata/yamanashi-kofu/raw
    python scripts/fetch_yamanashi_sample.py --out testdata/yamanashi-fujiyoshida/raw \
        --lat 35.4873 --lon 138.8078 --limit 4
"""

from __future__ import annotations

import argparse
import math
import sys
import urllib.request
import zipfile
from pathlib import Path

#: 索引ベクトルタイルの配信元。product ごとにパスが分かれている。
TILE_BASE = "https://gic-yamanashi.s3.ap-northeast-1.amazonaws.com/2024/Vectortile2026"

#: 製品名 -> 索引タイルのパス。txt 形式のグリッドデータのみ対象にする。
PRODUCTS = {
    "dem": "gridtxt",
    "dsm1": "dsm1gridtxt",
    "dsm2": "dsm2gridtxt",
}

#: 既定の取得位置（甲府市街）。ここを含むタイルの図郭を拾う。
DEFAULT_LAT = 35.6642
DEFAULT_LON = 138.5686

#: 図郭ポリゴンが1枚ずつ載る程度のズームレベル。
DEFAULT_ZOOM = 16


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    """経緯度を XYZ タイル座標に変換する。"""
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def fetch_index(product: str, lon: float, lat: float, zoom: int) -> list[dict[str, str]]:
    """索引タイルを取得し、図郭番号とダウンロード URL の一覧を返す。"""
    try:
        import mapbox_vector_tile
    except ImportError:
        sys.exit("mapbox-vector-tile が必要: pip install mapbox-vector-tile")

    x, y = lonlat_to_tile(lon, lat, zoom)
    url = f"{TILE_BASE}/{PRODUCTS[product]}/{zoom}/{x}/{y}.pbf"
    print(f"索引タイル: {url}")
    with urllib.request.urlopen(url, timeout=60) as response:
        tile = mapbox_vector_tile.decode(response.read())

    entries: list[dict[str, str]] = []
    for layer in tile.values():
        for feature in layer["features"]:
            props = feature["properties"]
            if "URL" in props and "MESH_NO" in props:
                entries.append({"mesh": props["MESH_NO"], "url": props["URL"]})
    return sorted(entries, key=lambda e: e["mesh"])


def download(entry: dict[str, str], out_dir: Path, *, extract: bool) -> None:
    """図郭1枚分の ZIP を取得し、必要なら展開する。"""
    zip_path = out_dir / f"{entry['mesh']}.zip"
    if not zip_path.exists():
        print(f"  取得 {entry['mesh']} ...", end="", flush=True)
        urllib.request.urlretrieve(entry["url"], zip_path)
        print(f" {zip_path.stat().st_size / 1e6:.1f} MB")
    else:
        print(f"  既存 {entry['mesh']}")

    if extract:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(out_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=Path("testdata/yamanashi-kofu/raw"), help="保存先"
    )
    parser.add_argument(
        "--product", choices=sorted(PRODUCTS), default="dem", help="取得する製品"
    )
    parser.add_argument("--lat", type=float, default=DEFAULT_LAT)
    parser.add_argument("--lon", type=float, default=DEFAULT_LON)
    parser.add_argument("--zoom", type=int, default=DEFAULT_ZOOM)
    parser.add_argument("--limit", type=int, default=4, help="取得する図郭数")
    parser.add_argument("--no-extract", action="store_true", help="ZIP を展開しない")
    args = parser.parse_args()

    entries = fetch_index(args.product, args.lon, args.lat, args.zoom)
    if not entries:
        sys.exit("索引タイルに図郭が見つからない。--lat/--lon/--zoom を見直すこと")

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"図郭 {len(entries)} 件のうち {min(args.limit, len(entries))} 件を取得")
    for entry in entries[: args.limit]:
        download(entry, args.out, extract=not args.no_extract)

    print(f"\n完了: {args.out}")
    print(f"次: grid2geotiff convert {args.out} -o data/out --crs EPSG:6676")


if __name__ == "__main__":
    main()
