# Amazon商品リサーチツール 引継ぎドキュメント

## プロジェクト概要

Amazon.co.jpで販売する商品候補を効率的に選定するためのWebアプリを構築中。
Keepa APIを活用し、条件に合う商品を自動抽出することで、最も工数がかかっていた
「何を売るか探す工程」を自動化することが目的。

---

## ユーザーの業務フロー（手動→自動化の背景）

**当初の手動フロー：**
1. Amazonで気になる商品を感覚で探す（←ここが一番しんどい）
2. 商品画像をコピーしてAlibaba/1688で画像検索
3. ヒットした商品の価格を手動確認・メモ
4. Amazon価格との価格差を手動計算

**解決すべき本質的課題：**
- 「何を探すか」が一番の課題であり、ツールより戦略の問題だった
- 最終的に「Keepaデータで条件に合う商品を自動抽出」→「人間が目で確認してキーワードを考える」方式に決定

---

## システム設計の決定事項（変更禁止）

| 項目 | 決定内容 |
|------|---------|
| アーキテクチャ | Webアプリ（Flask） |
| ホスティング | Windowsローカル（localhost:5000） |
| データソース | Keepa API（公式・規約リスクなし） |
| フロントエンド | HTML + JavaScript |
| バックエンド | Python Flask |
| DB | SQLite（data.db） |
| コスト | 無料（Keepa Proは契約済み） |

---

## 重要な前提・決定事項（変更禁止）

- **Keepa Proアカウント契約済み・APIキー設定済み（€49プラン：20トークン/分、最大1,200トークン貯蓄）**
- **無料構成で進める（Keepa以外の有料APIは使わない）**
- **ローカル動作（クラウドサーバー不使用）**
- **Amazonスクレイピングは使わない（Keepa API経由のみ）**
- **商品候補の最終判断は人間（ツールは候補を出すだけ）**
- **キーワードはツールが出した商品候補を見てから人間が考える**
- **大カテゴリは市場規模が大きすぎるため商品取得対象外（末端カテゴリのみ対象）**

---

## 商品抽出フィルタリング条件（決定済み）

- 月間売上推定：**10万円以上〜100万円以下**
- レビュー数：300件以下
- 月間売上推定が計算不可の商品：除外
- ランキング上位100件（1〜100位）から取得
- ※優先表示（51〜100位バナー）は廃止済み

---

## Keepaトークン消費の仕組み

- **€49プラン：1分あたり20トークン生成、最大1,200トークン貯蓄**
- **1日あたり：20×60×24 = 28,800トークン使用可能**
- 1カテゴリ取得あたり：best_sellers_query(1) + api.query×100件 = **約101トークン**
- 1日あたり最大 **約285カテゴリ**取得可能

---

## 環境構築（完了済み）

**ユーザー環境：**
- OS: Windows 11
- Python: 3.14.3
- プロジェクトフォルダ：`C:\amazon_research`

**インストール済みライブラリ：**
- keepa 1.4.4
- flask 3.1.3
- numpy 2.4.3
- pandas 3.0.0

---

## ファイル構成

```
C:\amazon_research\
├── app.py              ← Flask アプリ本体
├── config.py           ← APIキー・フィルタ設定
├── batch.py            ← バッチ取得スクリプト
├── data.db             ← SQLiteデータベース
├── requirements.txt    ← 依存パッケージ
└── templates/
    └── index.html      ← フロントエンド
```

**config.pyの設定：**
- KEEPA_API_KEY：ユーザーが設定済み
- DOMAIN：JP（amazon.co.jp）
- MAX_REVIEW_COUNT：300
- MAX_MONTHLY_REVENUE：1,000,000
- MIN_MONTHLY_REVENUE：100,000
- BESTSELLER_LIMIT：100

---

## このチャットでやってきたこと（前チャットからの前進内容）

### 1. お気に入りカテゴリ登録機能の実装 ✅

**実装内容：**

