from openpyxl import Workbook

from backend.parser_engine import parse_customer_sheet, parse_reference_quote


def test_reference_quote_prefers_sales_quote_sheet_over_kl_sheet(tmp_path):
    workbook = Workbook()
    kl = workbook.active
    kl.title = "KL"
    kl["A6"] = "STT"
    kl["B6"] = "MÃ HIỆU\nĐƠN GIÁ"
    kl["C6"] = "NỘI DUNG CÔNG VIỆC"
    kl["D6"] = "ĐƠN VỊ"
    kl["E6"] = "KHỐI LƯỢNG"
    kl["A7"] = 1
    kl["B7"] = "BB.82404"
    kl["C7"] = "Louver 300x300"
    kl["D7"] = "cái"
    kl["E7"] = 2

    quote = workbook.create_sheet("Sheet1")
    quote["A21"] = "STT"
    quote["B21"] = "Tên vật tư, thiết bị"
    quote["E21"] = "Đơn vị"
    quote["F21"] = "Khối lượng"
    quote["G21"] = "Đơn giá"
    quote["H21"] = "Thành tiền"
    quote["A22"] = 1
    quote["B22"] = "Louver 300x300"
    quote["E22"] = "cái"
    quote["F22"] = 2
    quote["G22"] = 291000
    quote["H22"] = 582000

    path = tmp_path / "completed_quote.xlsx"
    workbook.save(path)

    rows = parse_reference_quote(str(path))

    assert len(rows) == 1
    assert rows[0]["reference_unit_price"] == 291000
    assert rows[0]["reference_line_total"] == 582000


def test_order_takeoff_sheet_with_two_header_rows(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet["E8"] = "KHỐI LƯỢNG ĐẶT HÀNG ĐƠN 1"
    headers = [
        "STT",
        "Ký hiệu",
        "KH",
        "QUI CÁCH ỐNG GIÓ",
        "QUI CÁCH ỐNG GIÓ",
        "KÍCH THƯỚC ỐNG GIÓ",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        "Đơn vị",
        "Số lượng",
        "Thông số kỹ thuật ",
        "Ghi chú ",
    ]
    for col, value in enumerate(headers, start=1):
        sheet.cell(row=12, column=col, value=value)
    for col, value in {6: "W (mm)", 7: "H (mm)", 8: "W' (mm)", 9: "H' (mm)", 12: "L (mm)", 13: "R(Ø)\n(mm)", 14: "ĐỘ LỆCH /Góc Co(B)"}.items():
        sheet.cell(row=13, column=col, value=value)
    row = [
        1,
        "RA3-1",
        "t",
        "Ống CN",
        "Ống CN 1200x800xL1110",
        1200,
        800,
        None,
        None,
        None,
        None,
        1110,
        None,
        None,
        "cái",
        25,
        "Tôn mạ kẽm Z080 độ dày 0,75",
        None,
    ]
    for col, value in enumerate(row, start=1):
        sheet.cell(row=16, column=col, value=value)

    path = tmp_path / "order_takeoff.xlsx"
    workbook.save(path)

    rows = parse_customer_sheet(str(path))

    assert len(rows) == 1
    assert rows[0]["category"] == "SUPPLY_DUCT"
    assert rows[0]["width"] == 1200
    assert rows[0]["height"] == 800
    assert rows[0]["length"] == 1110
    assert rows[0]["quantity"] == 25
    assert rows[0]["thickness"] == 0.75
    assert rows[0].get("angle") is None
