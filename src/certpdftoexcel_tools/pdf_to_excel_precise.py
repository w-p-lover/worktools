#!/usr/bin/env python3
"""
PDF -> Excel converter focused on complex table cells.

Key goals:
1) Keep multi-line cell content (line breaks inside cells).
2) Try multiple extraction strategies and automatically pick better output.
3) Export each detected table to Excel with readable formatting.

Usage:
  python pdf_to_excel_precise.py input.pdf output.xlsx

Optional:
  python pdf_to_excel_precise.py input.pdf output.xlsx --pages all --flavor both
  python pdf_to_excel_precise.py input.pdf output.xlsx --engine pdfplumber
  python pdf_to_excel_precise.py input.pdf output.xlsx --target-only

Dependencies:
  pip install camelot-py[cv] pdfplumber pandas openpyxl

Notes:
  - For scanned PDFs (image-only pages), add OCR before extraction.
  - Camelot lattice works best for tables with visible borders.
  - Camelot stream works better for borderless/soft-lined tables.
"""

from __future__ import annotations

import argparse
import math
import re
import statistics
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from cdf_template_adapters import extract_known_template_records

try:
    import camelot
except Exception as exc:  # pragma: no cover
    camelot = None
    CAMELOT_IMPORT_ERROR = exc
else:
    CAMELOT_IMPORT_ERROR = None

try:
    import pdfplumber
except Exception as exc:  # pragma: no cover
    pdfplumber = None
    PDFPLUMBER_IMPORT_ERROR = exc
else:
    PDFPLUMBER_IMPORT_ERROR = None


MULTISPACE_RE = re.compile(r"[ \t]{2,}")
MATCH_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
STANDARD_PATTERN = re.compile(r"\b(IEC|EN|UL|ASTM|ISO|DIN|JIS|GB)\b", re.IGNORECASE)
MARK_PATTERN = re.compile(r"\b(UL|VDE|TUV|CSA|CQC|CE|ENEC|E\d{2,})\b", re.IGNORECASE)
PART_HINT_PATTERN = re.compile(r"(alternative|varistor|object|part)", re.IGNORECASE)
UL_FILE_PATTERN = re.compile(r"\bE\d{3,}\b", re.IGNORECASE)
ALT_SECTION_PATTERN = re.compile(r"\balternative\b", re.IGNORECASE)
TABLE_MARKER_RE = re.compile(r"\btable\s*:", re.IGNORECASE)
SECTION_ROW_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9 /+\-.,&()]+:\s*$")
SECTION_RELATION_MODEL_PATTERN = re.compile(r"\bW(?:19|27)-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b", re.IGNORECASE)
PDF_SYMBOL_CHAR_TRANSLATION = str.maketrans({
    "\uf06d": "µ",  # Symbol-font mu often appears as a private-use glyph.
    "\uf0b0": "°",  # Symbol-font degree sign used by values such as 200 °C.
})

TARGET_FIELDS = [
    "object_part_no",
    "manufacturer_trademark",
    "type_model",
    "technical_data",
    "standard",
    "marks_of_conformity",
]
TARGET_OUTPUT_COLUMNS = [
    "page",
    "table_index_on_page",
    "row_index_on_table",
    "engine",
    "row_type",
    "section_title",
    "product_model",
    "object_part_no",
    "manufacturer_trademark",
    "type_model",
    "technical_data",
    "standard",
    "marks_of_conformity",
]

TARGET_HEADER_HINTS: Dict[str, List[str]] = {
    "object_part_no": [
        "objectpart",
        "objectpartno",
        "partno",
        "partnumber",
        "object",
        "alternative",
    ],
    "manufacturer_trademark": ["manufacturer", "manufacture", "trademark"],
    "type_model": ["typemodel", "type", "model"],
    "technical_data": ["technicaldata", "technical"],
    "standard": ["standard", "standards"],
    "marks_of_conformity": ["marksofconformity", "marksof", "conformity", "mark"],
}

STRICT_HEADER_HINTS: Dict[str, List[str]] = {
    "object_part_no": ["objectpart", "objectpartno"],
    "manufacturer_trademark": ["manufacturer", "trademark"],
    "type_model": ["typemodel", "type", "model"],
    "technical_data": ["technicaldata", "technical"],
    "standard": ["standard"],
    "marks_of_conformity": ["marksofconformity", "marksof", "conformity"],
}

STRICT_HEADER_FIELD_ORDER = [
    "object_part_no",
    "manufacturer_trademark",
    "type_model",
    "technical_data",
    "standard",
    "marks_of_conformity",
]

MARKED_TABLE_PATTERNS: Dict[str, List[str]] = {
    "components_information": [
        "tablecomponentsinformation",
        "tablecriticalcomponentsinformation",
        "componentsinformation",
        "criticalcomponentsinformation",
    ],
    "list_of_compressor": [
        "tablelistofcompressor",
        "listofcompressor",
        "compressorlist",
    ],
}

ENABLE_SUPERSCRIPT_FIX = False
FLATTEN_CELL_NEWLINES = False
SUPERSCRIPT_MAP = {"2": "²", "3": "³"}
AREA_VOLUME_UNIT_RE = re.compile(
    r"(?i)\b(mm|cm|dm|m|km|in|ft|yd)\s*(?:\^|\*\*)?\s*([23])\b"
)
TECH_AREA_UNIT_RE = re.compile(r"(?<![A-Za-z])(mm|cm|dm|m|km)\s*(?:\^|\*\*)?\s*2\b")


@dataclass
class TableCandidate:
    engine: str
    page: int
    index_on_page: int
    df: pd.DataFrame
    score: float
    details: str

    @property
    def sheet_name(self) -> str:
        return f"p{self.page}_t{self.index_on_page}_{self.engine}"[:31]


