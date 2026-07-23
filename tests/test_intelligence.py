import os

os.environ["USE_SUPABASE_DB"] = "0"
os.environ["PRODUCT_ONLY_MODE"] = "0"

from backend.database import init_db
from backend.intelligence import (
    build_simple_pdf,
    build_audit_report,
    compare_items,
    create_price_list,
    evaluate_quote_readiness,
    get_company_settings,
    has_final_approval,
    list_price_lists,
    save_approval,
    save_quotation_session,
    load_quotation_session,
    upsert_price_list_item,
)


def test_intelligence_smoke():
    init_db()
    price_list_id = create_price_list("Smoke Test Price List", supplier="Test")
    upsert_price_list_item(price_list_id, "GI", 0.75, 123.0)

    price_lists = list_price_lists()
    assert any(row["id"] == price_list_id for row in price_lists)

    items = [
        {
            "mark": "SD-001",
            "description": "Duct 700x400",
            "category": "SUPPLY_DUCT",
            "material": "GI",
            "thickness": 0.75,
            "quantity": 2,
            "unit": "m",
            "calculated_area": 5.28,
            "material_cost": 649.44,
            "grand_total": 1000.0,
        }
    ]
    summary = {"total_area": 5.28, "subtotal": 900.0, "profit": 50.0, "vat": 95.0, "grand_total": 1045.0}
    session_id = save_quotation_session("Smoke Session", items, summary, {}, {("GI", 0.75): 123.0})
    loaded = load_quotation_session(session_id)
    assert loaded["summary"]["grand_total"] == 1045.0

    diff = compare_items(items, [{**items[0], "quantity": 1}])
    assert diff and diff[0]["status"] == "Changed"

    pdf = build_simple_pdf("Smoke Quote", items, summary, signer="Tester")
    assert pdf.startswith(b"%PDF")

    readiness = evaluate_quote_readiness(items, summary, [], {("GI", 0.75): 123.0}, get_company_settings())
    assert readiness["score"] > 0
    approval_id = save_approval("APPROVED", "Smoke Project", "Smoke Customer", "Tester", "OK", readiness["score"])
    assert approval_id
    assert has_final_approval("Smoke Project", "Smoke Customer")
    audit = build_audit_report(items, summary, readiness, get_company_settings())
    assert "HVAC Quotation Audit Report" in audit
