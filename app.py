import math
import traceback
import sqlite3
import json
import os
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, request

import config
import keepa

DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")

app = Flask(__name__)

# Amazon.co.jp のルートカテゴリ（ノードID）
# category_lookup(0, domain="JP") で取得可能だが、トークン節約のためハードコード
ROOT_CATEGORIES_JP = [
    {"id": "2277721051", "name": "食品・飲料・お酒"},
    {"id": "3210981",    "name": "エレクトロニクス"},
    {"id": "3828871",    "name": "ホーム&キッチン"},
    {"id": "13299531",   "name": "おもちゃ&ゲーム"},
    {"id": "52374051",   "name": "ビューティー"},
    {"id": "160384011",  "name": "ドラッグストア"},
    {"id": "14304371",   "name": "スポーツ&アウトドア"},
    {"id": "344845011",  "name": "ベビー&マタニティ"},
    {"id": "2229202051", "name": "ファッション"},
    {"id": "2127209051", "name": "パソコン・周辺機器"},
    {"id": "3210981",    "name": "カメラ"},
    {"id": "465392",     "name": "本・コミック・雑誌"},
    {"id": "637394",     "name": "TVゲーム"},
    {"id": "86731051",   "name": "文房具・オフィス用品"},
    {"id": "2017304051", "name": "車&バイク"},
    {"id": "2127212051", "name": "ペット用品"},
    {"id": "2016929051", "name": "DIY・工具・ガーデン"},
    {"id": "561956",     "name": "ミュージック"},
    {"id": "561958",     "name": "DVD・ブルーレイ"},
    {"id": "3445393051", "name": "産業・研究開発用品"},
]

# 大カテゴリのID一覧（クリック時は商品取得せずサブカテゴリ表示）
ROOT_CATEGORY_IDS = {
    "2277721051", "3210981", "3828871", "13299531", "52374051",
    "160384011", "14304371", "344845011", "2229202051", "2127209051",
    "465392", "637394", "86731051", "2017304051",
    "2127212051", "2016929051", "561956", "561958", "3445393051",
}

_api_instance = None


def init_db():
    """SQLiteデータベースの初期化（テーブルがなければ作成）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id TEXT, category_name TEXT, asin TEXT, parent_asin TEXT, title TEXT,
        image_url TEXT, price_jpy INTEGER, review_count INTEGER,
        rank INTEGER, monthly_sold INTEGER, monthly_revenue INTEGER,
        amazon_url TEXT, fetched_at TEXT)""")
    # 既存テーブルへの列追加（初回のみ実行、エラーは無視）
    for col in ["parent_asin TEXT", "category_tree_json TEXT", "leaf_rank INTEGER"]:
        try:
            cur.execute(f"ALTER TABLE products ADD COLUMN {col}")
        except Exception:
            pass
    cur.execute("""CREATE TABLE IF NOT EXISTS batch_state (
        id INTEGER PRIMARY KEY, last_index INTEGER DEFAULT 0, last_run TEXT)""")
    cur.execute("INSERT OR IGNORE INTO batch_state (id, last_index) VALUES (1, 0)")
    cur.execute("""CREATE TABLE IF NOT EXISTS favorite_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id TEXT UNIQUE,
        category_name TEXT,
        added_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS category_lookup_cache (
        category_id TEXT PRIMARY KEY,
        result_json TEXT,
        cached_at TEXT)""")

    # デフォルトお気に入り：テーブルが空の場合のみ挿入
    cur.execute("SELECT COUNT(*) FROM favorite_categories")
    if cur.fetchone()[0] == 0:
        default_categories = [
            ("14304371",   "スポーツ&アウトドア"),
            ("2016929051", "DIY・工具・ガーデン"),
        ]
        now = datetime.now().isoformat()
        for cat_id, cat_name in default_categories:
            cur.execute("""INSERT OR IGNORE INTO favorite_categories
                           (category_id, category_name, added_at) VALUES (?, ?, ?)""",
                        (cat_id, cat_name, now))

    conn.commit()
    conn.close()


def get_api() -> keepa.Keepa:
    global _api_instance
    if _api_instance is None:
        _api_instance = keepa.Keepa(config.KEEPA_API_KEY)
        _api_instance._timeout = 120
    return _api_instance


