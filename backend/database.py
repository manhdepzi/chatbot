import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hvac_quotation.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    import backend.supabase_store as supabase_store
    if supabase_store.enabled():
        supabase_store.ensure_seed_data()
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Product Categories Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS product_categories (
        normalized_key TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        keywords TEXT NOT NULL -- Comma separated keywords
    )
    """)

    # 2. Materials Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS materials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        description TEXT
    )
    """)

    # 3. Thickness Rules Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS thickness_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        material TEXT NOT NULL,
        min_width REAL NOT NULL,
        max_width REAL NOT NULL,
        thickness REAL NOT NULL,
        FOREIGN KEY (material) REFERENCES materials(name)
    )
    """)

    # 4. Pricing Rules Table (Default prices per thickness / material / unit)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pricing_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        material TEXT NOT NULL,
        thickness REAL NOT NULL,
        unit_price REAL NOT NULL, -- Material price per m2
        FOREIGN KEY (material) REFERENCES materials(name)
    )
    """)

    # 5. Coefficient Rules Table (For labor, installation, profit, etc.)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS coefficient_rules (
        key TEXT PRIMARY KEY,
        value REAL NOT NULL,
        type TEXT NOT NULL, -- 'percentage' or 'fixed_per_m2' or 'fixed_total'
        description TEXT
    )
    """)

    # 6. Mark Prefix Rules Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS mark_rules (
        category TEXT PRIMARY KEY,
        prefix TEXT NOT NULL,
        FOREIGN KEY (category) REFERENCES product_categories(normalized_key)
    )
    """)

    # 7. Engineering Formulas Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS engineering_formulas (
        category TEXT PRIMARY KEY,
        formula_type TEXT NOT NULL, -- e.g., 'rect_duct', 'round_duct', 'reducer', 'elbow', etc.
        formula_expression TEXT,
        FOREIGN KEY (category) REFERENCES product_categories(normalized_key)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS price_lists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        supplier TEXT,
        region TEXT,
        customer TEXT,
        valid_from TEXT,
        valid_to TEXT,
        notes TEXT,
        is_default INTEGER NOT NULL DEFAULT 0
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS price_list_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        price_list_id INTEGER NOT NULL,
        material TEXT NOT NULL,
        thickness REAL NOT NULL,
        unit_price REAL NOT NULL,
        unit TEXT NOT NULL DEFAULT 'm2',
        source TEXT,
        UNIQUE(price_list_id, material, thickness, unit),
        FOREIGN KEY (price_list_id) REFERENCES price_lists(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS product_pricing_rules (
        category TEXT PRIMARY KEY,
        quote_code TEXT,
        unit_mode TEXT NOT NULL DEFAULT 'area',
        material_spec TEXT,
        brand TEXT,
        base_unit_price REAL NOT NULL DEFAULT 0,
        area_multiplier REAL NOT NULL DEFAULT 1,
        length_mm REAL,
        note TEXT,
        source TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS learned_pricing (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signature TEXT NOT NULL,
        category TEXT,
        material TEXT,
        thickness REAL,
        unit TEXT,
        unit_price REAL,
        coefficients_json TEXT,
        source_project TEXT,
        source_customer TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS quote_memory (
        signature TEXT PRIMARY KEY,
        normalized_description TEXT NOT NULL,
        category TEXT,
        width REAL,
        height REAL,
        diameter REAL,
        length REAL,
        unit TEXT,
        quote_unit_price REAL NOT NULL,
        quote_material_spec TEXT,
        quote_brand TEXT,
        quote_note TEXT,
        source_project TEXT,
        source_customer TEXT,
        source_file TEXT,
        trained_count INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)

    _ensure_columns(cursor, "quote_memory", {
        "quote_code": "TEXT",
        "quote_output_quantity": "REAL",
        "quote_output_unit": "TEXT",
        "quote_output_unit_price": "REAL",
        "reference_line_total": "REAL",
        "formula_width": "REAL",
        "formula_height": "REAL",
        "formula_width2": "REAL",
        "formula_height2": "REAL",
        "formula_length": "REAL",
        "formula_radius": "REAL",
        "formula_angle": "REAL",
        "formula_area": "REAL",
        "formula_unit_price": "REAL",
        "area_multiplier": "REAL",
        "accessory_area": "REAL",
        "accessory_unit_price": "REAL",
    })

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS quotation_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        customer TEXT,
        project TEXT,
        source_file TEXT,
        price_list_id INTEGER,
        items_json TEXT NOT NULL,
        summary_json TEXT NOT NULL,
        coefficients_json TEXT,
        price_overrides_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS company_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        description TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS product_master (
        product_code TEXT PRIMARY KEY,
        display_name_vi TEXT NOT NULL,
        category TEXT NOT NULL,
        pricing_method TEXT NOT NULL,
        required_fields TEXT NOT NULL DEFAULT '',
        default_unit TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS product_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alias_text TEXT NOT NULL,
        normalized_alias TEXT NOT NULL,
        product_code TEXT NOT NULL,
        language TEXT,
        confidence REAL NOT NULL DEFAULT 100,
        status TEXT NOT NULL DEFAULT 'approved',
        source TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(normalized_alias, product_code)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sales_product_answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        line_signature TEXT NOT NULL,
        description TEXT NOT NULL,
        product_code TEXT,
        category TEXT,
        unit_price REAL,
        unit TEXT,
        note TEXT,
        approved_by TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS quotation_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        project TEXT,
        customer TEXT,
        status TEXT NOT NULL,
        approver TEXT,
        notes TEXT,
        readiness_score REAL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()

    # Pre-populate tables if empty
    cursor.execute("SELECT COUNT(*) FROM product_categories")
    if cursor.fetchone()[0] == 0:
        # Populate Categories
        categories = [
            ("SUPPLY_DUCT", "Supply Duct", "supply duct,s/a duct,sa duct,ống gió cấp,cấp gió"),
            ("RETURN_DUCT", "Return Duct", "return duct,r/a duct,ra duct,ống gió hồi,hồi gió"),
            ("FRESH_AIR_DUCT", "Fresh Air Duct", "fresh air,f/a duct,fa duct,cấp gió tươi,gió tươi"),
            ("EXHAUST_AIR_DUCT", "Exhaust Air Duct", "exhaust air,e/a duct,ea duct,thải gió,hút thải,hút mùi"),
            ("SMOKE_DUCT", "Smoke Duct", "smoke duct,smoke exhaust,hút khói,thải khói,gió thải khói"),
            ("FIRE_DAMPER", "Fire Damper", "fire damper,fd,van chặn lửa,van dập lửa"),
            ("MOTORIZED_DAMPER", "Motorized Damper", "motorized damper,md,obd điện,van điện"),
            ("VOLUME_CONTROL_DAMPER", "Volume Control Damper", "volume control damper,vcd,van chỉnh lưu lượng"),
            ("BACK_DRAFT_DAMPER", "Back Draft Damper", "back draft damper,bdd,van một chiều"),
            ("FLEXIBLE_DUCT", "Flexible Duct", "flexible duct,ống gió mềm,ống mềm"),
            ("FLEXIBLE_CONNECTOR", "Flexible Connector", "flexible connector,khớp nối mềm,nối mềm"),
            ("REDUCER", "Reducer", "reducer,côn thu,ống thu,côn giảm"),
            ("ELBOW", "Elbow", "elbow,cút,co,góc,cút 90,cút 45"),
            ("TRANSITION", "Transition", "transition,hộp chuyển,gót giày,chuyển đổi"),
            ("TEE", "Tee", "tee,ba ngã,tê,rẽ nhánh,chữ t"),
            ("CROSS", "Cross", "cross,tứ ngã,chữ thập"),
            ("OFFSET", "Offset", "offset,lếch,tránh dầm,côn lếch"),
            ("ACCESS_DOOR", "Access Door", "access door,cửa thăm,cửa quan sát,ad"),
            ("INSPECTION_DOOR", "Inspection Door", "inspection door,id,cửa kiểm tra"),
            ("SILENCER", "Silencer", "silencer,tiêu âm,hộp tiêu âm,ống tiêu âm"),
            ("PLENUM_BOX", "Plenum Box", "plenum box,hộp gió,hộp gom gió,plenum"),
            ("LINEAR_DIFFUSER", "Linear Diffuser", "linear diffuser,miệng gió slot,slot diffuser,mút slot"),
            ("SQUARE_DIFFUSER", "Square Diffuser", "square diffuser,miệng gió vuông,khuyếch tán vuông"),
            ("ROUND_DIFFUSER", "Round Diffuser", "round diffuser,miệng gió tròn,khuyếch tán tròn"),
            ("EGGCRATE_GRILLE", "Eggcrate Grille", "eggcrate grille,miệng gió eggcrate,lưới trứng"),
            ("JET_NOZZLE", "Jet Nozzle", "jet nozzle,miệng gió jet,loa phun"),
            ("LOUVER", "Louver", "louver,cửa chớp,chớp che mưa,outdoor louver"),
            ("UNKNOWN", "Unknown Product", "unknown,khác,chưa rõ")
        ]
        cursor.executemany("INSERT INTO product_categories (normalized_key, display_name, keywords) VALUES (?, ?, ?)", categories)

        # Populate Materials
        materials = [("GI", "Galvanized Iron (Tôn mạ kẽm)"), 
                     ("SS", "Stainless Steel (Inox)"), 
                     ("MS", "Mild Steel (Thép đen)"), 
                     ("PU", "Polyurethane Panel (Duct mút)")]
        cursor.executemany("INSERT INTO materials (name, description) VALUES (?, ?)", materials)

        # Populate Thickness Rules (SMACNA Standard for GI)
        thickness_rules = [
            ("GI", 0, 300, 0.5),
            ("GI", 300, 750, 0.6),
            ("GI", 750, 1200, 0.75),
            ("GI", 1200, 1500, 1.0),
            ("GI", 1500, 99999, 1.2),
            ("SS", 0, 300, 0.5),
            ("SS", 300, 750, 0.6),
            ("SS", 750, 1200, 0.8),
            ("SS", 1200, 99999, 1.0)
        ]
        cursor.executemany("INSERT INTO thickness_rules (material, min_width, max_width, thickness) VALUES (?, ?, ?, ?)", thickness_rules)

        # Populate Pricing Rules (Default per m2)
        pricing_rules = [
            ("GI", 0.5, 12.0),
            ("GI", 0.6, 14.5),
            ("GI", 0.75, 17.5),
            ("GI", 1.0, 22.0),
            ("GI", 1.2, 26.5),
            ("SS", 0.5, 28.0),
            ("SS", 0.6, 32.0),
            ("SS", 0.8, 42.0),
            ("SS", 1.0, 50.0)
        ]
        cursor.executemany("INSERT INTO pricing_rules (material, thickness, unit_price) VALUES (?, ?, ?)", pricing_rules)

        # Populate Mark Prefix Rules
        mark_rules = [
            ("SUPPLY_DUCT", "SD"),
            ("RETURN_DUCT", "RD"),
            ("FRESH_AIR_DUCT", "FA"),
            ("EXHAUST_AIR_DUCT", "EA"),
            ("SMOKE_DUCT", "SMD"),
            ("FIRE_DAMPER", "FD"),
            ("MOTORIZED_DAMPER", "MD"),
            ("VOLUME_CONTROL_DAMPER", "VCD"),
            ("BACK_DRAFT_DAMPER", "BDD"),
            ("FLEXIBLE_DUCT", "FLD"),
            ("FLEXIBLE_CONNECTOR", "FC"),
            ("REDUCER", "RED"),
            ("ELBOW", "ELB"),
            ("TRANSITION", "TRN"),
            ("TEE", "TEE"),
            ("CROSS", "CRS"),
            ("OFFSET", "OFF"),
            ("ACCESS_DOOR", "AD"),
            ("INSPECTION_DOOR", "ID"),
            ("SILENCER", "SIL"),
            ("PLENUM_BOX", "PB"),
            ("LINEAR_DIFFUSER", "LD"),
            ("SQUARE_DIFFUSER", "SDD"),
            ("ROUND_DIFFUSER", "RDD"),
            ("EGGCRATE_GRILLE", "EG"),
            ("JET_NOZZLE", "JN"),
            ("LOUVER", "LVR"),
            ("UNKNOWN", "UNK")
        ]
        cursor.executemany("INSERT INTO mark_rules (category, prefix) VALUES (?, ?)", mark_rules)

        # Populate Engineering Formula Mappings
        formulas = [
            ("SUPPLY_DUCT", "rect_duct"),
            ("RETURN_DUCT", "rect_duct"),
            ("FRESH_AIR_DUCT", "rect_duct"),
            ("EXHAUST_AIR_DUCT", "rect_duct"),
            ("SMOKE_DUCT", "rect_duct"),
            ("REDUCER", "reducer"),
            ("ELBOW", "elbow"),
            ("TRANSITION", "transition"),
            ("TEE", "tee"),
            ("CROSS", "cross"),
            ("OFFSET", "offset"),
            ("FLEXIBLE_DUCT", "flexible_duct"),
            ("SILENCER", "rect_duct"),
            ("PLENUM_BOX", "plenum"),
            ("FIRE_DAMPER", "damper"),
            ("MOTORIZED_DAMPER", "damper"),
            ("VOLUME_CONTROL_DAMPER", "damper"),
            ("BACK_DRAFT_DAMPER", "damper")
        ]
        cursor.executemany("INSERT INTO engineering_formulas (category, formula_type) VALUES (?, ?)", formulas)

        # Populate Default Pricing Coefficients
        coefficients = [
            ("labor_rate_pct", 0.0, "percentage", "Labor cost as % of material cost"),
            ("installation_rate_pct", 0.0, "percentage", "Installation cost as % of material cost"),
            ("accessory_rate_pct", 0.0, "percentage", "Accessory cost as % of material cost"),
            ("painting_rate_m2", 0.0, "fixed_per_m2", "Painting price per m2"),
            ("insulation_rate_m2", 0.0, "fixed_per_m2", "Insulation price per m2"),
            ("waste_rate_pct", 0.0, "percentage", "Material waste allowance"),
            ("transportation_total", 0.0, "fixed_total", "Fixed transportation fee"),
            ("machinery_total", 0.0, "fixed_total", "Fixed machinery wear fee"),
            ("management_fee_pct", 0.0, "percentage", "Project management fee"),
            ("risk_fee_pct", 0.0, "percentage", "Project risk fee"),
            ("profit_pct", 0.0, "percentage", "Net profit margin"),
            ("vat_pct", 0.10, "percentage", "Value Added Tax")
        ]
        cursor.executemany("INSERT INTO coefficient_rules (key, value, type, description) VALUES (?, ?, ?, ?)", coefficients)

    cursor.execute("SELECT COUNT(*) FROM price_lists")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO price_lists (name, supplier, region, notes, is_default)
            VALUES (?, ?, ?, ?, 1)
        """, ("Default HVAC Price List", "Internal", "Default", "Seeded material prices"))
        default_price_list_id = cursor.lastrowid
        cursor.execute("SELECT material, thickness, unit_price FROM pricing_rules")
        seeded_prices = [
            (default_price_list_id, row["material"], row["thickness"], row["unit_price"], "m2", "Seed pricing_rules")
            for row in cursor.fetchall()
        ]
        cursor.executemany("""
            INSERT OR IGNORE INTO price_list_items
            (price_list_id, material, thickness, unit_price, unit, source)
            VALUES (?, ?, ?, ?, ?, ?)
        """, seeded_prices)

    cursor.execute("SELECT COUNT(*) FROM company_settings")
    if cursor.fetchone()[0] == 0:
        settings = [
            ("company_name", "Kaiyo Vietnam", "Company name used in quotation workflow"),
            ("default_brand", "Kaiyo Viet Nam", "Default brand/manufacturer text"),
            ("min_profit_pct", "0.12", "Minimum accepted profit ratio"),
            ("default_vat_pct", "0.10", "Default VAT ratio"),
            ("require_all_prices", "1", "Block export if any detected price is zero"),
            ("require_no_unknown_products", "1", "Block export if unknown products remain"),
            ("require_human_approval", "1", "Require approval record before final issue"),
            ("quote_valid_days", "15", "Default quote validity in days"),
            ("payment_terms", "Theo thoa thuan / As agreed", "Default payment terms"),
            ("delivery_terms", "Theo tien do du an / According to project schedule", "Default delivery terms"),
        ]
        cursor.executemany("""
            INSERT INTO company_settings (key, value, description)
            VALUES (?, ?, ?)
        """, settings)

    cursor.execute("SELECT COUNT(*) FROM product_pricing_rules")
    if cursor.fetchone()[0] == 0:
        product_rules = [
            ("SUPPLY_DUCT", "t", "area", "Ton ma kem theo do day", "Kaiyo Viet Nam", 0, 1.00, None, "", "Editable seed"),
            ("RETURN_DUCT", "t", "area", "Ton ma kem theo do day", "Kaiyo Viet Nam", 0, 1.00, None, "", "Editable seed"),
            ("FRESH_AIR_DUCT", "t", "area", "Ton ma kem theo do day", "Kaiyo Viet Nam", 0, 1.00, None, "", "Editable seed"),
            ("EXHAUST_AIR_DUCT", "t", "area", "Ton ma kem theo do day", "Kaiyo Viet Nam", 0, 1.00, None, "", "Editable seed"),
            ("SMOKE_DUCT", "t", "area", "Ton ma kem + EI", "Kaiyo Viet Nam", 0, 1.25, None, "EI duct multiplier seed", "Editable seed"),
            ("REDUCER", "g", "area", "Ton ma kem theo do day", "Kaiyo Viet Nam", 0, 1.15, 500, "Tam tinh L=500", "Editable seed"),
            ("TRANSITION", "vt", "area", "Ton ma kem theo do day", "Kaiyo Viet Nam", 0, 1.15, 500, "Tam tinh L=500", "Editable seed"),
            ("ELBOW", "cv", "area", "Ton ma kem theo do day", "Kaiyo Viet Nam", 0, 1.35, None, "", "Editable seed"),
            ("TEE", "tt", "area", "Ton ma kem theo do day", "Kaiyo Viet Nam", 0, 1.55, None, "", "Editable seed"),
            ("CROSS", "cr", "area", "Ton ma kem theo do day", "Kaiyo Viet Nam", 0, 1.85, None, "", "Editable seed"),
            ("PLENUM_BOX", "tb", "area", "Ton ma kem theo do day", "Kaiyo Viet Nam", 0, 1.20, 200, "Tam tinh L=200", "Editable seed"),
            ("LOUVER", "c", "piece", "Nhom son tinh dien", "Kaiyo Viet Nam", 350000, 1.00, None, "Can cap nhat theo bang gia NCC", "Editable seed"),
            ("SQUARE_DIFFUSER", "c", "piece", "Nhom son tinh dien", "Kaiyo Viet Nam", 450000, 1.00, None, "Cua/mieng gio seed", "Editable seed"),
            ("LINEAR_DIFFUSER", "ld", "piece", "Nhom son tinh dien", "Kaiyo Viet Nam", 650000, 1.00, None, "Linear diffuser seed", "Editable seed"),
            ("FIRE_DAMPER", "fd", "piece_area", "Thep ma kem + EI", "Kaiyo Viet Nam", 900000, 1.00, 200, "L=200", "Editable seed"),
            ("MOTORIZED_DAMPER", "mfd", "piece_area", "Tam chong chay + motor", "Kaiyo Viet Nam", 3000000, 1.00, 250, "Van MFD, L=250", "Editable seed"),
            ("VOLUME_CONTROL_DAMPER", "vcd", "piece_area", "Thep ma kem", "Kaiyo Viet Nam", 650000, 1.00, 200, "L=200", "Editable seed"),
            ("BACK_DRAFT_DAMPER", "nrd", "piece_area", "Thep ma kem", "Kaiyo Viet Nam", 700000, 1.00, 200, "L=200", "Editable seed"),
            ("FLEXIBLE_CONNECTOR", "cb", "piece", "Co bat", "Kaiyo Viet Nam", 250000, 1.00, 150, "Co bat L=150", "Editable seed"),
        ]
        cursor.executemany("""
            INSERT INTO product_pricing_rules
            (category, quote_code, unit_mode, material_spec, brand, base_unit_price, area_multiplier, length_mm, note, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, product_rules)

    cursor.execute(
        "INSERT OR IGNORE INTO product_categories (normalized_key, display_name, keywords) VALUES (?, ?, ?)",
        ("ACCESSORY", "Accessory", "ty ren,phu kien treo,phu kien,treo ong gio"),
    )
    cursor.execute(
        "INSERT OR IGNORE INTO mark_rules (category, prefix) VALUES (?, ?)",
        ("ACCESSORY", "ACC"),
    )

    conn.commit()
    conn.close()

    from backend.product_catalog import seed_product_catalog
    seed_product_catalog()


def _ensure_columns(cursor, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
