from __future__ import annotations

import os
import sqlite3
import sys
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parents[1]
SQLITE_DB = ROOT / "backend" / "hvac_quotation.db"


def sqlite_rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(conn.execute(query, params).fetchall())


def fetch_one_value(pg: psycopg.Connection, query: str, params: tuple[Any, ...]) -> Any:
    with pg.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return row[0] if row else None


def upsert_company(pg: psycopg.Connection) -> str:
    company_name = os.getenv("COMPANY_NAME", "Kaiyo Việt Nam")
    with pg.cursor() as cur:
        cur.execute(
            """
            insert into public.companies (name)
            values (%s)
            on conflict do nothing
            returning id
            """,
            (company_name,),
        )
        row = cur.fetchone()
        if row:
            return str(row[0])
    company_id = fetch_one_value(pg, "select id from public.companies where name = %s limit 1", (company_name,))
    if not company_id:
        raise RuntimeError("Không tạo được company.")
    return str(company_id)


def split_keywords(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def migrate_master_data(sqlite_conn: sqlite3.Connection, pg: psycopg.Connection, company_id: str) -> dict[str, dict[Any, str]]:
    maps: dict[str, dict[Any, str]] = defaultdict(dict)

    for row in sqlite_rows(sqlite_conn, "select normalized_key, display_name, keywords from product_categories"):
        category_id = fetch_one_value(
            pg,
            """
            insert into public.product_categories (company_id, key, display_name_vi, display_name_en, keywords, requires_dimensions)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (company_id, key)
            do update set display_name_vi = excluded.display_name_vi,
                          display_name_en = excluded.display_name_en,
                          keywords = excluded.keywords
            returning id
            """,
            (
                company_id,
                row["normalized_key"],
                row["display_name"],
                row["display_name"],
                split_keywords(row["keywords"]),
                row["normalized_key"] in {
                    "SUPPLY_DUCT",
                    "RETURN_DUCT",
                    "FRESH_AIR_DUCT",
                    "EXHAUST_AIR_DUCT",
                    "SMOKE_DUCT",
                    "ELBOW",
                    "TEE",
                    "REDUCER",
                    "TRANSITION",
                    "CROSS",
                    "OFFSET",
                },
            ),
        )
        maps["categories"][row["normalized_key"]] = str(category_id)

    for row in sqlite_rows(sqlite_conn, "select name, description from materials"):
        material_id = fetch_one_value(
            pg,
            """
            insert into public.materials (company_id, code, name_vi, description)
            values (%s, %s, %s, %s)
            on conflict (company_id, code)
            do update set name_vi = excluded.name_vi,
                          description = excluded.description
            returning id
            """,
            (company_id, row["name"], row["name"], row["description"]),
        )
        maps["materials"][row["name"]] = str(material_id)

    for row in sqlite_rows(sqlite_conn, "select key, value, type, description from coefficient_rules"):
        pg.execute(
            """
            insert into public.coefficient_rules (company_id, key, value, value_type, description, approval_status)
            values (%s, %s, %s, %s, %s, 'approved')
            on conflict (company_id, key)
            do update set value = excluded.value,
                          value_type = excluded.value_type,
                          description = excluded.description
            """,
            (company_id, row["key"], row["value"], row["type"], row["description"]),
        )

    for row in sqlite_rows(sqlite_conn, "select material, min_width, max_width, thickness from thickness_rules"):
        material_id = maps["materials"].get(row["material"])
        if material_id:
            pg.execute(
                """
                insert into public.thickness_rules
                (company_id, material_id, min_size_mm, max_size_mm, thickness_mm, approval_status)
                values (%s, %s, %s, %s, %s, 'approved')
                """,
                (company_id, material_id, row["min_width"], row["max_width"], row["thickness"]),
            )

    for row in sqlite_rows(
        sqlite_conn,
        """
        select category, quote_code, unit_mode, material_spec, brand, base_unit_price,
               area_multiplier, length_mm, note, source
        from product_pricing_rules
        """,
    ):
        category_id = maps["categories"].get(row["category"])
        if category_id:
            pg.execute(
                """
                insert into public.product_pricing_rules
                (company_id, category_id, quote_code, unit_mode, material_spec, brand,
                 base_unit_price, area_multiplier, length_mm, notes, approval_status)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'approved')
                on conflict (company_id, category_id)
                do update set quote_code = excluded.quote_code,
                              unit_mode = excluded.unit_mode,
                              material_spec = excluded.material_spec,
                              brand = excluded.brand,
                              base_unit_price = excluded.base_unit_price,
                              area_multiplier = excluded.area_multiplier,
                              length_mm = excluded.length_mm,
                              notes = excluded.notes
                """,
                (
                    company_id,
                    category_id,
                    row["quote_code"],
                    row["unit_mode"],
                    row["material_spec"],
                    row["brand"],
                    row["base_unit_price"],
                    row["area_multiplier"],
                    row["length_mm"],
                    row["note"] or row["source"],
                ),
            )

    return maps


def migrate_price_lists(sqlite_conn: sqlite3.Connection, pg: psycopg.Connection, company_id: str, maps: dict[str, dict[Any, str]]) -> None:
    price_list_map: dict[int, str] = {}
    for row in sqlite_rows(sqlite_conn, "select * from price_lists"):
        price_list_id = fetch_one_value(
            pg,
            """
            insert into public.price_lists
            (company_id, name, region, notes, is_default, approval_status)
            values (%s, %s, %s, %s, %s, 'approved')
            on conflict (company_id, name)
            do update set region = excluded.region,
                          notes = excluded.notes,
                          is_default = excluded.is_default
            returning id
            """,
            (company_id, row["name"], row["region"], row["notes"], bool(row["is_default"])),
        )
        price_list_map[int(row["id"])] = str(price_list_id)

    for row in sqlite_rows(sqlite_conn, "select * from price_list_items"):
        price_list_id = price_list_map.get(int(row["price_list_id"]))
        material_id = maps["materials"].get(row["material"])
        if price_list_id:
            unit_code = ensure_unit(pg, row["unit"])
            pg.execute(
                """
                insert into public.price_list_items
                (price_list_id, material_id, thickness_mm, unit, unit_price, source_note)
                values (%s, %s, %s, %s, %s, %s)
                """,
                (price_list_id, material_id, row["thickness"], unit_code, row["unit_price"], row["source"]),
            )


def normalize_unit(unit: str | None) -> str:
    raw_original = (unit or "").strip()
    raw = normalize_text(raw_original)
    return {
        "cái": "cai",
        "cai": "cai",
        "ca": "cai",
        "chiec": "cai",
        "cua": "cai",
        "pcs": "cai",
        "pc": "cai",
        "bộ": "bo",
        "bo": "bo",
        "set": "bo",
        "lô": "lo",
        "lo": "lo",
        "ống": "ong",
        "ong": "ong",
        "cuộn": "cuon",
        "cuon": "cuon",
        "met": "m",
        "m": "m",
        "m2": "m2",
        "m²": "m2",
        "tan": "tan",
        "tấn": "tan",
        "kg": "kg",
        "chuyen": "chuyen",
        "chuyến": "chuyen",
    }.get(raw, safe_unit_code(raw) or "cai")


def normalize_text(value: str | None) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.lower()).strip()


