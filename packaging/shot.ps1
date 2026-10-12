param(
    [string]$Exe = "dist\audiocheck\audiocheck.exe",
    [string]$File,
    [string]$Out = "shots\native_1_overview.png",
    [int]$WaitSec = 18
)
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

$code = @"
using System;
using System.Runtime.InteropServices;
public class Win {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdcBlt, uint nFlags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
Add-Type -TypeDefinition $code -ErrorAction SilentlyContinue

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force (Split-Path -Parent $Out) | Out-Null

$args_ = @()
if ($File) { $args_ += $File }

$p = Start-Process $Exe -ArgumentList $args_ -PassThru
Write-Host "启动 PID=$($p.Id)，等 $WaitSec 秒渲染+分析 ..."
Start-Sleep -Seconds $WaitSec
$p.Refresh()
if ($p.HasExited) { Write-Host "!! 进程已退出"; exit 1 }

$h = $p.MainWindowHandle
Write-Host "窗口句柄 $h，标题 '$($p.MainWindowTitle)'"

$r = New-Object Win+RECT
[void][Win]::GetWindowRect($h, [ref]$r)
$w = $r.Right - $r.Left
$ht = $r.Bottom - $r.Top
Write-Host "窗口矩形 ${w}x${ht}"

$bmp = New-Object System.Drawing.Bitmap($w, $ht)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
$ok = [Win]::PrintWindow($h, $hdc, 0)
$g.ReleaseHdc($hdc)
$g.Dispose()

# PrintWindow 对硬件加速的 Qt 窗口常返回全黑 —— 校验平均亮度，太黑就退回整屏截取
$sum = 0.0; $n = 0
for ($x = 0; $x -lt $w; $x += 16) {
    for ($y = 0; $y -lt $ht; $y += 16) {
        $c = $bmp.GetPixel($x, $y)
        $sum += ($c.R + $c.G + $c.B) / 3.0; $n++
    }
}
$avg = $sum / $n
Write-Host ("PrintWindow 平均亮度 {0:F1}（<5 视为全黑）" -f $avg)

if (-not $ok -or $avg -lt 5) {
    Write-Host "退回整屏截取 ..."
    [Win]::SetForegroundWindow($h) | Out-Null
    Start-Sleep -Seconds 2
    $scr = [System.Windows.Forms.Screen]::Primary.Bounds
    $bmp2 = New-Object System.Drawing.Bitmap($scr.Width, $scr.Height)
    $g2 = [System.Drawing.Graphics]::FromImage($bmp2)
    $g2.CopyFromScreen($scr.Location, [System.Drawing.Point]::Empty, $scr.Size)
    $g2.Dispose()
    # 裁到窗口区域（避免带进桌面背景与任务栏）
    $crop = New-Object System.Drawing.Bitmap($w, $ht)
    $g3 = [System.Drawing.Graphics]::FromImage($crop)
    $g3.DrawImage($bmp2, (New-Object System.Drawing.Rectangle(0,0,$w,$ht)),
                  (New-Object System.Drawing.Rectangle($r.Left - $scr.Left, $r.Top - $scr.Top, $w, $ht)),
                  [System.Drawing.GraphicsUnit]::Pixel)
    $g3.Dispose()
    $bmp2.Dispose()
    $crop.Save((Join-Path $Root $Out))
    $crop.Dispose()
} else {
    $bmp.Save((Join-Path $Root $Out))
}
$bmp.Dispose()
Write-Host "已保存 $Out"

Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
Write-Host "已关闭应用"
