import sqlite3, os, math
from datetime import datetime
import config, keepa

DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")

BATCH_SIZE = 10


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

def main():
    init_db()
    api = keepa.Keepa(config.KEEPA_API_KEY)
    api._timeout = 120

    BATCH_CATEGORIES = get_favorite_categories()
    if not BATCH_CATEGORIES:
        print("お気に入りカテゴリが登録されていません。ブラウザからカテゴリを登録してください。")
        return

    last_index = get_last_index()
    print(f"お気に入りカテゴリ数: {len(BATCH_CATEGORIES)}")
    print(f"開始インデックス: {last_index}")

    for i in range(BATCH_SIZE):
        idx = (last_index + i) % len(BATCH_CATEGORIES)
        cat = BATCH_CATEGORIES[idx]
        print(f"取得中: {cat['name']} ({cat['id']})")
        try:
            asins = api.best_sellers_query(cat["id"], domain=config.DOMAIN)
            if not asins:
                print(f"  ベストセラーなし: {cat['name']}")
                continue
            asins = asins[:100]
            products_raw = api.query(asins, domain=config.DOMAIN,
                                     history=False, rating=True, stats=30)
            rows = []
            for product in products_raw:
                if not product:
                    continue
                stats_parsed = product.get("stats_parsed") or {}
                sp_current = stats_parsed.get("current") or {}
                sp_avg30 = stats_parsed.get("avg30") or {}
                sp_avg180 = stats_parsed.get("avg180") or {}
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
            print(f"  保存完了: {len(rows)}件")
        except Exception as e:
            print(f"  エラー: {e}")

    new_index = (last_index + BATCH_SIZE) % len(BATCH_CATEGORIES)
    save_last_index(new_index)
    print(f"バッチ完了。次回開始インデックス: {new_index}")

if __name__ == "__main__":
    main()