**app.py：**
- `init_db()` 関数を追加（`favorite_categories` テーブルの作成含む）
- `DB_PATH` 定数を追加（`data.db` のパス）
- `sqlite3`, `os`, `datetime` のimportを追加
- `/api/favorites` GET：お気に入り一覧取得
- `/api/favorites` POST：お気に入り登録
- `/api/favorites/<category_id>` DELETE：お気に入り解除
- `get_products` にSQLiteキャッシュ参照を追加（batch.py取得済みデータがあれば即返す）
- `get_products` にフィルタパラメータ（min_revenue, max_revenue, max_reviews）をリクエストボディから受け取るよう変更
- 価格取得ロジックをネスト構造対応版に修正（`stats_parsed["current"]["NEW"]` 等）
- フィルタリングに月間売上下限（MIN_MONTHLY_REVENUE）を追加
- 月間売上が計算不可の商品を除外
- priority（51〜100位優先表示）を廃止

**index.html：**
- `loadFavorites()` 関数を追加（起動時にお気に入りIDセットを取得）
- `toggleFavorite()` 関数を追加（登録/解除をAPI経由で実行）
- `renderProducts()` にお気に入りボタンを追加（商品一覧上部に表示）
- `renderProducts()` の引数に `categoryId` を追加
- キャッシュ表示バッジ（📦 キャッシュ）を追加
- フィルター表示を「月間売上10万〜100万円」に更新
- priority関連のUI（priorityBanner、rankBadgeStyle等）を削除
- お気に入りボタンのCSSを追加（`.fav-btn`, `.fav-active`）

**batch.py：**
- `BATCH_SIZE` を3→10に変更
- `BATCH_CATEGORIES` ハードコードを削除
- `get_favorite_categories()` 関数を追加（SQLiteから取得）
- `expand_to_leaf_categories()` 関数を追加（大カテゴリ→末端カテゴリへ再帰展開）※後述の問題あり
- `fetch_and_save()` 関数を追加（1カテゴリ取得・保存処理を関数化）
- `main()` をお気に入りカテゴリ取得→末端展開→バッチ取得の流れに変更

### 2. デフォルトお気に入り12カテゴリの自動登録 ✅

`init_db()` 内で `favorite_categories` テーブルが空の場合のみ以下を自動挿入：

| カテゴリ名 | カテゴリID |
|-----------|---------|
| スポーツ&アウトドア | 14304371 |
| ドラッグストア | 160384011 |
| パソコン・周辺機器 | 2127209051 |
| 文房具・オフィス用品 | 86731051 |
| 車&バイク | 2017304051 |
| おもちゃ&ゲーム | 13299531 |
| 家電・カメラ | 2277724051 |
| ホーム&キッチン | 3828871 |
| ペット用品 | 2127211051 |
| ベビー&マタニティ | 16333571 |
| ファッション | 352484011 |
| 産業・研究開発用品 | 3445393051 |

---

## 現在の動作状況

### 動いている機能 ✅
- Flaskサーバー起動（localhost:5000）
- Keepa API接続・残りトークン表示
- 大カテゴリ→中間カテゴリ→末端カテゴリの階層展開（サイドバー）
- 末端カテゴリクリックで商品取得・表示
- 商品カード表示（サムネイル・商品名・価格・ランク・レビュー数・月売上推定・Amazonリンク）
- フィルタリング（レビュー≦300件・月間売上10万〜100万円）
- SQLiteキャッシュ（batch.pyで取得済みカテゴリは即座に表示）
- お気に入り登録/解除ボタン（商品表示時に表示）
- デフォルト12カテゴリの自動お気に入り登録（初回起動時）

### 現在の問題点・未完了項目 ❌

**問題1（最重要）：batch.pyの末端カテゴリ展開が誤動作している**

