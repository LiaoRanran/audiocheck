param(
    [string]$Exe = "dist\audiocheck\audiocheck.exe",
    [string]$File = "C:\Temp\acdemo\sayonara_no_natsu.flac",
    [string]$Out = "shots\native_3_overview.png",
    [string]$Command = "跳转到 总览",
    [int]$WaitSec = 24
)
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

$code = @"
using System;
using System.Runtime.InteropServices;
public class W2 {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdcBlt, uint nFlags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
Add-Type -TypeDefinition $code -ErrorAction SilentlyContinue

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force (Split-Path -Parent $Out) | Out-Null

$p = Start-Process $Exe -ArgumentList $File -PassThru
Write-Host "启动 PID=$($p.Id)，等 ${WaitSec}s 渲染+分析 ..."
Start-Sleep -Seconds $WaitSec
$p.Refresh()
if ($p.HasExited) { Write-Host "!! 退出"; exit 1 }
$h = $p.MainWindowHandle

# 前置窗口，否则 SendKeys 发到别处
[void][W2]::SetForegroundWindow($h)
Start-Sleep -Seconds 2

# Ctrl+K 打开命令面板 → 输入命令名 → Enter
Write-Host "发送 Ctrl+K + '$Command' ..."
[System.Windows.Forms.SendKeys]::SendWait("^k")
Start-Sleep -Seconds 2
[System.Windows.Forms.SendKeys]::SendWait($Command)
Start-Sleep -Seconds 2
[System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
# 视图切换后要重画图表（matplotlib 渲染 + 合成），1–2 秒不够。
# 早先用 6s 抓到一张近乎全黑的帧：PrintWindow 赶在窗口重绘中途。
Start-Sleep -Seconds 12

$r = New-Object W2+RECT
[void][W2]::GetWindowRect($h, [ref]$r)
$w = $r.Right - $r.Left; $ht = $r.Bottom - $r.Top
$bmp = New-Object System.Drawing.Bitmap($w, $ht)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
$ok = [W2]::PrintWindow($h, $hdc, 0)
$g.ReleaseHdc($hdc); $g.Dispose()

$sum = 0.0; $n = 0
for ($x = 0; $x -lt $w; $x += 16) { for ($y = 0; $y -lt $ht; $y += 16) {
    $c = $bmp.GetPixel($x, $y); $sum += ($c.R+$c.G+$c.B)/3.0; $n++ } }
$avg = $sum / $n
Write-Host ("亮度 {0:F1}" -f $avg)

if (-not $ok -or $avg -lt 5) {
    Write-Host "PrintWindow 失败，退回整屏"
    $scr = [System.Windows.Forms.Screen]::Primary.Bounds
    $bmp2 = New-Object System.Drawing.Bitmap($scr.Width, $scr.Height)
    $g2 = [System.Drawing.Graphics]::FromImage($bmp2)
    $g2.CopyFromScreen($scr.Location, [System.Drawing.Point]::Empty, $scr.Size); $g2.Dispose()
    $crop = New-Object System.Drawing.Bitmap($w, $ht)
    $g3 = [System.Drawing.Graphics]::FromImage($crop)
    $g3.DrawImage($bmp2, (New-Object System.Drawing.Rectangle(0,0,$w,$ht)),
        (New-Object System.Drawing.Rectangle($r.Left-$scr.Left, $r.Top-$scr.Top, $w, $ht)),
        [System.Drawing.GraphicsUnit]::Pixel)
    $g3.Dispose(); $bmp2.Dispose()
    $crop.Save((Join-Path $Root $Out)); $crop.Dispose()
} else { $bmp.Save((Join-Path $Root $Out)) }
$bmp.Dispose()
Write-Host "已保存 $Out"
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
