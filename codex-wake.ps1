# codex-wake.ps1 —— 无人值守版
# 队列驱动：有新任务才唤起 Codex；失败不等人，把原因写进 output。
param(
    [string]$Root       = "E:\WorkBuddy\题目",
    [string]$CodexCmd   = "E:\Program Files\Codex\codex.cmd",
    [string]$Profile    = "wb",
    [int]$IntervalSec   = 3,
    [int]$MaxMinutes    = 45,
    [int]$MaxFailures   = 3,
    [int]$RetryBaseSec  = 30,
    [int]$Parallel      = 2,
    [int]$BeatSec       = 20,
    [int]$CooldownSec   = 30,
    [switch]$DryRun,
    [switch]$Once
)

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$HERE        = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pipeline    = Join-Path $Root "scripts\pipeline.py"
$QueueDir    = Join-Path $Root ".workbuddy\auto\queue"
$StateDir    = Join-Path $Root ".workbuddy\auto"
$OutDir      = Join-Path $Root "output"
$LogDir      = Join-Path $Root "logs"
$PromptDir   = Join-Path $StateDir "prompts"
$LockFile    = Join-Path $StateDir "codex-wake.lock"
$FailedFile  = Join-Path $StateDir "codex-wake-failed.json"
$Template    = Join-Path $HERE "templates\failure-template.html"
$LogFile     = Join-Path $LogDir ("codex-wake-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

foreach ($d in @($LogDir, $PromptDir)) { if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null } }

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    Write-Host $line
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch { }
}

# ---------- 失败登记（重启后不再重复消耗额度） ----------
function Get-FailedIds {
    if (-not (Test-Path -LiteralPath $FailedFile)) { return @() }
    try { $o = Get-Content -LiteralPath $FailedFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return @() }
    if (-not $o) { return @() }
    return @($o.PSObject.Properties.Name)
}

