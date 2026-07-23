import os

os.environ["USE_SUPABASE_DB"] = "0"
os.environ["PRODUCT_ONLY_MODE"] = "0"

from backend.pricing_engine import calculate_item_cost
from backend.product_catalog import match_product


def test_multilingual_alias_maps_to_standard_product_code():
    match = match_product("Louver grille with insect screen KT 400x300")
    assert match is not None
    assert match["product_code"] == "LOUVER_WITH_INSECT_SCREEN"
    assert match["category"] == "LOUVER"


def test_manual_price_product_is_blocked_until_sales_confirms_price():
    item = {
        "description": "Ty ren M10",
        "category": "ACCESSORY",
        "product_code": "HANGER_ACCESSORY",
        "product_pricing_method": "manual_or_price_list",
        "quantity": 10,
        "unit": "cai",
    }

    priced = calculate_item_cost(
        item,
        coeffs={},
        pricing={("GI", 0.6): 145000},
        product_rules={},
    )

    assert priced["subtotal"] == 0
    assert priced["price_source_status"] == "needs_manual_price"
    assert priced["can_auto_quote"] is False
