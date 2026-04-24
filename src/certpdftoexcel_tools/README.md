# PDF 转 Excel（复杂单元格增强版）

## 1) 安装依赖

```powershell
pip install -r requirements.txt
```

> Windows 下 `camelot` 可能还需要 Ghostscript。若报错请安装 Ghostscript 并加入 PATH。

## 2) 运行

```powershell
python pdf_to_excel_precise.py "你的输入.pdf" "输出.xlsx"
```

可选参数：

```powershell
python pdf_to_excel_precise.py "in.pdf" "out.xlsx" --pages 1-10 --engine pdfplumber --min-score 8
python pdf_to_excel_precise.py "in.pdf" "out.xlsx" --target-only --engine pdfplumber
```

参数说明：
- `--pages`：`all`、`1`、`1,3,5`、`2-8`、`1,3-5`
- `--engine`：`pdfplumber`（默认，推荐）、`camelot`、`all`
- `--flavor`：`lattice`（有边框表格更强）、`stream`（无边框更强）、`both`（仅在 `--engine camelot/all` 时生效）
- `--min-score`：过滤低质量识别结果，默认 `8`
- `--target-only`：仅导出目标六列（`Object/part no` 到 `Mark(s) of Conformity`）
- `--target-policy`：`focused`（默认，只跟随一张目标表，直到下一个 `TABLE:` 标记结束），`broad`（保留所有匹配到的六列结果）

## 3) 输出说明

Excel 中会有：
- 多个表格 sheet：每个检测到的表格一个 sheet
- `SUMMARY` sheet：记录每个表格来源页码、引擎、评分等

脚本会尽量保留单元格内换行，并自动设置换行显示与行高。

如果使用 `--target-only`，Excel 仅输出一个 `TARGET_6_ITEMS` sheet，
并支持“前面表有表头，后续表只有数据行”的场景（会沿用先前识别到的列映射）。
`--target-only` 采用两阶段策略：若 PDF 中存在 `TABLE: Components information`、`TABLE: list of compressor` 之类的明确表格标记，则优先沿着这张主表跨页提取；若未命中，再回退到宽松跨供应商六列表头规则。
当 `--target-only` 且 `--engine pdfplumber` 未命中时，脚本会自动回退到 `engine=all` 再尝试一次（兼容更多供应商版式）。
若回退后仍未命中，不会抛异常中断批处理，而是输出空的 `TARGET_6_ITEMS` sheet（0 行）。

## 4) 精度建议（接近 WPS 效果）

- PDF 为矢量文本时效果最好（不是扫描图片）
- 表格线清晰时优先 `lattice`
- 无边框或弱边框可尝试 `stream`
- 若是扫描件，请先 OCR 再转换
