"""Turn a family workbook into an editable draw.io diagram."""

import argparse
import base64
import re
import sys
import zlib
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote, unquote
from xml.etree import ElementTree as ET
from zipfile import BadZipFile

from family_layout import layout_people
from family_workbook import read_family_workbook


PAGE_ID = "oral-family-tree"
TITLE_ID = "oral-family-title"
PERSON_PREFIX = "oral-family-person-"
RELATION_PREFIX = "oral-family-relation-"
PARENT_KINDS = {"亲子", "生育", "收养", "继亲"}
PEER_KINDS = {"伴侣", "前伴侣", "手足"}
FONT = "fontFamily=Microsoft YaHei;html=0;"
CARD_STYLE = (
    FONT + "rounded=1;arcSize=12;whiteSpace=wrap;spacing=8;fontSize=15;"
    "fontColor=#263C35;strokeColor=#789288;strokeWidth=1;fillColor=#F3F7F2;"
)
TITLE_STYLE = FONT + "text;align=left;verticalAlign=middle;fontSize=26;fontStyle=1;fontColor=#263C35;"
EDGE_STYLE = (
    FONT + "edgeStyle=orthogonalEdgeStyle;rounded=0;fontSize=12;labelBackgroundColor=#FFFFFF;"
    "strokeWidth=1.5;endFill=1;"
)
VERTICAL_PORTS = {"exitX": "0.5", "exitY": "1", "entryX": "0.5", "entryY": "0"}
ORDINAL = re.compile(r"排(?:行)?\s*(?:老|第)?\s*(大|[一二三四五六七八九十]+|[0-9]+)")
NUMBER_WORDS = {word: number for number, word in enumerate("一二三四五六七八九十", 1)}


def indexed_rows(rows, column, sheet):
    indexed = {}
    for row in rows:
        identifier = row[column]
        if not identifier or identifier in indexed:
            raise ValueError(f"{sheet}表中的编号为空或重复：{identifier or '空白'}。")
        indexed[identifier] = row
    return indexed


def validate_relationships(people, relationships):
    seen = set()
    for identifier, row in relationships.items():
        source, target, kind = row["人物A"], row["人物B"], row["关系"]
        if source not in people or target not in people or source == target:
            raise ValueError(f"关系 {identifier} 的两端必须引用不同且存在的人物。")
        if kind not in PARENT_KINDS | PEER_KINDS:
            raise ValueError(f"关系 {identifier} 使用了未识别的关系类型：{kind}。")
        if row["状态"] not in {"已确认", "待确认"}:
            raise ValueError(f"关系 {identifier} 的状态必须为已确认或待确认。")
        endpoints = (source, target) if kind in PARENT_KINDS else tuple(sorted((source, target)))
        key = (*endpoints, kind)
        if key in seen:
            raise ValueError(f"关系 {identifier} 与已有关系重复。")
        seen.add(key)


def sibling_rank(note):
    match = ORDINAL.search(note)
    if not match:
        return None
    word = match.group(1)
    if word.isdecimal():
        return int(word)
    if word == "大":
        return 1
    if word in NUMBER_WORDS:
        return NUMBER_WORDS[word]
    if word.count("十") == 1:
        tens, ones = word.split("十")
        if len(tens) <= 1 and len(ones) <= 1:
            return NUMBER_WORDS.get(tens, 1) * 10 + NUMBER_WORDS.get(ones, 0)
    return None


def positions_for(people, relationships):
    parents, peers, order = [], [], {}
    for row in relationships.values():
        if row["状态"] != "已确认":
            continue
        pair = row["人物A"], row["人物B"]
        if row["关系"] in PARENT_KINDS:
            parents.append(pair)
            rank = sibling_rank(row.get("备注", ""))
            if rank is not None:
                order[pair] = rank
        else:
            peers.append(pair)
    return layout_people(list(people), parents, peers, order)


def make_document():
    document = ET.Element("mxfile", {"host": "app.diagrams.net", "compressed": "false"})
    page = ET.SubElement(document, "diagram", {"id": PAGE_ID, "name": "族谱图"})
    model = ET.SubElement(page, "mxGraphModel", {"grid": "1", "gridSize": "10", "page": "0"})
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    title = ET.SubElement(root, "mxCell", {
        "id": TITLE_ID, "value": "族谱图", "style": TITLE_STYLE, "vertex": "1", "parent": "1",
    })
    add_geometry(title, x=42, y=36, width=320, height=44)
    return document, model, None


