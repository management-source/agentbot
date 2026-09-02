from __future__ import annotations

import re
import csv
import html
import zipfile
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from io import BytesIO, StringIO
from typing import Any
import xml.etree.ElementTree as ET


NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
NS_REL = {"r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}

HEADER_ALIASES = {
    "property_address": ("property address", "property", "property name", "full property address", "address", "rental address"),
    "invoice_date": ("invoice date", "date invoiced", "invoice created", "created date", "issue date", "date"),
    "invoice_number": ("invoice number", "invoice no", "invoice #", "invoice id", "reference", "ref no"),
    "description": ("description", "details", "invoice details", "narration", "memo"),
    "supplier": ("supplier", "creditor", "contractor", "payee", "vendor"),
    "amount": ("invoice amount", "total amount", "gross amount", "amount inc gst", "amount including gst", "amount", "total"),
    "status": ("payment status", "invoice status", "status"),
    "due_date": ("due date", "payment due"),
    "paid_date": ("paid date", "payment date", "date paid"),
}


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.findall(".//a:t", NS)) for item in root.findall("a:si", NS)]


def _cell_value(cell: ET.Element, strings: list[str]) -> str:
    value = cell.find("a:v", NS)
    inline = cell.find("a:is", NS)
    if cell.attrib.get("t") == "s" and value is not None and value.text:
        index = int(value.text)
        return strings[index] if 0 <= index < len(strings) else ""
    if cell.attrib.get("t") == "inlineStr" and inline is not None:
        return "".join(node.text or "" for node in inline.findall(".//a:t", NS))
    return value.text if value is not None and value.text else ""


def _workbook_rows(content: bytes) -> list[tuple[str, list[dict[str, str]]]]:
    result: list[tuple[str, list[dict[str, str]]]] = []
    with zipfile.ZipFile(BytesIO(content)) as zf:
        strings = _shared_strings(zf)
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        relationships = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        for sheet in workbook.findall("a:sheets/a:sheet", NS):
            rel_id = sheet.attrib.get(f"{{{NS_REL['r']}}}id")
            target = rel_map.get(rel_id or "", "")
            path = target.lstrip("/") if target.startswith("/") else "xl/" + target.lstrip("/")
            if not path.startswith("xl/") or path not in zf.namelist():
                continue
            root = ET.fromstring(zf.read(path))
            rows: list[dict[str, str]] = []
            for row in root.findall("a:sheetData/a:row", NS):
                values: dict[str, str] = {}
                for cell in row.findall("a:c", NS):
                    match = re.match(r"([A-Z]+)\d+", cell.attrib.get("r", ""))
                    if match:
                        values[match.group(1)] = _cell_value(cell, strings).strip()
                if any(values.values()):
                    rows.append(values)
            result.append((sheet.attrib.get("name", "Sheet"), rows))
    return result


def _header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _header_map(rows: list[dict[str, str]]) -> tuple[int, dict[str, str]] | None:
    best: tuple[int, dict[str, str]] | None = None
    for index, row in enumerate(rows[:30]):
        mapped: dict[str, str] = {}
        for column, raw in row.items():
            heading = _header_key(raw)
            for field, aliases in HEADER_ALIASES.items():
                if heading in aliases and field not in mapped:
                    mapped[field] = column
        if "property_address" in mapped and ("invoice_date" in mapped or "invoice_number" in mapped) and (not best or len(mapped) > len(best[1])):
            best = (index, mapped)
    return best


def _date_value(raw: str) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        serial = float(text)
        if 15000 <= serial <= 90000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _amount_value(raw: str) -> float | None:
    cleaned = re.sub(r"[^0-9.()-]", "", str(raw or ""))
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    try:
        value = float(cleaned.strip("()"))
        return -value if negative else value
    except ValueError:
        return None


def parse_invoice_workbook(content: bytes) -> list[dict[str, Any]]:
    invoices: list[dict[str, Any]] = []
    for sheet_name, rows in _workbook_rows(content):
        detected = _header_map(rows)
        if not detected:
            continue
        header_index, columns = detected
        for row in rows[header_index + 1:]:
            address = row.get(columns["property_address"], "").strip()
            if not address:
                continue
            item = {field: row.get(column, "").strip() for field, column in columns.items()}
            item["invoice_date"] = _date_value(item.get("invoice_date", ""))
            item["due_date"] = _date_value(item.get("due_date", ""))
            item["paid_date"] = _date_value(item.get("paid_date", ""))
            item["amount"] = _amount_value(item.get("amount", ""))
            item["source_sheet"] = sheet_name
            invoices.append(item)
            if len(invoices) > 50_000:
                raise ValueError("The workbook contains more than 50,000 invoice rows.")
    return invoices


