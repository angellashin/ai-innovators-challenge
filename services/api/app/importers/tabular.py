"""Safe tabular upload parsing for RE:PLAN baseline imports.

The parser intentionally avoids executing formulas and does not require
openpyxl.  It reads the XML values that are already stored in the workbook.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_XLSX_ENTRIES = 512
MAX_COMPRESSION_RATIO = 100
HEADER_SCAN_ROWS = 20

EMPTY_RESULT = {
    "project": {},
    "tasks": [],
    "options": [],
    "events": [],
    "calendars": [],
    "mapping": {},
    "warnings": [],
}

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"x": NS_MAIN, "rel": NS_REL}

BUILTIN_DATE_FORMATS = {
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    27,
    30,
    36,
    45,
    46,
    47,
    50,
    57,
}

HEADER_ALIASES = {
    "supplier_id": {"supplier_id", "공급사id", "공급사", "owner_company"},
    "equipment_id": {"equipment_id", "설비id", "설비번호"},
    "country": {"country", "국가"},
    "country_code": {"country_code", "국가코드"},
    "risk_tags": {"risk_tags", "위험태그"},
    "planned_cost": {"planned_cost", "계획비용"},
    "currency": {"currency", "통화"},
    "task_id": {"작업id", "작업ID", "taskid", "task_id", "id", "작업번호"},
    "phase": {"단계", "phase"},
    "name": {"작업명", "taskname", "task_name", "name", "작업"},
    "planned_start": {"계획시작", "start", "plannedstart", "planned_start", "시작일"},
    "planned_finish": {"계획종료", "finish", "plannedfinish", "planned_finish", "plannedend", "planned_end", "종료일", "완료일"},
    "duration_days": {"durationdays", "duration_days"},
    "duration_workdays": {"작업일수", "기간작업일", "duration", "duration_workdays", "기간"},
    "owner": {"담당조직", "owner", "담당", "조직"},
    "location": {"위치", "location", "region", "site"},
    "status": {"상태", "status"},
    "progress": {"진척률", "progress", "percent", "완료율"},
    "notes": {"비고", "메모", "notes", "note"},
    "predecessor_ids": {"필수선행id", "선행id", "predecessors", "predecessor_ids", "pred"},
    "dependency_type": {"관계", "relationship", "dependencytype", "dependency_type"},
    "basis": {"근거구분", "basis"},
    "resource_group": {"자원그룹", "resource_group", "resource"},
    "demand_teams": {"수요팀", "demand_teams", "demand"},
    "capacity_teams": {"수용량팀", "capacity_teams", "capacity"},
    "outdoor": {"야외작업", "outdoor"},
    "mobility": {"이동가능성", "mobility"},
    "option_id": {"옵션id", "optionid", "option_id"},
    "method": {"대응방식", "method"},
    "target_id": {"대상id", "targetid", "target_id"},
    "condition": {"조건", "condition"},
    "operation": {"변경연산", "operation"},
    "new_value": {"새값", "new_value"},
    "extra_cost_krw": {"추가비용krw", "추가비용", "extra_cost_krw", "cost"},
    "execution_status": {"실행상태", "execution_status"},
    "caution": {"주의", "caution"},
    "event_id": {"이벤트id", "eventid", "event_id"},
    "received_at": {"공개접수시각", "접수시각", "received_at", "published_at"},
    "input_type": {"입력종류", "input_type", "type"},
    "source": {"출처표시", "source"},
    "body": {"본문", "body", "message"},
    "mode": {"모드", "mode"},
    "calendar_date": {"날짜", "date"},
    "scope": {"적용범위", "scope"},
    "calendar_type": {"구분", "type", "calendar_type"},
    "description": {"설명", "description"},
}

TASK_KEYS = {"task_id", "name", "planned_start", "planned_finish"}


@dataclass(frozen=True)
class Cell:
    ref: str
    raw: str | None
    value: Any
    formula: bool = False


def parse_upload(filename: str, content: bytes) -> dict[str, Any]:
    """Parse an uploaded workbook or CSV into RE:PLAN import buckets."""

    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Upload exceeds {MAX_UPLOAD_BYTES} byte limit")

    result = _empty_result()
    lower_name = filename.lower()
    if lower_name.endswith(".csv"):
        table = _parse_csv(content, result["warnings"])
        _merge_table(result, "CSV", table)
    elif lower_name.endswith(".xlsx"):
        workbook = _parse_xlsx(content, result["warnings"])
        for sheet_name, table in workbook.items():
            _merge_table(result, sheet_name, table)
    else:
        raise ValueError("Unsupported upload type; expected .xlsx or .csv")

    _apply_task_constraints(result)
    return result


def diff_tasks(current_tasks: list[dict[str, Any]], imported_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a task-id based diff without guessing renamed identifiers."""

    current = {_task_identity(task): task for task in current_tasks if _task_identity(task)}
    imported = {_task_identity(task): task for task in imported_tasks if _task_identity(task)}
    added = [imported[key] for key in sorted(imported.keys() - current.keys())]
    removed = [current[key] for key in sorted(current.keys() - imported.keys())]
    changed: list[dict[str, Any]] = []
    unchanged: list[str] = []

    for key in sorted(current.keys() & imported.keys()):
        before = _comparable_task(current[key])
        after = _comparable_task(imported[key])
        if before == after:
            unchanged.append(key)
            continue
        changed.append(
            {
                "task_id": key,
                "before": current[key],
                "after": imported[key],
                "changes": {
                    field: {"before": before.get(field), "after": after.get(field)}
                    for field in sorted(set(before) | set(after))
                    if before.get(field) != after.get(field)
                },
            }
        )

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": len(unchanged),
        },
    }


