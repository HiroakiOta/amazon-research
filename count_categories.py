import keepa
import config

TARGET_CATEGORIES = [
    {"id": "14304371",   "name": "スポーツ&アウトドア"},
    {"id": "2264620051", "name": "DIY・工具・ガーデン"},
    {"id": "48892051",   "name": "ビューティー"},
    {"id": "3828871",    "name": "ホーム&キッチン"},
    {"id": "86731051",   "name": "文房具・オフィス用品"},
    {"id": "13299531",   "name": "おもちゃ"},
    {"id": "3445393051", "name": "産業・研究開発用品"},
    {"id": "2017304051", "name": "車&バイク"},
    {"id": "2127211051", "name": "ペット用品"},
    {"id": "352484011",  "name": "ファッション"},
    {"id": "16333571",   "name": "ベビー&マタニティ"},
    {"id": "3828871",    "name": "ホーム&キッチン"},
]

INTERNAL_CAT_NAMES = {
    "arborist merchandising root",
    "featured categories",
    "self service",
    "カテゴリー別",
}

def count_leaf_nodes(api, cat_id, cat_name, depth=0):
    try:
        result = api.category_lookup(int(cat_id), domain=config.DOMAIN)
        data = None
        for v in result.values():
            if isinstance(v, dict) and str(v.get("catId","")) == str(cat_id):
                data = v
                break
        if not data:
            for v in result.values():
                if isinstance(v, dict):
                    data = v
                    break
        if not data:
            return 0
        children = data.get("children") or []
        if not children:
            return 1
        total = 0
        for child_id in children:
            child_result = api.category_lookup(int(child_id), domain=config.DOMAIN)
            child_data = None
            for v in child_result.values():
                if isinstance(v, dict):
                    child_data = v
                    break
            if not child_data:
                total += 1
                continue
            child_name = child_data.get("name","")
            if child_name.lower() in INTERNAL_CAT_NAMES:
                grandchildren = child_data.get("children") or []
                for gc_id in grandchildren:
                    total += count_leaf_nodes(api, gc_id, "", depth+1)
            else:
                total += count_leaf_nodes(api, child_id, child_name, depth+1)
        return total
    except Exception as e:
        print(f"  エラー (ID:{cat_id}): {e}")
        return 0

api = keepa.Keepa(config.KEEPA_API_KEY)
api._timeout = 120

grand_total = 0
for cat in TARGET_CATEGORIES:
    print(f"\n{cat['name']} のカウント中...")
    count = count_leaf_nodes(api, cat["id"], cat["name"])
    print(f"  → 末端カテゴリ数: {count}")
    grand_total += count

print(f"\n============================")
print(f"合計末端カテゴリ数: {grand_total}")
print(f"必要トークン概算: {grand_total * 101}")
print(f"1日285カテゴリで {grand_total // 285 + 1} 日で1周")
print(f"============================")