def normalize_cell(value: object) -> str:
    # Treat None/NaN/pandas NA as empty. This matters a lot across environments:
    # some pdfplumber->pandas paths yield empty cells as `nan`/`pd.NA`, and if we
    # stringify them we get "nan"/"<NA>", which breaks empty-column detection
    # and downstream header/continuation mapping.
    if value is None:
        return ""
    try:
        if pd.isna(value):  # type: ignore[arg-type]
            return ""
    except Exception:
        # Some objects don't play well with pd.isna (e.g., lists); fall back.
        return ""
    text = str(value).translate(PDF_SYMBOL_CHAR_TRANSLATION)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Normalize extra spaces in each line.
    lines = [MULTISPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    if FLATTEN_CELL_NEWLINES:
        # Replace intra-cell line breaks with spaces.
        normalized = " ".join([line for line in lines if line != ""]).strip()
    else:
        # Preserve intentional empty lines inside a cell.
        normalized = "\n".join(lines).strip()
    if ENABLE_SUPERSCRIPT_FIX:
        normalized = restore_superscript_units(normalized)
    return normalized


def normalize_for_match(text: str) -> str:
    return MATCH_NORMALIZE_RE.sub("", text.lower())


def restore_superscript_units(text: str) -> str:
    """
    Heuristic recovery for cases where superscript ²/³ is extracted as plain 2/3.
    Only targets common area/volume unit forms to avoid corrupting model numbers.
    """
    if text == "":
        return text

    def repl(match: re.Match[str]) -> str:
        unit = match.group(1)
        power = match.group(2)
        return f"{unit}{SUPERSCRIPT_MAP.get(power, power)}"

    return AREA_VOLUME_UNIT_RE.sub(repl, text)


def normalize_technical_data_text(text: str) -> str:
    """
    Field-specific normalization for technical_data:
    force mm2/cm2/dm2/m2/km2 (and ^2/**2 variants) -> superscript ².
    """
    if text == "":
        return text

    def repl(match: re.Match[str]) -> str:
        unit = match.group(1)
        return f"{unit}²"

    return TECH_AREA_UNIT_RE.sub(repl, text)


def split_label_value(text: str) -> tuple[str, str]:
    s = normalize_cell(text)
    if ":" in s:
        left, right = s.split(":", 1)
        return left.strip().lower(), right.strip()
    return s.strip().lower(), ""


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    cleaned = df.copy()
    # pandas 2.1+ prefers DataFrame.map over applymap.
    if hasattr(cleaned, "map"):
        cleaned = cleaned.map(normalize_cell)
    else:
        cleaned = cleaned.applymap(normalize_cell)
    # Drop fully empty rows/columns.
    cleaned = cleaned.loc[~(cleaned == "").all(axis=1)]
    cleaned = cleaned.loc[:, ~(cleaned == "").all(axis=0)]
    cleaned = cleaned.reset_index(drop=True)
    cleaned.columns = [f"col_{i+1}" for i in range(cleaned.shape[1])]
    return cleaned


def calc_candidate_score(df: pd.DataFrame) -> tuple[float, str]:
    """
    Heuristic score for table quality. Higher is better.
    Designed to prefer stable structure + meaningful content.
    """
    if df.empty:
        return -1e9, "empty"

    rows, cols = df.shape
    total_cells = rows * cols

    text_cells = 0
    non_empty_cells = 0
    long_cells = 0
    newline_cells = 0
    lengths: List[int] = []

    for v in df.to_numpy().flatten().tolist():
        txt = normalize_cell(v)
        if txt != "":
            non_empty_cells += 1
            lengths.append(len(txt))
            if any(ch.isalpha() for ch in txt) or any(ch.isdigit() for ch in txt):
                text_cells += 1
            if len(txt) >= 25:
                long_cells += 1
            if "\n" in txt:
                newline_cells += 1

    fill_ratio = non_empty_cells / total_cells if total_cells else 0.0
    text_ratio = text_cells / total_cells if total_cells else 0.0
    long_ratio = long_cells / total_cells if total_cells else 0.0
    newline_ratio = newline_cells / total_cells if total_cells else 0.0

    # Structure stability: avoid highly irregular rows after splitting.
    row_non_empty = [int((df.iloc[r] != "").sum()) for r in range(rows)]
    row_std = statistics.pstdev(row_non_empty) if len(row_non_empty) > 1 else 0.0
    row_stability = 1.0 / (1.0 + row_std)

    # Penalize tiny "tables" that are likely noise.
    size_bonus = math.log1p(rows * cols)
    tiny_penalty = 1.0 if (rows < 2 or cols < 2) else 0.0

    score = (
        fill_ratio * 35
        + text_ratio * 20
        + long_ratio * 8
        + newline_ratio * 7
        + row_stability * 15
        + size_bonus * 3
        - tiny_penalty * 20
    )
    details = (
        f"r={rows},c={cols},fill={fill_ratio:.2f},text={text_ratio:.2f},"
        f"multi={newline_ratio:.2f},stable={row_stability:.2f},score={score:.2f}"
    )
    return score, details


def extract_with_camelot(
    pdf_path: Path,
    pages: str,
    flavor: str,
) -> List[TableCandidate]:
    if camelot is None:
        raise RuntimeError(f"camelot import failed: {CAMELOT_IMPORT_ERROR}")

    flavors: Iterable[str]
    if flavor == "both":
        flavors = ("lattice", "stream")
    else:
        flavors = (flavor,)

    candidates: List[TableCandidate] = []
    for flv in flavors:
        read_kwargs = {
            "pages": pages,
            "flavor": flv,
            "strip_text": "\n",
            "split_text": False,
        }
        if flv == "lattice":
            read_kwargs["line_scale"] = 40

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"No tables found in table area.*",
                category=UserWarning,
            )
            tables = camelot.read_pdf(str(pdf_path), **read_kwargs)
        for t in tables:
            cleaned = clean_dataframe(t.df)
            score, details = calc_candidate_score(cleaned)
            candidates.append(
                TableCandidate(
                    engine=f"camelot_{flv}",
                    page=int(getattr(t, "page", 1)),
                    index_on_page=int(getattr(t, "order", 1)),
                    df=cleaned,
                    score=score,
                    details=details,
                )
            )
    return candidates


def extract_with_pdfplumber(pdf_path: Path, pages: str) -> List[TableCandidate]:
    if pdfplumber is None:
        raise RuntimeError(f"pdfplumber import failed: {PDFPLUMBER_IMPORT_ERROR}")

    settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "edge_min_length": 20,
        "min_words_vertical": 1,
        "min_words_horizontal": 1,
        "intersection_tolerance": 3,
        "text_tolerance": 3,
    }

    page_set: Optional[set[int]]
    if pages == "all":
        page_set = None
    else:
        page_set = set()
        for p in pages.split(","):
            p = p.strip()
            if "-" in p:
                s, e = p.split("-", 1)
                page_set.update(range(int(s), int(e) + 1))
            elif p:
                page_set.add(int(p))

    candidates: List[TableCandidate] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            if page_set is not None and i not in page_set:
                continue
            tables = page.extract_tables(table_settings=settings)
            for idx, table in enumerate(tables, start=1):
                if not table:
                    continue
                df = pd.DataFrame(table)
                cleaned = clean_dataframe(df)
                score, details = calc_candidate_score(cleaned)
                candidates.append(
                    TableCandidate(
                        engine="pdfplumber_lines",
                        page=i,
                        index_on_page=idx,
                        df=cleaned,
                        score=score,
                        details=details,
                    )
                )
    return candidates


def deduplicate_candidates(candidates: List[TableCandidate]) -> List[TableCandidate]:
    """
    Keep best candidate per (page, approx shape signature).
    """
    best_by_key: dict[tuple[int, int, int], TableCandidate] = {}
    for c in candidates:
        r, col = c.df.shape if not c.df.empty else (0, 0)
        key = (c.page, r, col)
        prev = best_by_key.get(key)
        if prev is None or c.score > prev.score:
            best_by_key[key] = c
    return sorted(best_by_key.values(), key=lambda x: (x.page, x.index_on_page, -x.score))


