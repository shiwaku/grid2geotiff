# grid2geotiff

航空レーザ計測のグリッドデータ（XYZ座標値テキスト）を GeoTIFF に変換する CLI。

セル中心で記録された座標を**半セルずらして**配置するため、出力が国土基本図の図郭境界にぴったり載る。格子に載っていない点群は**内挿せずエラーにする**。

```console
$ grid2geotiff convert data/raw -o data/out --crs EPSG:6676 -j 4
変換 4 ファイル -> data/out  (CRS EPSG:6676)
  OK   08LE2134.txt  800x600 @ 0.5m  点 478,294  欠損 1,706 (0.36%)  -> 08LE2134.tif
  OK   08LE2135.txt  800x600 @ 0.5m  点 480,000  欠損 0 (0.00%)  -> 08LE2135.tif
  OK   08LE2144.txt  800x600 @ 0.5m  点 480,000  欠損 0 (0.00%)  -> 08LE2144.tif
  OK   08LE2145.txt  800x600 @ 0.5m  点 471,545  欠損 8,455 (1.76%)  -> 08LE2145.tif

変換: 成功 4 / 失敗 0 / 計 4
```

## なぜこれが要るのか

「森林資源データ解析・管理標準仕様書 Ver3.1」はこう書いている。

> 一般のレーザ計測成果としては、XYZ 座標値（テキスト形式）で作成されるが、各種解析に利用するにはラスタ（TIFF 形式）への変換が必要となる。

この変換には、素朴にやると間違える点が2つある。

### 1. 座標はセル中心なので半セルずれる

グリッドデータの座標は**セルの中心**を指す。山梨県のデータなら `5600.25 -36900.25 271.95` のように `.25` / `.75` で並ぶ。これをそのままラスタの原点にすると、全体が**半セル（0.5m格子なら0.25m）ずれる**。

本ツールは原点を `xmin - res/2` に置き、`AREA_OR_POINT=Area` を明示する。値自体は点標本だが、`Point` 規約は解釈がソフトによって割れるため、どの GIS でも同じ位置に載る `Area` で統一している（GDAL の XYZ ドライバと同じ流儀）。元が点標本であることは `GRID_CONVENTION` タグに残す。

### 2. グリッドデータと不規則点群は別物

格子状に並んだ点は、インデックス計算で**直接配置**できる。内挿は不要であり、してはいけない（元の値が壊れる）。一方、不規則点群のラスタ化は内挿補間（TIN / IDW / Kriging など）を伴う別の作業で、手法の選択が成果物に焼き込まれる。

本ツールは入力の格子性を検証し、格子に載っていなければ**黙って内挿せずエラーにする**。

```console
$ grid2geotiff convert scatter.txt -o out --crs EPSG:6676
  NG   scatter.txt  X 軸が格子に載っていない（最大ずれ 0.0252 m > 許容 0.0005 m、
       推定間隔 0.0504 m）。不規則点群の可能性が高く、ラスタ化するには内挿補間が必要
```

## インストール

```console
pip install -e .
```

Python 3.10 以上。依存は rasterio / numpy / pandas / click のみで、GDAL の Python バインディングは不要。

## 使い方

### convert — GeoTIFF に変換する

```console
grid2geotiff convert <入力...> -o <出力先> --crs <EPSG>
```

入力にはファイルのほかディレクトリも指定できる（再帰探索）。

| オプション | 既定 | 説明 |
|---|---|---|
| `--crs` | 必須 | 入力座標の参照系（例 `EPSG:6676` = JGD2011 平面直角座標系第8系） |
| `-o, --out-dir` | 必須 | GeoTIFF の出力先 |
| `--res` | 自動推定 | 格子間隔 [m]。`0.5` か `0.5,0.5` |
| `--nodata` | `-9999` | 出力の NoData 値 |
| `--dtype` | `float32` | `float32` / `float64` / `int16` / `int32` |
| `--compress` | `deflate` | `deflate` / `lzw` / `zstd` / `none`（予測子2つき） |
| `--blocksize` | `256` | タイル化のブロックサイズ |
| `--delimiter` | 自動判定 | `space` / `comma` / `tab` / `semicolon` |
| `--columns` | `0,1,2` | X,Y,Z の列番号（0 始まり） |
| `--input-nodata` | なし | 入力側の欠測値。複数指定可 |
| `--tolerance-ratio` | `0.01` | 格子からのずれの許容量（格子間隔に対する比） |
| `-j, --jobs` | `1` | 並列処理数 |
| `--overwrite` | off | 既存の出力を上書き |

