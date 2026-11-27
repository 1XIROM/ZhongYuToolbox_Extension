param([switch]$Dry)
$ErrorActionPreference = "Continue"

$HERE = Split-Path -Parent $MyInvocation.MyCommand.Path
$PipelineRoot = "E:\WorkBuddy"
$BaseBat  = Join-Path $HERE "启动云笔记工具.bat"
$WakePs1  = Join-Path $HERE "codex-wake.ps1"

$WatchDir = $null
foreach ($d in (Get-ChildItem -LiteralPath $PipelineRoot -Directory -ErrorAction SilentlyContinue)) {
    if (Test-Path -LiteralPath (Join-Path $d.FullName "html2note.js")) { $WatchDir = $d.FullName; break }
}

Write-Host "============================================================"
Write-Host "  Toolbox + Codex auto-answer"
Write-Host "  1-3) static server / chrome debug / cloud-note watch"
Write-Host "  4)   codex-wake : queue to Codex, HTML to output, pdf to pdf"
Write-Host "============================================================"
Write-Host ""

if (-not (Test-Path -LiteralPath $BaseBat)) { Write-Host "[warn ] base launcher not found: $BaseBat" }
if (-not (Test-Path -LiteralPath $WakePs1)) { Write-Host "[warn ] codex-wake.ps1 not found: $WakePs1" }
if (-not $WatchDir)                         { Write-Host "[warn ] no html2note.js under $PipelineRoot -- edit PipelineRoot" }

# 已有答题器在跑 = 锁文件里的 PID 还活着且是 PowerShell（不看命令行，避免把
# 自己或别的 grep 进程误判成答题器）。
function Get-RunningWaker {
    if (-not $WatchDir) { return $null }
    $lock = Join-Path $WatchDir ".workbuddy\auto\codex-wake.lock"
    if (-not (Test-Path -LiteralPath $lock)) { return $null }
    $oldPid = 0
    try { $oldPid = [int]((Get-Content -LiteralPath $lock -Raw).Trim()) } catch { return $null }
    if ($oldPid -le 0) { return $null }
    $proc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -match "powershell|pwsh") { return $proc }
    return $null
}

if ($Dry) {
    Write-Host "       DRY: call `"$BaseBat`""
    Write-Host "       DRY: codex-wake.ps1 -Root `"$WatchDir`" -DryRun"
} else {
    if (Test-Path -LiteralPath $BaseBat) {
        Write-Host "[step ] 1-3) starting original launcher (reused as-is)"
        Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", ("`"{0}`"" -f $BaseBat)) -WorkingDirectory $HERE | Out-Null
    } else {
        Write-Host "[skip ] base launcher missing, 1-3 not started"
    }

    $alive = Get-RunningWaker
    if ($alive) {
        Write-Host ("[skip ] codex-wake already running (PID " + $alive.Id + ")")
    } elseif (-not $WatchDir) {
        Write-Host "[skip ] no pipeline folder found under $PipelineRoot"
    } elseif (-not (Test-Path -LiteralPath $WakePs1)) {
        Write-Host "[skip ] codex-wake.ps1 missing"
    } else {
        $inner = 'powershell -NoProfile -ExecutionPolicy Bypass -File "{0}" -Root "{1}"' -f $WakePs1, $WatchDir
        Write-Host "[start] codex-wake ..."
        Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", $inner) -WorkingDirectory $HERE | Out-Null
    }
}

Write-Host ""
Write-Host "------------------------------------------------------------"
if ($WatchDir) {
    Write-Host "pipeline: $WatchDir"
    Write-Host "output  : $WatchDir\output   answer HTML (pdf is generated from it)"
    Write-Host "pdf     : $WatchDir\pdf"
    Write-Host "logs    : $WatchDir\logs\codex-wake-*.log"
}
Write-Host "------------------------------------------------------------"