症状：
- `expand_to_leaf_categories()` 実行時に `3774d5d1-130f-4fe2-9aaa-27cae247e6e0_3601` のようなUUID形式の不正なカテゴリIDが展開される
- 末端カテゴリが7826件と膨大になっている（1日285件しか取得できないので27日以上かかる）
- ほぼ全カテゴリで「ベストセラーなし」エラーが出る

原因：Keepa APIの `category_lookup` が返すデータ構造から、不正なIDを子カテゴリとして拾っている可能性が高い。

**根本的な方針問題：**
「大カテゴリを登録→配下の末端を全自動展開」する案B/案Cは現実的でないことが判明。
1日285カテゴリという上限に対し、末端カテゴリ数が多すぎる。

→ **次チャットで方針を決め直す必要あり（下記「これから取り掛かる項目」参照）**

**問題2：DIY・工具・ガーデン、ビューティーのカテゴリIDが不正の可能性**
- batch.pyテストで「Best sellers search results not yet available」エラー
- デフォルト12カテゴリにはこの2カテゴリは含めていないが、app.pyのROOT_CATEGORIES_JPには残存している

**問題3：Windowsタスクスケジューラの設定未完了**
- batch.pyの手動実行は確認済みだが、タスクスケジューラへの登録がまだ

**問題4：index.htmlにpriority-banner等の不要なCSSが残存**
- 機能的には無影響だが、クリーンアップが必要

---

## これから取り掛かる必要がある項目と優先順位

### 優先度1（次チャットで最初に）：batch.pyのお気に入り登録方針を決め直す

**現状の問題：**
大カテゴリを登録→末端まで全自動展開する方式は現実的でない（7826件になった）

**検討中の選択肢：**

**案D（推奨）：末端カテゴリを手動で選んで登録**
- ブラウザで末端カテゴリの商品を見て「良さそう」と思ったカテゴリだけお気に入り登録
- batch.pyは登録された末端カテゴリIDをそのままベストセラー取得に使う（展開処理不要）
- 1日285カテゴリ以内に収まるよう自分でコントロール可能
- メリット：シンプル・確実・質の高いデータが集まる
- デメリット：最初の登録作業がやや手間

**案C：2階層目（中間カテゴリ）単位で登録、直下の末端のみ展開（1階層だけ）**
- 展開を1階層に限定することで件数を現実的に抑える
- メリット：登録が楽
- デメリット：中間カテゴリにベストセラーデータがない場合がある

→ **案Dが推奨。次チャット開始時に方針確認してから実装に入ること。**

**案Dに決まった場合のbatch.py修正内容：**
- `expand_to_leaf_categories()` を削除
- `get_favorite_categories()` で取得したカテゴリIDをそのまま `fetch_and_save()` に渡す
- シンプルな構成に戻す

### 優先度2：Windowsタスクスケジューラ設定
batch.pyを毎朝自動実行するタスクの登録。

### 優先度3：Alibaba画像検索リンクの自動生成
商品カードに「Alibabaで探す」ボタンを追加。
Amazon商品画像URLから1688.comの画像検索URLを自動生成してワンクリックで開けるようにする。

### 優先度4：利益率計算機能
Alibaba価格を手動入力すると利益率・粗利を自動計算して表示する機能。

### 優先度5：CSV出力・履歴保存
調査結果のCSVエクスポート機能。

---

## Keepa JPカテゴリIDマスター（調査済み）

| カテゴリ名 | 正しいID |
|-----------|---------|
| スポーツ&アウトドア | 14304371 |
| ドラッグストア | 160384011 |
| パソコン・周辺機器 | 2127209051 |
| 文房具・オフィス用品 | 86731051 |
| 車&バイク | 2017304051 |
| おもちゃ&ゲーム | 13299531 |
| 家電・カメラ | 2277724051 |
| ホーム&キッチン | 3828871 |
| ペット用品 | 2127211051 |
| ベビー&マタニティ | 16333571 |
| ファッション | 352484011 |
| 産業・研究開発用品 | 3445393051 |
| DIY・工具・ガーデン | 2264620051（要確認） |
| ビューティー | 48892051（要確認） |

