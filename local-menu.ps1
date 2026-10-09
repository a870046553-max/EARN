param([switch]$Dashboard)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$cfgPath = Join-Path $PSScriptRoot 'config.local.json'
if (-not (Test-Path -LiteralPath $cfgPath)) { $cfgPath = Join-Path $PSScriptRoot 'config.example.json' }
$cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
$pythonExe = $cfg.python
if ($Dashboard) {
    & $pythonExe -X utf8 cadflow.py dashboard
    if ($LASTEXITCODE -eq 0) { Start-Process (Join-Path $PSScriptRoot 'index.html') }
    exit
}
Write-Host 'CAD local tools / CAD 本地工具'
Write-Host '1. 查看工具状态  2. 打开进度  3. 登记新 DWG  4. 导入读图评审  5. 检查阶段计划  6. 执行已审阶段'
$choice = Read-Host '选择'
switch ($choice) {
    '1' { & $pythonExe -X utf8 cadflow.py doctor }
    '2' { & $pythonExe -X utf8 cadflow.py dashboard; if ($LASTEXITCODE -eq 0) { Start-Process (Join-Path $PSScriptRoot 'index.html') } }
    '3' { $caseId = Read-Host '新样本编号'; $inputPath = Read-Host 'DWG 路径'; & $pythonExe -X utf8 cadflow.py ingest --case $caseId --dwg $inputPath }
    '4' { $caseId = Read-Host '样本编号'; $inputPath = Read-Host '评审 JSON 路径'; & $pythonExe -X utf8 cadflow.py import-review --case $caseId --file $inputPath }
    '5' { $caseId = Read-Host '样本编号'; $inputPath = Read-Host '计划 JSON 路径'; & $pythonExe -X utf8 cadflow.py check-plan --case $caseId --plan $inputPath }
    '6' { $caseId = Read-Host '样本编号'; $inputPath = Read-Host '计划 JSON 路径'; & $pythonExe -X utf8 cadflow.py run --case $caseId --plan $inputPath }
}
Read-Host '回车退出'
