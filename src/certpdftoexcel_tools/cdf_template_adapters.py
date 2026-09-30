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
    headers = [_header_text(candidate) for candidate in candidates]
    combined_header = "|".join(headers)
    if (
        "productinformationformodel" in combined_header
        and "objectpartno" in combined_header
        and "manufacturertrademark" in combined_header
        and "marksofconformity" in combined_header
    ):
        return "dekra_multi_page_6"

    for header in headers:
        if (
            "tablecomponentsonlyusedinthesamples" in header
            and "objectpartno" in header
            and "manufacturertrademark" in header
            and "typemodel" in header
            and "technicaldata" in header
        ):
            return "cvc_components_4"
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
            and "項次" in header
            and "零組件" in header
            and "取得標誌" in header
        ):
            return "taiwan_etc_6"
    return None


def _is_placeholder(value: str) -> bool:
    return value.strip() in {"", "-", "--", "---"}


def _clean(value: object, normalize_cell: NormalizeCell) -> str:
    # Keep source placeholders such as `--` and `----` in the exported detail
    # rows. They are values present in the PDF, not missing cells.
    return normalize_cell(value)


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
    product_model: str = "",
) -> Dict[str, object]:
    return {
        "page": candidate.page,
        "table_index_on_page": candidate.index_on_page,
        "row_index_on_table": row_idx,
        "engine": candidate.engine,
        "row_type": "detail",
        "section_title": "",
        "product_model": product_model,
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
    def build_mapping(row_values: Sequence[object]) -> Dict[str, int]:
        mapping: Dict[str, int] = {}
        for col_idx, value in enumerate(row_values):
            normalized = _compact(value)
            for field, hints in field_hints.items():
                if field not in mapping and any(hint in normalized for hint in hints):
                    mapping[field] = col_idx
                    break
        return mapping

    scan_rows = min(6, len(candidate.df))
    for row_idx in range(scan_rows):
        row_values = candidate.df.iloc[row_idx].tolist()
        mapping = build_mapping(row_values)
        if len(mapping) == len(field_hints):
            return row_idx, mapping

        if row_idx + 1 < scan_rows:
            next_values = candidate.df.iloc[row_idx + 1].tolist()
            merged_values = [
                "\n".join(str(value or "") for value in pair if str(value or "").strip())
                for pair in zip(row_values, next_values)
            ]
            mapping = build_mapping(merged_values)
            if len(mapping) == len(field_hints):
                return row_idx + 1, mapping
    return None


def _extract_dekra_multi_page(
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
    candidates_by_page: Dict[int, List[object]] = {}
    for candidate in candidates:
        candidates_by_page.setdefault(candidate.page, []).append(candidate)

    last_target_page: Optional[int] = None
    last_product_model = ""
    for page in sorted(candidates_by_page):
        page_candidates = sorted(candidates_by_page[page], key=lambda item: item.index_on_page)
        for candidate in page_candidates:
            if "productinformationformodel" not in _header_text(candidate):
                continue
            scan_rows = min(6, len(candidate.df))
            for row_idx in range(scan_rows - 1):
                row_values = candidate.df.iloc[row_idx].tolist()
                model_col = next(
                    (col_idx for col_idx, value in enumerate(row_values) if _compact(value) == "model"),
                    None,
                )
                if model_col is None:
                    continue
                next_row = candidate.df.iloc[row_idx + 1].tolist()
                if model_col < len(next_row):
                    product_model = _clean(next_row[model_col], normalize_cell)
                    if product_model:
                        last_product_model = product_model
                break

        target_candidates: List[tuple[object, int, Dict[str, int]]] = []
        for candidate in page_candidates:
            header = _find_header_mapping(candidate, hints)
            if header is not None:
                header_row, mapping = header
                target_candidates.append((candidate, header_row + 1, mapping))

        if not target_candidates and last_target_page == page - 1 and len(page_candidates) == 1:
            candidate = page_candidates[0]
            if candidate.df.shape[1] == len(hints):
                mapping = {field: idx for idx, field in enumerate(hints)}
                target_candidates.append((candidate, 0, mapping))

        if not target_candidates:
            continue

        for candidate, start_row, mapping in target_candidates:
            for row_idx in range(start_row, len(candidate.df)):
                row = candidate.df.iloc[row_idx].tolist()
                values = {
                    field: _clean(row[col_idx], normalize_cell) if col_idx < len(row) else ""
                    for field, col_idx in mapping.items()
                }
                object_part_no = values["object_part_no"]
                if not object_part_no or object_part_no.lower().startswith("remark"):
                    continue
                technical_data = normalize_technical_data(values["technical_data"])
                if not any(
                    [
                        values["manufacturer_trademark"],
                        values["type_model"],
                        technical_data,
                        values["standard"],
                        values["marks_of_conformity"],
                    ]
                ):
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
                        last_product_model,
                    )
                )
        last_target_page = page

    return records


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


def _extract_cvc_components(
    candidates: Sequence[object],
    normalize_cell: NormalizeCell,
    normalize_technical_data: NormalizeTechnicalData,
) -> List[Dict[str, object]]:
    hints = {
        "object_part_no": ("objectpartno",),
        "manufacturer_trademark": ("manufacturertrademark",),
        "type_model": ("typemodel",),
        "technical_data": ("technicaldata",),
    }
    records: List[Dict[str, object]] = []

    for candidate in candidates:
        if candidate.df.shape[1] != 4:
            continue
        if "tablecomponentsonlyusedinthesamples" not in _header_text(candidate):
            continue
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
            manufacturer = values["manufacturer_trademark"]
            type_model = values["type_model"]
            technical_data = normalize_technical_data(values["technical_data"])
            if not object_part_no or not any([manufacturer, type_model, technical_data]):
                continue

            records.append(
                _record(
                    candidate,
                    row_idx,
                    object_part_no,
                    manufacturer,
                    type_model,
                    technical_data,
                    "",
                    "",
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
    template: str = "auto",
) -> Optional[List[Dict[str, object]]]:
    extractors = {
        "cvc_components": _extract_cvc_components,
        "korea_ktl": _extract_korea_ktl,
        "taiwan_etc": _extract_taiwan_etc,
        "taiwan_pmc": _extract_taiwan_pmc,
        "dekra_product_info": _extract_dekra_multi_page,
        "cvc_components_4": _extract_cvc_components,
        "korea_ktl_5": _extract_korea_ktl,
        "taiwan_etc_6": _extract_taiwan_etc,
        "taiwan_pmc_extended": _extract_taiwan_pmc,
        "dekra_multi_page_6": _extract_dekra_multi_page,
    }
    canonical_family = {
        "cvc_components": "cvc_components_4",
        "korea_ktl": "korea_ktl_5",
        "taiwan_etc": "taiwan_etc_6",
        "taiwan_pmc": "taiwan_pmc_extended",
        "dekra_product_info": "dekra_multi_page_6",
    }
    requested = (template or "auto").strip().lower()
    family = _detect_family(candidates)
    requested_family = canonical_family.get(requested, requested)
    if requested in extractors and (family is None or family == requested_family):
        records = extractors[requested](candidates, normalize_cell, normalize_technical_data)
        if records:
            return records

    extractor = extractors.get(family or "")
    if extractor is not None:
        records = extractor(candidates, normalize_cell, normalize_technical_data)
        if records:
            return records
    return None