def detect_target_header_mapping(df: pd.DataFrame) -> Optional[tuple[int, Dict[str, int]]]:
    best_row_index: Optional[int] = None
    best_mapping: Dict[str, int] = {}

    scan_rows = min(6, len(df))
    for row_idx in range(scan_rows):
        row_values = [normalize_cell(v) for v in df.iloc[row_idx].tolist()]
        row_mapping: Dict[str, int] = {}
        for col_idx, value in enumerate(row_values):
            norm = normalize_for_match(value)
            if not norm:
                continue
            for field, hints in TARGET_HEADER_HINTS.items():
                if field in row_mapping:
                    continue
                if any(hint in norm for hint in hints):
                    row_mapping[field] = col_idx
                    break

        if len(row_mapping) > len(best_mapping):
            best_mapping = row_mapping
            best_row_index = row_idx

    if best_row_index is None or len(best_mapping) < 4:
        return None
    return best_row_index, best_mapping


def detect_components_header_mapping(df: pd.DataFrame) -> Optional[tuple[int, Dict[str, int]]]:
    """
    Strict header detector for:
    Object/part No. | Manufacturer/trademark | Type/model | Technical data | Standard | Mark(s) of conformity
    """
    best_start_row: Optional[int] = None
    best_mapping: Dict[str, int] = {}

    def build_mapping(row_values: List[str]) -> Dict[str, int]:
        row_mapping: Dict[str, int] = {}
        for col_idx, value in enumerate(row_values):
            norm = normalize_for_match(value)
            if not norm:
                continue
            for field, hints in STRICT_HEADER_HINTS.items():
                if field in row_mapping:
                    continue
                if any(hint in norm for hint in hints):
                    row_mapping[field] = col_idx
                    break
        return row_mapping

    scan_rows = min(10, len(df))
    for row_idx in range(scan_rows):
        row_values = [normalize_cell(v) for v in df.iloc[row_idx].tolist()]
        row_mapping = build_mapping(row_values)
        if len(row_mapping) > len(best_mapping):
            best_mapping = row_mapping
            best_start_row = row_idx + 1

        if row_idx + 1 < scan_rows:
            next_row_values = [normalize_cell(v) for v in df.iloc[row_idx + 1].tolist()]
            merged_values = []
            for left, right in zip(row_values, next_row_values):
                merged_values.append("\n".join([v for v in [left, right] if v]))
            merged_mapping = build_mapping(merged_values)
            if len(merged_mapping) > len(best_mapping):
                best_mapping = merged_mapping
                best_start_row = row_idx + 2

    required_core = {
        "object_part_no",
        "manufacturer_trademark",
        "type_model",
        "technical_data",
        "standard",
        "marks_of_conformity",
    }
    if best_start_row is None:
        return None
    if len(best_mapping) < 5:
        return None
    if "object_part_no" not in best_mapping:
        return None
    if len(required_core.intersection(best_mapping.keys())) < 5:
        return None
    return best_start_row, best_mapping


def header_cell_matches_field(norm: str, field: str) -> bool:
    if not norm:
        return False
    if field == "object_part_no":
        has_obj_part = ("object" in norm and "part" in norm) or "objectpart" in norm
        has_no = "no" in norm or "number" in norm or norm.endswith("no")
        return has_obj_part and has_no
    if field == "manufacturer_trademark":
        return ("manufacturer" in norm or "manufacture" in norm) and "trademark" in norm
    if field == "type_model":
        return ("type" in norm and "model" in norm) or "typemodel" in norm
    if field == "technical_data":
        return ("technical" in norm and "data" in norm) or "technicaldata" in norm
    if field == "standard":
        return "standard" in norm
    if field == "marks_of_conformity":
        has_marks = "mark" in norm
        has_conf = "conformity" in norm or "conform" in norm
        return (has_marks and has_conf) or "marksofconformity" in norm
    return False


def detect_components_header_template_mapping(df: pd.DataFrame) -> Optional[tuple[int, Dict[str, int]]]:
    """
    Very strict template detector for supplier style where table header is explicit.
    Supported header families:
    1) Object/part No. | Manufacturer/trademark | Type/model | Technical data | Standard | Mark(s) of conformity...
    2) Item No. | Object/part no. | Manufacturer/Trademark | Type/Model | Technical Data | Standard | Mark(s)...
    """
    if df.empty:
        return None

    def find_ordered_mapping(row_values: List[str]) -> Optional[Dict[str, int]]:
        norms = [normalize_for_match(v) for v in row_values]
        mapping: Dict[str, int] = {}
        prev = -1
        for field in STRICT_HEADER_FIELD_ORDER:
            found = None
            for col_idx in range(prev + 1, len(norms)):
                if header_cell_matches_field(norms[col_idx], field):
                    found = col_idx
                    break
            if found is None:
                return None
            mapping[field] = found
            prev = found

        # Keep it sane: header columns should stay relatively compact.
        span = mapping["marks_of_conformity"] - mapping["object_part_no"]
        if span > 9:
            return None
        return mapping

    best: Optional[tuple[int, Dict[str, int]]] = None
    scan_rows = min(12, len(df))
    for row_idx in range(scan_rows):
        row_values = [normalize_cell(v) for v in df.iloc[row_idx].tolist()]
        mapping = find_ordered_mapping(row_values)
        if mapping is not None:
            best = (row_idx + 1, mapping)
            break

        if row_idx + 1 < scan_rows:
            next_row_values = [normalize_cell(v) for v in df.iloc[row_idx + 1].tolist()]
            merged_values = []
            for left, right in zip(row_values, next_row_values):
                merged_values.append("\n".join([v for v in [left, right] if v]))
            merged_mapping = find_ordered_mapping(merged_values)
            if merged_mapping is not None:
                best = (row_idx + 2, merged_mapping)
                break

    return best


def detect_best_components_header_mapping(df: pd.DataFrame) -> Optional[tuple[int, Dict[str, int]]]:
    # Prefer the stricter ordered detector: it correctly skips split header
    # continuation rows such as "No." / "conformity1)".
    return detect_components_header_template_mapping(df) or detect_components_header_mapping(df)


def candidate_normalized_text(candidate: TableCandidate) -> str:
    values = [normalize_cell(v) for v in candidate.df.to_numpy().flatten().tolist()]
    return normalize_for_match(" ".join(values))

def candidate_has_any_table_marker(candidate: TableCandidate) -> bool:
    values = [normalize_cell(v) for v in candidate.df.to_numpy().flatten().tolist()]
    raw = " ".join(values)
    return bool(TABLE_MARKER_RE.search(raw))


def detect_marked_table_kind(candidate: TableCandidate) -> Optional[str]:
    flat = candidate_normalized_text(candidate)
    for kind, patterns in MARKED_TABLE_PATTERNS.items():
        if any(pattern in flat for pattern in patterns):
            return kind
    return None


def choose_six_window(row_values: List[str]) -> Optional[int]:
    if len(row_values) < 6:
        return None

    best_start: Optional[int] = None
    best_score = -1e9
    for start in range(0, len(row_values) - 6 + 1):
        w = row_values[start : start + 6]
        non_empty = sum(1 for cell in w if cell != "")
        score = float(non_empty * 2)

        if PART_HINT_PATTERN.search(w[0]):
            score += 2.0
        if ":" in w[3] or "\n" in w[3]:
            score += 1.0
        if STANDARD_PATTERN.search(w[4] or ""):
            score += 3.0
        if MARK_PATTERN.search(w[5] or ""):
            score += 4.0
        if non_empty < 4:
            score -= 6.0

        if score > best_score:
            best_score = score
            best_start = start

    if best_start is None or best_score < 6.0:
        return None
    return best_start


