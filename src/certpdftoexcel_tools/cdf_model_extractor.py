#!/usr/bin/env python3
"""Extract product models from the first pages of CDF report PDFs."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd
import pdfplumber


OUTPUT_COLUMNS = [
    "文件名",
    "文件路径",
    "页码",
    "型号",
    "匹配规则",
    "原始内容",
    "状态",
    "说明",
]

SPACE_RE = re.compile(r"[ \t\u3000]+")
MODEL_TYPE_RE = re.compile(
    r"(?is)Model\s*/\s*Type\s+reference\s*[.\s]*:\s*(.+?)"
    r"(?=\n\s*(?:Ratings?|Responsible\s+Testing|Tests?\s+performed|Summary\s+of)\b|\Z)"
)
CHINESE_APPLICATION_RE = re.compile(
    r"(?is)本次申请型号\s*[:：]\s*(.+?)"
    r"(?=\n\s*(?:差异说明|主检型号|覆盖型号|试验说明|本次检验)|\Z)"
)
IDENTIFICATION_RE = re.compile(
    r"(?is)(?:Identification|Bezeichnung)\s*/\s*(?:Type\s*no\.?|Typ-Nr\.?)\s*:\s*(.+?)"
    r"(?=\n\s*(?:Order\s+content|Auftrags-Inhalt|Test\s+specification|Prüfgrundlage)\b|\Z)"
)
GENERIC_MODELS_RE = re.compile(
    r"(?im)^\s*Models?\s*:\s*(.+?)\s*$"
)
ROLE_PREFIX_RE = re.compile(
    r"(?i)(?:^|\s)\d+\)\s*(?:Original|Co-license)\s+model\s*:\s*"
)
BRAND_PREFIX_RE = re.compile(r"(?i)\bFor\s+([^,:;\n]+)\s+brand\s*:\s*")
RATING_SUFFIX_RE = re.compile(
    r"\s+\d{2,3}(?:\s*[-–]\s*\d{2,3})?\s*V(?:ac)?(?:\s*[~～])?.*$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class Match:
    page: int
    rule: str
    priority: int
    raw_value: str


def normalize_text(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00ad", "").replace("，", ",").replace("；", ";")
    lines = [SPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def clean_raw_value(value: str) -> str:
    value = normalize_text(value)
    value = re.sub(r"-\s*\n\s*(?=[A-Za-z0-9])", "-", value)
    brands = BRAND_PREFIX_RE.findall(value)
    value = BRAND_PREFIX_RE.sub("\n", value)
    for brand in brands:
        value = re.sub(
            rf"(?im)(^|[,;\n]\s*){re.escape(brand.strip())}\s+(?=[A-Z0-9])",
            r"\1",
            value,
        )
    value = ROLE_PREFIX_RE.sub("\n", value)
    value = RATING_SUFFIX_RE.sub("", value)
    return value.strip(" \n,;:。")


def split_models(raw_value: str) -> list[str]:
    cleaned = clean_raw_value(raw_value)
    if not cleaned:
        return []

    parts = re.split(r"\s*[,;、]\s*|\n+", cleaned)
    models: list[str] = []
    for part in parts:
        model = SPACE_RE.sub(" ", part).strip(" .,:;，；。")
        model = re.sub(r"\s*-\s*", "-", model)
        model = re.sub(
            r"(?i)^\d+\)\s*(?:Original|Co-license)\s+model\s*:\s*",
            "",
            model,
        ).strip()
        if not model or not re.search(r"\d", model):
            continue
        if len(model) > 100 or re.search(r"(?i)\b(?:rating|refrigerant|clause)\b", model):
            continue
        if model not in models:
            models.append(model)
    return models


def table_matches(page: object, page_number: int) -> list[Match]:
    matches: list[Match] = []
    for table in page.extract_tables() or []:
        rows = [
            [normalize_text(cell).replace("\n", " ") for cell in (row or [])]
            for row in (table or [])
        ]
        for row_index, row in enumerate(rows):
            compact = [re.sub(r"[^a-z]", "", cell.lower()) for cell in row]

            for column_index, key in enumerate(compact):
                if key == "models":
                    value = ", ".join(cell for cell in row[column_index + 1 :] if cell)
                    if value:
                        matches.append(Match(page_number, "Models键值行", 80, value))

            model_column = next(
                (index for index, key in enumerate(compact) if key in {"model", "models"}),
                None,
            )
            has_model_list_context = model_column is not None and any(
                key in {"brandname", "rating", "ratedcurrenta"} for key in compact
            )
            if not has_model_list_context:
                continue

            values: list[str] = []
            for data_row in rows[row_index + 1 :]:
                if model_column >= len(data_row):
                    break
                value = data_row[model_column].strip()
                if not value:
                    break
                values.append(value)
            if values:
                matches.append(Match(page_number, "Models列表表格", 80, "\n".join(values)))
    return matches


def text_matches(text: str, page_number: int) -> list[Match]:
    matches: list[Match] = []
    for pattern, rule, priority in (
        (CHINESE_APPLICATION_RE, "本次申请型号", 100),
        (MODEL_TYPE_RE, "Model/Type reference", 90),
        (IDENTIFICATION_RE, "Identification / Type no.", 85),
        (GENERIC_MODELS_RE, "Models文本行", 70),
    ):
        matches.extend(
            Match(page_number, rule, priority, match.group(1))
            for match in pattern.finditer(text)
        )
    return matches


def extract_models(pdf_path: Path, max_pages: int = 5) -> list[dict[str, object]]:
    matches: list[Match] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages[:max_pages], start=1):
            text = normalize_text(page.extract_text(x_tolerance=2, y_tolerance=3) or "")
            matches.extend(text_matches(text, page_number))
            matches.extend(table_matches(page, page_number))

    parsed: list[tuple[Match, list[str]]] = [
        (match, split_models(match.raw_value)) for match in matches
    ]
    parsed = [(match, models) for match, models in parsed if models]
    if not parsed:
        return [result_row(pdf_path, status="未识别", note=f"前{max_pages}页未找到型号")]

    highest_priority = max(match.priority for match, _ in parsed)
    selected = [(match, models) for match, models in parsed if match.priority == highest_priority]

    rows: list[dict[str, object]] = []
    seen_models: set[str] = set()
    for match, models in selected:
        raw_value = clean_raw_value(match.raw_value)
        for model in models:
            dedup_key = model.casefold()
            if dedup_key in seen_models:
                continue
            seen_models.add(dedup_key)
            rows.append(
                result_row(
                    pdf_path,
                    page=match.page,
                    model=model,
                    rule=match.rule,
                    raw_value=raw_value,
                    status="成功",
                )
            )
    return rows


def result_row(
    pdf_path: Path,
    *,
    page: object = "",
    model: str = "",
    rule: str = "",
    raw_value: str = "",
    status: str,
    note: str = "",
) -> dict[str, object]:
    return {
        "文件名": pdf_path.name,
        "文件路径": str(pdf_path.resolve()),
        "页码": page,
        "型号": model,
        "匹配规则": rule,
        "原始内容": raw_value,
        "状态": status,
        "说明": note,
    }


def list_pdfs(input_path: Path, recursive: bool) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".pdf":
            raise ValueError("输入文件必须是 PDF。")
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"输入路径不存在：{input_path}")
    iterator: Iterable[Path] = input_path.rglob("*") if recursive else input_path.iterdir()
    return sorted(path for path in iterator if path.is_file() and path.suffix.lower() == ".pdf")


def default_output_path(input_path: Path) -> Path:
    if input_path.is_file():
        return input_path.with_name(f"{input_path.stem}_型号提取.xlsx")
    return input_path / "型号提取结果.xlsx"


def export_excel(rows: Sequence[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
        frame.to_excel(writer, sheet_name="型号提取结果", index=False)
        workbook = writer.book
        worksheet = writer.sheets["型号提取结果"]
        header_format = workbook.add_format(
            {"bold": True, "font_color": "#FFFFFF", "bg_color": "#1F4E78", "border": 1}
        )
        body_format = workbook.add_format({"valign": "top", "text_wrap": True, "border": 1})
        for column_index, column_name in enumerate(OUTPUT_COLUMNS):
            worksheet.write(0, column_index, column_name, header_format)
        if not frame.empty:
            worksheet.set_column(0, 0, 38, body_format)
            worksheet.set_column(1, 1, 72, body_format)
            worksheet.set_column(2, 2, 8, body_format)
            worksheet.set_column(3, 4, 28, body_format)
            worksheet.set_column(5, 5, 72, body_format)
            worksheet.set_column(6, 7, 14, body_format)
            worksheet.autofilter(0, 0, len(frame), len(OUTPUT_COLUMNS) - 1)
        worksheet.freeze_panes(1, 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="提取 CDF 报告前几页中的产品型号并汇总到 Excel。")
    parser.add_argument("input", type=Path, help="单个 PDF 文件或包含 PDF 的文件夹")
    parser.add_argument("-o", "--output", type=Path, help="输出 Excel 路径")
    parser.add_argument("--max-pages", type=int, default=5, help="每份 PDF 最多读取页数，默认 5")
    parser.add_argument("--recursive", action="store_true", help="输入文件夹时递归扫描子文件夹")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.max_pages < 1:
        raise SystemExit("--max-pages 必须大于 0")

    try:
        pdfs = list_pdfs(args.input, args.recursive)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    if not pdfs:
        raise SystemExit("输入路径中未找到 PDF 文件。")

    rows: list[dict[str, object]] = []
    success_files = 0
    for pdf_path in pdfs:
        try:
            file_rows = extract_models(pdf_path, args.max_pages)
            if any(row["状态"] == "成功" for row in file_rows):
                success_files += 1
            rows.extend(file_rows)
        except Exception as exc:
            rows.append(result_row(pdf_path, status="失败", note=f"{type(exc).__name__}: {exc}"))
            print(f"[失败] {pdf_path.name}: {exc}", file=sys.stderr)

    output_path = args.output or default_output_path(args.input)
    export_excel(rows, output_path)
    model_count = sum(1 for row in rows if row["状态"] == "成功")
    print(
        f"完成：文件 {len(pdfs)} 个，识别成功 {success_files} 个，型号 {model_count} 个。"
        f"\n输出：{output_path.resolve()}"
    )


if __name__ == "__main__":
    main()