def safe_unit_code(value: str | None) -> str:
    text = normalize_text(value)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[:40]


def ensure_unit(pg: psycopg.Connection, unit: str | None) -> str:
    code = normalize_unit(unit)
    name = (unit or code).strip() or code
    pg.execute(
        """
        insert into public.units (code, name_vi, base_code, is_package)
        values (%s, %s, null, false)
        on conflict (code) do nothing
        """,
        (code, name),
    )
    return code


def migrate_quote_memory(sqlite_conn: sqlite3.Connection, pg: psycopg.Connection, company_id: str, maps: dict[str, dict[Any, str]]) -> None:
    sources: dict[str, str] = {}
    rows = sqlite_rows(sqlite_conn, "select * from quote_memory")
    for row in rows:
        source_file = row["source_file"] or "SQLite legacy quote memory"
        if source_file not in sources:
            source_id = fetch_one_value(
                pg,
                """
                select id
                from public.quote_memory_sources
                where company_id = %s and source_file_name = %s
                order by created_at
                limit 1
                """,
                (company_id, source_file),
            )
            if not source_id:
                source_id = fetch_one_value(
                    pg,
                    """
                    insert into public.quote_memory_sources
                    (company_id, source_file_name, approval_status, notes)
                    values (%s, %s, 'approved', 'Migrated from local SQLite')
                    returning id
                    """,
                    (company_id, source_file),
                )
            sources[source_file] = str(source_id)

        unit_code = ensure_unit(pg, row["unit"])
        pg.execute(
            """
            insert into public.quote_memory_items
            (company_id, source_id, signature, normalized_description, category_id,
             width_mm, height_mm, diameter_mm, length_mm, material_id, unit,
             quote_unit_price, material_spec, brand, note, approval_status)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'approved')
            on conflict (source_id, signature)
            do update set quote_unit_price = excluded.quote_unit_price,
                          material_spec = excluded.material_spec,
                          brand = excluded.brand,
                          note = excluded.note,
                          unit = excluded.unit,
                          approval_status = 'approved'
            """,
            (
                company_id,
                sources[source_file],
                row["signature"],
                row["normalized_description"],
                maps["categories"].get(row["category"]),
                row["width"],
                row["height"],
                row["diameter"],
                row["length"],
                None,
                unit_code,
                row["quote_unit_price"],
                row["quote_material_spec"],
                row["quote_brand"],
                row["quote_note"],
            ),
        )


def main() -> int:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url or "[YOUR-PASSWORD]" in database_url:
        print("DATABASE_URL chưa hợp lệ. Hãy thay [YOUR-PASSWORD] bằng password Supabase thật.")
        return 1
    if not SQLITE_DB.exists():
        print(f"Không tìm thấy SQLite DB: {SQLITE_DB}")
        return 1

    sqlite_conn = sqlite3.connect(SQLITE_DB)
    sqlite_conn.row_factory = sqlite3.Row

    with psycopg.connect(database_url) as pg:
        company_id = upsert_company(pg)
        maps = migrate_master_data(sqlite_conn, pg, company_id)
        migrate_price_lists(sqlite_conn, pg, company_id, maps)
        migrate_quote_memory(sqlite_conn, pg, company_id, maps)
        pg.commit()

    sqlite_conn.close()
    print("Đã migrate dữ liệu SQLite lên Supabase/PostgreSQL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
