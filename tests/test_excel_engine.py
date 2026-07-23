from io import BytesIO
import os

os.environ["PRODUCT_ONLY_MODE"] = "0"

from openpyxl import Workbook, load_workbook

from backend.excel_engine import build_quotation_workbook


def test_kaiyo_template_mapping_smoke():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "báo giá"
    sheet["H13"] = "Dự án"
    sheet["A9"] = "Kính Gửi:"
    sheet["A21"] = "STT"
    sheet["B21"] = "Tên vật tư, thiết bị"
    sheet["C21"] = "Thông số kỹ thuật"
    sheet["D21"] = "Nhãn hiệu"
    sheet["E21"] = "Đơn vị"
    sheet["F21"] = "Khối lượng"
    sheet["G21"] = "Đơn giá"
    sheet["H21"] = "Thành tiền"
    sheet["I21"] = "Ghi chú"
    sheet["K20"] = "Tên vật tư, thiết bị"
    sheet["L21"] = "Mã SP"
    sheet["M21"] = "W1"
    sheet["N21"] = "H1"

    template = BytesIO()
    workbook.save(template)

    items = [
        {
            "item_no": "1",
            "description": "Co 700x400",
            "quote_material_spec": "Ton ma kem day 0.75 mm",
            "quote_brand": "Kaiyo Viet Nam",
            "unit": "cai",
            "quantity": 2,
            "quote_unit_price": 1147828,
            "quote_note": "",
            "quote_code": "cv",
            "width": 700,
            "height": 400,
            "calculated_area": 2.4,
            "pricing_mode": "area",
            "material": "GI",
            "thickness": 0.75,
        }
    ]
    summary = {"items_subtotal": 2295656, "vat": 229565.6, "grand_total": 2525221.6}

    output = build_quotation_workbook(
        items,
        summary,
        template_bytes=template.getvalue(),
        project="Project A",
        customer="Customer B",
    )
    result = load_workbook(BytesIO(output), data_only=False)
    quote = result["báo giá"]

    assert quote["I13"].value == "Project A"
    assert quote["C9"].value == "Customer B"
    assert quote["B24"].value == "Co 700x400"
    assert quote["C24"].value == "Ton ma kem day 0.75 mm"
    assert quote["D24"].value == "Kaiyo Viet Nam"
    assert quote["F24"].value == 2
    assert quote["G24"].value == 1147828
    assert quote["H24"].value == 2295656
    assert quote["L24"].value == "cv"
    assert quote["M24"].value == 700
    assert quote["N24"].value == 400


def test_kaiyo_template_mapping_does_not_require_sheet_name():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Mẫu - xây lắp"
    sheet["A21"] = "STT"
    sheet["B21"] = "Tên vật tư, thiết bị"
    sheet["C21"] = "Vật liệu chế tạo"
    sheet["D21"] = "Xuất Xứ"
    sheet["E21"] = "Đơn vị"
    sheet["F21"] = "Khối lượng"
    sheet["G21"] = "Đơn giá"
    sheet["H21"] = "Thành tiền"
    sheet["I21"] = "Ghi chú"

    template = BytesIO()
    workbook.save(template)

    output = build_quotation_workbook(
        [
            {
                "item_no": "1",
                "description": "Cửa gió nan thẳng",
                "quote_material_spec": "Nhôm định hình",
                "quote_brand": "Kaiyo Việt Nam",
                "unit": "cái",
                "quantity": 3,
                "quote_unit_price": 100000,
                "subtotal": 300000,
            }
        ],
        {"items_subtotal": 300000, "vat": 30000, "grand_total": 330000},
        template_bytes=template.getvalue(),
    )

    result = load_workbook(BytesIO(output), data_only=False)

    assert "Dữ liệu báo giá AI" not in result.sheetnames
    assert "AI Quotation Data" not in result.sheetnames
    quote = result["Mẫu - xây lắp"]
    assert quote["B24"].value == "Cửa gió nan thẳng"
    assert quote["F24"].value == 3
    assert quote["G24"].value == 100000
    assert quote["H24"].value == 300000