def build_image_url(images_csv: str) -> str:
    """Keepa の imagesCSV から Amazon 画像 URL を生成"""
    if not images_csv:
        return ""
    first = images_csv.split(",")[0].strip()
    if not first:
        return ""
    # Keepa は拡張子なしのIDを返す場合がある
    if "." not in first:
        first += ".jpg"
    return f"https://m.media-amazon.com/images/I/{first}"


def _find_in_result(result: dict, category_id) -> dict | None:
    """category_lookup の結果から指定IDのカテゴリデータを探す (キー型に依存しない)"""
    cat_id_str = str(category_id)
    cat_id_int = int(category_id)
    if cat_id_str in result:
        return result[cat_id_str]
    if cat_id_int in result:
        return result[cat_id_int]
    # catId フィールドで検索（キーが想定外の場合のフォールバック）
    for v in result.values():
        if isinstance(v, dict) and str(v.get("catId", "")) == cat_id_str:
            return v
    return None


_cat_lookup_cache: dict = {}      # L1: インメモリキャッシュ（category_id → result）

CAT_CACHE_TTL_DAYS = 7            # SQLiteキャッシュの有効期間


def _cached_category_lookup(category_id: int) -> dict:
    """category_lookup の結果を L1（メモリ）→ L2（SQLite）→ API の順で取得・キャッシュする。"""
    key = str(category_id)

    # L1: メモリキャッシュ
    if key in _cat_lookup_cache:
        return _cat_lookup_cache[key]

    # L2: SQLiteキャッシュ
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT result_json, cached_at FROM category_lookup_cache WHERE category_id=?", (key,))
        row = cur.fetchone()
        conn.close()
        if row:
            cached_at = datetime.fromisoformat(row[1])
            if datetime.now() - cached_at < timedelta(days=CAT_CACHE_TTL_DAYS):
                result = json.loads(row[0])
                _cat_lookup_cache[key] = result
                return result
    except Exception:
        pass

    # API呼び出し
    result = get_api().category_lookup(category_id, domain=config.DOMAIN)
    _cat_lookup_cache[key] = result

    # SQLiteに保存
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""INSERT OR REPLACE INTO category_lookup_cache (category_id, result_json, cached_at)
                       VALUES (?, ?, ?)""", (key, json.dumps(result), datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception:
        pass

    return result


def _get_root_result() -> dict:
    """category_lookup(0, domain=JP) をキャッシュして返す。"""
    return _cached_category_lookup(0)


# 内部管理用カテゴリ名（これに該当する場合は子階層に自動的に潜る）
_INTERNAL_CAT_NAMES = {
    "arborist merchandising root",
    "featured categories",
    "self service",
    "カテゴリー別",
}

def fetch_subcategories(category_id: str) -> list[dict]:
    """指定カテゴリのサブカテゴリ一覧を返す。エラー時は空リスト。

    親のlookup結果に子データが含まれていれば再利用し（APIコール節約）、
    含まれていない子のみ個別lookupする。結果はインメモリキャッシュに保存。
    内部管理用カテゴリ（Arborist Merchandising Root 等）は自動的にスキップして
    その子階層に潜る。
    """
    def _resolve_cat(cat_id, parent_result: dict):
        """parent_result から cat_id のデータを探し、なければキャッシュlookupする。"""
        data = _find_in_result(parent_result, cat_id)
        if not data:
            try:
                result = _cached_category_lookup(int(cat_id))
                data = _find_in_result(result, cat_id)
            except Exception:
                pass
        return data

    try:
        # 親カテゴリを lookup（キャッシュ利用）
        parent_result = _cached_category_lookup(int(category_id))
        parent_data = _find_in_result(parent_result, category_id)

        if not parent_data:
            return []

        children_ids: list = parent_data.get("children", [])
        if not children_ids:
            return []

        categories = []
        for child_id in children_ids[:30]:
            child_data = _resolve_cat(child_id, parent_result)
            child_name = (child_data.get("name") if child_data else None) or f"サブカテゴリ {child_id}"
            child_children_ids = (child_data.get("children") or []) if child_data else []
            child_has_children = len(child_children_ids) > 0

            # 内部管理用カテゴリの場合は子階層に潜る（追加lookupは1回のみ）
            if child_name.lower() in _INTERNAL_CAT_NAMES:
                child_result = _cached_category_lookup(int(child_id)) if child_data is None else parent_result
                for grandchild_id in child_children_ids[:30]:
                    gc_data = _resolve_cat(grandchild_id, child_result)
                    gc_name = (gc_data.get("name") if gc_data else None) or f"サブカテゴリ {grandchild_id}"
                    gc_has_children = len((gc_data.get("children") or []) if gc_data else []) > 0
                    categories.append({
                        "id": str(grandchild_id),
                        "name": gc_name,
                        "has_children": gc_has_children,
                    })
            else:
                categories.append({
                    "id": str(child_id),
                    "name": child_name,
                    "has_children": child_has_children,
                })

        return categories
    except Exception as e:
        import traceback as tb
        print(f"fetch_subcategories ERROR: {e}")
        print(tb.format_exc())
        return []


def safe_get(lst, idx, default=None):
    """リストから安全に値を取得し、0 や負値は None として扱う"""
    try:
        v = lst[idx]
        if hasattr(v, "item"):  # numpy scalar → Python int/float
            v = v.item()
        if v is None or v < 0:
            return default
        return v
    except (IndexError, TypeError):
        return default


PARENT_REVENUE_CTE = """
    WITH parent_revenue AS (
        SELECT COALESCE(parent_asin, asin) AS p_asin,
               SUM(monthly_revenue) AS total_revenue
        FROM products
        GROUP BY p_asin
    )
