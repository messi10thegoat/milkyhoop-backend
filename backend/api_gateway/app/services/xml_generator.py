"""
Config-driven XML generator for DJP e-Faktur export.
Pure function — no DB access. Data fetched by router, passed here.
"""
import xml.etree.ElementTree as ET
from xml.dom import minidom
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime
import json
import os
import logging

logger = logging.getLogger(__name__)


def load_xml_config() -> dict:
    config_path = os.path.join(
        os.path.dirname(__file__), '..', 'config', 'efaktur_xml_schema.json'
    )
    with open(config_path, 'r') as f:
        return json.load(f)


def format_value(value, fmt: str = None) -> str:
    if value is None:
        return ""

    if fmt == "date":
        if isinstance(value, date):
            return value.strftime("%d-%m-%Y")
        elif isinstance(value, str):
            try:
                dt = datetime.strptime(str(value)[:10], "%Y-%m-%d")
                return dt.strftime("%d-%m-%Y")
            except Exception:
                return str(value)
        return str(value)

    elif fmt == "decimal":
        try:
            d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return str(d)
        except Exception:
            return "0.00"

    elif fmt == "decimal_int":
        try:
            d = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            return str(int(d))
        except Exception:
            return "0"

    else:
        return str(value) if value is not None else ""


def generate_xml(invoice_data: list, config: dict) -> str:
    root = ET.Element(config["root_element"])

    for inv in invoice_data:
        header = inv["header"]
        items = inv["items"]

        faktur_el = ET.SubElement(root, config["faktur_element"])

        # Header
        header_el = ET.SubElement(faktur_el, config["header_element"])
        for mapping in config["header_mapping"]:
            db_field = mapping["db_field"]
            xml_el_name = mapping["xml_element"]
            fmt = mapping.get("format")
            value = header.get(db_field)

            # Retur reference: append to Referensi field
            if db_field == "referensi" and header.get("retur_of_faktur_number"):
                ref = value or ""
                retur_ref = header["retur_of_faktur_number"]
                value = f"{ref} (Retur: {retur_ref})".strip()

            el = ET.SubElement(header_el, xml_el_name)
            el.text = format_value(value, fmt)

        # Detail
        detail_el = ET.SubElement(faktur_el, config["detail_element"])
        for item in items:
            item_el = ET.SubElement(detail_el, config["item_element"])
            for mapping in config["detail_mapping"]:
                db_field = mapping["db_field"]
                xml_el_name = mapping["xml_element"]
                fmt = mapping.get("format")
                value = item.get(db_field)
                el = ET.SubElement(item_el, xml_el_name)
                el.text = format_value(value, fmt)

    rough_string = ET.tostring(root, encoding="unicode")
    dom = minidom.parseString(rough_string)
    xml_str = dom.toprettyxml(indent="  ", encoding=None)

    lines = xml_str.split('\n')
    if lines[0].startswith('<?xml'):
        lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
    xml_str = '\n'.join(lines)

    return xml_str
