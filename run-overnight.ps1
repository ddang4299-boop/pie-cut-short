# run-overnight.ps1 - run every phase automatically with Claude Code (headless)
# Usage (PowerShell, inside D:\VSHORT):
#   powershell -ExecutionPolicy Bypass -File .\run-overnight.ps1
#   powershell -ExecutionPolicy Bypass -File .\run-overnight.ps1 -Phases 3,4,5
param(
  [string[]]$Phases = @("0", "0b", "1", "2", "3", "4", "5", "6", "7"),
  [int]$MaxAttempts = 4,          # real attempts per phase (usage-limit waits not counted)
  [int]$LimitWaitMinutes = 30,    # wait when the Pro usage limit is hit
  [int]$MaxLimitWaits = 24,       # give up after ~12h of waiting
  [int]$MaxTurns = 250
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path ".autopilot" | Out-Null
$log = Join-Path ".autopilot" "run.log"

function Log([string]$m) {
  $line = "{0}  {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"), $m
  Write-Host $line
  Add-Content -Path $log -Value $line -Encoding UTF8
}

# --- preflight -------------------------------------------------------------
foreach ($tool in @("claude", "git", "python", "node", "ffmpeg")) {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
    Log "MISSING TOOL: $tool - install it and add to PATH, then run again."
    exit 1
  }
}
if (-not (Test-Path "samples")) { New-Item -ItemType Directory -Path "samples" | Out-Null }
if (-not (Get-ChildItem "samples" -Filter *.mp4 -ErrorAction SilentlyContinue)) {
  Log "WARNING: no .mp4 in samples\ - Claude will generate a synthetic test video (no real speech)."
}
if (-not (Test-Path ".git")) {
  git init | Out-Null
  git add -A | Out-Null
  git commit -m "autopilot: initial" | Out-Null
}

$limitWaits = 0
Log "=== Autopilot start. Phases: $($Phases -join ', ') ==="

foreach ($p in $Phases) {
  $done = Join-Path ".autopilot" "phase-$p.done"
  if (Test-Path $done) { Log "Phase $p already done - skip."; continue }

  $attempt = 0
  while (-not (Test-Path $done) -and $attempt -lt $MaxAttempts) {
    $attempt++
    Log "Phase $p - attempt $attempt"

    $prompt = "AUTOPILOT MODE. Read CLAUDE.md and PROMPTS.md fully, especially the section 'Autopilot mode'. " +
              "Do Phase $p only. If earlier attempts left partial work, inspect git log, git status and .autopilot/*.log, then continue from there instead of starting over. " +
              "Do not ask the user anything; decide and record decisions in DECISIONS.md. " +
              "When the phase meets its done criteria and tests pass, commit, update MORNING_REPORT.md, and create the file .autopilot/phase-$p.done containing a short summary."

    $attemptLog = Join-Path ".autopilot" ("phase-{0}.attempt{1}.log" -f $p, $attempt)
    # Stream Claude's progress live to the terminal and to the attempt log
    Set-Content -Path $attemptLog -Value "" -Encoding UTF8
    & claude -p $prompt --dangerously-skip-permissions --max-turns $MaxTurns --output-format stream-json --verbose 2>&1 | ForEach-Object {
      $line = "$_"
      Add-Content -Path $attemptLog -Value $line -Encoding UTF8
      try {
        $j = $line | ConvertFrom-Json -ErrorAction Stop
        if ($j.type -eq "assistant") {
          foreach ($c in $j.message.content) {
            if ($c.type -eq "text" -and $c.text) {
              Write-Host ("  [Claude] " + $c.text) -ForegroundColor Cyan
            } elseif ($c.type -eq "tool_use") {
              $d = ($c.input | ConvertTo-Json -Compress -Depth 3)
              if ($d.Length -gt 160) { $d = $d.Substring(0, 160) + "..." }
              Write-Host ("  [" + $c.name + "] " + $d) -ForegroundColor DarkGray
            }
          }
        } elseif ($j.type -eq "result") {
          Write-Host ("  [result] " + $j.subtype + "  turns=" + $j.num_turns) -ForegroundColor Green
          if ($j.result) { Write-Host ("  " + $j.result) -ForegroundColor Green }
        }
      } catch {
        if ($line.Trim()) { Write-Host ("  " + $line) -ForegroundColor Yellow }
      }
    }
    # Look only at the CLI's own signals (final result, rate-limit events, non-JSON CLI errors),
    # not at tool output: Claude may read files that quote these phrases (e.g. this script),
    # which used to trigger false 30-minute waits.
    $out = (Get-Content -Path $attemptLog | Where-Object {
      $_ -match '^\s*\{"type":"(result|rate_limit_event)"' -or ($_.Trim() -and $_ -notmatch '^\s*\{')
    }) -join "`n"

    if ($out -match '(?i)usage limit|limit reached|hit your (usage )?limit|rate limit|resets at|try again later|"status":"rejected"') {
      $limitWaits++
      if ($limitWaits -gt $MaxLimitWaits) { Log "Too many usage-limit waits - stopping."; exit 2 }
      Log "Usage limit hit - waiting $LimitWaitMinutes min (wait #$limitWaits)."
      Start-Sleep -Seconds ($LimitWaitMinutes * 60)
      $attempt--          # a limit hit does not count as a failed attempt
      continue
    }
  }

  if (-not (Test-Path $done)) {
    Log "Phase $p NOT finished after $MaxAttempts attempts - stopping. See .autopilot\ logs and BLOCKED.md."
    exit 3
  }
  Log "Phase $p done: $((Get-Content $done -Raw).Trim())"
}

Log "=== Autopilot finished all requested phases. Read MORNING_REPORT.md ==="
