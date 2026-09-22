#!/usr/bin/env python3
"""山梨県の DEM グリッドデータ（0.50m）を全県分取得する。

`fetch_yamanashi_sample.py` は索引タイルを1枚だけ見てサンプルを取るが、こちらは
**図郭番号の一覧を入力にして全県分を取る**。山梨県のデータは URL が図郭番号から
機械的に決まるので、索引タイルを毎回舐め直す必要がない。

    .../Yamanashi/2024/03/LP/Grid/TXT/08/LE/21/08LE2134.zip
                                  ~~~ ~~ ~~ ~~ ~~~~~~~~
                                  形式 系 記号 上2桁 図郭番号

**txt と GeoTIFF を対で取る。** 山梨県は同じ図郭の同じ格子を txt と GeoTIFF の
両方で配布している数少ないデータセットで、**GeoTIFF を正解データとして変換結果を
ピクセル単位で照合できる**。URL は `/TXT/` と `/TIFF/` が違うだけで、あとは同じ。

図郭番号の一覧は `testdata/meshlist-yamanashi-dem.txt` にある（索引タイルから
列挙したもの。作り直す手順は `testdata/README.md` を参照）。

依存は標準ライブラリのみ。mapbox-vector-tile は要らない。

使い方::

    python scripts/fetch_yamanashi_dem.py --out testdata/yamanashi-dem
    python scripts/fetch_yamanashi_dem.py --out testdata/yamanashi-dem --limit 100 -j 4
"""

from __future__ import annotations

import argparse
import concurrent.futures
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

#: 実ファイルの配信元。図郭番号から URL を組み立てる。
BASE = (
    "https://japan-pointcloud.s3.ap-northeast-1.amazonaws.com/Yamanashi/2024/03/LP/Grid"
)

#: 取得する形式 -> (URL 中のパス, 保存先サブディレクトリ)。
#: txt が変換の入力、GeoTIFF が照合用の正解データ。
KINDS = {
    "txt": ("TXT", "raw"),
    "tif": ("TIFF", "truth"),
}

#: 1ファイルあたりの再試行回数。S3 が散発的に切ることがあるため。
RETRIES = 3


def build_url(mesh: str, kind: str) -> str:
    """図郭番号から ZIP の URL を組み立てる。

    図郭番号 `08LE2134` は 系 `08` / 記号 `LE` / 1/5000 図郭 `21` / 細分 `34` に
    分かれ、URL のディレクトリ階層はこの並びをそのまま使っている。
    """
    path = KINDS[kind][0]
    return f"{BASE}/{path}/{mesh[:2]}/{mesh[2:4]}/{mesh[4:6]}/{mesh}.zip"


def fetch_one(mesh: str, kind: str, out_dir: Path) -> tuple[str, str, int]:
    """図郭1枚を取得する。戻り値は (図郭番号, 状態, バイト数)。

    途中で切れたファイルを完成品と取り違えないよう、`.part` に書いてから改名する。
    **中身が ZIP として開けることも確かめる。** 配信側が HTML のエラーページを
    200 で返すことがあり、拡張子だけでは気づけない。
    """
    dest = out_dir / f"{mesh}.zip"
    if dest.exists():
        return mesh, "既存", dest.stat().st_size

    url = build_url(mesh, kind)
    part = dest.with_suffix(".part")
    for attempt in range(1, RETRIES + 1):
        try:
            urllib.request.urlretrieve(url, part)
            with zipfile.ZipFile(part) as archive:
                if not archive.namelist():
                    raise zipfile.BadZipFile("空の ZIP")
            size = part.stat().st_size
            part.replace(dest)
            return mesh, "取得", size
        except (urllib.error.URLError, zipfile.BadZipFile, OSError) as exc:
            part.unlink(missing_ok=True)
            if attempt == RETRIES:
                return mesh, f"失敗({exc})", 0
            time.sleep(2**attempt)
    return mesh, "失敗", 0


def run(meshes: list[str], kind: str, out_dir: Path, jobs: int) -> int:
    """指定形式をまとめて取得し、失敗数を返す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(meshes)
    done = skipped = failed = 0
    downloaded = 0
    started = time.monotonic()

    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        futures = [pool.submit(fetch_one, m, kind, out_dir) for m in meshes]
        for future in concurrent.futures.as_completed(futures):
            mesh, state, size = future.result()
            done += 1
            if state == "既存":
                skipped += 1
            elif state == "取得":
                downloaded += size
            else:
                failed += 1
                print(f"  NG {mesh}  {state}", file=sys.stderr)
            if done % 200 == 0 or done == total:
                elapsed = time.monotonic() - started
                rate = downloaded / elapsed / 2**20 if elapsed else 0
                print(
                    f"  {done:,}/{total:,}  取得 {downloaded / 2**30:.1f} GiB"
                    f"  既存 {skipped:,}  失敗 {failed:,}"
                    f"  {rate:.0f} MiB/s  経過 {elapsed / 60:.1f} 分",
                    flush=True,
                )
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="保存先のディレクトリ")
    parser.add_argument(
        "--meshlist",
        type=Path,
        default=Path("testdata/meshlist-yamanashi-dem.txt"),
        help="図郭番号の一覧（1行1件）",
    )
    parser.add_argument(
        "--kind",
        choices=[*KINDS, "both"],
        default="both",
        help="txt（変換の入力）/ tif（照合用の正解）/ both",
    )
    parser.add_argument(
        "--limit", type=int, help="先頭から指定件数だけ取る（試し取り用）"
    )
    parser.add_argument("-j", "--jobs", type=int, default=4, help="並列数")
    args = parser.parse_args()

    meshes = [
        line.strip() for line in args.meshlist.read_text().splitlines() if line.strip()
    ]
    if args.limit:
        meshes = meshes[: args.limit]
    kinds = list(KINDS) if args.kind == "both" else [args.kind]

    print(f"図郭 {len(meshes):,} 件 × {len(kinds)} 形式 -> {args.out}  並列 {args.jobs}")
    failed = 0
    for kind in kinds:
        sub = args.out / KINDS[kind][1]
        print(f"\n[{kind}] -> {sub}")
        failed += run(meshes, kind, sub, args.jobs)

    print(f"\n完了: 失敗 {failed:,}")
    if failed:
        sys.exit(1)
    print(f"次: grid2geotiff convert {args.out / 'raw'} -o {args.out / 'out'} -j 8")


if __name__ == "__main__":
    main()