def is_valid_target_record(record: Dict[str, str]) -> bool:
    values = [record.get(field, "") for field in TARGET_FIELDS]
    non_empty = sum(1 for v in values if v != "")
    if non_empty < 4:
        return False

    has_standard = bool(STANDARD_PATTERN.search(record.get("standard", "")))
    has_mark = bool(MARK_PATTERN.search(record.get("marks_of_conformity", "")))
    has_tech = record.get("technical_data", "") != ""
    return has_standard or has_mark or has_tech


def detect_section_row(row_values: List[str]) -> Optional[str]:
    non_empty = [value.strip() for value in row_values if value.strip()]
    if len(non_empty) != 1:
        return None

    title = non_empty[0]
    title_norm = normalize_for_match(title)
    if not title_norm:
        return None
    if title_norm in ("alternative", "componentsinformation"):
        return None
    if "object" in title_norm and "part" in title_norm and ("no" in title_norm or "number" in title_norm):
        return None
    if SECTION_ROW_PATTERN.match(title) is None and SECTION_RELATION_MODEL_PATTERN.search(title) is None:
        return None
    return title


def collect_records_with_mapping(
    candidate: TableCandidate,
    mapping: Dict[str, int],
    start_row: int,
    strict_components: bool = False,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for row_idx in range(start_row, len(candidate.df)):
        row_values = [normalize_cell(v) for v in candidate.df.iloc[row_idx].tolist()]
        section_title = detect_section_row(row_values)
        if section_title is not None:
            rows.append(
                {
                    "page": candidate.page,
                    "table_index_on_page": candidate.index_on_page,
                    "row_index_on_table": row_idx,
                    "engine": candidate.engine,
                    "row_type": "section",
                    "section_title": section_title,
                    "object_part_no": section_title,
                    "manufacturer_trademark": "",
                    "type_model": "",
                    "technical_data": "",
                    "standard": "",
                    "marks_of_conformity": "",
                    "_section_row": True,
                }
            )
            continue

        record: Dict[str, object] = {
            "page": candidate.page,
            "table_index_on_page": candidate.index_on_page,
            "row_index_on_table": row_idx,
            "engine": candidate.engine,
            "row_type": "detail",
            "section_title": "",
        }
        for field in TARGET_FIELDS:
            col = mapping.get(field)
            value = row_values[col] if col is not None and col < len(row_values) else ""
            if field == "technical_data":
                value = normalize_technical_data_text(value)
            record[field] = value

        if strict_components:
            obj = str(record.get("object_part_no", "")).strip().lower()
            obj_norm = normalize_for_match(obj)
            mfg = str(record.get("manufacturer_trademark", "")).strip()
            typ = str(record.get("type_model", "")).strip()
            tech = str(record.get("technical_data", "")).strip()
            std = str(record.get("standard", "")).strip()
            mark = str(record.get("marks_of_conformity", "")).strip()
            mark_norm = normalize_for_match(mark)

            if obj.startswith("remark"):
                continue
            if "table:" in obj and "components information" in obj:
                continue
            if "object" in obj and "part" in obj and ("no" in obj or "number" in obj):
                continue
            if obj_norm in ("no", "number") and (
                "conformity" in mark_norm or (not mfg and not typ and not tech and not std)
            ):
                continue
            rows.append(record)
        else:
            if is_valid_target_record(record):  # type: ignore[arg-type]
                rows.append(record)
    return rows


def postprocess_marked_rows(rows: List[Dict[str, object]], kind: Optional[str] = None) -> List[Dict[str, object]]:
    """
    Fix common extractor artifacts for marked 6-column tables:
    - Wrapped cell content emitted as extra rows with empty first column
    - "(Alternative)" split into its own row
    """
    merged: List[Dict[str, object]] = []

    def non_object_non_empty_count(row: Dict[str, object]) -> int:
        return sum(1 for field in TARGET_FIELDS[1:] if normalize_cell(row.get(field, "")) != "")

    def should_merge_into_previous(obj_raw: str, obj_norm: str, row: Dict[str, object]) -> bool:
        # Empty object column is typically a wrapped continuation line.
        if obj_raw.strip() == "":
            return True
        # "Alternative" as a standalone row can mean either:
        # 1) a continuation marker (should merge), or
        # 2) a full independent record (must stay separate).
        # Only merge when the row is sparse enough to look like a marker/continuation.
        if obj_norm in ("alternative", "(alternative)"):
            return non_object_non_empty_count(row) <= 1
        return False

    def merge_field(prev: Dict[str, object], field: str, value: str) -> None:
        if not value:
            return
        cur = normalize_cell(prev.get(field, ""))
        if cur == "":
            prev[field] = value
            return
        if value in cur:
            return
        prev[field] = (cur + "\n" + value).strip()

    for row in rows:
        obj_raw = normalize_cell(row.get("object_part_no", ""))
        obj_norm = normalize_for_match(obj_raw)

        if merged:
            if should_merge_into_previous(obj_raw, obj_norm, row):
                prev = merged[-1]
                # Preserve the alternative marker by appending to object name.
                if obj_norm in ("alternative", "(alternative)"):
                    prev_obj = normalize_cell(prev.get("object_part_no", ""))
                    if "alternative" not in prev_obj.lower():
                        prev["object_part_no"] = (prev_obj + "\n" + obj_raw).strip()

                for field in TARGET_FIELDS[1:]:
                    merge_field(prev, field, normalize_cell(row.get(field, "")))
                continue

        merged.append(row)

    # Final strict filter: must have object + at least one of tech/standard/marks.
    filtered: List[Dict[str, object]] = []
    for row in merged:
        if row.get("_section_row"):
            filtered.append(row)
            continue
        obj = normalize_cell(row.get("object_part_no", ""))
        tech = normalize_cell(row.get("technical_data", ""))
        std = normalize_cell(row.get("standard", ""))
        mark = normalize_cell(row.get("marks_of_conformity", ""))
        if obj == "":
            continue
        if kind == "list_of_compressor":
            if "compressor" not in obj.lower():
                continue
        if tech == "" and std == "" and mark == "":
            continue
        filtered.append(row)

    return filtered


def compress_mapping_by_empty_columns(
    source_df: pd.DataFrame,
    mapping: Dict[str, int],
    start_row: int,
    sample_rows: int = 12,
) -> Optional[Dict[str, int]]:
    """
    Some extractors (notably pdfplumber) may insert a fully-empty column, causing
    header mapping like: object=0, manufacturer=2, ..., marks=6.
    Continuation pages often drop that empty column, becoming 6 columns wide.
    """
    if source_df.empty:
        return None
    ncols = source_df.shape[1]
    if ncols <= 1:
        return None

    end_row = min(len(source_df), start_row + sample_rows)
    if start_row >= end_row:
        start_row = 0
        end_row = min(len(source_df), sample_rows)

    empty_cols: set[int] = set()
    for col_idx in range(ncols):
        all_empty = True
        for row_idx in range(start_row, end_row):
            if normalize_cell(source_df.iat[row_idx, col_idx]) != "":
                all_empty = False
                break
        if all_empty:
            empty_cols.add(col_idx)

    # Don't compress away mapped columns.
    if any(idx in empty_cols for idx in mapping.values()):
        return None

    kept = [i for i in range(ncols) if i not in empty_cols]
    if len(kept) == ncols:
        return dict(mapping)

    new_index: Dict[int, int] = {}
    for new_i, old_i in enumerate(kept):
        new_index[old_i] = new_i

    compressed: Dict[str, int] = {}
    for field, old_idx in mapping.items():
        if old_idx not in new_index:
            return None
        compressed[field] = new_index[old_idx]

    return compressed


def score_marked_table_candidate(
    candidate: TableCandidate,
    mapping: Dict[str, int],
    start_row: int,
    kind_override: Optional[str] = None,
) -> tuple[float, List[Dict[str, object]]]:
    rows = collect_records_with_mapping(
        candidate=candidate,
        mapping=mapping,
        start_row=start_row,
        strict_components=True,
    )
    kind = kind_override or detect_marked_table_kind(candidate)
    rows = postprocess_marked_rows(rows, kind=kind)
    object_non_empty = sum(1 for row in rows if normalize_cell(row.get("object_part_no", "")) != "")
    column_bonus = max(0, 12 - abs(candidate.df.shape[1] - 6) * 4)
    score = candidate.score + len(rows) * 15 + object_non_empty * 8 + column_bonus
    return score, rows


def extract_marked_table_records(selected: List[TableCandidate]) -> List[Dict[str, object]]:
    """
    Prefer explicit marked tables such as:
    - TABLE: Components information
    - TABLE: list of compressor

    Once such a table is found, continue extracting following pages using the
    same 6-column mapping until the table stops yielding rows.
    """
    records: List[Dict[str, object]] = []
    candidates = sorted(selected, key=lambda c: (c.page, c.index_on_page))
    by_page: Dict[int, List[TableCandidate]] = {}
    for candidate in candidates:
        by_page.setdefault(candidate.page, []).append(candidate)

    max_page = max(by_page.keys(), default=0)

    def page_has_other_marker(page_candidates: List[TableCandidate], active_kind: str) -> bool:
        for c in page_candidates:
            kind = detect_marked_table_kind(c)
            if kind is not None and kind != active_kind:
                return True
        return False

    for kind in MARKED_TABLE_PATTERNS.keys():
        best_start: Optional[TableCandidate] = None
        best_mapping: Optional[Dict[str, int]] = None
        best_start_row = 0
        best_rows: List[Dict[str, object]] = []

        for candidate in candidates:
            if detect_marked_table_kind(candidate) != kind:
                continue
            header = detect_best_components_header_mapping(candidate.df)
            if header is None:
                continue
            start_row, mapping = header
            _, rows = score_marked_table_candidate(candidate, mapping, start_row=start_row, kind_override=kind)
            if rows:
                best_start = candidate
                best_mapping = mapping
                best_start_row = start_row
                best_rows = rows
                break

        if best_start is None or best_mapping is None or not best_rows:
            continue

        records.extend(best_rows)
        active_mapping = best_mapping
        active_cols = best_start.df.shape[1]
        active_start_row = best_start_row
        misses = 0

        # Follow forward until another marked table starts, or extraction stops yielding rows.
        for page in range(best_start.page + 1, max_page + 1):
            page_candidates = by_page.get(page, [])
            if not page_candidates:
                break

            # Table boundary rule:
            # If this page contains a new TABLE: (different kind), we still try to
            # extract continuation rows from candidates *without* TABLE: markers,
            # then stop after this page.
            has_same_marker = any(detect_marked_table_kind(c) == kind for c in page_candidates)
            has_other_table_marker = any(
                candidate_has_any_table_marker(c) and detect_marked_table_kind(c) != kind
                for c in page_candidates
            )
            boundary_page = has_other_table_marker and not has_same_marker

            continuation_best_score = -1e9
            continuation_best_rows: List[Dict[str, object]] = []
            continuation_best_mapping: Optional[Dict[str, int]] = None

            for candidate in page_candidates:
                # If this page repeats the same marker, allow resetting mapping.
                c_kind = detect_marked_table_kind(candidate)
                header = detect_best_components_header_mapping(candidate.df)
                if c_kind == kind and header is not None:
                    start_row, mapping = header
                    score, rows = score_marked_table_candidate(candidate, mapping, start_row=start_row, kind_override=kind)
                    if rows and score > continuation_best_score:
                        continuation_best_score = score
                        continuation_best_rows = rows
                        continuation_best_mapping = mapping
                    continue

                # If we're at a boundary page, ignore all candidates that contain TABLE:
                # (they belong to a new table). Only consider the continuation chunks.
                if boundary_page and candidate_has_any_table_marker(candidate):
                    continue

                if c_kind is not None:
                    continue

                # Continuation pages usually keep the same column layout.
                if abs(candidate.df.shape[1] - active_cols) > 2:
                    continue
                use_mapping = active_mapping
                max_col = max(use_mapping.values(), default=-1)
                if max_col >= candidate.df.shape[1]:
                    compressed = compress_mapping_by_empty_columns(
                        source_df=best_start.df,
                        mapping=active_mapping,
                        start_row=active_start_row,
                    )
                    if compressed is None:
                        continue
                    use_mapping = compressed
                    max_col = max(use_mapping.values(), default=-1)
                    if max_col >= candidate.df.shape[1]:
                        continue
                score, rows = score_marked_table_candidate(candidate, use_mapping, start_row=0, kind_override=kind)
                if rows and score > continuation_best_score:
                    continuation_best_score = score
                    continuation_best_rows = rows
                    continuation_best_mapping = None

            if continuation_best_rows:
                records.extend(continuation_best_rows)
                misses = 0
                if continuation_best_mapping is not None:
                    active_mapping = continuation_best_mapping
                    active_cols = active_cols
            else:
                misses += 1
                # Some PDFs have a blank/failed extraction page; allow a small gap.
                if misses >= 2:
                    break

            # After a boundary page, stop following this table (we've consumed the tail).
            if boundary_page:
                break

    return records


def extract_header_tracked_components_records(selected: List[TableCandidate]) -> List[Dict[str, object]]:
    """
    Fallback when PDFs don't include explicit "TABLE:" markers:
    find the best strict 6-column header match and follow its continuation pages.
    """
    records: List[Dict[str, object]] = []
    candidates = sorted(selected, key=lambda c: (c.page, c.index_on_page))
    by_page: Dict[int, List[TableCandidate]] = {}
    for candidate in candidates:
        by_page.setdefault(candidate.page, []).append(candidate)

    max_page = max(by_page.keys(), default=0)

    best_start: Optional[TableCandidate] = None
    best_mapping: Optional[Dict[str, int]] = None
    best_start_row = 0
    best_score = -1e9
    best_rows: List[Dict[str, object]] = []

    for candidate in candidates:
        header = detect_components_header_template_mapping(candidate.df)
        if header is None:
            header = detect_components_header_mapping(candidate.df)
        if header is None:
            continue
        start_row, mapping = header
        score, rows = score_marked_table_candidate(
            candidate, mapping, start_row=start_row, kind_override="components_information"
        )
        if rows and score > best_score:
            best_score = score
            best_start = candidate
            best_mapping = mapping
            best_start_row = start_row
            best_rows = rows

    if best_start is None or best_mapping is None or not best_rows:
        return []

    records.extend(best_rows)
    active_mapping = best_mapping
    active_cols = best_start.df.shape[1]
    active_start_row = best_start_row

    for page in range(best_start.page + 1, max_page + 1):
        page_candidates = by_page.get(page, [])
        if not page_candidates:
            break

        # Stop if another explicitly marked table starts.
        if any(detect_marked_table_kind(c) is not None for c in page_candidates):
            break

        continuation_best_score = -1e9
        continuation_best_rows: List[Dict[str, object]] = []
        continuation_best_mapping: Optional[Dict[str, int]] = None

        for candidate in page_candidates:
            header = detect_components_header_template_mapping(candidate.df)
            if header is None:
                header = detect_components_header_mapping(candidate.df)
            if header is not None:
                start_row, mapping = header
                score, rows = score_marked_table_candidate(
                    candidate, mapping, start_row=start_row, kind_override="components_information"
                )
                if rows and score > continuation_best_score:
                    continuation_best_score = score
                    continuation_best_rows = rows
                    continuation_best_mapping = mapping
                continue

            if abs(candidate.df.shape[1] - active_cols) > 2:
                continue
            use_mapping = active_mapping
            max_col = max(use_mapping.values(), default=-1)
            if max_col >= candidate.df.shape[1]:
                compressed = compress_mapping_by_empty_columns(
                    source_df=best_start.df,
                    mapping=active_mapping,
                    start_row=active_start_row,
                )
                if compressed is None:
                    continue
                use_mapping = compressed
                max_col = max(use_mapping.values(), default=-1)
                if max_col >= candidate.df.shape[1]:
                    continue
            score, rows = score_marked_table_candidate(
                candidate, use_mapping, start_row=0, kind_override="components_information"
            )
            if rows and score > continuation_best_score:
                continuation_best_score = score
                continuation_best_rows = rows
                continuation_best_mapping = None

        if continuation_best_rows:
            records.extend(continuation_best_rows)
            if continuation_best_mapping is not None:
                active_mapping = continuation_best_mapping
        else:
            break

    return records


def extract_explicit_header_tables_records(selected: List[TableCandidate]) -> List[Dict[str, object]]:
    """
    Strict mode for supplier PDFs without TABLE: markers but with explicit
    target header on each page/table. Only extract tables matching the known
    6-column header templates.
    """
    records: List[Dict[str, object]] = []
    for candidate in sorted(selected, key=lambda c: (c.page, c.index_on_page)):
        header = detect_components_header_template_mapping(candidate.df)
        if header is None:
            continue
        start_row, mapping = header
        _, rows = score_marked_table_candidate(
            candidate,
            mapping,
            start_row=start_row,
            kind_override="components_information",
        )
        if rows:
            records.extend(rows)
    return records


def extract_marks(text: str) -> str:
    marks: List[str] = []
    for token in MARK_PATTERN.findall(text):
        t = token.upper()
        if t not in marks:
            marks.append(t)
    for token in UL_FILE_PATTERN.findall(text):
        t = token.upper()
        if t not in marks:
            marks.append(t)
    return ", ".join(marks)


def parse_alternative_blocks(candidate: TableCandidate) -> List[Dict[str, object]]:
    """
    Parse key-value style blocks used by some suppliers:
    "Alternative X:" + rows like "Manufacturer:", "Designation:", "Rating:", etc.
    """
    df = candidate.df
    if df.empty or df.shape[1] < 2:
        return []

    rows = [[normalize_cell(v) for v in df.iloc[i].tolist()] for i in range(len(df))]
    row_texts = [" | ".join([x for x in row if x]).strip() for row in rows]

    starts: List[int] = []
    for i, text in enumerate(row_texts):
        if ALT_SECTION_PATTERN.search(text):
            starts.append(i)
    if not starts:
        return []

    starts.append(len(rows))
    results: List[Dict[str, object]] = []

    for idx in range(len(starts) - 1):
        start = starts[idx]
        end = starts[idx + 1]
        block = rows[start:end]
        if not block:
            continue

        title = row_texts[start]
        attrs: Dict[str, str] = {}
        last_key: Optional[str] = None

        for r_i, row in enumerate(block[1:], start=1):
            combined = " ".join([x for x in row if x]).strip()
            if combined == "":
                continue

            first = row[0] if row else ""
            label, inline_value = split_label_value(first)
            rest = " ".join([x for x in row[1:] if x]).strip()
            row_value = " ".join([inline_value, rest]).strip()

            is_header_noise = (
                "project:" in label
                or "report:" in label
                or "page no" in label
                or "master contract" in label
            )
            if is_header_noise:
                continue

            has_label = ":" in first or label.endswith(")")
            if has_label and label:
                if row_value:
                    attrs[label] = (attrs.get(label, "") + ("\n" if attrs.get(label) else "") + row_value).strip()
                last_key = label
            else:
                # continuation line
                if last_key:
                    attrs[last_key] = (attrs.get(last_key, "") + "\n" + combined).strip()
                elif r_i == 1 and combined:
                    # sometimes first value spills without explicit key.
                    attrs["details"] = (attrs.get("details", "") + ("\n" if attrs.get("details") else "") + combined).strip()

        if len(attrs) == 0:
            continue

        def pick(*keys: str) -> str:
            for k in keys:
                if k in attrs and attrs[k]:
                    return attrs[k]
            return ""

        manufacturer = pick("manufacturer", "material manufacturer", "manufacture", "trademark")
        type_model = pick("designation", "model", "material designation", "type", "type/model")

        # Merge likely technical detail fields.
        technical_bits: List[str] = []
        technical_keys = [
            "technical data",
            "rating",
            "thickness(mm)",
            "thickness",
            "dimensions(mm)",
            "dimensions",
            "mounting",
            "refrigerant",
            "flammability rating",
            "insulation system",
            "details",
        ]
        for k in technical_keys:
            if k in attrs and attrs[k]:
                technical_bits.append(f"{k}: {attrs[k]}")
        technical_data = normalize_technical_data_text("\n".join(technical_bits).strip())

        all_text = "\n".join([title] + [f"{k}: {v}" for k, v in attrs.items()])
        standard_tokens: List[str] = []
        for token in STANDARD_PATTERN.findall(all_text):
            t = token.upper()
            if t not in standard_tokens:
                standard_tokens.append(t)

        standard = ", ".join(standard_tokens)
        marks = extract_marks(all_text)

        record = {
            "page": candidate.page,
            "table_index_on_page": candidate.index_on_page,
            "row_index_on_table": start,
            "engine": candidate.engine,
            "object_part_no": title,
            "manufacturer_trademark": manufacturer,
            "type_model": type_model,
            "technical_data": technical_data,
            "standard": standard,
            "marks_of_conformity": marks,
        }
        if is_valid_target_record(record):
            results.append(record)

    return results


def extract_target_records_from_candidate(
    candidate: TableCandidate,
    carry_mapping: Optional[Dict[str, int]] = None,
) -> List[Dict[str, object]]:
    df = candidate.df
    if df.empty:
        return []

    found: List[Dict[str, object]] = []
    header = detect_target_header_mapping(df)

    if header is not None:
        header_row_idx, mapping = header
        found = collect_records_with_mapping(
            candidate=candidate,
            mapping=mapping,
            start_row=header_row_idx + 1,
            strict_components=False,
        )
        if found:
            return found
    elif carry_mapping is not None:
        max_col = max(carry_mapping.values(), default=-1)
        if max_col < df.shape[1]:
            found = collect_records_with_mapping(
                candidate=candidate,
                mapping=carry_mapping,
                start_row=0,
                strict_components=False,
            )
            if found:
                return found

    # Supplier variant: key-value "Alternative ..." blocks.
    alt_records = parse_alternative_blocks(candidate)
    if alt_records:
        return alt_records

    if df.shape[1] >= 6:
        for row_idx in range(len(df)):
            row_values = [normalize_cell(v) for v in df.iloc[row_idx].tolist()]
            start = choose_six_window(row_values)
            if start is None:
                continue
            window = row_values[start : start + 6]
            record = {
                "page": candidate.page,
                "table_index_on_page": candidate.index_on_page,
                "row_index_on_table": row_idx,
                "engine": candidate.engine,
                "object_part_no": window[0],
                "manufacturer_trademark": window[1],
                "type_model": window[2],
                "technical_data": window[3],
                "standard": window[4],
                "marks_of_conformity": window[5],
            }
            record["technical_data"] = normalize_technical_data_text(
                normalize_cell(record.get("technical_data", ""))
            )
            if is_valid_target_record(record):
                found.append(record)

    return found


def deduplicate_target_records(records: List[Dict[str, object]]) -> List[Dict[str, object]]:
    deduped: List[Dict[str, object]] = []
    seen: set[tuple[object, ...]] = set()

    sorted_records = sorted(
        records,
        key=lambda r: (
            int(r.get("page", 0)),
            int(r.get("table_index_on_page", 0)),
            int(r.get("row_index_on_table", 0)),
        ),
    )
    for row in sorted_records:
        if row.get("row_type") == "section":
            key = (
                "section",
                int(row.get("page", 0)),
                int(row.get("table_index_on_page", 0)),
                int(row.get("row_index_on_table", 0)),
                normalize_cell(row.get("section_title", "")).lower(),
            )
        else:
            key = (normalize_cell(row.get("product_model", "")).lower(),) + tuple(
                normalize_cell(row.get(field, "")).lower()  # type: ignore[arg-type]
                for field in TARGET_FIELDS
            )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def select_candidates(
    input_pdf: Path,
    pages: str,
    flavor: str,
    engine: str,
    min_score: float,
    dedup: bool = True,
) -> tuple[List[TableCandidate], List[TableCandidate]]:
    all_candidates: List[TableCandidate] = []

    if engine in ("camelot", "all"):
        camelot_candidates = extract_with_camelot(input_pdf, pages=pages, flavor=flavor)
        all_candidates.extend(camelot_candidates)

    if engine in ("pdfplumber", "all"):
        plumber_candidates = extract_with_pdfplumber(input_pdf, pages=pages)
        all_candidates.extend(plumber_candidates)

    if not all_candidates:
        raise RuntimeError(
            f"No tables detected with engine='{engine}'. "
            "If PDF is scanned, run OCR first; or try a page range with clear table lines."
        )

    selected = deduplicate_candidates(all_candidates) if dedup else sorted(
        all_candidates, key=lambda x: (x.page, x.index_on_page, -x.score)
    )
    selected = [c for c in selected if c.score >= min_score and not c.df.empty]
    return all_candidates, selected


def extract_target_records(
    input_pdf: Path,
    pages: str,
    flavor: str,
    engine: str,
    min_score: float,
    target_policy: str = "focused",
    dedup: bool = True,
    template: str = "auto",
) -> pd.DataFrame:
    _, selected = select_candidates(
        input_pdf=input_pdf,
        pages=pages,
        flavor=flavor,
        engine=engine,
        min_score=min_score,
        dedup=dedup,
    )

    if target_policy not in ("focused", "broad"):
        raise ValueError("target_policy must be 'focused' or 'broad'")

    known_template_records = extract_known_template_records(
        selected,
        normalize_cell=normalize_cell,
        normalize_technical_data=normalize_technical_data_text,
        template=template,
    )
    if known_template_records is not None:
        if dedup:
            known_template_records = deduplicate_target_records(known_template_records)
        return pd.DataFrame(known_template_records, columns=TARGET_OUTPUT_COLUMNS)

    if target_policy == "focused":
        # Phase 1: if the PDF explicitly labels the target table, follow that table first.
        marked_records = extract_marked_table_records(selected)
        if dedup:
            marked_records = deduplicate_target_records(marked_records)
        if marked_records:
            return pd.DataFrame(marked_records, columns=TARGET_OUTPUT_COLUMNS)

        # Phase 2: strict template headers (for PDFs that repeat explicit header each page).
        strict_template_records = extract_explicit_header_tables_records(selected)
        if dedup:
            strict_template_records = deduplicate_target_records(strict_template_records)
        if strict_template_records:
            return pd.DataFrame(strict_template_records, columns=TARGET_OUTPUT_COLUMNS)

        # Phase 3: strict header tracking fallback (for one-header + continuation pages).
        header_tracked = extract_header_tracked_components_records(selected)
        if dedup:
            header_tracked = deduplicate_target_records(header_tracked)
        if header_tracked:
            return pd.DataFrame(header_tracked, columns=TARGET_OUTPUT_COLUMNS)

    # Broad mode (or focused fallback): cross-supplier generic extraction.
    all_records: List[Dict[str, object]] = []
    last_header_mapping: Optional[Dict[str, int]] = None

    for candidate in sorted(selected, key=lambda c: (c.page, c.index_on_page)):
        header = detect_target_header_mapping(candidate.df)
        if header is not None:
            _, last_header_mapping = header

        all_records.extend(
            extract_target_records_from_candidate(
                candidate=candidate,
                carry_mapping=last_header_mapping,
            )
        )

    records = deduplicate_target_records(all_records) if dedup else all_records
    if not records:
        raise RuntimeError(
            "No target rows found. Try lowering --min-score (e.g. 5) or run --engine all for comparison."
        )

    return pd.DataFrame(records, columns=TARGET_OUTPUT_COLUMNS)


def empty_target_dataframe() -> pd.DataFrame:
    return pd.DataFrame(columns=TARGET_OUTPUT_COLUMNS)


def autosize_and_wrap(writer: pd.ExcelWriter, sheet_name: str, df: pd.DataFrame) -> None:
    wb = writer.book
    ws = writer.sheets[sheet_name]
    wrap_fmt = wb.add_format({"text_wrap": True, "valign": "top"})
    header_fmt = wb.add_format({"bold": True, "bg_color": "#EDEDED", "border": 1})
    cell_fmt = wb.add_format({"text_wrap": True, "valign": "top", "border": 1})

    # Header formatting
    for c in range(df.shape[1]):
        ws.write(0, c, df.columns[c], header_fmt)

    # Body formatting and width estimation (newline aware)
    for col_idx in range(df.shape[1]):
        max_len = len(str(df.columns[col_idx]))
        for row_idx in range(df.shape[0]):
            val = normalize_cell(df.iat[row_idx, col_idx])
            ws.write(row_idx + 1, col_idx, val, cell_fmt)
            parts = val.split("\n") if val else [""]
            max_len = max(max_len, max((len(p) for p in parts), default=0))
        ws.set_column(col_idx, col_idx, min(max(12, max_len + 2), 60), wrap_fmt)

    # Set row heights based on line counts.
    for row_idx in range(df.shape[0]):
        max_lines = 1
        for col_idx in range(df.shape[1]):
            val = normalize_cell(df.iat[row_idx, col_idx])
            max_lines = max(max_lines, val.count("\n") + 1 if val else 1)
        ws.set_row(row_idx + 1, min(15 * max_lines, 180))


def convert_pdf_to_excel(
    input_pdf: Path,
    output_xlsx: Path,
    pages: str = "all",
    flavor: str = "both",
    engine: str = "pdfplumber",
    min_score: float = 8.0,
) -> List[TableCandidate]:
    if not input_pdf.exists():
        raise FileNotFoundError(f"Input file not found: {input_pdf}")
    if input_pdf.suffix.lower() != ".pdf":
        raise ValueError("Input file must be a PDF.")

    all_candidates, selected = select_candidates(
        input_pdf=input_pdf,
        pages=pages,
        flavor=flavor,
        engine=engine,
        min_score=min_score,
    )

    if not selected:
        top = sorted(all_candidates, key=lambda x: x.score, reverse=True)[:5]
        details = "\n".join(
            f"- {c.engine} p{c.page} t{c.index_on_page}: {c.details}" for c in top
        )
        raise RuntimeError(
            "No high-confidence table passed threshold. Top candidates:\n" + details
        )

    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(str(output_xlsx), engine="xlsxwriter") as writer:
        summary_rows = []
        for i, c in enumerate(selected, start=1):
            name = f"T{i}_{c.engine}_p{c.page}"[:31]
            c.df.to_excel(writer, sheet_name=name, index=False)
            autosize_and_wrap(writer, name, c.df)
            summary_rows.append(
                {
                    "sheet": name,
                    "engine": c.engine,
                    "page": c.page,
                    "table_index_on_page": c.index_on_page,
                    "rows": c.df.shape[0],
                    "cols": c.df.shape[1],
                    "score": round(c.score, 2),
                    "details": c.details,
                }
            )
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="SUMMARY", index=False)

    return selected


