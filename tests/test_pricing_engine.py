from backend.pricing_engine import calculate_duct_area, calculate_item_cost, _kaiyo_quote_area_multiplier


def test_mfd_l250_area_formula():
    assert calculate_duct_area(
        {"category": "MOTORIZED_DAMPER", "description": "Van MFD 3000x2400", "width": 3000, "height": 2400}
    ) == 9.9
    assert calculate_duct_area(
        {"category": "MOTORIZED_DAMPER", "description": "Van MFD 500x500", "width": 500, "height": 500}
    ) == 0.75


def test_mfd_l250_price_formula():
    item = {
        "category": "MOTORIZED_DAMPER",
        "description": "Van MFD 500x500, EI120",
        "width": 500,
        "height": 500,
        "length": 1200,
        "quantity": 2,
        "unit": "cai",
        "material": "GI",
        "thickness": 1.15,
        "warnings": [],
    }
    result = calculate_item_cost(
        item,
        {"vat_pct": {"value": 0, "type": "percentage"}, "profit_pct": {"value": 0, "type": "percentage"}},
        {("GI", 1.15): 0},
        {"MOTORIZED_DAMPER": {"unit_mode": "piece_area", "base_unit_price": 3000000, "length_mm": 250}},
    )
    assert result["formula_length"] == 250
    assert result["formula_area"] == 0.75
    assert result["accessory_unit_price"] == 6000000
    assert result["subtotal"] == 16500000


def test_valve_price_formulas_from_order_file():
    coeffs = {"vat_pct": {"value": 0, "type": "percentage"}, "profit_pct": {"value": 0, "type": "percentage"}}
    pricing = {("GI", 1.15): 0}
    rules = {}

    fd = calculate_item_cost(
        {
            "category": "FIRE_DAMPER",
            "description": "Van FD 900x500",
            "width": 900,
            "height": 500,
            "quantity": 12,
            "unit": "cai",
            "material": "GI",
            "thickness": 1.15,
            "warnings": [],
        },
        coeffs,
        pricing,
        rules,
    )
    assert fd["quote_unit_price"] == 3950000

    backdraft = calculate_item_cost(
        {
            "category": "BACK_DRAFT_DAMPER",
            "description": "Van 1 chieu - boc chong chay 1400x500",
            "width": 1400,
            "height": 500,
            "quantity": 29,
            "unit": "cai",
            "material": "GI",
            "thickness": 1.15,
            "warnings": [],
        },
        coeffs,
        pricing,
        rules,
    )
    assert backdraft["quote_unit_price"] == 1469000

    mfd_kyotech = calculate_item_cost(
        {
            "category": "MOTORIZED_DAMPER",
            "description": "Van MFD 900x500 dong co Kyotech K-FSLS",
            "width": 900,
            "height": 500,
            "quantity": 12,
            "unit": "cai",
            "material": "GI",
            "thickness": 1.15,
            "warnings": [],
        },
        coeffs,
        pricing,
        rules,
    )
    assert mfd_kyotech["quote_unit_price"] == 6250000

    grille = calculate_item_cost(
        {
            "category": "SQUARE_DIFFUSER",
            "description": "Cua gio nan bau duc kt 600x600 + van OBD",
            "width": 600,
            "height": 600,
            "quantity": 136,
            "unit": "cai",
            "material": "AL",
            "thickness": 0,
            "warnings": [],
        },
        coeffs,
        {},
        rules,
    )
    assert grille["quote_unit_price"] == 588540


