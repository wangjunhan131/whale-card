# 把源码目录的插件同步到 DSH profile（DSH 只加载 profile 里那份）
# 2026-11-06：之前这个脚本被写成无 BOM，5.1 按 GBK 读中文全烂，已补 BOM。
$ErrorActionPreference = "Stop"
$src = Join-Path $PSScriptRoot "lib"
$dst = Join-Path $env:USERPROFILE ".dsh\profiles\desktop\node_modules\dsh-whale-bridge\lib"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
foreach ($f in @("index.js", "card.js", "client.js")) {
  Copy-Item -Force (Join-Path $src $f) (Join-Path $dst $f)
}
Copy-Item -Force (Join-Path $PSScriptRoot "package.json") (Join-Path (Split-Path $dst -Parent) "package.json")
$n = (Select-String -Path (Join-Path $dst "index.js") -Pattern "reg\(\{" -AllMatches).Matches.Count
Write-Host "synced to profile, tools = $n"
