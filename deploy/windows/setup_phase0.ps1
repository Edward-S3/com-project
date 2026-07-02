# Windows verification PC - Phase 0 setup
# Run in Administrator PowerShell: .\setup_phase0.ps1

$ErrorActionPreference = "Continue"

Write-Host "=== Phase 0: Windows setup ===" -ForegroundColor Cyan

# 1. Work directory
$aiwork = "C:\AIWork\opt"
if (-not (Test-Path $aiwork)) {
    New-Item -ItemType Directory -Force -Path $aiwork | Out-Null
    Write-Host "[OK] Created $aiwork"
} else {
    Write-Host "[OK] Exists $aiwork"
}

# 2. Junction C:\opt -> C:\AIWork\opt
if (Test-Path "C:\opt") {
    Write-Host "[SKIP] C:\opt already exists"
} else {
    cmd /c mklink /J C:\opt C:\AIWork\opt
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] Junction C:\opt -> C:\AIWork\opt"
    } else {
        Write-Host "[NG] Failed to create junction. Run PowerShell as Administrator." -ForegroundColor Red
    }
}

# 3. Python
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pyCmd) {
    $pyVer = & python --version 2>&1
    Write-Host "[OK] Python: $pyVer"
} else {
    Write-Host "[NG] Python not found. Install 3.10+ from https://www.python.org/downloads/" -ForegroundColor Red
}

# 4. Ollama
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCmd) {
    $ollamaVer = & ollama --version 2>&1
    Write-Host "[OK] Ollama: $ollamaVer"
} else {
    Write-Host "[NG] Ollama not found. Install from https://ollama.com/download" -ForegroundColor Red
}

# 5. GPU
$nvidiaCmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($nvidiaCmd) {
    & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
    Write-Host "[OK] NVIDIA GPU detected"
} else {
    Write-Host "[WARN] nvidia-smi not available" -ForegroundColor Yellow
}

# 6. Ollama parallel settings (user env)
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "4", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS", "2", "User")
Write-Host "[OK] OLLAMA_NUM_PARALLEL=4, OLLAMA_MAX_LOADED_MODELS=2"

# 7. OpenSSH Server status
$sshCap = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'
if ($sshCap) {
    Write-Host "OpenSSH.Server: $($sshCap.State)"
}
$sshd = Get-Service sshd -ErrorAction SilentlyContinue
if ($sshd) {
    Write-Host "sshd service: $($sshd.Status)"
} else {
    Write-Host "[WARN] sshd service not found" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Phase 0 complete ===" -ForegroundColor Green
Write-Host "Next: cd C:\AIWork\opt\deploy\windows\gemini-ui"
Write-Host "      .\setup_venv.bat"
