from __future__ import annotations

import re
import unicodedata
from typing import Callable, Dict, List, Optional, Sequence


NormalizeCell = Callable[[object], str]
NormalizeTechnicalData = Callable[[str], str]


def _compact(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[^0-9a-z\u3400-\u9fff\uac00-\ud7a3]+", "", text)


def _header_text(candidate: object) -> str:
    values: List[str] = []
    scan_rows = min(5, len(candidate.df))
    for row_idx in range(scan_rows):
        values.extend(_compact(value) for value in candidate.df.iloc[row_idx].tolist())
    return "|".join(values)


def _detect_family(candidates: Sequence[object]) -> Optional[str]:
    for candidate in candidates:
        header = _header_text(candidate)
        if (
            ("componentpartno" in header or "componentspartno" in header)
            and "manufacturerbrand" in header
            and "modeltype" in header
            and ("testedby" in header or "approvedby" in header)
        ):
            return "korea_ktl_5"
        if (
            "ci證書號碼" in header
            and "適用型號" in header
            and "新申請" in header
            and "驗證標誌及號碼" in header
        ):
            return "taiwan_pmc_extended"
        if (
            "objectpartno" in header
            and "manufacturertrademark" in header
            and "marksofconformity" in header
        ):
            return "taiwan_etc_6"
    return None


def _is_placeholder(value: str) -> bool:
    return value.strip() in {"", "-", "--", "---"}


def _clean(value: object, normalize_cell: NormalizeCell) -> str:
    text = normalize_cell(value)
    return "" if _is_placeholder(text) else text


def _join_values(*values: str) -> str:
    result: List[str] = []
    for value in values:
        value = value.strip()
        if not value or value in result:
            continue
        result.append(value)
    return "；".join(result)


def _record(
    candidate: object,
    row_idx: int,
    object_part_no: str,
    manufacturer_trademark: str,
    type_model: str,
    technical_data: str,
    standard: str,
    marks_of_conformity: str,
) -> Dict[str, object]:
    return {
        "page": candidate.page,
        "table_index_on_page": candidate.index_on_page,
        "row_index_on_table": row_idx,
        "engine": candidate.engine,
        "row_type": "detail",
        "section_title": "",
        "object_part_no": object_part_no,
        "manufacturer_trademark": manufacturer_trademark,
        "type_model": type_model,
        "technical_data": technical_data,
        "standard": standard,
        "marks_of_conformity": marks_of_conformity,
    }


def _is_korea_header(row_values: Sequence[str]) -> bool:
    header = "|".join(_compact(value) for value in row_values)
    return (
        "componentpartno" in header
        or "manufacturerbrand" in header
        or "ratingorcharacteristics" in header
        or "부품명회로기호" in header
    )


def _append_continuation(record: Dict[str, object], fields: Sequence[str], values: Sequence[str]) -> None:
    for field, value in zip(fields, values):
        if not value:
            continue
        current = str(record.get(field, "")).strip()
        if current and _compact(value) in _compact(current):
            continue
        separator = "；" if field == "marks_of_conformity" else " "
        record[field] = f"{current}{separator if current else ''}{value}"


def _extract_korea_ktl(
    candidates: Sequence[object],
    normalize_cell: NormalizeCell,
    normalize_technical_data: NormalizeTechnicalData,
) -> List[Dict[str, object]]:
    records: List[Dict[str, object]] = []
    fields = (
        "object_part_no",
        "manufacturer_trademark",
        "type_model",
        "technical_data",
        "marks_of_conformity",
    )
    last_object_part_no = ""

    for candidate in candidates:
        if candidate.df.shape[1] != 5:
            continue
        for row_idx in range(len(candidate.df)):
            values = [_clean(value, normalize_cell) for value in candidate.df.iloc[row_idx].tolist()]
            if _is_korea_header(values):
                continue

            object_part_no, manufacturer, type_model, technical_data, marks = values
            technical_data = normalize_technical_data(technical_data)
            non_empty = sum(bool(value) for value in values)

            is_cross_page_continuation = (
                records
                and row_idx == 0
                and candidate.page != records[-1]["page"]
                and non_empty <= 2
            )
            if records and ((not object_part_no and non_empty <= 2) or is_cross_page_continuation):
                _append_continuation(
                    records[-1],
                    fields,
                    (object_part_no, manufacturer, type_model, technical_data, marks),
                )
                continue

            if not object_part_no:
                object_part_no = last_object_part_no
            if not object_part_no or sum(bool(value) for value in values[1:]) < 1:
                continue
            last_object_part_no = object_part_no

            records.append(
                _record(
                    candidate,
                    row_idx,
                    object_part_no,
                    manufacturer,
                    type_model,
                    technical_data,
                    "",
                    marks,
                )
            )
    return records


def _find_header_mapping(candidate: object, field_hints: Dict[str, Sequence[str]]) -> Optional[tuple[int, Dict[str, int]]]:
    scan_rows = min(6, len(candidate.df))
    for row_idx in range(scan_rows):
        mapping: Dict[str, int] = {}
        for col_idx, value in enumerate(candidate.df.iloc[row_idx].tolist()):
            normalized = _compact(value)
            for field, hints in field_hints.items():
                if field not in mapping and any(hint in normalized for hint in hints):
                    mapping[field] = col_idx
                    break
        if len(mapping) == len(field_hints):
            return row_idx, mapping
    return None


def _extract_taiwan_etc(
    candidates: Sequence[object],
    normalize_cell: NormalizeCell,
    normalize_technical_data: NormalizeTechnicalData,
) -> List[Dict[str, object]]:
    hints = {
        "object_part_no": ("objectpartno",),
        "manufacturer_trademark": ("manufacturertrademark",),
        "type_model": ("typemodel",),
        "technical_data": ("technicaldata",),
        "standard": ("standard",),
        "marks_of_conformity": ("marksofconformity",),
    }
    records: List[Dict[str, object]] = []
    last_object_part_no = ""

    for candidate in candidates:
        header = _find_header_mapping(candidate, hints)
        if header is None:
            continue
        header_row, mapping = header
        for row_idx in range(header_row + 1, len(candidate.df)):
            row = candidate.df.iloc[row_idx].tolist()
            values = {
                field: _clean(row[col_idx], normalize_cell) if col_idx < len(row) else ""
                for field, col_idx in mapping.items()
            }
            object_part_no = values["object_part_no"]
            if object_part_no:
                last_object_part_no = object_part_no
            else:
                object_part_no = last_object_part_no

            technical_data = normalize_technical_data(values["technical_data"])
            other_values = [
                values["manufacturer_trademark"],
                values["type_model"],
                technical_data,
                values["standard"],
                values["marks_of_conformity"],
            ]
            if not object_part_no or not any(other_values):
                continue

            records.append(
                _record(
                    candidate,
                    row_idx,
                    object_part_no,
                    values["manufacturer_trademark"],
                    values["type_model"],
                    technical_data,
                    values["standard"],
                    values["marks_of_conformity"],
                )
            )
    return records


def _extract_taiwan_pmc(
    candidates: Sequence[object],
    normalize_cell: NormalizeCell,
    normalize_technical_data: NormalizeTechnicalData,
) -> List[Dict[str, object]]:
    hints = {
        "object_part_no": ("零組件材料名稱",),
        "manufacturer": ("製造商",),
        "trademark": ("商標",),
        "type_model": ("型號零組件",),
        "technical_data": ("技術規格電氣規格",),
        "standard": ("驗證標準",),
        "marks": ("驗證標誌及號碼",),
        "ci_certificate": ("ci證書號碼",),
    }
    records: List[Dict[str, object]] = []
    last_object_part_no = ""

    for candidate in candidates:
        header = _find_header_mapping(candidate, hints)
        if header is None:
            continue
        header_row, mapping = header
        for row_idx in range(header_row + 1, len(candidate.df)):
            row = candidate.df.iloc[row_idx].tolist()
            values = {
                field: _clean(row[col_idx], normalize_cell) if col_idx < len(row) else ""
                for field, col_idx in mapping.items()
            }
            object_part_no = values["object_part_no"]
            if object_part_no:
                last_object_part_no = object_part_no
            else:
                object_part_no = last_object_part_no

            manufacturer = _join_values(values["manufacturer"], values["trademark"])
            technical_data = normalize_technical_data(values["technical_data"])
            marks = _join_values(values["marks"], values["ci_certificate"])
            if not object_part_no or not any(
                [manufacturer, values["type_model"], technical_data, values["standard"], marks]
            ):
                continue

            records.append(
                _record(
                    candidate,
                    row_idx,
                    object_part_no,
                    manufacturer,
                    values["type_model"],
                    technical_data,
                    values["standard"],
                    marks,
                )
            )
    return records


def extract_known_template_records(
    candidates: Sequence[object],
    normalize_cell: NormalizeCell,
    normalize_technical_data: NormalizeTechnicalData,
) -> Optional[List[Dict[str, object]]]:
    family = _detect_family(candidates)
    if family == "korea_ktl_5":
        return _extract_korea_ktl(candidates, normalize_cell, normalize_technical_data)
    if family == "taiwan_etc_6":
        return _extract_taiwan_etc(candidates, normalize_cell, normalize_technical_data)
    if family == "taiwan_pmc_extended":
        return _extract_taiwan_pmc(candidates, normalize_cell, normalize_technical_data)
    return None
