import keepa

API_KEY = "ここにKeepaのAPIキーを貼り付ける"

api = keepa.Keepa(API_KEY)
print(f"接続成功！残りトークン: {api.tokens_left}")

# amazon.co.jpのカテゴリ検索テスト
categories = api.search_for_categories("キッチン", domain="JP")
print(f"\nカテゴリ検索結果（最初の3件）:")
for cat_id, cat_info in list(categories.items())[:3]:
    print(f"  ID: {cat_id} / 名前: {cat_info.get('name', '不明')}")