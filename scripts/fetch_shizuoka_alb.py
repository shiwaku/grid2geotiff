#!/usr/bin/env python3
"""静岡県 ALB グリッドデータ（テキスト）を URL リストから一括取得する。

G空間情報センターの「VIRTUAL SHIZUOKA 静岡県 中西部沿岸 点群データ」の
「ALBデータ　グリッドデータ」を、1/500 図郭ごとの ZIP のまま取得する。ZIP は
展開しない。grid2geotiff は単一のテキストを含む ZIP をそのまま入力にできるので、
展開すると容量が 8 倍ほどに膨らむだけで得がない。

ALB（航空レーザ測深）の沿岸データなので、図郭ごとの被覆率が大きく振れる。
0.02MB から 1.5MB まであり、欠損だらけの図郭が多く混ざる点が山梨県のサンプルと
違う。マージの被覆率判定を実データで試すのに向いている。

使い方::

    python scripts/fetch_shizuoka_alb.py \
        --list testdata/urllist-shizuoka-alb.txt --out testdata/shizuoka-alb/raw
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def fetch(url: str, out_dir: Path) -> tuple[str, int, str]:
    """1件取得する。既にあれば読み飛ばす。"""
    name = url.rsplit("/", 1)[1]
    path = out_dir / name
    if path.exists() and path.stat().st_size > 0:
        return (name, path.stat().st_size, "既存")
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        return (name, 0, f"失敗: {exc}")
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return (name, len(data), "取得")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", type=Path, required=True, help="URL を1行1件で並べたファイル"
    )
    parser.add_argument(
        "--out", type=Path, default=Path("testdata/shizuoka-alb/raw"), help="保存先"
    )
    parser.add_argument("--jobs", "-j", type=int, default=8, help="並列数")
    parser.add_argument(
        "--limit", type=int, default=None, help="先頭から N 件だけ取得する"
    )
    args = parser.parse_args(argv)

    urls = [line.strip() for line in args.list.read_text().splitlines() if line.strip()]
    if args.limit:
        urls = urls[: args.limit]
    args.out.mkdir(parents=True, exist_ok=True)

    total = 0
    failed: list[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(fetch, u, args.out) for u in urls]
        for future in as_completed(futures):
            name, size, status = future.result()
            done += 1
            total += size
            if status.startswith("失敗"):
                failed.append(f"{name}: {status}")
            if done % 100 == 0 or done == len(urls):
                print(
                    f"  {done}/{len(urls)}  累計 {total / 1048576:.0f} MB",
                    file=sys.stderr,
                    flush=True,
                )

    print(
        f"\n完了: {args.out}  {len(urls) - len(failed)}/{len(urls)} 件  "
        f"{total / 1048576:.0f} MB"
    )
    for f in failed[:20]:
        print(f"  {f}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