def export_target_records_to_excel(target_df: pd.DataFrame, output_xlsx: Path) -> None:
    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(str(output_xlsx), engine="xlsxwriter") as writer:
        target_df.to_excel(writer, sheet_name="TARGET_6_ITEMS", index=False)
        autosize_and_wrap(writer, "TARGET_6_ITEMS", target_df)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert PDF tables to Excel (high precision for complex cells)."
    )
    parser.add_argument("input_pdf", type=Path, help="Path to input PDF file")
    parser.add_argument("output_xlsx", type=Path, help="Path to output Excel file")
    parser.add_argument(
        "--pages",
        default="all",
        help="Pages to parse, e.g. all | 1 | 1,3,5 | 2-8 | 1,3-5",
    )
    parser.add_argument(
        "--flavor",
        choices=["lattice", "stream", "both"],
        default="both",
        help="Camelot extraction mode (used when --engine is camelot/all)",
    )
    parser.add_argument(
        "--engine",
        choices=["pdfplumber", "camelot", "all"],
        default="pdfplumber",
        help="Table extractor engine; default keeps only pdfplumber_lines results",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=8.0,
        help="Minimum heuristic score to keep a table",
    )
    parser.add_argument(
        "--target-only",
        action="store_true",
        help="Only export normalized target records (ignore non-target tables)",
    )
    parser.add_argument(
        "--target-policy",
        choices=["focused", "broad"],
        default="focused",
        help="Target extraction policy: focused follows a single table (until next TABLE:); broad keeps all matches",
    )
    parser.add_argument(
        "--no-dedup",
        action="store_true",
        help="Disable all deduplication (both candidate-level and target-row-level)",
    )
    parser.add_argument(
        "--fix-superscript",
        action="store_true",
        help="Recover common unit superscripts (m2/mm2/cm2/m3 -> m²/mm²/cm²/m³)",
    )
    parser.add_argument(
        "--newline-as-space",
        action="store_true",
        help="Replace intra-cell newlines with spaces",
    )
    parser.add_argument(
        "--template",
        default="auto",
        help="Preferred CDF template; unknown or unsuccessful templates fall back to auto detection",
    )
    return parser.parse_args()


