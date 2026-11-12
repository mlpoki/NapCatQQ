$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot/../..").Path
$pins = Get-Content "$PSScriptRoot/release-components.json" -Raw | ConvertFrom-Json
$temp = Join-Path $env:RUNNER_TEMP 'onekey-inputs'
New-Item -ItemType Directory -Force $temp | Out-Null
Invoke-WebRequest $pins.qq_url -OutFile "$temp/QQ.exe"
if ((Get-FileHash "$temp/QQ.exe" -Algorithm SHA256).Hash.ToLower() -ne $pins.qq_sha256) { throw 'QQ SHA256 mismatch' }
$nodeName = "node-v$($pins.node_version)-win-x64.zip"
Invoke-WebRequest "https://nodejs.org/dist/v$($pins.node_version)/$nodeName" -OutFile "$temp/$nodeName"
$checksums = (Invoke-WebRequest "https://nodejs.org/dist/v$($pins.node_version)/SHASUMS256.txt").Content
$expected = (($checksums -split "`n" | Where-Object { $_.Trim().EndsWith("  $nodeName") }) -split '\s+')[0]
if (!$expected -or (Get-FileHash "$temp/$nodeName" -Algorithm SHA256).Hash.ToLower() -ne $expected) { throw 'Node SHA256 mismatch' }
& 7z x -y "-o$temp/qq" "$temp/QQ.exe" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'QQ extraction failed' }
Expand-Archive "$temp/$nodeName" "$temp/node"
$out = Join-Path $root 'OneKey'
New-Item -ItemType Directory $out | Out-Null
Copy-Item -Recurse "$root/packages/napcat-shell/dist" "$out/napcat"
$required = @('avif_convert.dll','broadcast_ipc.dll','config.json','crypto.dll','ssl.dll','libglib-2.0-0.dll','libgobject-2.0-0.dll','libvips-42.dll','ncnn.dll','opencv.dll','package.json','QBar.dll','wrapper.node','LightQuic.dll')
foreach ($name in $required) {
    $file = Get-ChildItem "$temp/qq" -Recurse -File | Where-Object Name -IEQ $name | Select-Object -First 1
    if (!$file) { throw "QQ input missing: $name" }
    Copy-Item -LiteralPath $file.FullName -Destination $out
}
New-Item -ItemType Directory "$out/win64" | Out-Null
foreach ($name in @('SSOShareInfoHelper64.dll','parent-ipc-core-x64.dll')) {
    $file = Get-ChildItem "$temp/qq" -Recurse -File | Where-Object { $_.Name -ieq $name -and $_.Directory.Name -ieq 'win64' } | Select-Object -First 1
    if (!$file) { throw "QQ native dependency missing: $name" }
    Copy-Item -LiteralPath $file.FullName -Destination "$out/win64"
}
foreach ($name in @('napcat.bat','index.js','QQNT.dll')) { Copy-Item "$root/packages/napcat-develop/$name" $out }
Copy-Item "$temp/node/node-v$($pins.node_version)-win-x64/node.exe" $out
Copy-Item "$PSScriptRoot/release-components.json" $out
@{source_branch='main'; source_sha=$env:SOURCE_SHA; release_tag=$env:NAPCAT_VERSION; workflow_sha=$env:GITHUB_WORKFLOW_SHA} | ConvertTo-Json | Set-Content "$out/build-source.json"
New-Item -ItemType Directory -Force "$root/release-assets" | Out-Null
Compress-Archive "$out/*" "$root/release-assets/NapCat.Shell.Windows.OneKey.zip"