def open_previous(path):
    document = ET.parse(path).getroot()
    if document.tag == "mxGraphModel":
        model, compressed_page = document, None
    else:
        page = document.find(f"diagram[@id='{PAGE_ID}']")
        if page is None:
            raise ValueError("旧图缺少「口述家谱」生成的族谱页面，请选择对应的 .drawio 文件。")
        model = page.find("mxGraphModel")
        compressed_page = None
        if model is None and page.text:
            text = base64.b64decode(page.text)
            model = ET.fromstring(unquote(zlib.decompress(text, wbits=-15).decode("utf-8")))
            compressed_page = page
    if model is None or model.find("root") is None:
        raise ValueError("旧图的族谱页面缺少图形数据。")
    if document.tag == "mxGraphModel" and not any(
        identifier == TITLE_ID or managed(identifier) for identifier in cells_by_id(model.find("root"))
    ):
        raise ValueError("旧图缺少「口述家谱」生成的人物和关系编号。")
    return document, model, compressed_page


def add_geometry(cell, **attributes):
    return ET.SubElement(cell, "mxGeometry", {**{k: str(v) for k, v in attributes.items()}, "as": "geometry"})


def cells_by_id(root):
    result = {}
    for item in root:
        cell = item if item.tag == "mxCell" else item.find("mxCell")
        identifier = item.get("id")
        if cell is not None and identifier:
            if identifier in result:
                raise ValueError(f"旧图有重复的图形编号：{identifier}。")
            result[identifier] = item, cell
    return result


def managed(identifier):
    return identifier.startswith((PERSON_PREFIX, RELATION_PREFIX))


def add_item(root, identifier, label, **attributes):
    item = ET.SubElement(root, "object", {"id": identifier, "label": label, "familyLabel": label})
    cell = ET.SubElement(item, "mxCell", {"parent": "1", **attributes})
    return item, cell


def refresh_label(item, value, messages):
    label_key = "value" if item.tag == "mxCell" else "label"
    previous = item.get("familyLabel")
    current = item.get(label_key)
    manual = previous is not None and current not in (None, previous)
    if not manual or value != previous:
        if manual and current != value:
            messages.append(f"图中手改的文字「{current}」已按底稿更新为「{value}」。")
        item.set(label_key, value)
    item.set("familyLabel", value)


def title_for(row):
    name = row["姓名"]
    aliases = row.get("称呼或小名", "")
    if name and aliases and name != aliases:
        return f"{name}\n{aliases}"
    return name or aliases or f"姓名待补（{row['人物编号']}）"


def occupied_boxes(cells, ignored):
    layers = {identifier for identifier, (_, cell) in cells.items() if cell.get("parent") == "0"}
    boxes = []
    for identifier, (_, cell) in cells.items():
        geometry = cell.find("mxGeometry")
        if identifier in ignored or cell.get("parent") not in layers or cell.get("vertex") != "1":
            continue
        if geometry is not None and geometry.get("relative") != "1":
            items = (float(geometry.get(key, "0")) for key in ("x", "y", "width", "height"))
            boxes.append(tuple(items))
    return boxes


def place_card(x, y, occupied):
    width, height, gap = 160, 58, 24
    for other_x, other_y, other_width, other_height in sorted(occupied):
        overlap_y = y < other_y + other_height + gap and y + height + gap > other_y
        if overlap_y and x < other_x + other_width + gap and x + width + gap > other_x:
            x = int(other_x + other_width + gap) + 1
    occupied.append((x, y, width, height))
    return {"x": x, "y": y, "width": width, "height": height}


def edge_label(row):
    kind = row["关系"]
    if row["状态"] == "待确认":
        return f"待确认：{kind}"
    return "" if kind == "亲子" else kind


def edge_style(row):
    pending = row["状态"] == "待确认"
    directed = row["关系"] in PARENT_KINDS
    arrow = "block" if directed else "none"
    color = "#9A733F" if pending else "#5D7A70"
    style = EDGE_STYLE + f"endArrow={arrow};dashed={int(pending)};strokeColor={color};fontColor={color};"
    return update_style(style, **VERTICAL_PORTS) if directed and not pending else style


def update_style(style, **changes):
    properties = [entry for entry in style.split(";") if entry and entry.split("=", 1)[0] not in changes]
    return ";".join([*properties, *(f"{key}={value}" for key, value in changes.items() if value is not None)]) + ";"


def remove_old_items(root, cells, stale, desired):
    for identifier, (_, cell) in cells.items():
        if identifier in stale:
            continue
        keys = ("parent",) if identifier in desired else ("source", "target", "parent")
        references = {cell.get(key) for key in keys}
        if references & stale:
            raise ValueError(f"旧图的手工图形 {identifier} 仍连着已从底稿移除的项目，请先核对。")
    for identifier in stale:
        root.remove(cells[identifier][0])


