certpdftoexcel_tools

用于认证报告的PDF转Excel提取工具。

本文件夹记录了以 `pdf_to_excel_precise.py` 为核心的当前工作流程。

## 目标

从供应商PDF中仅提取目标6列表格，同时保留复杂的多行单元格内容。

目标字段：

1. `Object / part No.`（物件/零件号）
2. `Manufacturer / trademark`（制造商/商标）
3. `Type / model`（类型/型号）
4. `Technical data`（技术参数）
5. `Standard`（标准）
6. `Mark(s) of conformity`（符合性标志）（及文件编号变体）

## 提取策略（当前版本）

该脚本采用分阶段策略：

### 1. 基于「TABLE:」标记的聚焦提取

适用于包含以下标记的PDF：

- `TABLE: Components information`（表格：组件信息）
- `TABLE: list of compressor`（表格：压缩机清单）

仅提取该标记后续的目标表格。

若仅第一页包含表头，后续无表头的延续页面仍会被跟踪，直至下一个表格边界。

### 2. 严格表头模板提取（无「TABLE:」标记场景）

适用于每一页均重复显式表头的供应商PDF。

脚本优先匹配已知的目标表头类型，减少无关表格的捕获。

### 3. 降级策略：严格表头映射+延续跟踪

若严格模板匹配未提取到行数据，将降级为「严格表头映射+延续逻辑」进行提取。

### 4. 宽模式（可选）

通用的跨供应商6列扫描模式。

召回率更高，但可能包含更多非目标行。

## 命令行（CLI）使用方法

### 1) 聚焦模式（推荐）

用于生产环境导出（精度最优）：

```powershell
python "C:\Users\IT074\Documents\New project\pdf_to_excel_precise.py" `
  "C:\path\to\input.pdf" `
  "C:\path\to\output.xlsx" `
  --target-only --pages all --engine pdfplumber --target-policy focused --min-score 4
```

说明：

- `--target-only`：仅导出目标6列数据行。
- `--target-policy focused`：启用分阶段精确提取逻辑。
- `--engine pdfplumber`：推荐作为此类报告的默认解析引擎。

### 2) 宽模式（当聚焦模式遗漏过多数据时使用）

```powershell
python "C:\Users\IT074\Documents\New project\pdf_to_excel_precise.py" `
  "C:\path\to\input.pdf" `
  "C:\path\to\output.xlsx" `
  --target-only --pages all --engine pdfplumber --target-policy broad --min-score 4
```

仅当聚焦模式遗漏过多数据时，才使用宽模式。

### 3) 解析所有表格（仅用于调试）

```powershell
python "C:\Users\IT074\Documents\New project\pdf_to_excel_precise.py" `
  "C:\path\to\input.pdf" `
  "C:\path\to\output_all_tables.xlsx" `
  --pages all --engine pdfplumber
```

此命令会导出多个工作表，主要用于问题诊断。

## 批量转换（文件夹→文件夹）

```powershell
$script = "C:\Users\IT074\Documents\New project\pdf_to_excel_precise.py"
$inDir  = "C:\Users\IT074\Desktop\测试PDF"
$outDir = "C:\Users\IT074\Desktop\测试PDF输出"

New-Item -ItemType Directory -Path $outDir -Force | Out-Null

Get-ChildItem -Path $inDir -Filter *.pdf -File | ForEach-Object {
    $outFile = Join-Path $outDir ($_.BaseName + ".xlsx")
    python $script $_.FullName $outFile --target-only --pages all --engine pdfplumber --target-policy focused --min-score 4
}
```

## 推荐参数配置

- 默认配置：
  - `--target-only`
  - `--pages all`
  - `--engine pdfplumber`
  - `--target-policy focused`
  - `--min-score 4`
- 若聚焦模式返回数据过少：
  - 保持聚焦模式，降低阈值（例如 `--min-score 3`）。
  - 若仍无法满足需求，切换至 `--target-policy broad`（宽模式）。

## 常见问题

### 问题1：在一个环境中仅导出少量行，另一个环境却导出大量行

可能原因：

- 不同的Python环境（虚拟环境 `.venv` 与系统Python）可能导致数据框（dataframe）空单元格行为不一致。

当前处理方式：

- 将类空值（`None`、`NaN`、`pd.NA`）标准化为空字符串，以稳定列映射。

### 问题2：TABLE模式下，行数据被意外合并

当前处理方式：

- 后续合并逻辑受到限制：        
  - 物件（object）单元格为空的行，将被视为换行延续内容，可能会被合并。
  - 包含「Alternative」（替代项）的行，仅当该行数据稀疏（类似标记）时才会合并，独立完整的记录行不会被合并。

### 问题3：无TABLE标记的PDF中，导出过多无关行

建议：

- 使用 `--target-policy focused`（聚焦模式），确保先应用严格表头模板提取，再执行宽模式降级。

## 输出格式

目标输出工作表包含以下列：

- `page`（页码）
- `table_index_on_page`（页面内表格索引）
- `row_index_on_table`（表格内行索引）
- `engine`（解析引擎）
- `object_part_no`（物件/零件号）
- `manufacturer_trademark`（制造商/商标）
- `type_model`（类型/型号）
- `technical_data`（技术参数）
- `standard`（标准）
- `marks_of_conformity`（符合性标志）

## 快速验证清单

每次运行后，请执行以下验证：

1. 确认控制台输出行：`Done. Saved N target row(s) ...`（完成。已保存N行目标数据...）
2. 验证输出结果中的起始页码和结束页码是否正确。
3. 抽样检查：        
   1. 前3行数据
   2. 页面边界处的行数据
   3. 包含「Alternative」的行数据
4. 若结果存在大量无关数据，重新使用聚焦模式运行。

## 文件位置

当前文档路径：

```
C:\Users\IT074\Documents\New project\src\certpdftoexcel_tools\README.md
```