"""

def estimate_monthly_revenue(monthly_sold: int, price_jpy: float, rank: int) -> float | None:
    """
    月間売上推定 (円)
    - monthly_sold が取得できた場合: 販売数 × 価格
    - 取得できない場合: ランクから粗推定 (Amazon.co.jp 向け経験則)
    """
    if monthly_sold and monthly_sold > 0 and price_jpy:
        return monthly_sold * price_jpy
    if rank and rank > 0 and price_jpy:
        # 粗推定: rank が低いほど売れる、sqrt で減衰
        estimated_units = max(1, int(2500 / math.sqrt(rank)))
        return estimated_units * price_jpy
    return None


# ---------------------------------------------------------------------------
# ルート
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/tokens")
def get_tokens():
    """残りトークン数を返す"""
    try:
        api = get_api()
        api.update_status()
        return jsonify({"tokens_left": api.tokens_left})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/categories")
def get_categories():
    """
    カテゴリ一覧を返す。
    parent_id=0 または未指定 → ルートカテゴリ (ハードコード)
    parent_id=<node_id>     → Keepa API でサブカテゴリを取得
    """
    parent_id = request.args.get("parent_id", "0")

    if parent_id == "0":
        # API からルートカテゴリを取得（キャッシュ済みなら無料）
        # 失敗時はハードコードリストにフォールバック
        try:
            root_result = _get_root_result()
            api_cats = []
            for v in root_result.values():
                if isinstance(v, dict) and v.get("parent") == 0:
                    api_cats.append({
                        "id": str(v.get("catId", "")),
                        "name": v.get("name", ""),
                        "has_children": len(v.get("children", [])) > 0,
                    })
            # API から取得できた場合のみ使用（空なら fallback）
            if api_cats:
                return jsonify({"categories": api_cats})
        except Exception:
            pass
        return jsonify({"categories": ROOT_CATEGORIES_JP})

    try:
        categories = fetch_subcategories(parent_id)
        return jsonify({"categories": categories})
    except Exception as e:
        return jsonify({"error": str(e), "categories": []}), 500


@app.route("/api/products", methods=["POST"])
def get_products():
    """
    指定カテゴリのベストセラー上位100件を取得し、条件でフィルタリングして返す。
    フィルタ条件:
      - レビュー数 300件以下
      - 月間売上推定 100万円以下
    優先表示: ランキング 51〜100位
    """
    body = request.get_json(force=True)
    category_id: str = str(body.get("category_id", ""))
    category_name: str = body.get("category_name", "")
    min_revenue: int = int(body.get("min_revenue", config.MIN_MONTHLY_REVENUE))
    max_revenue: int = int(body.get("max_revenue", config.MAX_MONTHLY_REVENUE))
    max_reviews = body.get("max_reviews")  # None = 上限なし

    if not category_id or category_id == "0":
        return jsonify({"error": "category_id が指定されていません"}), 400

    init_db()

    # SQLiteキャッシュ確認（batch.pyで取得済みなら即座に返す）
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT * FROM products WHERE category_id=? LIMIT 1", (category_id,))
        cached = cur.fetchone()
        conn.close()
        if cached:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            # 親ASIN単位の売上合算でフィルタ
            cur.execute(PARENT_REVENUE_CTE + """
                SELECT p.asin, p.title, p.image_url, p.price_jpy, p.review_count,
                       p.rank, p.monthly_sold, p.monthly_revenue, p.amazon_url,
                       COALESCE(p.parent_asin, p.asin) AS p_asin, pr.total_revenue
                FROM products p
                JOIN parent_revenue pr ON COALESCE(p.parent_asin, p.asin) = pr.p_asin
                WHERE p.category_id=?
            """, (category_id,))
            rows = cur.fetchall()
            conn.close()
            all_products = []
            for row in rows:
                all_products.append({
                    "asin": row[0], "title": row[1], "image_url": row[2],
                    "price_jpy": row[3], "review_count": row[4],
                    "rank": row[5], "monthly_sold": row[6],
                    "monthly_revenue": row[7], "amazon_url": row[8],
                    "parent_asin": row[9], "parent_revenue": row[10],
                })
            results = [p for p in all_products
                       if p["parent_revenue"] is not None
                       and p["parent_revenue"] >= min_revenue
                       and p["parent_revenue"] <= max_revenue
                       and (max_reviews is None or p["review_count"] is None or p["review_count"] <= max_reviews)]
            results.sort(key=lambda x: x["rank"] or 9999)
            return jsonify({
                "products": results,
                "all_products": all_products,
                "total": len(results),
                "total_fetched": len(all_products),
                "filtered_count": len(all_products) - len(results),
                "category_name": category_name,
                "from_cache": True,
            })
    except Exception:
        pass

    # 大カテゴリの場合はサブカテゴリ一覧を返す
    if category_id in ROOT_CATEGORY_IDS:
        subcategories = fetch_subcategories(category_id)
        return jsonify({
            "products": [],
            "is_root_category": True,
            "subcategories": subcategories,
            "category_name": category_name,
            "total": 0,
        })

    try:
        api = get_api()

        # Step 1: ベストセラー ASIN リストを取得
        asins: list[str] = api.best_sellers_query(
            category_id,
            domain=config.DOMAIN,
        )
        if not asins:
            return jsonify({
                "products": [],
                "message": "このカテゴリにはベストセラー情報がありません",
                "total": 0,
            })

        asins = asins[:config.BESTSELLER_LIMIT]

        # Step 2: 商品詳細を取得
        products_raw: list[dict] = api.query(
            asins,
            domain=config.DOMAIN,
            history=False,   # 価格履歴は不要
            rating=True,     # レビュー数・評価を含める
            stats=30,        # 30日平均統計
        )

        results = []
        all_products = []
        filtered_count = 0

        for idx, product in enumerate(products_raw):
            if not product:
                continue

            asin = product.get("asin", "")
            title = product.get("title") or "商品名不明"
            images_csv = product.get("imagesCSV", "")
            monthly_sold = product.get("monthlySold") or 0

            # --- 価格・ランク・レビュー数を取得 ---
            stats_parsed: dict = product.get("stats_parsed") or {}

            # stats_parsed はネスト構造: stats_parsed["current"], ["avg30"] など
            sp_current = stats_parsed.get("current") or {}
            sp_avg30   = stats_parsed.get("avg30")   or {}
            sp_avg180  = stats_parsed.get("avg180")  or {}
            sp_avg90   = stats_parsed.get("avg90")   or {}

            # ランク: current → avg30 → avg90 の順で取得
            rank_raw = sp_current.get("SALES") or sp_avg30.get("SALES") or sp_avg90.get("SALES")
            rank: int | None = int(rank_raw) if rank_raw and rank_raw > 0 else None

            # レビュー数: current → avg30 の順で取得
            rc_raw = sp_current.get("COUNT_REVIEWS") or sp_avg30.get("COUNT_REVIEWS")
            review_count: int | None = int(rc_raw) if rc_raw and rc_raw > 0 else None

            # 価格: current → avg30 → avg180 の順で取得し × 100 して円換算
            price_raw = (
                sp_current.get("NEW") or sp_current.get("AMAZON") or
                sp_avg30.get("NEW")   or sp_avg30.get("AMAZON")   or
                sp_avg180.get("NEW")  or sp_avg180.get("AMAZON")
            )
            price_jpy: float | None = round(price_raw * 100) if price_raw and price_raw > 0 else None

            # ベストセラーリスト上の順位 (1-based)
            bestseller_rank = idx + 1

            # 月間売上推定
            monthly_revenue = estimate_monthly_revenue(monthly_sold, price_jpy, rank)

            # 共通フィールドを組み立て
            product_dict = {
                "asin": asin,
                "title": title,
                "image_url": build_image_url(images_csv),
                "price_jpy": int(price_jpy) if price_jpy is not None else None,
                "review_count": int(review_count) if review_count is not None else None,
                "rank": int(rank) if rank is not None else None,
                "bestseller_rank": bestseller_rank,
                "monthly_sold": int(monthly_sold),
                "monthly_revenue": int(monthly_revenue) if monthly_revenue is not None else None,
                "amazon_url": f"https://www.amazon.co.jp/dp/{asin}",
            }

            # フィルタ前の全商品リストに追加
            all_products.append(product_dict)

            # --- フィルタリング ---
            # 月間売上が計算不可の商品は除外
            if monthly_revenue is None:
                filtered_count += 1
                continue

            if config.MAX_REVIEW_COUNT is not None and review_count is not None and review_count > config.MAX_REVIEW_COUNT:
                filtered_count += 1
                continue

            if monthly_revenue > config.MAX_MONTHLY_REVENUE:
                filtered_count += 1
                continue

            if monthly_revenue < config.MIN_MONTHLY_REVENUE:
                filtered_count += 1
                continue

            results.append({
                "asin": asin,
                "title": title,
                "image_url": build_image_url(images_csv),
                "price_jpy": int(price_jpy) if price_jpy is not None else None,
                "review_count": int(review_count) if review_count is not None else None,
                "rank": int(rank) if rank is not None else None,
                "bestseller_rank": bestseller_rank,
                "monthly_sold": int(monthly_sold),
                "monthly_revenue": int(monthly_revenue),
                "amazon_url": f"https://www.amazon.co.jp/dp/{asin}",
            })

        # ランク昇順
        results.sort(key=lambda x: x["rank"] or 9999)

        return jsonify({
            "products": results,
            "all_products": results,
            "total": len(results),
            "total_fetched": len(products_raw),
            "filtered_count": filtered_count,
            "category_name": category_name,
            "from_cache": False,
        })

    except RuntimeError as e:
        # best_sellers_query でリストが存在しない場合 → サブカテゴリを案内
        subcategories = fetch_subcategories(category_id)
        return jsonify({
            "products": [],
            "message": str(e),
            "total": 0,
            "subcategories": subcategories,
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc(),
        }), 500


@app.route("/api/debug/category/<category_id>")
def debug_category(category_id):
    """category_lookup の生レスポンスを確認するデバッグ用エンドポイント"""
    try:
        # category_lookup(0) でルートカテゴリ全体を取得
        root_result = _get_root_result()

        key_types = list(set(type(k).__name__ for k in root_result.keys()))
        entries = {}
        for k, v in list(root_result.items())[:8]:
            entries[str(k)] = {
                "catId": v.get("catId"),
                "name": v.get("name"),
                "parent": v.get("parent"),
                "children_count": len(v.get("children", [])),
                "children_first5": v.get("children", [])[:5],
            }

        found = _find_in_result(root_result, category_id)
        fetch_result = fetch_subcategories(category_id)
        return jsonify({
            "category_id_requested": category_id,
            "root_result_key_count": len(root_result),
            "root_result_key_types": key_types,
            "find_in_root": {
                "found": found is not None,
                "catId": found.get("catId") if found else None,
                "name": found.get("name") if found else None,
                "children_count": len(found.get("children", [])) if found else 0,
                "children_first5": found.get("children", [])[:5] if found else [],
            },
            "fetch_subcategories_result": fetch_result,
            "root_entries_sample": entries,
        })
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()})


@app.route("/api/debug/direct_lookup/<category_id>")
def debug_direct_lookup(category_id):
    """category_lookup を直接呼んだ結果を確認するデバッグ用エンドポイント"""
    try:
        api = get_api()
        result = api.category_lookup([int(category_id)], domain=config.DOMAIN)
        key_count = len(result) if result else 0
        keys_sample = [str(k) for k in list(result.keys())[:10]] if result else []
        found = _find_in_result(result, category_id)
        return jsonify({
            "category_id": category_id,
            "result_key_count": key_count,
            "result_keys_sample": keys_sample,
            "target_found": found is not None,
            "target_data": {
                "catId": found.get("catId") if found else None,
                "name": found.get("name") if found else None,
                "children_count": len(found.get("children", [])) if found else 0,
                "children_first5": found.get("children", [])[:5] if found else [],
            },
            "fetch_subcategories_result": fetch_subcategories(category_id),
        })
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()})


# ---------------------------------------------------------------------------
# お気に入りカテゴリ API
# ---------------------------------------------------------------------------

@app.route("/api/favorites", methods=["GET"])
def get_favorites():
    """お気に入りカテゴリ一覧を返す"""
    init_db()
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT category_id, category_name, added_at FROM favorite_categories ORDER BY added_at DESC")
        rows = cur.fetchall()
        conn.close()
        return jsonify({
            "favorites": [{"category_id": r[0], "category_name": r[1], "added_at": r[2]} for r in rows]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/favorites", methods=["POST"])
def add_favorite():
    """お気に入りカテゴリを登録する"""
    init_db()
    body = request.get_json(force=True)
    category_id = str(body.get("category_id", ""))
    category_name = str(body.get("category_name", ""))
    if not category_id:
        return jsonify({"error": "category_id が必要です"}), 400
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""INSERT OR REPLACE INTO favorite_categories
                       (category_id, category_name, added_at) VALUES (?, ?, ?)""",
                    (category_id, category_name, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "category_id": category_id, "category_name": category_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/export/csv", methods=["GET"])
def export_csv():
    """フィルタ済み全商品をCSV形式でダウンロードする。
    クエリパラメータ: min_revenue, max_revenue, min_price
    """
    import io, csv
    from flask import Response

    init_db()
    min_revenue = int(request.args.get("min_revenue", config.MIN_MONTHLY_REVENUE))
    max_revenue = int(request.args.get("max_revenue", config.MAX_MONTHLY_REVENUE))
    min_price   = int(request.args.get("min_price", 0))

    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(PARENT_REVENUE_CTE + """
            SELECT p.category_name, p.category_tree_json,
                   p.asin, COALESCE(p.parent_asin, p.asin), p.title,
                   p.price_jpy, p.review_count, p.rank, p.leaf_rank,
                   p.monthly_sold, p.monthly_revenue, pr.total_revenue,
                   p.amazon_url, p.fetched_at
            FROM products p
            JOIN parent_revenue pr ON COALESCE(p.parent_asin, p.asin) = pr.p_asin
            WHERE pr.total_revenue BETWEEN ? AND ?
              AND (? = 0 OR p.price_jpy IS NULL OR p.price_jpy >= ?)
            ORDER BY p.category_name, pr.total_revenue DESC
        """, (min_revenue, max_revenue, min_price, min_price))
        rows = cur.fetchall()
        conn.close()

        if config.MAX_REVIEW_COUNT is not None:
            rows = [r for r in rows if r[6] is None or r[6] <= config.MAX_REVIEW_COUNT]

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    def parse_tree(tree_json, root_name):
        """categoryTree JSON からカテゴリパスと末端カテゴリ名を返す。"""
        if not tree_json:
            return root_name, root_name
        try:
            tree = json.loads(tree_json)
            names = [node.get("name", "") for node in tree if node.get("name")]
            if not names:
                return root_name, root_name
            return " > ".join(names), names[-1]
        except Exception:
            return root_name, root_name

    output = io.StringIO()
    writer = csv.writer(output)
    output.write("\ufeff")
    writer.writerow(["大カテゴリ", "カテゴリパス", "末端カテゴリ",
                     "ASIN", "親ASIN", "商品名", "価格(円)", "レビュー数",
                     "大カテゴリランク", "末端カテゴリランク",
                     "月間販売数", "月間売上推定(円)", "親ASIN合算売上(円)",
                     "AmazonURL", "取得日時"])
    for r in rows:
        root_name = r[0]
        tree_json  = r[1]
        cat_path, leaf_cat = parse_tree(tree_json, root_name)
        # r: category_name, tree_json, asin, parent_asin, title,
        #    price_jpy, review_count, rank, leaf_rank,
        #    monthly_sold, monthly_revenue, total_revenue, amazon_url, fetched_at
        writer.writerow([root_name, cat_path, leaf_cat,
                         r[2], r[3], r[4], r[5], r[6],
                         r[7], r[8],
                         r[9], r[10], r[11], r[12], r[13]])

    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"amazon_research_{today}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    )


@app.route("/api/favorites/<category_id>", methods=["DELETE"])
def remove_favorite(category_id):
    """お気に入りカテゴリを解除する"""
    init_db()
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM favorite_categories WHERE category_id=?", (category_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "category_id": category_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