def _clean_crm_text(value: str) -> str:
    text = html.unescape(str(value or "")).replace("\xa0", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


INVOICE_REPORT_TYPES = {"outgoing", "incoming", "bond", "mortgage"}


def _invoice_csv_reader(content: bytes) -> csv.DictReader:
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = content.decode("cp1252")
    text_stream = StringIO(decoded, newline="")
    first_line = text_stream.readline()
    if "property address" in first_line.lower():
        text_stream.seek(0)
    return csv.DictReader(text_stream)


def detect_invoice_csv_type(content: bytes) -> str | None:
    reader = _invoice_csv_reader(content)
    headings = {_header_key(value) for value in (reader.fieldnames or [])}
    if "property address" not in headings:
        return None
    if "profile" in headings and "created" in headings and "invoice number" not in headings:
        return "mortgage"
    if "creditor" in headings or "priority invoice" in headings:
        return "outgoing"
    if "owners" in headings and "tenants" in headings:
        return "incoming" if "created" in headings or "created user" in headings or "detail description" in headings else "bond"
    return None


def parse_invoice_csv(content: bytes, report_type: str | None = None) -> list[dict[str, Any]]:
    detected_type = detect_invoice_csv_type(content)
    selected_type = str(report_type or detected_type or "outgoing").strip().lower()
    if selected_type not in INVOICE_REPORT_TYPES:
        raise ValueError("Unknown invoice report type.")
    reader = _invoice_csv_reader(content)
    if not reader.fieldnames or "Property Address" not in reader.fieldnames:
        return []
    result: list[dict[str, Any]] = []
    for row in reader:
        address = _clean_crm_text(row.get("Property Address", ""))
        if not address:
            continue
        detail = _clean_crm_text(row.get("Detail Description", ""))
        description = _clean_crm_text(row.get("Description", ""))
        due_date = _date_value(row.get("Due Date", ""))
        invoice_date = _date_value(row.get("Created", "") or row.get("Invoice Date", ""))
        if invoice_date is None and selected_type == "bond":
            invoice_date = due_date
        result.append({
            "property_address": address,
            "invoice_date": invoice_date,
            "invoice_number": _clean_crm_text(row.get("Invoice Number", "")),
            "description": (detail or description)[:1000],
            "summary": description[:500],
            "supplier": _clean_crm_text(row.get("Creditor", "") or row.get("Profile", ""))[:300],
            "category": (_clean_crm_text(row.get("Category", "")) or selected_type.title())[:160],
            "amount": _amount_value(row.get("Total Amount", "")),
            "gst": _amount_value(row.get("GST", "")),
            "status": _clean_crm_text(row.get("Status", ""))[:160],
            "due_date": due_date,
            "paid_date": _date_value(row.get("Paid To Date", "")),
            "payment_method": _clean_crm_text(row.get("Payment Method", "")),
            "source_type": selected_type,
            "source_sheet": f"{selected_type.title()} invoices Report.csv",
        })
        if len(result) > 50_000:
            raise ValueError("The export contains more than 50,000 invoice rows.")
    return result


def normalize_address(value: str) -> str:
    text = _header_key(value)
    replacements = {
        "street": "st", "road": "rd", "avenue": "ave", "drive": "dr", "court": "ct",
        "place": "pl", "crescent": "cres", "lane": "ln", "highway": "hwy",
        "north": "n", "south": "s", "east": "e", "west": "w", "victoria": "vic",
        "unit": "u", "apartment": "u",
    }
    return " ".join(replacements.get(token, token) for token in text.split())


def address_match_score(invoice_address: str, property_address: str) -> float:
    left, right = normalize_address(invoice_address), normalize_address(property_address)
    if not left or not right:
        return 0.0
    left_numbers, right_numbers = re.findall(r"\d+", left), re.findall(r"\d+", right)
    if left_numbers and right_numbers and not (set(left_numbers) & set(right_numbers)):
        return 0.0
    left_tokens, right_tokens = set(left.split()), set(right.split())
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    sequence = SequenceMatcher(None, left, right).ratio()
    postcode_bonus = 0.08 if left_numbers and right_numbers and left_numbers[-1] == right_numbers[-1] and len(left_numbers[-1]) == 4 else 0
    return min(1.0, (sequence * 0.58) + (overlap * 0.42) + postcode_bonus)
