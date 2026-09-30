Attribute VB_Name = "MaterialQuantitySplitter"
Option Explicit

Public Sub SplitMaterialQuantity()
    Dim wb As Workbook
    Dim sourceSheet As Worksheet
    Dim resultSheet As Worksheet
    Dim headerRow As Long
    Dim detailColumn As Long
    Dim lastRow As Long
    Dim lastColumn As Long
    Dim sourceRow As Long
    Dim outputRow As Long
    Dim columnNumber As Long
    Dim detail As String
    Dim pairRegex As Object
    Dim modelRegex As Object
    Dim matches As Object
    Dim match As Object

    On Error GoTo Failed
    Application.ScreenUpdating = False

    Set wb = ActiveWorkbook
    If wb Is Nothing Then Err.Raise vbObjectError + 1000, , "No active workbook."

    Application.DisplayAlerts = False
    On Error Resume Next
    wb.Worksheets(ResultSheetName()).Delete
    On Error GoTo Failed
    Application.DisplayAlerts = True

    Set sourceSheet = FindSourceSheet(wb, headerRow, detailColumn)
    If sourceSheet Is Nothing Then
        Err.Raise vbObjectError + 1001, , "Material detail header was not found in the first 30 rows."
    End If

    lastRow = sourceSheet.UsedRange.Row + sourceSheet.UsedRange.Rows.Count - 1
    lastColumn = sourceSheet.Cells(headerRow, sourceSheet.Columns.Count).End(xlToLeft).Column

    Set resultSheet = wb.Worksheets.Add(Before:=wb.Worksheets(1))
    resultSheet.Name = ResultSheetName()

    sourceSheet.Range(sourceSheet.Cells(headerRow, 1), _
                      sourceSheet.Cells(headerRow, lastColumn)).Copy _
                      Destination:=resultSheet.Cells(1, 1)
    sourceSheet.Cells(headerRow, detailColumn).Copy Destination:=resultSheet.Cells(1, lastColumn + 1)
    resultSheet.Cells(1, lastColumn + 1).Value = CountHeaderName()

    For columnNumber = 1 To lastColumn
        resultSheet.Columns(columnNumber).ColumnWidth = sourceSheet.Columns(columnNumber).ColumnWidth
    Next columnNumber
    resultSheet.Columns(lastColumn + 1).ColumnWidth = 10

    Set pairRegex = CreateObject("VBScript.RegExp")
    pairRegex.Global = True
    pairRegex.IgnoreCase = True
    pairRegex.Pattern = "(" & ModelPattern() & ")[ \t]*(" & _
                        SampleWord() & "|" & FixedFrequencyWord() & ")?[ \t]*(" & _
                        "[0-9]+|[" & ChineseNumberCharacters() & "]+)[ \t]*" & UnitWord()

    Set modelRegex = CreateObject("VBScript.RegExp")
    modelRegex.Global = True
    modelRegex.IgnoreCase = True
    modelRegex.Pattern = ModelPattern()

    outputRow = 2
    For sourceRow = headerRow + 1 To lastRow
        If Application.WorksheetFunction.CountA( _
            sourceSheet.Range(sourceSheet.Cells(sourceRow, 1), _
                              sourceSheet.Cells(sourceRow, lastColumn))) > 0 Then

            detail = Trim$(CStr(sourceSheet.Cells(sourceRow, detailColumn).Value))
            Set matches = pairRegex.Execute(detail)

            If matches.Count > 0 Then
                For Each match In matches
                    WriteOutputRow sourceSheet, resultSheet, sourceRow, outputRow, _
                                   lastColumn, detailColumn, _
                                   CStr(match.SubMatches(0)), _
                                   QuantityToLong(CStr(match.SubMatches(3)))
                    outputRow = outputRow + 1
                Next match
            Else
                Set matches = modelRegex.Execute(detail)
                If matches.Count > 0 Then
                    For Each match In matches
                        WriteOutputRow sourceSheet, resultSheet, sourceRow, outputRow, _
                                       lastColumn, detailColumn, CStr(match.Value), Empty
                        outputRow = outputRow + 1
                    Next match
                Else
                    WriteOutputRow sourceSheet, resultSheet, sourceRow, outputRow, _
                                   lastColumn, detailColumn, detail, Empty
                    outputRow = outputRow + 1
                End If
            End If
        End If
    Next sourceRow

    resultSheet.Range(resultSheet.Cells(1, 1), _
                      resultSheet.Cells(outputRow - 1, lastColumn + 1)).AutoFilter
    resultSheet.Activate
    resultSheet.Range("A2").Select
    ActiveWindow.FreezePanes = True

    Application.CutCopyMode = False
    Application.DisplayAlerts = True
    Application.ScreenUpdating = True
    MsgBox "Done. Created " & CStr(outputRow - 2) & " split rows in sheet: " & _
           ResultSheetName(), vbInformation
    Exit Sub

Failed:
    Application.CutCopyMode = False
    Application.DisplayAlerts = True
    Application.ScreenUpdating = True
    MsgBox "Failed: " & Err.Description, vbCritical
