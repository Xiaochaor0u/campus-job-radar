# InternOps real-LLM acceptance launcher.
#
# Run this in the SAME PowerShell session where the DeepSeek key was set, or let
# the script prompt for it:
#
#     .\scripts\run_real_llm_acceptance.ps1
#
# Safety:
#   * the key lives only in this process; it is removed before the script exits;
#   * the prompt uses Read-Host -AsSecureString (no echo, nothing on disk);
#   * the key is never passed as a command-line argument or written to a file;
#   * only two paid analyses are performed, and the run stops after the first
#     failure so a wrong key or model cannot burn the second call.
#
# This file is deliberately ASCII-only: Windows PowerShell 5.1 reads a BOM-less
# UTF-8 file as ANSI, which corrupts non-ASCII text and breaks parsing.

param(
    [switch]$SkipConfirm
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$python = Join-Path $repo '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Host "[FAIL] virtualenv python not found: $python" -ForegroundColor Red
    exit 1
}

if (-not $env:RECRUITOPS_LLM_API_KEY) {
    Write-Host '[INFO] RECRUITOPS_LLM_API_KEY is not set in this session.' -ForegroundColor Yellow
    $secure = Read-Host 'Paste the DeepSeek API key (input is hidden)' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $env:RECRUITOPS_LLM_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    $len = $env:RECRUITOPS_LLM_API_KEY.Length
    Write-Host "[OK] key set for this process only (length $len, not written to disk)" -ForegroundColor Green
}

Write-Host ''
Write-Host '=== 1/2 preflight (no network, no cost) ===' -ForegroundColor Cyan
& $python scripts\verify_real_llm.py --plan
if ($LASTEXITCODE -ne 0) {
    Write-Host '[FAIL] preflight failed; nothing was called.' -ForegroundColor Red
    Remove-Item Env:RECRUITOPS_LLM_API_KEY -ErrorAction SilentlyContinue
    exit 1
}

if (-not $SkipConfirm) {
    Write-Host ''
    Write-Host 'About to run 2 real analyses with deepseek-flash (expected total cost below 0.01 USD).' -ForegroundColor Yellow
    $go = Read-Host 'Type yes to continue, anything else to cancel'
    if ($go -ne 'yes') {
        Write-Host '[INFO] cancelled; no call was made.' -ForegroundColor Yellow
        Remove-Item Env:RECRUITOPS_LLM_API_KEY -ErrorAction SilentlyContinue
        exit 0
    }
}

Write-Host ''
Write-Host '=== 2/2 real calls ===' -ForegroundColor Cyan
& $python scripts\verify_real_llm.py --confirm
$code = $LASTEXITCODE

Remove-Item Env:RECRUITOPS_LLM_API_KEY -ErrorAction SilentlyContinue
Write-Host ''
Write-Host "done, exit code $code" -ForegroundColor Cyan
Write-Host 'on success an evidence file is written under docs\internops\evidence\task-010-real-llm-*.json'
exit $code
