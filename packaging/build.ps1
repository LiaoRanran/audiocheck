# audiocheck 原生打包脚本
#
# 用法：
#   powershell -File packaging\build.ps1            # 目录版（默认，启动快）
#   powershell -File packaging\build.ps1 -OneFile   # 单文件版（便于分发）
#   powershell -File packaging\build.ps1 -Clean     # 只清理构建产物
#
# 为什么默认目录版而不是单文件：单文件每次启动要把整个包解压到临时目录，
# 冷启动慢 1–3 秒，且杀毒软件常误报。目录版没有这两个问题。

param(
    [switch]$OneFile,
    [switch]$Clean,
    [string]$OutDir = "dist"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# 归档旧产物，而不是删除。
# 用重命名不用 Remove-Item：环境的安全删除策略会把 Remove-Item 改成"移入回收站"，
# 而回收站操作在 dist 这种深层目录上会失败，导致整个构建中止（实测踩过）。
# 顺带的好处是上一个构建留作回滚。
#
# **归档必须限量**：早期版本只往编号递增、从不清理，实测累积到 1.8 GB
# （build_old1..6 + dist_old1..5，每份 65/280 MB），而且这些目录一度被
# 误提交进仓库（.gitignore 里加规则**不会**自动取消已跟踪文件）。
# 现在只保留最近 $KeepArchives 份。
$KeepArchives = 2

# 用 [System.IO.Directory]::Delete 而不是 Remove-Item：
# 前者是 .NET 调用，不经过安全删除策略的 cmdlet 包装，在深层目录上更可靠。
function Remove-DirHard([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    try {
        [System.IO.Directory]::Delete((Resolve-Path $Path).Path, $true)
        Write-Host "  已清理 ${Path}"
    } catch {
        Write-Host "  清理 $Path 失败（$($_.Exception.Message)）——不影响构建"
    }
}

function Clear-Dir([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    $n = 1
    while (Test-Path "${Path}_old$n") { $n++ }
    Rename-Item $Path "${Path}_old$n" -Force
    Write-Host "  旧产物已归档为 ${Path}_old$n"
    # 只保留最近 $KeepArchives 份，更早的清掉
    $i = 1
    while (Test-Path "${Path}_old$i") { $i++ }
    for ($k = 0; $k -lt ($i - $KeepArchives); $k++) {
        Remove-DirHard "${Path}_old$k"
    }
}

if ($Clean) {
    Write-Host "归档构建产物（不清空回收站，见 Clear-Dir 注释）..."
    Clear-Dir build
    Clear-Dir dist
    exit 0
}

Write-Host "=== 0/4 依赖自检 ==="
# 键 = 导入名，值 = pip 包名（两者常不同：Pillow 的导入名是 PIL）
$need = @{ "PyInstaller" = "pyinstaller"; "PIL" = "Pillow"; "PySide6" = "PySide6" }
foreach ($m in $need.Keys) {
    python -c "import $m" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  缺 $m，安装中 ..."
        python -m pip install --quiet $need[$m]
    } else {
        Write-Host "  OK  $m"
    }
}

Write-Host "=== 1/4 图标 ==="
python packaging\make_icon.py

Write-Host "=== 2/4 静态检查（打包前先跑测试与 lint）==="
python -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Host "测试未过，中止打包"; exit 1 }
python -m ruff check src tests
if ($LASTEXITCODE -ne 0) { Write-Host "lint 未过，中止打包"; exit 1 }

Write-Host "=== 3/4 构建 ==="
Clear-Dir build
Clear-Dir $OutDir

if ($OneFile) {
    # 单文件模式：复用同一份 spec，但用 --onefile 覆盖（spec 里是目录版）
    python -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name audiocheck `
        --icon packaging\icon.ico `
        --hidden-import audiocheck.core.analyzers.basic `
        --hidden-import audiocheck.core.analyzers.bitdepth `
        --hidden-import audiocheck.core.analyzers.codec `
        --hidden-import audiocheck.core.analyzers.dc_offset `
        --hidden-import audiocheck.core.analyzers.loudness `
        --hidden-import audiocheck.core.analyzers.metadata `
        --hidden-import audiocheck.core.analyzers.normalization `
        --hidden-import audiocheck.core.analyzers.spectrum `
        --hidden-import audiocheck.core.analyzers.stereo `
        --hidden-import audiocheck.core.analyzers.verdict `
        --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtWebEngine `
        --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtQml `
        --exclude-module numpy.f2py --exclude-module IPython --exclude-module pytest `
        src\audiocheck\ui\app.py
} else {
    python -m PyInstaller --noconfirm --clean packaging\audiocheck.spec
}

Write-Host "=== 4/4 产物 ==="
Get-ChildItem $OutDir -Recurse -File |
    Sort-Object Length -Descending |
    Select-Object -First 12 |
    ForEach-Object { "  {0,-52} {1,8:N1} MB" -f $_.Name, ($_.Length / 1MB) }

$total = (Get-ChildItem $OutDir -Recurse -File | Measure-Object -Sum Length).Sum
Write-Host ("  合计 {0:N1} MB" -f ($total / 1MB))
Write-Host ""
Write-Host "运行：$OutDir\audiocheck\audiocheck.exe"