def test_mfd_fd_prd_price_formulas_from_big_valve_file():
    coeffs = {"vat_pct": {"value": 0, "type": "percentage"}, "profit_pct": {"value": 0, "type": "percentage"}}
    pricing = {("GI", 1.15): 0}

    mfd_ei30 = calculate_item_cost(
        {
            "category": "MOTORIZED_DAMPER",
            "description": "Van chan lua bang motor MFD 1000x550-EI30",
            "width": 1000,
            "height": 550,
            "quantity": 199,
            "unit": "Cai",
            "material": "GI",
            "thickness": 1.15,
            "warnings": [],
        },
        coeffs,
        pricing,
        {},
    )
    assert mfd_ei30["quote_unit_price"] == 8975000

    mfd_ei60_large = calculate_item_cost(
        {
            "category": "MOTORIZED_DAMPER",
            "description": "Van chan lua bang motor MFD 3500x1000, EI60",
            "width": 3500,
            "height": 1000,
            "quantity": 2,
            "unit": "Cai",
            "material": "GI",
            "thickness": 1.15,
            "warnings": [],
        },
        coeffs,
        pricing,
        {},
    )
    assert mfd_ei60_large["quote_unit_price"] == 41250000

    fd_ei30 = calculate_item_cost(
        {
            "category": "FIRE_DAMPER",
            "description": "Van chan lua FD 300x200-EI30",
            "width": 300,
            "height": 200,
            "quantity": 442,
            "unit": "Cai",
            "material": "GI",
            "thickness": 1.15,
            "warnings": [],
        },
        coeffs,
        pricing,
        {},
    )
    assert fd_ei30["quote_unit_price"] == 1330000

    prd = calculate_item_cost(
        {
            "category": "VOLUME_CONTROL_DAMPER",
            "description": "Van Giam ap PRD 600x400",
            "width": 600,
            "height": 400,
            "quantity": 142,
            "unit": "Cai",
            "material": "GI",
            "thickness": 1.15,
            "warnings": [],
        },
        coeffs,
        pricing,
        {},
    )
    assert prd["quote_unit_price"] == 712000


def test_kaiyo_t_multiplier_rules():
    assert _kaiyo_quote_area_multiplier(
        {"category": "SUPPLY_DUCT", "description": "Lắp đặt ống thông gió thường KT 500x400"},
        "t",
        9,
    ) == 1.0
    assert _kaiyo_quote_area_multiplier(
        {"category": "SUPPLY_DUCT", "description": "Lắp đặt ống bù thông gió KT 500x400"},
        "t",
        9,
    ) == 1.2
    assert _kaiyo_quote_area_multiplier(
        {"category": "UNKNOWN", "description": "Chi tiết phụ dùng mã T"},
        "t",
        9,
    ) == 1.3


def test_quote_unit_price_is_pre_tax_line_unit_price():
    result = calculate_item_cost(
        {
            "category": "SUPPLY_DUCT",
            "description": "Ong gio 1000x500xL1000",
            "width": 1000,
            "height": 500,
            "length": 1000,
            "quantity": 2,
            "unit": "cai",
            "material": "GI",
            "thickness": 0.75,
            "warnings": [],
        },
        {
            "vat_pct": {"value": 0.10, "type": "percentage"},
            "profit_pct": {"value": 0.10, "type": "percentage"},
            "labor_rate_pct": {"value": 0, "type": "percentage"},
            "accessory_rate_pct": {"value": 0, "type": "percentage"},
            "installation_rate_pct": {"value": 0, "type": "percentage"},
            "waste_rate_pct": {"value": 0, "type": "percentage"},
        },
        {("GI", 0.75): 100000},
        {},
    )
    assert result["subtotal"] == 600000
    assert result["quote_unit_price"] == 300000
    assert result["grand_total"] == 726000


def test_closest_thickness_price_is_flagged_for_qs_review():
    result = calculate_item_cost(
        {
            "category": "SUPPLY_DUCT",
            "description": "Ong gio 1000x500xL1000",
            "width": 1000,
            "height": 500,
            "length": 1000,
            "quantity": 1,
            "unit": "cai",
            "material": "GI",
            "thickness": 0.8,
            "warnings": [],
        },
        {"vat_pct": {"value": 0, "type": "percentage"}, "profit_pct": {"value": 0, "type": "percentage"}},
        {("GI", 0.75): 100000},
        {},
    )
    assert result["price_source_status"] == "closest_thickness"
    assert any("gần nhất" in warning for warning in result["warnings"])