def sync_diagram(model, people, relationships, layout):
    root = model.find("root")
    cells = cells_by_id(root)
    if "1" not in cells:
        raise ValueError("旧图缺少原始绘图图层。")
    desired = {PERSON_PREFIX + key for key in people} | {RELATION_PREFIX + key for key in relationships}
    stale = {identifier for identifier in cells if managed(identifier) and identifier not in desired}
    remove_old_items(root, cells, stale, desired)
    occupied = occupied_boxes(cells, stale)
    messages = []

    for key, (x, y) in layout.items():
        identifier = PERSON_PREFIX + key
        label = title_for(people[key])
        if identifier in cells:
            refresh_label(cells[identifier][0], label, messages)
        else:
            _, cell = add_item(root, identifier, label, vertex="1", style=CARD_STYLE)
            add_geometry(cell, **place_card(x, y, occupied))

    for key, row in relationships.items():
        identifier = RELATION_PREFIX + key
        source, target = PERSON_PREFIX + row["人物A"], PERSON_PREFIX + row["人物B"]
        label, kind = edge_label(row), row["关系"]
        if identifier not in cells:
            item, cell = add_item(root, identifier, label, edge="1", source=source, target=target, style=edge_style(row))
            item.set("familyKind", kind)
            add_geometry(cell, relative=1)
            continue
        item, cell = cells[identifier]
        was_pending = (item.get("familyLabel") or "").startswith("待确认：")
        is_pending = row["状态"] == "待确认"
        old_kind = item.get("familyKind")
        changes = {}
        if was_pending != is_pending:
            changes["dashed"] = int(is_pending)
        if old_kind != kind:
            changes["endArrow"] = "block" if kind in PARENT_KINDS else "none"
        if (old_kind in PARENT_KINDS and not was_pending) != (kind in PARENT_KINDS and not is_pending):
            changes.update(VERTICAL_PORTS if kind in PARENT_KINDS and not is_pending else dict.fromkeys(VERTICAL_PORTS))
        refresh_label(item, label, messages)
        item.set("familyKind", kind)
        if changes:
            cell.set("style", update_style(cell.get("style", ""), **changes))
        if (source, target) != (cell.get("source"), cell.get("target")):
            cell.set("source", source)
            cell.set("target", target)
            geometry = cell.find("mxGeometry")
            if geometry is not None:
                cell.remove(geometry)
            add_geometry(cell, relative=1)
    return messages


def write_document(document, model, compressed_page, output):
    if compressed_page is not None:
        data = quote(ET.tostring(model, encoding="unicode")).encode("utf-8")
        compressor = zlib.compressobj(wbits=-15)
        compressed_page.text = base64.b64encode(compressor.compress(data) + compressor.flush()).decode("ascii")
    ET.indent(document, space="  ")
    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        ET.ElementTree(document).write(temporary, encoding="utf-8", xml_declaration=True)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="将口述家谱 Excel 底稿导出为可继续编辑的 .drawio 文件。")
    parser.add_argument("workbook", type=Path, help="含人物、关系两张表的 Excel 底稿")
    parser.add_argument("--output", type=Path, required=True, help="输出 .drawio 文件")
    parser.add_argument("--previous", type=Path, help="已经排过版的旧 .drawio 文件")
    args = parser.parse_args()
    try:
        if args.output.suffix.lower() != ".drawio" or args.output.resolve() == args.workbook.resolve():
            raise ValueError("请选择扩展名为 .drawio 的输出文件。")
        people_rows, relation_rows = read_family_workbook(args.workbook)
        people = indexed_rows(people_rows, "人物编号", "人物")
        relationships = indexed_rows(relation_rows, "关系编号", "关系")
        if not people:
            raise ValueError("底稿还没有人物，请先录入一位家人。")
        validate_relationships(people, relationships)
        layout = positions_for(people, relationships)
        previous = open_previous(args.previous) if args.previous else make_document()
        document, model, compressed_page = previous
        messages = sync_diagram(model, people, relationships, layout)
        write_document(document, model, compressed_page, args.output)
    except (ValueError, OSError, ET.ParseError, BadZipFile, zlib.error) as error:
        parser.exit(2, f"导出失败：{error}\n")
    for message in messages:
        print(message, file=sys.stderr)
    print(f"已生成 {args.output}，{len(people)} 人，{len(relationships)} 条关系。")


if __name__ == "__main__":
    main()
