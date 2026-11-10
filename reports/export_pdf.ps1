# Update all fields (TOC, lists of figures/tables, captions) in Word and export the report to PDF.
param(
    [string]$Docx = (Join-Path $PSScriptRoot "Group13_Viral_Product_Intelligence_Final_Report.docx")
)
$Docx = (Resolve-Path $Docx).Path
$Pdf = [System.IO.Path]::ChangeExtension($Docx, ".pdf")
# Work on a uniquely named copy so Word's per-file crash blacklist can never block automation.
$Work = Join-Path $env:TEMP ("report_" + [guid]::NewGuid().ToString("N") + ".docx")
Copy-Item $Docx $Work
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($Work, $false, $false, $false)
    $doc.Fields.Update() | Out-Null
    foreach ($toc in $doc.TablesOfContents) { $toc.Update() }
    foreach ($tof in $doc.TablesOfFigures) { $tof.Update() }
    $doc.Repaginate()
    foreach ($toc in $doc.TablesOfContents) { $toc.UpdatePageNumbers() }
    foreach ($tof in $doc.TablesOfFigures) { $tof.UpdatePageNumbers() }
    $doc.Save()
    $doc.ExportAsFixedFormat($Pdf, 17, $false, 0, 0, 1, 1, 0, $true, $true, 1)
    "pages: " + $doc.ComputeStatistics(2)
    $doc.Close($false)
    Copy-Item $Work $Docx -Force
    "saved $Pdf"
}
finally {
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
    Remove-Item $Work -ErrorAction SilentlyContinue
}
