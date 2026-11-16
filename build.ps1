param([switch]$LocalOnly)

$ErrorActionPreference = 'Stop'
if (-not $LocalOnly) {
    throw 'This bundles dependencies for local testing. Use -LocalOnly or run make_source_release.py for public source distribution.'
}
Set-Location -LiteralPath $PSScriptRoot
$python = '.\.venv\Scripts\python.exe'
$originalPath = $env:Path
try {
    # Keep unrelated third-party DLLs out of PyInstaller's dependency search.
    $env:Path = "$env:SystemRoot\System32;$env:SystemRoot"
    & $python -m PyInstaller --clean --noconfirm --windowed --onedir --name Yomiquill --specpath build `
        --collect-all openvino --collect-all openvino_genai --collect-all openvino_tokenizers `
        --collect-all pypdfium2_raw desktop.py
    if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
} finally {
    $env:Path = $originalPath
}
Copy-Item -LiteralPath LICENSE,THIRD_PARTY_NOTICES.md,README.md,setup_models.py,local_ai.py,local_vision.py -Destination 'dist/Yomiquill'
Compress-Archive -LiteralPath 'dist/Yomiquill' -DestinationPath 'dist/Yomiquill-local-win-x64.zip' -Force
