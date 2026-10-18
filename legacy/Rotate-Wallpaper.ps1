# Rotate-Wallpaper.ps1
# Pick a random image from the user's Pictures library (and any extra folders), then set it
# as the background for ALL monitors - the same picture on every screen.
#
# Eligibility : file > 1 MB AND resolution >= 1920x1080 (both dimensions)
# Weighting   : newer files get a higher chance, decaying with a half-life (default 7 days).
#               Old files keep a small floor weight so they still show up sometimes.
# Rescan      : the folder is scanned on every run, so newly added pictures join automatically.
# Used by the scheduled task "NasCabWallpaperRotate" (every 6 hours).

param(
    [string[]]$Folders = @([Environment]::GetFolderPath('MyPictures')),
    [double]$MinSizeMB = 1,
    [int]$MinWidth = 1920,
    [int]$MinHeight = 1080,
    [double]$HalfLifeDays = 7,   # weight halves every N days
    [double]$MinWeight = 0.05,   # floor weight for old pictures
    [int]$ShowTop = 5,           # print the N highest-weight candidates
    [switch]$DryRun
)

Add-Type -AssemblyName System.Drawing

$exts = '.jpg', '.jpeg', '.png'
$minBytes = $MinSizeMB * 1MB
$now = Get-Date
$items = New-Object System.Collections.Generic.List[object]
$scanned = 0
$skippedSize = 0
$skippedRes = 0

foreach ($folder in $Folders) {
    if ([string]::IsNullOrWhiteSpace($folder)) { continue }
    if (-not (Test-Path -LiteralPath $folder)) { continue }

    Get-ChildItem -LiteralPath $folder -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
        if ($exts -notcontains $_.Extension.ToLower()) { return }
        if ($_.Length -lt $minBytes) { $script:skippedSize++; return }

        $scanned++
        $img = $null
        try {
            $img = [System.Drawing.Image]::FromFile($_.FullName)
            if ($img.Width -lt $MinWidth -or $img.Height -lt $MinHeight) { $script:skippedRes++; return }

            $added = $_.CreationTime
            if ($_.LastWriteTime -lt $added) { $added = $_.LastWriteTime }
            $ageDays = ($now - $added).TotalDays
            if ($ageDays -lt 0) { $ageDays = 0 }

            $w = [Math]::Pow(0.5, $ageDays / $HalfLifeDays)
            if ($w -lt $MinWeight) { $w = $MinWeight }

            $items.Add([PSCustomObject]@{
                Path    = $_.FullName
                Weight  = $w
                AgeDays = [Math]::Round($ageDays, 1)
            })
        } catch {
            # unreadable / corrupt image -> skip
        } finally {
            if ($img) { $img.Dispose() }
        }
    }
}

Write-Output ("SCANNED: " + $scanned + "  (skipped: size " + $skippedSize + ", resolution " + $skippedRes + ")")
Write-Output ("ELIGIBLE: " + $items.Count)

if ($items.Count -eq 0) {
    Write-Output "NO_IMAGE_MATCHES_CRITERIA"
    exit 1
}

# weighted random pick
$total = 0.0
foreach ($it in $items) { $total += $it.Weight }
$rnd = New-Object System.Random
$r = $rnd.NextDouble() * $total
$pick = $null
$acc = 0.0
foreach ($it in $items) {
    $acc += $it.Weight
    if ($acc -ge $r) { $pick = $it; break }
}
if (-not $pick) { $pick = $items[$items.Count - 1] }

Write-Output ("TOP" + $ShowTop + " (newest first):")
$items | Sort-Object -Property AgeDays | Select-Object -First $ShowTop |
    ForEach-Object { Write-Output ("   " + $_.AgeDays + "d  w=" + [Math]::Round($_.Weight, 3) + "  " + [System.IO.Path]::GetFileName($_.Path)) }

Write-Output ("PICKED (" + $pick.AgeDays + "d old, w=" + [Math]::Round($pick.Weight, 3) + "): " + $pick.Path)

if ($DryRun) { exit 0 }

if (-not ("WallpaperSetter" -as [type])) {
    Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
public class WallpaperSetter {
    [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern bool SystemParametersInfo(int uAction, int uParam, string lpvParam, int fuWinIni);
}
"@
}

# SPI_SETDESKWALLPAPER = 20 ; SPIF_UPDATEINIFILE(1) | SPIF_SENDCHANGE(2) = 3
$ok = [WallpaperSetter]::SystemParametersInfo(20, 0, $pick.Path, 3)

if ($ok) {
    # "Fill" keeps the aspect ratio, no stretching
    Set-ItemProperty -Path 'HKCU:\Control Panel\Desktop' -Name 'WallpaperStyle' -Value '10' -ErrorAction SilentlyContinue
    Set-ItemProperty -Path 'HKCU:\Control Panel\Desktop' -Name 'TileWallpaper' -Value '0' -ErrorAction SilentlyContinue
    Write-Output "OK"
    exit 0
} else {
    Write-Output "SET_FAILED"
    exit 1
}
