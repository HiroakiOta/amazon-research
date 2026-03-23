import sqlite3, os, math, json
from datetime import datetime
import config, keepa

DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")

BATCH_SIZE = 2  # 1回のバッチで処理するカテゴリ数（お気に入り数に合わせて調整）

_INTERNAL_CAT_NAMES = {
    "arborist merchandising root",
    "featured categories",
    "self service",
    "カテゴリー別",
}

def build_image_url(images_csv):
    if not images_csv:
        return ""
    first = images_csv.split(",")[0].strip()
    if not first:
        return ""
    if "." not in first:
        first += ".jpg"
    return f"https://m.media-amazon.com/images/I/{first}"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id TEXT, category_name TEXT, asin TEXT, title TEXT,
        image_url TEXT, price_jpy INTEGER, review_count INTEGER,
        rank INTEGER, monthly_sold INTEGER, monthly_revenue INTEGER,
        amazon_url TEXT, fetched_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS batch_state (
        id INTEGER PRIMARY KEY, last_index INTEGER DEFAULT 0, last_run TEXT)""")
    cur.execute("INSERT OR IGNORE INTO batch_state (id, last_index) VALUES (1, 0)")
    cur.execute("""CREATE TABLE IF NOT EXISTS favorite_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id TEXT UNIQUE,
        category_name TEXT,
        added_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS leaf_categories_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_id TEXT,
        category_id TEXT,
        category_name TEXT,
        cached_at TEXT)""")
    conn.commit()
    conn.close()

def get_last_index():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT last_index FROM batch_state WHERE id=1")
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0

def save_last_index(idx):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE batch_state SET last_index=?, last_run=? WHERE id=1",
                (idx, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_favorite_categories():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT category_id, category_name FROM favorite_categories ORDER BY added_at")
        rows = cur.fetchall()
        conn.close()
        return [{"id": r[0], "name": r[1]} for r in rows]
    except Exception:
        return []

def get_child_categories(api, category_id, category_name):
    """1階層だけ子カテゴリを取得して返す。内部管理カテゴリはスキップ。"""
    try:
        result = api.category_lookup(int(category_id), domain=config.DOMAIN)
        cat_data = None
        for v in result.values():
            if isinstance(v, dict) and str(v.get("catId", "")) == str(category_id):
                cat_data = v
                break
        if not cat_data:
            return [{"id": category_id, "name": category_name}]

        children = cat_data.get("children", []) or []
        if not children:
            return [{"id": category_id, "name": category_name}]

        cats = []
        for child_id in children[:50]:
            child_data = result.get(str(child_id)) or result.get(child_id)
            if not child_data:
                cats.append({"id": str(child_id), "name": f"カテゴリ {child_id}"})
                continue
            child_name = child_data.get("name", f"カテゴリ {child_id}")
            if child_name.lower() in _INTERNAL_CAT_NAMES:
                # 内部カテゴリはその子を追加
                for gc_id in (child_data.get("children") or [])[:50]:
                    cats.append({"id": str(gc_id), "name": f"サブカテゴリ {gc_id}"})
            else:
                cats.append({"id": str(child_id), "name": child_name})
        return cats if cats else [{"id": category_id, "name": category_name}]
    except Exception as e:
        print(f"  子カテゴリ取得エラー ({category_id}): {e}")
        return [{"id": category_id, "name": category_name}]

def get_target_categories(api, favorite_cats):
    """
    お気に入りカテゴリからバッチ対象カテゴリ一覧を作成する。
    - best_sellers_query が成功するカテゴリはそのまま使用
    - 失敗する場合のみ1階層展開して子カテゴリを使用
    """
    target_cats = []
    for fav in favorite_cats:
        print(f"  確認中: {fav['name']} ({fav['id']})")
        try:
            asins = api.best_sellers_query(fav["id"], domain=config.DOMAIN)
            if asins:
                print(f"    → 直接取得可能（ASIN {len(asins)}件）")
                target_cats.append(fav)
            else:
                print(f"    → ベストセラーなし。子カテゴリを展開...")
                children = get_child_categories(api, fav["id"], fav["name"])
                print(f"    → 子カテゴリ {len(children)}件")
                target_cats.extend(children)
        except Exception as e:
            print(f"    → エラー: {e}。子カテゴリを展開...")
            children = get_child_categories(api, fav["id"], fav["name"])
            target_cats.extend(children)
    return target_cats

def fetch_and_save(api, cat):
    """1カテゴリのベストセラーを取得してSQLiteに保存する"""
    print(f"  取得中: {cat['name']} ({cat['id']})")
    try:
        asins = api.best_sellers_query(cat["id"], domain=config.DOMAIN)
        if not asins:
            print(f"    ベストセラーなし: {cat['name']}")
            return 0
        asins = asins[:100]
        products_raw = api.query(asins, domain=config.DOMAIN,
                                 history=False, rating=True, stats=30)
        rows = []
        for product in products_raw:
            if not product:
                continue
            stats_parsed = product.get("stats_parsed") or {}
            sp_current = stats_parsed.get("current") or {}
            sp_avg30   = stats_parsed.get("avg30")   or {}
            sp_avg180  = stats_parsed.get("avg180")  or {}
            rank_raw = sp_current.get("SALES") or sp_avg30.get("SALES")
            rank = int(rank_raw) if rank_raw and rank_raw > 0 else None
            rc_raw = sp_current.get("COUNT_REVIEWS") or sp_avg30.get("COUNT_REVIEWS")
            review_count = int(rc_raw) if rc_raw and rc_raw > 0 else None
            price_raw = (sp_current.get("NEW") or sp_current.get("AMAZON") or
                         sp_avg30.get("NEW") or sp_avg30.get("AMAZON") or
                         sp_avg180.get("NEW") or sp_avg180.get("AMAZON"))
            price_jpy = round(price_raw * 100) if price_raw and price_raw > 0 else None
            asin = product.get("asin", "")
            parent_asin = product.get("parentAsin") or asin
            category_tree_json = json.dumps(product.get("categoryTree") or [], ensure_ascii=False)
            monthly_sold = product.get("monthlySold") or 0
            # 月間売上推定: 実売上数が取得できた場合は優先、なければランク推定
            monthly_revenue = None
            if monthly_sold and monthly_sold > 0 and price_jpy:
                monthly_revenue = monthly_sold * price_jpy
            elif rank and rank > 0 and price_jpy:
                monthly_revenue = int(2500 / math.sqrt(rank)) * price_jpy
            rows.append((
                cat["id"], cat["name"],
                asin, parent_asin, category_tree_json,
                product.get("title") or "商品名不明",
                build_image_url(product.get("imagesCSV", "")),
                price_jpy, review_count, rank,
                monthly_sold,
                monthly_revenue,
                f"https://www.amazon.co.jp/dp/{asin}",
                datetime.now().isoformat()
            ))
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM products WHERE category_id=?", (cat["id"],))
        cur.executemany("""INSERT INTO products
            (category_id, category_name, asin, parent_asin, category_tree_json,
             title, image_url, price_jpy, review_count, rank,
             monthly_sold, monthly_revenue, amazon_url, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        conn.commit()
        conn.close()
        print(f"    保存完了: {len(rows)}件")
        return len(rows)
    except Exception as e:
        print(f"    エラー: {e}")
        return 0

def main():
    init_db()
    api = keepa.Keepa(config.KEEPA_API_KEY)
    api._timeout = 120

    favorite_cats = get_favorite_categories()
    if not favorite_cats:
        print("お気に入りカテゴリが登録されていません。ブラウザからカテゴリを登録してください。")
        return

    print("バッチ対象カテゴリを確認中...")
    target_cats = get_target_categories(api, favorite_cats)

    seen = set()
    unique_cats = []
    for cat in target_cats:
        if cat["id"] not in seen:
            seen.add(cat["id"])
            unique_cats.append(cat)

    print(f"\n対象カテゴリ合計: {len(unique_cats)}件")

    last_index = get_last_index()
    print(f"開始インデックス: {last_index}")

    for i in range(BATCH_SIZE):
        idx = (last_index + i) % len(unique_cats)
        cat = unique_cats[idx]
        fetch_and_save(api, cat)

    new_index = (last_index + BATCH_SIZE) % len(unique_cats)
    save_last_index(new_index)
    print(f"\nバッチ完了。次回開始インデックス: {new_index}")

if __name__ == "__main__":
    main()
