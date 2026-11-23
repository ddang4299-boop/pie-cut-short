# Creates "PieCut" shortcuts on the Desktop and in the Start Menu.
# Run once from the repo root:  powershell -ExecutionPolicy Bypass -File desktop\install_shortcuts.ps1
# Re-run it if the repo folder moves. Remove the shortcuts by deleting the two PieCut.lnk files.

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$launcher = Join-Path $root "PieCut.pyw"
$icon = Join-Path $root "desktop\piecut.ico"

foreach ($p in @($pythonw, $launcher, $icon)) {
    if (-not (Test-Path $p)) { throw "Missing: $p" }
}

$shell = New-Object -ComObject WScript.Shell
$targets = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "")
)
foreach ($dir in $targets) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "PieCut.lnk"))
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = "`"$launcher`""
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = "$icon,0"
    $lnk.Description = "PieCut - turn long videos into 9:16 shorts"
    $lnk.Save()
    Write-Output "Created $($lnk.FullName)"
}
