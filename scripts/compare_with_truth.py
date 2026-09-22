#!/usr/bin/env python3
"""変換結果を、配布元の GeoTIFF（正解データ）と突き合わせる。

山梨県は**同じ図郭の同じ格子を txt と GeoTIFF の両方で配布している**。txt を
本ツールで変換した結果が、県の作った GeoTIFF と一致するかを見れば、変換が正しい
ことを配布元の成果物で裏づけられる。

見るのは次の4点。

- **ジオリファレンス** …… 大きさ・transform・CRS。ここがずれていれば位置が違う。
  本ツールの中心的な主張である「セル中心で記録された座標を半セルずらして配置する」を
  誤れば、transform の原点が 0.25m（半セル）ずれるので、ここで露見する。
- **欠損の位置** …… どのセルを欠損とみなしたか。NoData 値そのものは配布元と違って
  よい（本ツールは -9999 に統一する）が、**欠損とするセルの集合は一致すべき**である。
- **画素値** …… 有効セルの標高。float32 同士なので完全一致を期待してよい。
- **差の分布** …… 一致しない場合に、全面的にずれているのか一部だけかを見る。

正解データは ZIP のまま読む。GDAL の仮想ファイルシステム（`zip://`）を使うので、
33GiB を展開せずに済む。

使い方::

    python scripts/compare_with_truth.py --out testdata/yamanashi-dem/out \\
        --truth testdata/yamanashi-dem/truth -j 8
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import rasterio

#: 正解データ側が NoData に使う値。山梨県は float32 の最大値に近い値を使っており、
#: NoData タグにも入っているが、念のため巨大値もまとめて欠損として扱う。
HUGE = 1e30


def truth_path(truth_dir: Path, mesh: str) -> str | None:
    """正解データの読み出し先を返す。ZIP なら `zip://` 経由で中身を直接開く。"""
    tif = truth_dir / f"{mesh}.tif"
    if tif.exists():
        return str(tif)
    archive = truth_dir / f"{mesh}.zip"
    if not archive.exists():
        return None
    with zipfile.ZipFile(archive) as handle:
        names = [n for n in handle.namelist() if n.lower().endswith((".tif", ".tiff"))]
    if not names:
        return None
    return f"zip://{archive.as_posix()}!{names[0]}"


def compare_one(args: tuple[str, str, str]) -> dict[str, object]:
    """図郭1枚を照合する。"""
    mesh, mine_path, truth = args
    result: dict[str, object] = {"mesh": mesh}
    with rasterio.open(mine_path) as mine, rasterio.open(truth) as ref:
        if (mine.width, mine.height) != (ref.width, ref.height):
            result["ng"] = (
                f"大きさ違い {mine.width}x{mine.height} vs {ref.width}x{ref.height}"
            )
            return result
        pairs = zip(mine.transform[:6], ref.transform[:6], strict=True)
        if any(abs(a - b) > 1e-6 for a, b in pairs):
            result["ng"] = f"transform 違い {mine.transform[:6]} vs {ref.transform[:6]}"
            return result
        if mine.crs != ref.crs:
            result["ng"] = f"CRS 違い {mine.crs} vs {ref.crs}"
            return result

        a = mine.read(1)
        b = ref.read(1)
        valid_a = a != mine.nodata
        valid_b = np.isfinite(b) & (b != ref.nodata) & (np.abs(b) < HUGE)
        both = valid_a & valid_b
        diff = np.abs(a[both] - b[both])

        result.update(
            cells=int(a.size),
            valid=int(both.sum()),
            only_mine=int((valid_a & ~valid_b).sum()),
            only_truth=int((~valid_a & valid_b).sum()),
            exact=int((diff == 0).sum()) if diff.size else 0,
            max_diff=float(diff.max()) if diff.size else 0.0,
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="変換結果の GeoTIFF")
    parser.add_argument("--truth", type=Path, required=True, help="正解データ（ZIP 可）")
    parser.add_argument("-j", "--jobs", type=int, default=4, help="並列数")
    parser.add_argument("--report", type=Path, help="図郭ごとの結果を JSONL で書き出す")
    args = parser.parse_args()

    jobs = []
    missing = []
    for mine in sorted(args.out.glob("*.tif")):
        mesh = mine.stem
        truth = truth_path(args.truth, mesh)
        if truth is None:
            missing.append(mesh)
        else:
            jobs.append((mesh, str(mine), truth))

    print(f"照合 {len(jobs):,} 図郭  正解データ無し {len(missing):,}")
    if missing[:5]:
        print("  正解が無い例:", ", ".join(missing[:5]))

    ng: list[dict[str, object]] = []
    cells = valid = exact = only_mine = only_truth = 0
    worst = 0.0
    report = args.report.open("w", encoding="utf-8") if args.report else None

    with concurrent.futures.ProcessPoolExecutor(args.jobs) as pool:
        for done, result in enumerate(pool.map(compare_one, jobs, chunksize=8), 1):
            if report:
                report.write(json.dumps(result, ensure_ascii=False) + "\n")
            if "ng" in result:
                ng.append(result)
                print(f"  NG {result['mesh']}  {result['ng']}", file=sys.stderr)
            else:
                cells += int(result["cells"])
                valid += int(result["valid"])
                exact += int(result["exact"])
                only_mine += int(result["only_mine"])
                only_truth += int(result["only_truth"])
                worst = max(worst, float(result["max_diff"]))
            if done % 1000 == 0:
                print(f"  {done:,}/{len(jobs):,}", flush=True)

    if report:
        report.close()

    print(f"\nセル {cells:,}  共通の有効セル {valid:,}")
    print(f"完全一致 {exact:,}" + (f" ({exact / valid * 100:.4f}%)" if valid else ""))
    print(f"差の最大 {worst}")
    print(f"欠損の食い違い 当方だけ有効 {only_mine:,} / 正解だけ有効 {only_truth:,}")
    print(f"ジオリファレンス不一致 {len(ng):,} 図郭")
    if ng or only_mine or only_truth or worst:
        sys.exit(1)


if __name__ == "__main__":
    main()