### inspect — 変換せずに点検する

大量の図郭を変換する前に、格子間隔の食い違いや不規則点群の混入を洗い出す。

```console
$ grid2geotiff inspect data/raw -j 4
点検 4 ファイル
  OK   08LE2134.txt  800x600 @ 0.5m  点 478,294  欠損 1,706 (0.36%)  X 5600.00..6000.00 Y -37200.00..-36900.00
  OK   08LE2135.txt  800x600 @ 0.5m  点 480,000  欠損 0 (0.00%)  X 6000.00..6400.00 Y -37200.00..-36900.00
```

## 入力形式

`X Y Z` を1行1点で並べたテキスト。

- 拡張子: `.txt` / `.csv` / `.xyz` / `.dat`、およびそれを1つだけ含む `.zip`
- 区切り: 空白 / カンマ / タブ / セミコロン（先頭行から自動判定）
- 改行: LF / CRLF どちらも可
- `#` で始まる行と空行は読み飛ばす

## 出力仕様

- GeoTIFF（タイル化、既定 256×256 ブロック、Deflate + 予測子2）
- 1バンド `float32`、バンド説明 `elevation`
- NoData `-9999`（欠損セルに充填。マップタイル作成マニュアルの「ラスタ値は -9999 に統一」に準拠）
- `AREA_OR_POINT=Area`、`GRID_CONVENTION` / `GRID_RESOLUTION` / `SOURCE_FILE` タグ

## サンプルデータ

山梨県の点群データ（CC BY 4.0 / ODbL デュアルライセンス）で動作確認できる。

```console
pip install mapbox-vector-tile
python scripts/fetch_yamanashi_sample.py --out data/raw
grid2geotiff convert data/raw -o data/out --crs EPSG:6676 -j 4
```

配布リソースの URL は `{z}/{x}/{y}.pbf` のベクトルタイルだが、これは実データではなく**ダウンロード用の索引**である。タイルのフィーチャ属性に図郭番号（`MESH_NO`）と ZIP の URL が入っており、スクリプトはそこから実ファイルを辿る。

取得できるのは 1/500 図郭（400m × 300m）単位の 0.5m 格子で、1枚あたり 800 × 600 = 480,000 点。座標系は JGD2011 平面直角座標系第8系（EPSG:6676）。

## 開発

```console
pip install -e ".[dev]"
pytest -q
ruff check . && ruff format --check .
```

テストは実データを使わず、同じ性質（セル中心座標・欠損セル・CRLF）を持つ合成データで検証する。

## 参考資料

- [森林GISフォーラム 標準仕様分科会](https://fgis.jp/cloud) — 森林資源データ解析・管理標準仕様書 Ver3.1、森林情報に関するオープンデータ標準仕様書 Ver2.1
- [マップタイル作成マニュアル 第1.0版](https://forestgeo.info/250510_manual_maptiles_1.0.pdf)（室木直樹＠林野庁、CC BY 4.0） — NoData 統一やアルファバンドなど実務上の手当て
- [作業規程の準則 航空レーザ測量編](https://www.gsi.go.jp/common/000258818.pdf)（国土地理院） — 第8節 メッシュデータ作成、内挿補間の5手法
- [山梨県 点群データ（航空LP・MMS）](https://www.geospatial.jp/ckan/dataset/yamanashi-pointcloud-2024) — 動作確認に使ったサンプル

## ライセンス

MIT License