function Add-Failed {
    param([string]$Id, [string]$Reason)
    $map = @{}
    if (Test-Path -LiteralPath $FailedFile) {
        try {
            $o = Get-Content -LiteralPath $FailedFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($o) { foreach ($p in $o.PSObject.Properties) { $map[$p.Name] = $p.Value } }
        } catch { }
    }
    $map[$Id] = [pscustomobject]@{ reason = $Reason; time = (Get-Date -Format "s") }
    [IO.File]::WriteAllText($FailedFile, ($map | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
}

function Clear-Failed {
    param([string]$Id)
    if (-not (Test-Path -LiteralPath $FailedFile)) { return }
    try { $o = Get-Content -LiteralPath $FailedFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return }
    if (-not $o) { return }
    $o.PSObject.Properties.Remove($Id)
    [IO.File]::WriteAllText($FailedFile, ($o | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
}

# ---------- 队列 ----------
function Get-Pending {
    if (-not (Test-Path -LiteralPath $QueueDir)) { return @() }
    $failed = Get-FailedIds
    $list = @()
    foreach ($f in (Get-ChildItem -LiteralPath $QueueDir -Filter *.json -ErrorAction SilentlyContinue | Sort-Object Name)) {
        try { $j = Get-Content -LiteralPath $f.FullName -Raw -Encoding UTF8 | ConvertFrom-Json } catch { continue }
        if ($j.status -ne "pending_answer") { continue }
        $id = [IO.Path]::GetFileNameWithoutExtension($f.Name)
        if ($failed -contains $id) { continue }
        $list += [pscustomobject]@{ File = $f.FullName; Id = $id; Data = $j }
    }
    return $list
}

function Get-Fingerprint {
    $q = ""
    if (Test-Path -LiteralPath $QueueDir) {
        $q = (Get-ChildItem -LiteralPath $QueueDir -Filter *.json -ErrorAction SilentlyContinue | Sort-Object Name |
              ForEach-Object { "$($_.Name):$($_.Length)" }) -join "|"
    }
    $o = ""
    if (Test-Path -LiteralPath $OutDir) {
        $o = (Get-ChildItem -LiteralPath $OutDir -Filter *.html -ErrorAction SilentlyContinue | Sort-Object Name |
              ForEach-Object { $_.Name }) -join "|"
    }
    return ($q + "`n" + $o)
}

function Resolve-TaskPath {
    param([string]$p)
    if ([string]::IsNullOrWhiteSpace($p)) { return $null }
    if ([IO.Path]::IsPathRooted($p)) { return $p }
    return (Join-Path $Root $p)
}

# ---------- 失败说明（预先写好的 HTML 模板） ----------
function ConvertTo-HtmlText {
    param([string]$s)
    if ($null -eq $s) { return "" }
    return ($s -replace "&", "&amp;" -replace "<", "&lt;" -replace ">", "&gt;")
}

function Write-Report {
    param($Task, [string]$Reason, [string]$Detail, [string]$LogTail,
          [string]$Kind = "失败说明",
          [string]$TitlePrefix = "自动答题未完成",
          [string]$HintText = "",
          [switch]$Register)

    if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

    $label = "未命名任务"
    if ($Task -and $Task.Data -and $Task.Data.label) { $label = [string]$Task.Data.label }
    $safe = ($label -replace '[\\/:*?"<>|]', "_").Trim()
    if ([string]::IsNullOrWhiteSpace($safe)) { $safe = "task" }
    if ($safe.Length -gt 60) { $safe = $safe.Substring(0, 60) }

    $taskId  = if ($Task) { $Task.Id } else { "(无)" }
    $source  = if ($Task) { [string]$Task.Data.source_dir } else { "(无)" }
    $hint = @"
1. 打开 <b>$($Root)\logs</b> 里当天的 codex-wake 日志，看这一次的完整输出。
2. 常见原因：额度不足 / 模型被账号规则挡住 / 图为空或读不出题目 / 草稿被结构校验拦下。
3. 处理完原因后，把 <b>.workbuddy\auto\codex-wake-failed.json</b> 里这一条删掉，再重新启动「启动云笔记工具-自动答题.bat」，程序会自动重试这条任务。
"@

    $html = $null
    if (Test-Path -LiteralPath $Template) {
        try { $html = [IO.File]::ReadAllText($Template, [Text.Encoding]::UTF8) } catch { $html = $null }
    }
    if (-not $html) {
        $html = "<!DOCTYPE html><html><head><meta charset='utf-8'><title>{{TITLE}}</title></head><body>" +
                "<h1>{{TITLE}}</h1><p>时间：{{TIME}}</p><p>任务：{{TASKID}}</p><p>素材：{{SOURCE}}</p>" +
                "<p>原因：{{REASON}}</p><p>{{DETAIL}}</p><pre>{{LOGTAIL}}</pre><p>{{HINT}}</p></body></html>"
    }

    if ($HintText) { $hint = $HintText }
    $title = "${TitlePrefix}：$label"
    $html = $html.Replace("{{TITLE}}", (ConvertTo-HtmlText $title))
    $html = $html.Replace("{{TIME}}", (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    $html = $html.Replace("{{TASKID}}", (ConvertTo-HtmlText $taskId))
    $html = $html.Replace("{{SOURCE}}", (ConvertTo-HtmlText $source))
    $html = $html.Replace("{{REASON}}", (ConvertTo-HtmlText $Reason))
    $html = $html.Replace("{{DETAIL}}", (ConvertTo-HtmlText $Detail))
    $html = $html.Replace("{{LOGTAIL}}", (ConvertTo-HtmlText $LogTail))
    $html = $html.Replace("{{HINT}}", $hint)

    $name = "{0}__{1}_{2}.html" -f $safe, $Kind, (Get-Date -Format "yyyyMMdd-HHmmss")
    $path = Join-Path $OutDir $name
    [IO.File]::WriteAllText($path, $html, [Text.UTF8Encoding]::new($false))

    if ($Register -and $Task) { Add-Failed -Id $Task.Id -Reason $Reason }
    Write-Log ("[note ] 已写{0}：{1}" -f $Kind, $path)
    return $path
}

function Write-FailureReport {
    param($Task, [string]$Reason, [string]$Detail, [string]$LogTail)
    return (Write-Report -Task $Task -Reason $Reason -Detail $Detail -LogTail $LogTail `
        -Kind "失败说明" -TitlePrefix "自动答题未完成" -Register)
}

# 素材被 skip 时，保证 output 仍有一份 HTML（提示词 §13 的硬要求）
function Write-SkipNote {
    param($Task, [string]$SkipReason)
    $detail = "模型判定这条素材无需作答，队列标了 skip；按「input 里读到的每条素材都要在 output 有一份 HTML」的规则补写本页。`r`n`r`n判定依据：$SkipReason`r`n`r`n素材目录：$([string]$Task.Data.source_dir)"
    $hint = "这一页是自动补写的说明页，不是答案页。同一张图重复投递不会重新入队（内容去重）；要重新处理，需换一份未处理过的素材，或清掉 .workbuddy\auto\manifest.json 里对应条目。"
    return (Write-Report -Task $Task -Reason ("素材被判定无需作答（skip）：" + $SkipReason) -Detail $detail -LogTail "" `
        -Kind "说明" -TitlePrefix "素材说明（未产出答案）" -HintText $hint)
}

# ---------- 单条任务：启动 / 心跳 / 收尾 ----------
function Start-CodexTask {
    param($Task, [int]$Retries = 0)

    $d        = $Task.Data
    $label    = [string]$d.label
    $srcDir   = Resolve-TaskPath ([string]$d.source_dir)
    $primary  = Resolve-TaskPath ([string]$d.primary_file)
    $answers  = Resolve-TaskPath ([string]$d.answers_md)
    $outHtml  = Resolve-TaskPath ([string]$d.output_html)

    $imageArg = @()
    if ($primary -and (Test-Path -LiteralPath $primary)) {
        $ext = [IO.Path]::GetExtension($primary).ToLowerInvariant()
        if ($ext -in @(".jpg", ".jpeg", ".png", ".webp")) { $imageArg = @("-i", $primary) }
    }

    $prompt = @"
这是一条自动答题任务，请按工作区 AGENTS.md 的流程独立完成，不要向我提问。

任务编号：$($Task.Id)
素材标题：$label
素材目录：$srcDir
主素材图：$primary
草稿路径：$answers
成品路径：$outHtml

要求：
1. 先读主素材图（不是 00_screenshot.png 预览图），看清题干与图形条件。
2. 按 AGENTS.md 写 Markdown 草稿到上面的草稿路径。
3. 自检通过后发布成品 HTML 到上面的成品路径。
4. 最后把队列里这条任务标记 done。
5. 图里带着「答案／解析」说明是成品回灌：把页面上的答案与解析整理成规范横版 HTML 发布，再用 skip 标队列。
6. 图里没有题干（空白页／测试页）：出一页说明 HTML（写清看到什么、为什么无法作答）发布，再用 skip 标队列。
7. 无论哪种情况，output 里都必须有本素材对应的 HTML；只有 skip 没有 HTML 视为任务未完成。
8. 提速纪律：render --check 最多跑 2 轮，出现「结果：通过，可以 publish」就立刻 publish → done，不要再微调、不要为了更好看重跑。
9. 不要用 chrome 截图自检（高度以 render --check 的实测输出为准）；实在要截图，文件名带上本任务编号，不要写成固定的 preview.png（可能有多条任务并行）。
10. 禁止探路：不要读 scripts/ 下的源码、不要 rg/grep 管线代码、不要 dir 目录树。允许的命令只有四条：render / publish / done / skip；允许的文件操作只有「读主素材图」和「写草稿」。
11. 一次成稿：直接按 AGENTS.md §4 的块结构写，每块正文 ≤14 行（超过 711px 会被打回），行内公式用 $...$，化学式用 \mathrm{}。
12. 被 render 打回时只做一件事：按提示删掉最长的「补充理解」段落或最少的一行，重跑同一条命令，最多 2 轮。
"@

    $promptFile = Join-Path $PromptDir ("task-{0}.txt" -f $Task.Id)
    [IO.File]::WriteAllText($promptFile, $prompt, [Text.UTF8Encoding]::new($false))

    $stdout = Join-Path $StateDir ("task-{0}.out" -f $Task.Id)
    $stderr = Join-Path $StateDir ("task-{0}.err" -f $Task.Id)
    $codexArgs = @("exec", "-C", $Root, "-p", $Profile, "-s", "workspace-write", "--skip-git-repo-check") + $imageArg + @("--", "-")

    $job = [pscustomobject]@{
        Task = $Task; Label = $label; Answers = $answers; OutHtml = $outHtml
        Stdout = $stdout; Stderr = $stderr; Proc = $null; ProcId = 0
        Sw = [Diagnostics.Stopwatch]::StartNew(); LastBeat = 0; Retries = $Retries
        TimedOut = $false; StartError = $null
    }

    Write-Log ("[task ] 开始：{0}（{1}）" -f $label, $Task.Id)
    Write-Log ("[cmd  ] $CodexCmd " + ($codexArgs -join " "))
    try {
        $job.Proc = Start-Process -FilePath $CodexCmd -ArgumentList $codexArgs -WorkingDirectory $Root `
                        -RedirectStandardInput $promptFile -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
                        -NoNewWindow -PassThru
        $job.ProcId = $job.Proc.Id
    } catch {
        $job.StartError = $_.Exception.Message
        [IO.File]::WriteAllText($stderr, ("启动失败：" + $_.Exception.Message), [Text.UTF8Encoding]::new($false))
        $job.Sw.Stop()
    }
    return $job
}

function Write-JobBeat {
    param($Job)
    $el = [int]$Job.Sw.Elapsed.TotalSeconds
    $Job.LastBeat = $el
    $mark = "还在读题/思考"
    if ($Job.OutHtml -and (Test-Path -LiteralPath $Job.OutHtml)) { $mark = "成品已出现，等它收尾" }
    elseif ($Job.Answers -and (Test-Path -LiteralPath $Job.Answers)) { $mark = "草稿已写（" + (Get-Item -LiteralPath $Job.Answers).Length + " 字节），在渲染自检" }
    Write-Log ("[busy ] 仍在处理 {0}（{1:N0}s）：{2}" -f $Job.Label, $Job.Sw.Elapsed.TotalSeconds, $mark)
}

function Complete-CodexTask {
    param($Job)
    $Job.Sw.Stop()
    $exitCode = $null
    if ($Job.Proc) { try { $Job.Proc.Refresh(); $exitCode = $Job.Proc.ExitCode } catch { $exitCode = $null } }

    $outText = ""
    $errText = ""
    if (Test-Path -LiteralPath $Job.Stdout) { try { $outText = Get-Content -LiteralPath $Job.Stdout -Raw -Encoding UTF8 } catch { } }
    if (Test-Path -LiteralPath $Job.Stderr) { try { $errText = Get-Content -LiteralPath $Job.Stderr -Raw -Encoding UTF8 } catch { } }

    $st = $null
    if (Test-Path -LiteralPath $Job.Task.File) {
        try { $st = Get-Content -LiteralPath $Job.Task.File -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $st = $null }
    }

    $hasOut = $false
    if ($st -and $st.output_html) {
        $op = Resolve-TaskPath ([string]$st.output_html)
        if ($op -and (Test-Path -LiteralPath $op)) { $hasOut = $true }
    }
    if (-not $hasOut -and $Job.OutHtml -and (Test-Path -LiteralPath $Job.OutHtml)) { $hasOut = $true }

    $isSkip = [bool]($st -and ($st.skipped -eq $true))
    $statusDone = [bool]($st -and $st.status -and ($st.status -ne "pending_answer"))

    $combined = "$outText`n$errText"
    $hard = $null
    if ($Job.StartError) { $hard = "无法启动 Codex（codex.cmd 路径或环境变量问题）" }
    elseif ($combined -match "model_not_allowed|model rules") { $hard = "模型被账号规则挡住（model_not_allowed）" }
    elseif ($combined -match "insufficient|credits|402|quota") { $hard = "额度不足或被限流" }
    elseif ($combined -match "401|Unauthorized|invalid.*key|Missing environment variable") { $hard = "网关鉴权失败（API key 或环境变量问题）" }
    elseif ($combined -match "stream disconnected|502|bad gateway") { $hard = "上游连接中断（502/断流）" }
    elseif ($exitCode -eq -1) { $hard = "无法启动 Codex（codex.cmd 路径或环境变量问题）" }

    Write-Log ("[done ] {0} 用时 {1:N1}s  exit={2}  超时={3}" -f $Job.Label, $Job.Sw.Elapsed.TotalSeconds, $(if ($null -eq $exitCode) { "n/a" } else { $exitCode }), $Job.TimedOut)
    if ($errText) { Write-Log ("[stderr] " + (($errText -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 3) -join " | ")) }

    return [pscustomobject]@{
        Ok = [bool]($hasOut -or ($isSkip -and $statusDone))
        Skip = $isSkip; HasOut = $hasOut; Done = $statusDone; State = $st
        Output = $outText; ErrorText = $errText
        Hard = $hard; TimedOut = $Job.TimedOut; ExitCode = $exitCode
        Seconds = $Job.Sw.Elapsed.TotalSeconds
    }
}

# ---------- 主流程 ----------
if (-not $DryRun) {
    if (Test-Path -LiteralPath $LockFile) {
        $oldPid = 0
        try { $oldPid = [int]((Get-Content -LiteralPath $LockFile -Raw).Trim()) } catch { $oldPid = 0 }
        if ($oldPid -gt 0) {
            $p = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
            if ($p -and $p.ProcessName -match "powershell|pwsh") {
                Write-Host ("[exit ] 已有答题器在跑（PID {0}）" -f $oldPid)
                exit 0
            }
        }
    }
    Set-Content -Path $LockFile -Value $PID -Encoding ASCII
}

Write-Log "==== codex-wake 启动 ===="
Write-Log ("Root={0}  Profile={1}  间隔={2}s  单任务上限={3}min  并发={4}  重试基数={5}s  模式={6}" -f $Root, $Profile, $IntervalSec, $MaxMinutes, $Parallel, $RetryBaseSec, $(if ($DryRun) { "演练" } else { "无人值守" }))
if ($DryRun) { Write-Log "[dry  ] 只检查队列与命令，不启动 Codex" }

$pending = @(Get-Pending)
Write-Log ("[scan ] 待答 {0} 条" -f $pending.Count)

try {
    if ($DryRun) {
        foreach ($t in $pending) {
            $img = ""
            $pf = Resolve-TaskPath ([string]$t.Data.primary_file)
            if ($pf -and (Test-Path -LiteralPath $pf)) { $img = " -i `"$pf`"" }
            Write-Log ("[dry  ] {0} -> exec -C `"{1}`" -p {2} -s workspace-write --skip-git-repo-check{3} -- -" -f $t.Id, $Root, $Profile, $img)
        }
        Write-Log "[dry  ] 演练结束"
        exit 0
    }

    $slots = [Math]::Max(1, $Parallel)
    $running = New-Object System.Collections.ArrayList
    $retryQ  = New-Object System.Collections.ArrayList
    $sentinel = $null
    $sentinelSo = Join-Path $StateDir "sentinel.out"
    $sentinelSe = Join-Path $StateDir "sentinel.err"
    $lastStat = [DateTime]::MinValue
    $processed = 0

    while ($true) {
        try {
            # --- 哨兵常驻：退出即重启，运行中也继续收新素材 ---
            if ($null -eq $sentinel -or $sentinel.HasExited) {
                if ($sentinel) {
                    $sentinel.Refresh()
                    if (Test-Path -LiteralPath $sentinelSo) {
                        foreach ($ln in @(Get-Content -LiteralPath $sentinelSo -Encoding UTF8 -ErrorAction SilentlyContinue)) {
                            $s = [string]$ln
                            if ($s.Trim().Length -eq 0) { continue }
                            if ($s.TrimStart().StartsWith("[ACTION]")) { continue }
                            if ($s.TrimStart().StartsWith("[watch]")) { continue }
                            Write-Log ("[wait ] " + $s.Trim())
                        }
                    }
                    if ($null -ne $sentinel.ExitCode -and $sentinel.ExitCode -ne 0) {
                        $eText = ""
                        if (Test-Path -LiteralPath $sentinelSe) { $eText = [string]::Join(" ", @(Get-Content -LiteralPath $sentinelSe -Encoding UTF8 -ErrorAction SilentlyContinue)) }
                        Write-Log ("[warn ] 哨兵退出码 {0}：{1}" -f $sentinel.ExitCode, $eText.Trim())
                    }
                    $sentinel = $null
                }
                $sentinel = Start-Process -FilePath "python" -ArgumentList @("-u", $Pipeline, "watch", "--interval", "$IntervalSec", "--exit-on-new") `
                        -WorkingDirectory $Root -RedirectStandardOutput $sentinelSo -RedirectStandardError $sentinelSe -NoNewWindow -PassThru
                if ($running.Count -eq 0) { Write-Log "[wait ] 哨兵已就位，有新素材会自动开工" }
            }

            # --- 1. 收割完成的任务 ---
            foreach ($j in @($running)) {
                if ($j.Proc -and -not $j.Proc.HasExited) {
                    if ($j.Sw.Elapsed.TotalMinutes -ge $MaxMinutes) {
                        $j.TimedOut = $true
                        if ($j.ProcId) { & taskkill /PID $j.ProcId /T /F 2>&1 | Out-Null }
                        Write-Log ("[kill ] {0} 超过 {1} 分钟，已终止" -f $j.Label, $MaxMinutes)
                        continue
                    }
                    if (($j.Sw.Elapsed.TotalSeconds - $j.LastBeat) -ge $BeatSec) { Write-JobBeat -Job $j }
                    continue
                }

                [void]$running.Remove($j)
                $r = Complete-CodexTask -Job $j
                $processed++

                if ($r.Ok) {
                    Clear-Failed -Id $j.Task.Id
                    if ($r.Skip) {
                        Write-Log ("[skip ] 已标记跳过：{0}（{1}）" -f $j.Task.Id, [string]$r.State.skip_reason)
                        if (-not $r.HasOut) { [void](Write-SkipNote -Task $j.Task -SkipReason ([string]$r.State.skip_reason)) }
                    }
                    else { Write-Log ("[ok   ] 已发布：{0}" -f (Resolve-TaskPath ([string]$r.State.output_html))) }
                    continue
                }

                if ($r.Hard) { $reason = $r.Hard }
                elseif ($r.TimedOut) { $reason = ("单任务超过 {0} 分钟被终止" -f $MaxMinutes) }
                elseif ($null -ne $r.ExitCode -and $r.ExitCode -ne 0) { $reason = ("Codex 退出码 {0}，未产出成品" -f $r.ExitCode) }
                elseif ($r.Done) { $reason = "队列已标 done，但 output 里没有成品 HTML" }
                else { $reason = "Codex 跑完了，但队列与 output 没有变化（没有发布成品）" }

                $tries = [int]$j.Retries
                if (-not $r.Hard -and $tries -lt ($MaxFailures - 1)) {
                    $wait = $RetryBaseSec * [Math]::Pow(2, $tries)
                    [void]$retryQ.Add([pscustomobject]@{ Task = $j.Task; Retries = ($tries + 1); At = (Get-Date).AddSeconds($wait) })
                    Write-Log ("[retry] {0} 第 {1}/{2} 次无进展（{3}），{4:N0}s 后重试" -f $j.Task.Id, ($tries + 1), $MaxFailures, $reason, $wait)
                } else {
                    $tail = (($r.Output + "`n" + $r.ErrorText) -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 25) -join "`n"
                    $detail = "程序尝试 {0} 次都没有产出成品，已停止重试以免反复消耗额度。" -f ($tries + 1)
                    $detail += ("`n`n最后一次运行用时 {0:N1} 秒，退出码 {1}。" -f $r.Seconds, $(if ($null -eq $r.ExitCode) { "无法取得" } else { $r.ExitCode }))
                    $detail += "`n`n如果你希望它再试一次：把 .workbuddy\auto\codex-wake-failed.json 里 " + $j.Task.Id + " 这一条删掉，然后重新启动启动器。"
                    Write-FailureReport -Task $j.Task -Reason $reason -Detail $detail -LogTail $tail
                }
            }

            # --- 2. 到点的重试 ---
            foreach ($rt in @($retryQ)) {
                if ((Get-Date) -lt $rt.At) { continue }
                if ($running.Count -ge $slots) { continue }
                [void]$retryQ.Remove($rt)
                $still = @(Get-Pending | Where-Object { $_.Id -eq $rt.Task.Id })
                if ($still.Count -eq 0) { continue }
                [void]$running.Add((Start-CodexTask -Task $still[0] -Retries $rt.Retries))
            }

            # --- 3. 补满槽位 ---
            while ($running.Count -lt $slots) {
                $busyIds = @($running | ForEach-Object { $_.Task.Id })
                $waitIds = @($retryQ | ForEach-Object { $_.Task.Id })
                $next = @(Get-Pending | Where-Object { ($busyIds -notcontains $_.Id) -and ($waitIds -notcontains $_.Id) })
                if ($next.Count -eq 0) { break }
                [void]$running.Add((Start-CodexTask -Task $next[0]))
            }

            # --- 4. 定期状态行 ---
            if (((Get-Date) - $lastStat).TotalSeconds -ge 120) {
                $lastStat = Get-Date
                Write-Log ("[stat ] 运行中 {0}/{1}，重试排队 {2}，待答 {3}" -f $running.Count, $slots, $retryQ.Count, @(Get-Pending).Count)
            }

            if ($Once -and $processed -ge 1 -and $running.Count -eq 0) { Write-Log "[once ] 已处理 1 条，退出"; break }

            Start-Sleep -Milliseconds 1500
        } catch {
            Write-Log ("[error] " + $_.Exception.Message)
            Start-Sleep -Seconds $CooldownSec
        }
    }
} finally {
    if (-not $DryRun -and (Test-Path -LiteralPath $LockFile)) { Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue }
}