def _empty_result() -> dict[str, Any]:
    return {key: (value.copy() if isinstance(value, dict) else list(value)) for key, value in EMPTY_RESULT.items()}


def _parse_csv(content: bytes, warnings: list[str]) -> dict[str, Any]:
    text = content.decode("utf-8-sig")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    rows = [[Cell(_cell_ref(row_idx, col_idx), value, _coerce_scalar(value)) for col_idx, value in enumerate(row)] for row_idx, row in enumerate(csv.reader(io.StringIO(text), dialect), start=1)]
    return _table_from_rows(rows, "CSV", warnings)


def _parse_xlsx(content: bytes, warnings: list[str]) -> dict[str, dict[str, Any]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid .xlsx archive") from exc

    with archive as zf:
        _validate_zip(zf)
        shared_strings = _read_shared_strings(zf)
        date_style_ids = _read_date_style_ids(zf)
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        date_1904 = workbook.find("x:workbookPr", NS) is not None and workbook.find("x:workbookPr", NS).attrib.get("date1904") in {"1", "true", "True"}
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels").decode("utf-8-sig").encode("utf-8"))
        rel_targets = {rel.attrib["Id"]: _normalise_target(rel.attrib["Target"]) for rel in rels.findall("rel:Relationship", NS)}

        parsed: dict[str, dict[str, Any]] = {}
        for sheet in workbook.findall("x:sheets/x:sheet", NS):
            sheet_name = sheet.attrib["name"]
            rel_id = sheet.attrib[f"{{{NS_OFFICE_REL}}}id"]
            target = rel_targets.get(rel_id)
            if not target:
                warnings.append(f"Sheet {sheet_name!r} has no readable worksheet target")
                continue
            rows = _read_sheet_rows(zf, target, shared_strings, date_style_ids, warnings, sheet_name, date_1904)
            parsed[sheet_name] = _table_from_rows(rows, sheet_name, warnings)
        return parsed


def _validate_zip(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    if len(infos) > MAX_XLSX_ENTRIES:
        raise ValueError("Workbook has too many files")
    total = sum(info.file_size for info in infos)
    compressed = max(sum(info.compress_size for info in infos), 1)
    if total > MAX_XLSX_UNCOMPRESSED_BYTES:
        raise ValueError("Workbook expands beyond safe size limit")
    if total / compressed > MAX_COMPRESSION_RATIO:
        raise ValueError("Workbook compression ratio exceeds safe limit")
    for info in infos:
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Workbook contains unsafe zip entry paths")
    if "xl/workbook.xml" not in zf.namelist() or "xl/_rels/workbook.xml.rels" not in zf.namelist():
        raise ValueError("Workbook is missing required workbook metadata")


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings = []
    for item in root.findall("x:si", NS):
        strings.append("".join(text.text or "" for text in item.findall(".//x:t", NS)))
    return strings


def _read_date_style_ids(zf: zipfile.ZipFile) -> set[int]:
    if "xl/styles.xml" not in zf.namelist():
        return set()
    root = ET.fromstring(zf.read("xl/styles.xml"))
    custom_formats: dict[int, str] = {}
    for fmt in root.findall("x:numFmts/x:numFmt", NS):
        custom_formats[int(fmt.attrib["numFmtId"])] = fmt.attrib.get("formatCode", "")

    date_style_ids: set[int] = set()
    for index, xf in enumerate(root.findall("x:cellXfs/x:xf", NS)):
        num_fmt_id = int(xf.attrib.get("numFmtId", "0"))
        if num_fmt_id in BUILTIN_DATE_FORMATS or _format_code_is_date(custom_formats.get(num_fmt_id, "")):
            date_style_ids.add(index)
    return date_style_ids


def _format_code_is_date(format_code: str) -> bool:
    cleaned = re.sub(r'".*?"', "", format_code.lower())
    cleaned = re.sub(r"\\.", "", cleaned)
    cleaned = re.sub(r"\[[^\]]+\]", "", cleaned)
    return bool(re.search(r"[ymdhHs]", cleaned))


def _read_sheet_rows(
    zf: zipfile.ZipFile,
    target: str,
    shared_strings: list[str],
    date_style_ids: set[int],
    warnings: list[str],
    sheet_name: str,
    date_1904: bool,
) -> list[list[Cell]]:
    root = ET.fromstring(zf.read(target))
    rows: list[list[Cell]] = []
    for row in root.findall("x:sheetData/x:row", NS):
        values: list[Cell] = []
        for cell in row.findall("x:c", NS):
            ref = cell.attrib.get("r", "")
            style_id = int(cell.attrib.get("s", "0"))
            cell_type = cell.attrib.get("t")
            raw_value = _cell_text(cell)
            formula = cell.find("x:f", NS) is not None
            if formula:
                warnings.append(f"{sheet_name}!{ref} contains a formula; stored value only was read")
            values.append(Cell(ref=ref, raw=raw_value, value=_convert_cell_value(raw_value, cell_type, style_id, shared_strings, date_style_ids, date_1904), formula=formula))
        rows.append(values)
    return rows


def _cell_text(cell: ET.Element) -> str | None:
    value = cell.find("x:v", NS)
    if value is not None:
        return value.text
    inline = cell.find("x:is", NS)
    if inline is not None:
        return "".join(text.text or "" for text in inline.findall(".//x:t", NS))
    return None


def _convert_cell_value(raw: str | None, cell_type: str | None, style_id: int, shared_strings: list[str], date_style_ids: set[int], date_1904: bool) -> Any:
    if raw is None:
        return None
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (IndexError, ValueError):
            return raw
    if cell_type in {"str", "inlineStr"}:
        return raw
    if cell_type == "b":
        return raw == "1"
    if cell_type == "n" or cell_type is None:
        number = _number(raw)
        if style_id in date_style_ids and isinstance(number, (int, float)):
            return _excel_serial_to_iso(number, date_1904=date_1904)
        return number
    return raw


def _excel_serial_to_iso(value: int | float, *, date_1904: bool = False) -> str:
    base = datetime(1904, 1, 1) if date_1904 else datetime(1899, 12, 30)
    dt = base + timedelta(days=float(value))
    if dt.time() == time(0, 0):
        return dt.date().isoformat()
    return dt.replace(microsecond=0).isoformat()


def _normalise_target(target: str) -> str:
    target = target.lstrip("/")
    if not target.startswith("xl/"):
        target = f"xl/{target}"
    return target


def _table_from_rows(rows: list[list[Cell]], source_name: str, warnings: list[str]) -> dict[str, Any]:
    header_index, mapping = _detect_header(rows)
    if header_index is None:
        return {"headers": [], "records": [], "mapping": {"source": source_name, "header_row": None, "columns": {}}}

    headers = rows[header_index]
    header_cells_by_column = {_column_index(cell.ref): cell for cell in headers}
    records: list[dict[str, Any]] = []
    for row in rows[header_index + 1 :]:
        record: dict[str, Any] = {}
        refs: dict[str, str] = {}
        raw: dict[str, str | None] = {}
        for header_cell in headers:
            col_idx = _column_index(header_cell.ref)
            key = mapping.get(col_idx)
            if not key:
                continue
            value_cell = _cell_at_column(row, col_idx)
            value = value_cell.value if value_cell else None
            if _is_blank(value):
                continue
            record[key] = _coerce_by_key(key, value)
            refs[key] = value_cell.ref if value_cell else _cell_ref(header_index + 2, col_idx)
            raw[key] = value_cell.raw if value_cell else None
        if record:
            record["_source_sheet"] = source_name
            record["_row"] = _row_number(row) or header_index + 2 + len(records)
            record["_cell_refs"] = refs
            record["_raw"] = raw
            records.append(record)

    return {
        "headers": [_display_value(cell.value) for cell in headers],
        "records": records,
        "mapping": {
            "source": source_name,
            "header_row": header_index + 1,
            "columns": {key: header_cells_by_column[col_idx].ref for col_idx, key in mapping.items() if col_idx in header_cells_by_column},
        },
    }


def _detect_header(rows: list[list[Cell]]) -> tuple[int | None, dict[int, str]]:
    best_index: int | None = None
    best_mapping: dict[int, str] = {}
    best_score = 0
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        mapping: dict[int, str] = {}
        for cell in row:
            key = _canonical_header(cell.value)
            if key:
                mapping[_column_index(cell.ref)] = key
        score = len(mapping) + (4 if TASK_KEYS <= set(mapping.values()) else 0)
        if score > best_score:
            best_index = index
            best_mapping = mapping
            best_score = score
    if best_score < 2:
        return None, {}
    return best_index, best_mapping


def _merge_table(result: dict[str, Any], source_name: str, table: dict[str, Any]) -> None:
    result["mapping"][source_name] = table["mapping"]
    records = table["records"]
    normalised_name = _normalise_text(source_name)

    if normalised_name in {"프로젝트", "project", "projects"}:
        result["project"].update(_project_from_records(records))
    elif normalised_name in {"일정표", "baseline_schedule", "schedule", "tasks"} or _looks_like_tasks(records):
        result["tasks"].extend(_strip_empty_task_fields(record) for record in records if record.get("task_id"))
    elif normalised_name in {"작업조건", "task_constraints", "constraints"}:
        result.setdefault("_constraints", []).extend(records)
    elif normalised_name in {"캘린더", "work_calendar", "calendar", "calendars"}:
        result["calendars"].extend(records)
    elif normalised_name in {"대응옵션", "response_catalog", "options"}:
        result["options"].extend(records)
    elif normalised_name in {"변경이벤트", "event_replay_fixtures", "events"}:
        result["events"].extend(records)
    elif records and not any([result["tasks"], result["project"], result["options"], result["events"], result["calendars"]]):
        result["tasks"].extend(_strip_empty_task_fields(record) for record in records if record.get("task_id") or record.get("name"))


def _project_from_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    project: dict[str, Any] = {}
    for record in records:
        key = record.get("key") or record.get("task_id") or record.get("name")
        value = record.get("value")
        if key is None or value is None:
            raw_keys = [k for k in record if not k.startswith("_")]
            if len(raw_keys) >= 2:
                key, value = record[raw_keys[0]], record[raw_keys[1]]
        if key is not None and value is not None:
            project[str(key)] = value
    return project


def _apply_task_constraints(result: dict[str, Any]) -> None:
    constraints = result.pop("_constraints", [])
    by_id = {task.get("task_id"): task for task in result["tasks"]}
    for constraint in constraints:
        task = by_id.get(constraint.get("task_id"))
        if not task:
            continue
        for key in (
            "predecessor_ids",
            "dependency_type",
            "basis",
            "duration_workdays",
            "resource_group",
            "demand_teams",
            "capacity_teams",
            "outdoor",
            "mobility",
            "notes",
        ):
            if key in constraint:
                task[key] = constraint[key]
                task.setdefault("_cell_refs", {}).setdefault(key, constraint.get("_cell_refs", {}).get(key))


def _looks_like_tasks(records: list[dict[str, Any]]) -> bool:
    if not records:
        return False
    keys = set().union(*(record.keys() for record in records))
    return "task_id" in keys and ("planned_start" in keys or "planned_finish" in keys or "name" in keys)


def _strip_empty_task_fields(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not _is_blank(value)}


def _canonical_header(value: Any) -> str | None:
    normalised = _normalise_text(value)
    if not normalised:
        return None
    if normalised in {"키", "key", "field"}:
        return "key"
    if normalised in {"값", "value"}:
        return "value"
    for canonical, aliases in HEADER_ALIASES.items():
        if normalised in {_normalise_text(alias) for alias in aliases}:
            return canonical
    return None


def _normalise_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "", str(value)).lower()


def _coerce_by_key(key: str, value: Any) -> Any:
    if key in {"planned_start", "planned_finish", "calendar_date"}:
        return _coerce_date(value)
    if key == "received_at":
        return _coerce_datetime(value)
    if key in {"duration_days", "duration_workdays", "demand_teams", "capacity_teams", "extra_cost_krw"}:
        return _coerce_int(value)
    if key == "progress":
        return _coerce_float(value)
    if key == "predecessor_ids":
        return _split_ids(value)
    if key == "target_id" and isinstance(value, str) and "," in value:
        return _split_ids(value)
    if key == "outdoor":
        return str(value).strip() in {"예", "Y", "y", "yes", "true", "True", "1"}
    return value


def _coerce_scalar(value: str) -> Any:
    stripped = value.strip()
    if stripped == "":
        return None
    for parser in (_parse_iso_date, _parse_number_string):
        parsed = parser(stripped)
        if parsed is not None:
            return parsed
    return stripped


def _coerce_date(value: Any) -> Any:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, (int, float)):
        return _excel_serial_to_iso(value).split("T", 1)[0]
    if isinstance(value, str):
        if re.fullmatch(r"\d+(\.\d+)?", value):
            return _excel_serial_to_iso(float(value)).split("T", 1)[0]
        parsed = _parse_iso_date(value)
        return parsed if parsed is not None else value
    return value


