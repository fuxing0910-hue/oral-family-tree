"""Read the two family-tree tables from an XLSX workbook.

Return cell values as text and retain the workbook's column names and row order.
"""

from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import BadZipFile, ZipFile


WORKBOOK_PATH = "xl/workbook.xml"
WORKBOOK_RELATIONSHIPS = "xl/_rels/workbook.xml.rels"
REQUIRED_HEADERS = {
    "人物": ("人物编号", "姓名"),
    "关系": ("关系编号", "人物A", "关系", "人物B", "状态"),
}


def _part_path(target):
    if target.startswith("/"):
        return posixpath.normpath(target).lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(WORKBOOK_PATH), target))


def _string_text(element):
    fragments = element.findall("{*}t") + element.findall("{*}r/{*}t")
    return "".join(fragment.text or "" for fragment in fragments)


def _cell_value(cell, shared_strings):
    kind = cell.get("t")
    value = cell.findtext("{*}v", default="") or ""
    if kind == "inlineStr":
        inline = cell.find("{*}is")
        return _string_text(inline).strip() if inline is not None else ""
    if kind == "s" and value:
        try:
            index = int(value)
            if index < 0:
                raise IndexError(index)
            return shared_strings[index].strip()
        except (IndexError, ValueError) as error:
            raise ValueError("Excel 文件中的共享文本索引无效。") from error
    return value.strip()


def _row_cells(row, shared_strings):
    cells = {}
    column = 0
    for cell in row.findall("{*}c"):
        reference = cell.get("r", "")
        if reference:
            match = re.fullmatch(r"([A-Z]+)[1-9][0-9]*", reference)
            if match is None:
                raise ValueError(f"Excel 文件中有无效的单元格地址 {reference!r}。")
            column = 0
            for letter in match[1]:
                column = column * 26 + ord(letter) - ord("A") + 1
        else:
            column += 1
        value = _cell_value(cell, shared_strings)
        if value:
            cells[column] = value
    return cells


def _read_sheet(package, sheet_path, sheet_name, shared_strings):
    sheet = ET.fromstring(package.read(sheet_path))
    rows = (_row_cells(row, shared_strings) for row in sheet.findall("{*}sheetData/{*}row"))
    headers = next((row for row in rows if row), {})
    values = set(headers.values())
    if len(values) != len(headers):
        raise ValueError(f"工作表「{sheet_name}」有重复的表头。")
    missing = [header for header in REQUIRED_HEADERS[sheet_name] if header not in values]
    if missing:
        raise ValueError(f"工作表「{sheet_name}」缺少表头 {'、'.join(missing)}。")

    columns = sorted(headers.items())
    result = []
    for row in rows:
        record = {header: row.get(column, "") for column, header in columns}
        if any(record.values()):
            result.append(record)
    return result


def read_family_workbook(path: str | Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return the people table and the relationships table, in that order."""
    try:
        with ZipFile(path) as package:
            workbook = ET.fromstring(package.read(WORKBOOK_PATH))
            relationships = ET.fromstring(package.read(WORKBOOK_RELATIONSHIPS))
            parts = {
                rel.get("Id"): (_part_path(rel.get("Target")), rel.get("Type", ""))
                for rel in relationships.findall("{*}Relationship")
                if rel.get("Target") and rel.get("TargetMode") != "External"
            }
            shared_path = next((path for path, kind in parts.values() if kind.endswith("/sharedStrings")), None)
            shared_strings = []
            if shared_path:
                shared = ET.fromstring(package.read(shared_path))
                shared_strings = [_string_text(item) for item in shared.findall("{*}si")]

            sheets = {}
            for sheet in workbook.findall("{*}sheets/{*}sheet"):
                reference = next((value for key, value in sheet.attrib.items() if key.endswith("}id")), None)
                if reference in parts:
                    sheets[sheet.get("name")] = parts[reference][0]
            for name in REQUIRED_HEADERS:
                if name not in sheets:
                    raise ValueError(f"Excel 底稿缺少工作表「{name}」。")

            people = _read_sheet(package, sheets["人物"], "人物", shared_strings)
            relations = _read_sheet(package, sheets["关系"], "关系", shared_strings)
            return people, relations
    except (BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError("无法读取 Excel 底稿。请检查文件是否为有效的 XLSX 工作簿。") from error
