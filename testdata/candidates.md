# 検証対象データの候補（G空間情報センター）

[G空間情報センター](https://www.geospatial.jp/) の CKAN API を `グリッドデータ` で検索して得た
**18 データセット**の一覧（2026-09-22 時点）。grid2geotiff の検証に使えるかどうかの判断材料として
まとめる。

```console
curl -s "https://www.geospatial.jp/ckan/api/3/action/package_search?q=グリッドデータ&rows=50"
```

## 見方

**「グリッドデータ」という名前でも中身が格子とは限らない。** 実際、`_grd` を名前に持つ和歌山県・
埼玉県の点群は**グラウンドデータ**（地表面に分類した不規則点群）であって格子ではなかった。
下表の「格子」列は次の意味で使う。

- **確認済** …… 手元のファイルで `grid2geotiff inspect` が格子として通ったもの
- **未確認** …… カタログの説明文が「格子状」「メッシュに整えた」と書いているが、実物を見ていないもの

**配布形式にも注意。** 同じ「グリッドデータ」でも、東京都は GeoTIFF、神奈川県は CSV、山梨県は txt と
GeoTIFF の両方で、**GeoTIFF なら `convert` の入力にはならない**（`merge` の入力にはなる）。

## ダウンロードの仕組み

静岡県・山梨県・神奈川県・東京都のリソース URL は `{z}/{x}/{y}.pbf` のベクトルタイルで、これは
実データではなく**ダウンロード用の索引**である。タイルのフィーチャ属性に図郭番号と実ファイルの URL が
入っており、そこから辿る。`scripts/fetch_yamanashi_sample.py` がこの方式の実装例。

エアロトヨタの2件だけは ZIP の直リンクが公開されている。

## 一覧

### 直リンクで取れる（検証が最も手軽）

| データセット | 提供者 | 対象範囲 | 格子間隔 | 形式 | 容量 | 格子 | ライセンス |
|---|---|---|---|---|---:|---|---|
| [2021年7月10日静岡県熱海市土石流災害航空レーザ計測データ他](https://www.geospatial.jp/ckan/dataset/aac-2021710-atami-lp) | エアロトヨタ | 熱海市伊豆山地区 | 0.5m | TXT (ZIP) | 43MB | 未確認 | CC BY |
| [新潟県村上市小岩内地区航空レーザ計測データ](https://www.geospatial.jp/ckan/dataset/aac-20220807-murakami) | エアロトヨタ | 村上市小岩内地区 | 0.5m | **GRD** (ZIP) | 150MB | 未確認 | 独自利用規約 |

```
熱海:   https://gsic-opendata.s3.ap-northeast-1.amazonaws.com/company/aero-toyota/point-cloud/2021/aac-2021710-atami-lp/aac/20210710atami/griddata.zip
村上市: https://gic-datastorage.s3.ap-northeast-1.amazonaws.com/aac/20220807murakami/Kri50cm.zip
```

村上市の `GRD` は拡張子から Surfer 形式などの独自グリッドの可能性があり、XYZ テキストとは限らない。

### 山梨県

| リソース | 格子間隔 | 形式 | 索引 URL |
|---|---|---|---|
| グリッドデータ_DEM（グラウンド由来） | 0.5m | txt / GeoTIFF | `…/2024/Vectortile2026/gridtxt/{z}/{x}/{y}.pbf` / `gridtif` |
| グリッドデータ_DSM1（Class1+Class9 由来） | 0.5m | txt / GeoTIFF | `…/dsm1gridtxt` / `dsm1gridtif` |
| グリッドデータ_DSM2（Class1 由来） | 0.5m | txt / GeoTIFF | `…/dsm2gridtxt` / `dsm2gridtif` |

- データセット: [山梨県点群データ（航空LP・MMS）](https://www.geospatial.jp/ckan/dataset/yamanashi-pointcloud-2024)
- 対象範囲: 山梨県全域（航空LP）／県管理道路（MMS）
- CRS: JGD2011 平面直角座標系第8系、1/500 図郭単位
- ライセンス: CC BY 4.0 / ODbL デュアル
- **格子: 確認済**（`testdata/yamanashi-kofu`、`testdata/yamanashi-fujiyoshida` で変換実績）
- ホスト: `https://gic-yamanashi.s3.ap-northeast-1.amazonaws.com/`

DEM / DSM1 / DSM2 の3系統があり、**同じ図郭で由来の違う3種類を比べられる**のはここだけ。

### 静岡県（VIRTUAL SHIZUOKA）

| データセット | 対象範囲 | グリッド | 形式 | 格子 | ライセンス |
|---|---|---|---|---|---|
| [中・西部](https://www.geospatial.jp/ckan/dataset/virtual-shizuoka-mw) | 県中部・西部 | LP 0.5m | ZIP（**無圧縮**）+ txt、5列カンマ区切り | **確認済**（800×600、H: に 34,439 図郭 292GiB） | ODbL |
| [中西部沿岸](https://www.geospatial.jp/ckan/dataset/shizuoka-2025-pointcloud-alb) | 中西部の沿岸（ALB） | ALB 0.5m | ZIP（deflate）+ txt、5列カンマ区切り | **確認済**（`testdata/shizuoka-alb` 1,164 図郭） | CC BY |
| [富士山および静岡東部](https://www.geospatial.jp/ckan/dataset/shizuoka-2021-pointcloud) | 富士山・県東部 | LP 0.5m / ALB 0.5m | ZIP（deflate）+ txt、**空白区切り3列** | **確認済**（778×600、H: に 7,987 図郭） | CC BY |
| [富士山南東部・伊豆東部](https://www.geospatial.jp/ckan/dataset/shizuoka-2019-pointcloud) | 富士山南東部・伊豆東部 | LP 0.5m / ALB 0.5m | ZIP（deflate）+ txt、**空白区切り3列** | **確認済**（800×600、E: に1図郭） | CC BY |
| [伊豆西部](https://www.geospatial.jp/ckan/dataset/shizuoka-2020-pointcloud) | 伊豆西部 | LP 0.5m / ALB 0.5m | 不明 | 未確認 | CC BY |
| [北西部](https://www.geospatial.jp/ckan/dataset/shizuoka-2025-pointcloud) | 県北西部 | LP 0.5m | 不明 | 未確認（索引 URL が未設定） | CC BY |
| [北部（南アルプス）](https://www.geospatial.jp/ckan/dataset/shizuoka-2022-pointcloud) | 南アルプス | LP 0.5m | 不明 | 未確認 | CC BY |

- CRS: JGD2011 平面直角座標系第8系（中西部沿岸のみ **JGD2024**）、1/500 図郭単位（800×600 セル）
- ホスト: `https://gic-shizuoka.s3.ap-northeast-1.amazonaws.com/`

**形式は同じ静岡県内でも揃っていない。** 中部・西部と中西部沿岸は5列カンマ区切りで `--columns 1,2,3`
が要る（`testdata/README.md` 参照）のに対し、富士山まわりの2件は `X Y Z` の空白区切り3列で
オプションなしに通る。ZIP の圧縮方法も中・西部だけ**無圧縮**（stored）で、他は deflate である。

「不明」の3件はグリッドのリソースが索引タイル（PBF）しか公開しておらず、実ファイルを
辿らないと形式が分からない。上4件の実績からは ZIP + txt と見込まれるが、区切り文字と列数は
取ってみるまで確定できない。

**中西部沿岸だけ測地系が JGD2024** と書かれており、他と混ぜて結合できない可能性がある。

### 神奈川県

| データセット | 対象範囲 | グリッド |
|---|---|---|
| [令和6年度](https://www.geospatial.jp/ckan/dataset/kanagawa-2024-pointcloud) | 県西部、相模原 | 0.5m（CSV / メッシュ） |
| [令和4年度](https://www.geospatial.jp/ckan/dataset/kanagawa-2022-pointcloud) | 横浜北部、川崎 | 0.5m、**1m**、DCHM、DSM（CSV / TXT / メッシュ） |
| [令和3年度](https://www.geospatial.jp/ckan/dataset/kanagawa-2021-pointcloud) | 横浜南部、湘南、横須賀三浦 | 0.5m（CSV / メッシュ） |
| [令和2年度](https://www.geospatial.jp/ckan/dataset/kanagawa-2020-2-pointcloud) | 相模原 | 0.5m（CSV / メッシュ） |
| [令和2年度](https://www.geospatial.jp/ckan/dataset/kanagawa-2020-1-pointcloud) | 県央部 | 0.5m（CSV / メッシュ） |
| [令和元年度](https://www.geospatial.jp/ckan/dataset/kanagawa-2019-pointcloud) | 県西部 | 0.5m（CSV / メッシュ） |

- ライセンス: CC BY、ホスト `https://gic-kanagawa.s3.ap-northeast-1.amazonaws.com/`
- **格子: 全件未確認**（手元に1ファイルも無い）

**他県に無い性質を3つ持つ。**

1. **1m 格子**がある（令和4年度）。手元の検証データは 0.5m と 0.25m だけなので、格子間隔の推定を別の値で試せる
2. **CSV 形式**を明示している。区切り文字の自動判定と `--columns` の検証に使える
3. DCHM（数値樹冠高）と DSM も格子で配布されており、**標高以外の値を持つ格子**の確認に使える

### 東京都デジタルツイン

| データセット | 対象範囲 | 格子間隔 | 形式 |
|---|---|---|---|
| [多摩地域](https://www.geospatial.jp/ckan/dataset/tokyopc-tama-2023) | 多摩地域 | 0.25m / 0.50m | **GeoTIFF** |
| [島しょ地域](https://www.geospatial.jp/ckan/dataset/tokyopc-shima-2023) | 島しょ部 | 0.25m / 0.50m | **GeoTIFF** |

- ライセンス: CC BY、ホスト `https://gic-tokyo.s3.ap-northeast-1.amazonaws.com/`
- **`convert` の入力にはならない**（配布時点で GeoTIFF）。`merge` の入力としてなら使える
- 手元では H: に多摩の ZIP が 10,050 件、C: に23区 0.25m が 99 件ある（いずれも中身は GeoTIFF）

23区のデータセットはこの検索語に掛からなかったが、同じプロジェクトで 0.25m の GeoTIFF を配布している。

## 検証対象としての優先順位（案）

1. **神奈川県 令和4年度** — 1m 格子・CSV・DCHM/DSM と、手元に無い性質が3つある
2. **熱海（エアロトヨタ）** — 43MB の直リンクで、索引タイルを辿らずに試せる
3. **新潟県村上市（エアロトヨタ）** — `GRD` 形式が XYZ テキストかどうかの確認
4. 静岡県の未取得分（伊豆西部・北西部・南アルプス） — 既確認分と同形式の見込みだが、区切り文字は確定できていない

## 注意

この表の「格子間隔」「対象範囲」はカタログの記載をそのまま写したもので、**実物で検算したのは
「確認済」と書いた5件だけ**である。ダウンロードの際は各データセットの利用規約を確認すること
（村上市のみ独自利用規約）。
