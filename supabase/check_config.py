from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    load_dotenv()

    database_url = os.getenv("DATABASE_URL", "")
    supabase_url = os.getenv("SUPABASE_URL", "")
    service_role = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    errors: list[str] = []
    warnings: list[str] = []

    if not supabase_url:
        errors.append("Thiếu SUPABASE_URL.")
    elif "/rest/v1" in supabase_url:
        warnings.append("SUPABASE_URL nên là domain gốc, ví dụ https://xxx.supabase.co, không thêm /rest/v1/.")

    if not service_role:
        errors.append("Thiếu SUPABASE_SERVICE_ROLE_KEY.")

    if not database_url:
        errors.append("Thiếu DATABASE_URL.")
    elif "[YOUR-PASSWORD]" in database_url:
        errors.append("DATABASE_URL vẫn còn [YOUR-PASSWORD]. Hãy thay bằng Database password thật trong Supabase.")
    else:
        parsed = urlparse(database_url)
        if parsed.scheme not in {"postgresql", "postgres"}:
            errors.append("DATABASE_URL phải bắt đầu bằng postgresql://")
        if not parsed.hostname:
            errors.append("DATABASE_URL thiếu hostname.")

    if errors:
        print("Cấu hình chưa sẵn sàng:")
        for error in errors:
            print(f"- {error}")
    else:
        print("Cấu hình .env cơ bản đã sẵn sàng.")

    if warnings:
        print("\nCảnh báo:")
        for warning in warnings:
            print(f"- {warning}")

    print("\nBước tiếp theo:")
    print("1. Chạy supabase/schema.sql trong Supabase SQL Editor.")
    print("2. Tạo Storage bucket tên quotation-files.")
    print("3. Chạy: python supabase/migrate_sqlite_to_supabase.py")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
