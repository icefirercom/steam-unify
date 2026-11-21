$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 需要 Python 3.10+；PyInstaller 只用于打包，运行时不需要 Python。
python -m pip install --disable-pip-version-check --no-input pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败，请检查网络或 pip 镜像" }

python -m PyInstaller `
    --noconfirm --clean --onedir --windowed `
    --name "Steam统译" `
    --icon "assets\steam_zh.ico" `
    --add-data "data\category_model.json;data" `
    --add-data "data\category_overrides.json;data" `
    --add-data "data\category_rules.json;data" `
    --add-data "data\genres_zh.json;data" `
    --add-data "data\official_names.json;data" `
    --add-data "data\tag_names_zh.json;data" `
    --distpath "dist" `
    --workpath "build-onedir" `
    --specpath "." `
    "run.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }

$target = Join-Path (Split-Path $PSScriptRoot -Parent) "Steam统译"
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
Copy-Item (Join-Path $PSScriptRoot "dist\Steam统译") $target -Recurse
Copy-Item (Join-Path $PSScriptRoot "README.md") (Join-Path $target "README.md") -Force
Write-Host "已生成: $target\Steam统译.exe"