End Sub

Private Sub WriteOutputRow(ByVal sourceSheet As Worksheet, _
                           ByVal resultSheet As Worksheet, _
                           ByVal sourceRow As Long, _
                           ByVal outputRow As Long, _
                           ByVal lastColumn As Long, _
                           ByVal detailColumn As Long, _
                           ByVal model As String, _
                           ByVal quantity As Variant)
    sourceSheet.Range(sourceSheet.Cells(sourceRow, 1), _
                      sourceSheet.Cells(sourceRow, lastColumn)).Copy _
                      Destination:=resultSheet.Cells(outputRow, 1)

    resultSheet.Cells(outputRow, detailColumn).Value = model
    sourceSheet.Cells(sourceRow, detailColumn).Copy
    resultSheet.Cells(outputRow, lastColumn + 1).PasteSpecial xlPasteFormats
    Application.CutCopyMode = False

    If IsEmpty(quantity) Then
        resultSheet.Cells(outputRow, lastColumn + 1).ClearContents
    Else
        resultSheet.Cells(outputRow, lastColumn + 1).Value = quantity
    End If
    resultSheet.Rows(outputRow).RowHeight = sourceSheet.Rows(sourceRow).RowHeight
End Sub

Private Function FindSourceSheet(ByVal wb As Workbook, _
                                 ByRef headerRow As Long, _
                                 ByRef detailColumn As Long) As Worksheet
    Dim sheet As Worksheet
    Dim rowNumber As Long
    Dim columnNumber As Long
    Dim rowLastColumn As Long

    For Each sheet In wb.Worksheets
        For rowNumber = 1 To 30
            rowLastColumn = sheet.Cells(rowNumber, sheet.Columns.Count).End(xlToLeft).Column
            For columnNumber = 1 To rowLastColumn
                If Trim$(CStr(sheet.Cells(rowNumber, columnNumber).Value)) = HeaderName() Then
                    headerRow = rowNumber
                    detailColumn = columnNumber
                    Set FindSourceSheet = sheet
                    Exit Function
                End If
            Next columnNumber
        Next rowNumber
    Next sheet
End Function

Private Function QuantityToLong(ByVal text As String) As Long
    Dim total As Long
    Dim current As Long
    Dim index As Long
    Dim code As Long

    If IsNumeric(text) Then
        QuantityToLong = CLng(text)
        Exit Function
    End If

    For index = 1 To Len(text)
        code = CLng(AscW(Mid$(text, index, 1))) And &HFFFF&
        Select Case code
            Case &H5341
                If current = 0 Then current = 1
                total = total + current * 10
                current = 0
            Case &H767E
                If current = 0 Then current = 1
                total = total + current * 100
                current = 0
            Case Else
                current = ChineseDigit(code)
        End Select
    Next index
    QuantityToLong = total + current
End Function

Private Function ChineseDigit(ByVal code As Long) As Long
    Select Case code
        Case &H96F6, &H3007: ChineseDigit = 0
        Case &H4E00: ChineseDigit = 1
        Case &H4E8C, &H4E24: ChineseDigit = 2
        Case &H4E09: ChineseDigit = 3
        Case &H56DB: ChineseDigit = 4
        Case &H4E94: ChineseDigit = 5
        Case &H516D: ChineseDigit = 6
        Case &H4E03: ChineseDigit = 7
        Case &H516B: ChineseDigit = 8
        Case &H4E5D: ChineseDigit = 9
        Case Else: Err.Raise vbObjectError + 1002, , "Unsupported Chinese number."
    End Select
End Function

Private Function ModelPattern() As String
    ModelPattern = "[A-Za-z]{1,6}[0-9]*([-.][A-Za-z0-9]+)+"
End Function

Private Function HeaderName() As String
    HeaderName = U(&H7269) & U(&H6599) & U(&H660E) & U(&H7EC6)
End Function

Private Function ResultSheetName() As String
    ResultSheetName = U(&H62C6) & U(&H5206) & U(&H7ED3) & U(&H679C)
End Function

Private Function CountHeaderName() As String
    CountHeaderName = U(&H53F0) & U(&H6570)
End Function

Private Function SampleWord() As String
    SampleWord = U(&H6837) & U(&H673A)
End Function

Private Function FixedFrequencyWord() As String
    FixedFrequencyWord = U(&H5B9A) & U(&H9891)
End Function

Private Function UnitWord() As String
    UnitWord = U(&H53F0)
End Function

Private Function ChineseNumberCharacters() As String
    ChineseNumberCharacters = U(&H96F6) & U(&H3007) & U(&H4E00) & _
                              U(&H4E8C) & U(&H4E24) & U(&H4E09) & _
                              U(&H56DB) & U(&H4E94) & U(&H516D) & _
                              U(&H4E03) & U(&H516B) & U(&H4E5D) & _
                              U(&H5341) & U(&H767E)
End Function

Private Function U(ByVal code As Long) As String
    U = ChrW(code)
End Function