※IDに「要確認」のものはbatch.pyのテストでベストセラーデータなしエラーが出たため、正しいIDの再調査が必要。

---

## Keepa APIの重要な仕様メモ

- `category_lookup(0, domain='JP')` ではJP全カテゴリは取得できない（32件のみ）
- `category_lookup(特定ID, domain='JP')` は整数で渡す（リスト形式は不可）
- カテゴリ階層の直下に「Arborist Merchandising Root」「カテゴリー別」「Featured Categories」「Self Service」などの内部管理カテゴリが挟まっている場合がある → 自動スキップして孫カテゴリを取得する処理が必要
- Keepaトークンは1分ごとに20トークン生成、最大1,200トークン貯蓄（€49プラン）
- 1日あたり28,800トークン利用可能（20×60×24）
- stats_parsedはネスト構造（"current"/"avg30"/"avg180"等のサブキーを持つ）
- 価格はstats_parsedの値を×100して円換算する必要がある

---

## 現在のapp.pyの主要な実装メモ

### 価格取得ロジック（正しいネスト構造版）
```python
stats_parsed: dict = product.get("stats_parsed") or {}
sp_current = stats_parsed.get("current") or {}
sp_avg30   = stats_parsed.get("avg30")   or {}
sp_avg180  = stats_parsed.get("avg180")  or {}
sp_avg90   = stats_parsed.get("avg90")   or {}

rank_raw = sp_current.get("SALES") or sp_avg30.get("SALES") or sp_avg90.get("SALES")
rank = int(rank_raw) if rank_raw and rank_raw > 0 else None

rc_raw = sp_current.get("COUNT_REVIEWS") or sp_avg30.get("COUNT_REVIEWS")
review_count = int(rc_raw) if rc_raw and rc_raw > 0 else None

price_raw = (
    sp_current.get("NEW") or sp_current.get("AMAZON") or
    sp_avg30.get("NEW")   or sp_avg30.get("AMAZON")   or
    sp_avg180.get("NEW")  or sp_avg180.get("AMAZON")
)
price_jpy = round(price_raw * 100) if price_raw and price_raw > 0 else None
```

### SQLiteテーブル構成
- `products`：バッチ取得した商品データ
- `batch_state`：バッチ進捗（last_index）
- `favorite_categories`：お気に入りカテゴリ（category_id UNIQUE, category_name, added_at）

### お気に入りAPIエンドポイント
- `GET /api/favorites`：一覧取得
- `POST /api/favorites`：登録（body: {category_id, category_name}）
- `DELETE /api/favorites/<category_id>`：解除

---

## Claude Codeの使い方

### 起動方法
```powershell
cd C:\amazon_research
claude
```

### Flaskアプリの起動方法
別のPowerShellウィンドウで：
```powershell
cd C:\amazon_research
python app.py
```
ブラウザで `http://localhost:5000` を開く。

### batch.pyの手動実行
別のPowerShellウィンドウで：
```powershell
cd C:\amazon_research
python batch.py
```

### 長いプロンプトの貼り付け方
PowerShellへの直接貼り付けが失敗する場合は、
メモ帳で `C:\amazon_research\prompt.txt` に内容を保存してから
Claude Codeで以下を実行する：
```
C:\amazon_research\prompt.txtの内容を読んで、その通りに実装してください。
```

---

## 次回チャット開始時の注意

1. このMDファイルを読み込んで状況を把握してから作業開始
2. `C:\amazon_research` フォルダの各ファイルの最新状態はローカルにある（MDより実ファイルが正）
3. **batch.pyの方針（案D推奨）を確認してから実装に入ること**
4. 優先度1のbatch.py修正から始めること
5. コード修正はすべてClaude Codeプロンプト形式で提示し、コミットも必ずプロンプト内に含めること
6. 変更を加えたファイル（app.py、index.html、batch.py）と本MDファイルをプロジェクトナレッジにアップロードすること
