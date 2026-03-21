import math
import traceback

from flask import Flask, jsonify, render_template, request

import config
import keepa

app = Flask(__name__)

# Amazon.co.jp のルートカテゴリ（ノードID）
# category_lookup(0, domain="JP") で取得可能だが、トークン節約のためハードコード
ROOT_CATEGORIES_JP = [
    {"id": "2277721051", "name": "食品・飲料・お酒"},
    {"id": "3210991",    "name": "エレクトロニクス"},
    {"id": "3828871",    "name": "ホーム&キッチン"},
    {"id": "13299531",   "name": "おもちゃ&ゲーム"},
    {"id": "48892051",   "name": "ビューティー"},
    {"id": "14304371",   "name": "ドラッグストア"},
    {"id": "2016926051", "name": "スポーツ&アウトドア"},
    {"id": "16333571",   "name": "ベビー&マタニティ"},
    {"id": "352484011",  "name": "服&ファッション小物"},
    {"id": "2123629051", "name": "パソコン・周辺機器"},
    {"id": "2127212051", "name": "カメラ"},
    {"id": "465392",     "name": "本・コミック・雑誌"},
    {"id": "2474017051", "name": "TVゲーム"},
    {"id": "86731051",   "name": "文房具・オフィス用品"},
    {"id": "2017304051", "name": "車&バイク"},
    {"id": "2127211051", "name": "ペット用品"},
    {"id": "2264620051", "name": "DIY・工具・ガーデン"},
    {"id": "2016930051", "name": "楽器"},
    {"id": "637764",     "name": "ミュージック"},
    {"id": "561958",     "name": "DVD・ブルーレイ"},
]

_api_instance = None


def get_api() -> keepa.Keepa:
    global _api_instance
    if _api_instance is None:
        _api_instance = keepa.Keepa(config.KEEPA_API_KEY)
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


_root_cat_cache: dict = {}   # category_lookup(0) の結果をキャッシュ


def _get_root_result() -> dict:
    """category_lookup(0, domain=JP) の結果をキャッシュして返す。
    JP ドメインで特定 ID を直接 lookup すると空が返る場合でも
    id=0 ならルートカテゴリ一覧が確実に返る。"""
    global _root_cat_cache
    if not _root_cat_cache:
        _root_cat_cache = get_api().category_lookup(0, domain=config.DOMAIN)
    return _root_cat_cache


def fetch_subcategories(category_id: str) -> list[dict]:
    """指定カテゴリのサブカテゴリ一覧を返す。エラー時は空リスト。

    親カテゴリを lookup して children ID を取得し、
    各子カテゴリも個別に lookup して名前を取得する。
    """
    try:
        api = get_api()

        # 親カテゴリを lookup
        parent_result = api.category_lookup(int(category_id), domain=config.DOMAIN)
        parent_data = _find_in_result(parent_result, category_id)

        if not parent_data:
            return []

        children_ids: list = parent_data.get("children", [])
        if not children_ids:
            return []

        categories = []
        for child_id in children_ids[:30]:
            # 子カテゴリを個別に lookup して名前を取得
            child_name = f"サブカテゴリ {child_id}"
            child_has_children = False
            try:
                child_result = api.category_lookup(int(child_id), domain=config.DOMAIN)
                child_data = _find_in_result(child_result, child_id)
                if child_data:
                    child_name = child_data.get("name", child_name)
                    child_has_children = len(child_data.get("children", [])) > 0
            except Exception:
                pass

            categories.append({
                "id": str(child_id),
                "name": child_name,
                "has_children": child_has_children,
            })

        return categories
    except Exception:
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

    if not category_id or category_id == "0":
        return jsonify({"error": "category_id が指定されていません"}), 400

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
        filtered_count = 0

        for idx, product in enumerate(products_raw):
            if not product:
                continue

            asin = product.get("asin", "")
            title = product.get("title") or "商品名不明"
            images_csv = product.get("imagesCSV", "")
            monthly_sold = product.get("monthlySold") or 0

            # --- 価格・ランク・レビュー数を取得 ---
            # keepa ライブラリは stats=N 指定時に stats_parsed (名前付き辞書) を返す
            # CSV type インデックス: 0=AMAZON価格, 1=NEW価格, 3=SALES, 16=RATING, 17=COUNT_REVIEWS
            # stats_parsed の値: 価格は /100 済み (JPY単位), ランク・レビュー数はそのまま
            stats_parsed: dict = product.get("stats_parsed") or {}

            price_jpy: float | None = stats_parsed.get("NEW") or stats_parsed.get("AMAZON")
            rank: int | None = stats_parsed.get("SALES")
            review_count: int | None = stats_parsed.get("COUNT_REVIEWS")

            # フォールバック①: stats.current (raw 配列) から直接読む
            if price_jpy is None or rank is None or review_count is None:
                stats_raw = product.get("stats") or {}
                current: list = stats_raw.get("current") or []
                if price_jpy is None:
                    raw = safe_get(current, 1) or safe_get(current, 0)
                    price_jpy = raw / 100 if raw else None
                if rank is None:
                    rank = safe_get(current, 3)
                if review_count is None:
                    review_count = safe_get(current, 17)  # 17=COUNT_REVIEWS (16はRATING)

            # フォールバック②: data['COUNT_REVIEWS'] 配列の末尾値
            if review_count is None:
                try:
                    import numpy as np
                    cr = (product.get("data") or {}).get("COUNT_REVIEWS")
                    if cr is not None and len(cr) > 0:
                        valid = cr[cr >= 0]
                        if len(valid) > 0:
                            review_count = int(valid[-1])
                except Exception:
                    pass

            # ベストセラーリスト上の順位 (1-based)
            bestseller_rank = idx + 1

            # 月間売上推定
            monthly_revenue = estimate_monthly_revenue(monthly_sold, price_jpy, rank)

            # --- フィルタリング ---
            reject_reason = []

            if review_count is not None and review_count > config.MAX_REVIEW_COUNT:
                reject_reason.append(f"レビュー数 {review_count} > {config.MAX_REVIEW_COUNT}")

            if monthly_revenue is not None and monthly_revenue > config.MAX_MONTHLY_REVENUE:
                reject_reason.append(
                    f"月間売上推定 ¥{monthly_revenue:,.0f} > ¥{config.MAX_MONTHLY_REVENUE:,}"
                )

            if reject_reason:
                filtered_count += 1
                continue

            # 優先フラグ: ランキング 51〜100位
            priority = (
                rank is not None
                and config.PRIORITY_RANK_MIN <= rank <= config.PRIORITY_RANK_MAX
            )

            results.append({
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
                "priority": priority,
            })

        # 優先 (rank 51-100) を先頭に、次いでランク昇順
        results.sort(key=lambda x: (0 if x["priority"] else 1, x["rank"] or 9999))

        return jsonify({
            "products": results,
            "total": len(results),
            "total_fetched": len(products_raw),
            "filtered_count": filtered_count,
            "category_name": category_name,
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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