def _coerce_datetime(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return _excel_serial_to_iso(value)
    if isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?", value):
        return _excel_serial_to_iso(float(value))
    return value


def _coerce_int(value: Any) -> Any:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        number = _parse_number_string(value)
        if isinstance(number, (int, float)):
            return int(number)
    return value


def _coerce_float(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        number = _parse_number_string(value)
        if isinstance(number, (int, float)):
            return float(number)
    return value


def _parse_iso_date(value: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_number_string(value: str) -> int | float | None:
    cleaned = value.replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return None
    number = float(cleaned) if "." in cleaned else int(cleaned)
    if isinstance(number, float) and number.is_integer():
        return int(number)
    return number


def _number(raw: str) -> int | float | str:
    number = _parse_number_string(raw)
    return raw if number is None else number


def _split_ids(value: Any) -> list[str]:
    if value is None:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _cell_at_column(row: list[Cell], col_idx: int) -> Cell | None:
    for cell in row:
        if _column_index(cell.ref) == col_idx:
            return cell
    return None


def _column_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    total = 0
    for letter in letters:
        total = total * 26 + ord(letter.upper()) - 64
    return total - 1


def _row_number(row: list[Cell]) -> int | None:
    for cell in row:
        match = re.search(r"\d+", cell.ref)
        if match:
            return int(match.group(0))
    return None


def _cell_ref(row_idx: int, col_idx: int) -> str:
    col = ""
    col_num = col_idx + 1
    while col_num:
        col_num, remainder = divmod(col_num - 1, 26)
        col = chr(65 + remainder) + col
    return f"{col}{row_idx}"


def _display_value(value: Any) -> str:
    return "" if value is None else str(value)


def _is_blank(value: Any) -> bool:
    return value is None or value == ""


def _task_identity(task: dict[str, Any]) -> str | None:
    value = task.get("task_id") or task.get("id")
    return str(value) if value is not None else None


def _comparable_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in task.items()
        if not key.startswith("_") and key not in {"raw", "cell_refs"}
    }