def main() -> None:
    global ENABLE_SUPERSCRIPT_FIX, FLATTEN_CELL_NEWLINES
    args = parse_args()
    ENABLE_SUPERSCRIPT_FIX = args.fix_superscript
    FLATTEN_CELL_NEWLINES = args.newline_as_space
    if args.target_only:
        target_df: Optional[pd.DataFrame] = None
        try:
            target_df = extract_target_records(
                input_pdf=args.input_pdf,
                pages=args.pages,
                flavor=args.flavor,
                engine=args.engine,
                min_score=args.min_score,
                target_policy=args.target_policy,
                dedup=not args.no_dedup,
                template=args.template,
            )
        except RuntimeError as exc:
            # Mixed supplier PDFs may need camelot fallback for key-value alternative blocks.
            if args.engine == "pdfplumber":
                print(
                    "No target rows with engine='pdfplumber'. "
                    "Retrying with engine='all' for broader compatibility..."
                )
                try:
                    target_df = extract_target_records(
                        input_pdf=args.input_pdf,
                        pages=args.pages,
                        flavor=args.flavor,
                        engine="all",
                        min_score=args.min_score,
                        target_policy=args.target_policy,
                        dedup=not args.no_dedup,
                        template=args.template,
                    )
                except RuntimeError:
                    print(
                        "No target rows found after fallback. "
                        "Writing empty TARGET_6_ITEMS sheet."
                    )
                    target_df = empty_target_dataframe()
            else:
                print(str(exc))
                print("Writing empty TARGET_6_ITEMS sheet.")
                target_df = empty_target_dataframe()

        if target_df is None:
            target_df = empty_target_dataframe()

        export_target_records_to_excel(target_df, args.output_xlsx)
        print(
            f"Done. Saved {len(target_df)} target row(s) to: {args.output_xlsx.resolve()}"
        )
        return

    selected = convert_pdf_to_excel(
        input_pdf=args.input_pdf,
        output_xlsx=args.output_xlsx,
        pages=args.pages,
        flavor=args.flavor,
        engine=args.engine,
        min_score=args.min_score,
    )
    print(
        f"Done. Saved {len(selected)} table(s) to: {args.output_xlsx.resolve()}"
    )
    print("Detected tables:")
    for c in selected:
        print(f"- {c.engine:16s} p{c.page:<3d} t{c.index_on_page:<3d} {c.details}")


if __name__ == "__main__":
    main()
