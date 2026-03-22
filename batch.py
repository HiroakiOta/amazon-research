import sqlite3, os, math
from datetime import datetime
import config, keepa

DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")

BATCH_SIZE = 10

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
    """SQLiteのfavorite_categoriesテーブルからカテゴリ一覧を取得する"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT category_id, category_name FROM favorite_categories ORDER BY added_at")
        rows = cur.fetchall()
        conn.close()
        return [{"id": r[0], "name": r[1]} for r in rows]
    except Exception:
        return []

def expand_to_leaf_categories(api, category_id, category_name, depth=0):
    """
    指定カテゴリを末端カテゴリまで再帰展開して返す。
    末端カテゴリ（has_children=False）のリストを返す。
    depth上限=4（無限ループ防止）
    """
    if depth > 4:
        return [{"id": category_id, "name": category_name}]

    try:
        result = api.category_lookup(int(category_id), domain=config.DOMAIN)
        if not result:
            return [{"id": category_id, "name": category_name}]

        # 対象カテゴリのデータを取得
        cat_data = None
        cat_id_str = str(category_id)
        for k, v in result.items():
            if isinstance(v, dict) and str(v.get("catId", "")) == cat_id_str:
                cat_data = v
                break
        if not cat_data:
            return [{"id": category_id, "name": category_name}]

        children = cat_data.get("children", [])
        if not children:
            # 末端カテゴリ
            return [{"id": category_id, "name": category_name}]

        # 内部管理カテゴリをスキップして子カテゴリを展開
        leaf_cats = []
        for child_id in children[:50]:
            child_data = result.get(str(child_id)) or result.get(child_id)
            if not child_data:
                try:
                    child_result = api.category_lookup(int(child_id), domain=config.DOMAIN)
                    child_data = child_result.get(str(child_id)) or child_result.get(child_id)
                except Exception:
                    pass

            if not child_data:
                leaf_cats.append({"id": str(child_id), "name": f"カテゴリ {child_id}"})
                continue

            child_name = child_data.get("name", f"カテゴリ {child_id}")

            # 内部管理カテゴリはスキップしてその子に潜る
            if child_name.lower() in _INTERNAL_CAT_NAMES:
                leaf_cats.extend(
                    expand_to_leaf_categories(api, str(child_id), child_name, depth + 1)
                )
                continue

            child_children = child_data.get("children", [])
            if child_children:
                # さらに子がいれば再帰
                leaf_cats.extend(
                    expand_to_leaf_categories(api, str(child_id), child_name, depth + 1)
                )
            else:
                # 末端
                leaf_cats.append({"id": str(child_id), "name": child_name})

        return leaf_cats if leaf_cats else [{"id": category_id, "name": category_name}]

    except Exception as e:
        print(f"  カテゴリ展開エラー ({category_id}): {e}")
        return [{"id": category_id, "name": category_name}]

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
            monthly_revenue = None
            if rank and rank > 0 and price_jpy:
                monthly_revenue = int(2500 / math.sqrt(rank)) * price_jpy
            rows.append((
                cat["id"], cat["name"],
                product.get("asin", ""),
                product.get("title") or "商品名不明",
                build_image_url(product.get("imagesCSV", "")),
                price_jpy, review_count, rank,
                product.get("monthlySold") or 0,
                monthly_revenue,
                f"https://www.amazon.co.jp/dp/{product.get('asin','')}",
                datetime.now().isoformat()
            ))
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM products WHERE category_id=?", (cat["id"],))
        cur.executemany("""INSERT INTO products
            (category_id, category_name, asin, title, image_url,
             price_jpy, review_count, rank, monthly_sold, monthly_revenue,
             amazon_url, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
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

    # お気に入りカテゴリを末端まで展開
    print("カテゴリを末端まで展開中...")
    all_leaf_cats = []
    for fav in favorite_cats:
        print(f"  展開中: {fav['name']} ({fav['id']})")
        leaves = expand_to_leaf_categories(api, fav["id"], fav["name"])
        print(f"    → {len(leaves)}件の末端カテゴリ")
        all_leaf_cats.extend(leaves)

    # 重複除去
    seen = set()
    unique_leaf_cats = []
    for cat in all_leaf_cats:
        if cat["id"] not in seen:
            seen.add(cat["id"])
            unique_leaf_cats.append(cat)

    print(f"\n末端カテゴリ合計: {len(unique_leaf_cats)}件")

    last_index = get_last_index()
    print(f"開始インデックス: {last_index}")

    for i in range(BATCH_SIZE):
        idx = (last_index + i) % len(unique_leaf_cats)
        cat = unique_leaf_cats[idx]
        fetch_and_save(api, cat)

    new_index = (last_index + BATCH_SIZE) % len(unique_leaf_cats)
    save_last_index(new_index)
    print(f"\nバッチ完了。次回開始インデックス: {new_index}")

if __name__ == "__main__":
    main()
