# Arete Windows Installer
# Run in PowerShell: irm https://raw.githubusercontent.com/adamtrnguyen/Arete/main/scripts/install-windows.ps1 | iex

param(
    [string]$VaultPath = "",
    [string]$Version = "v1.2.0"
)

$ErrorActionPreference = "Stop"

Write-Host "=== Arete Installer for Windows ===" -ForegroundColor Cyan
Write-Host ""

# --- Configuration ---
$GH_REPO = "adamtrnguyen/Arete"
$RELEASE_URL = "https://github.com/$GH_REPO/releases/download/$Version"

# --- Detect Paths ---
$AnkiAddonsPath = "$env:APPDATA\Anki2\addons21"

# Prompt for Vault if not provided
if (-not $VaultPath) {
    Write-Host "Please enter the FULL path to your Obsidian vault:" -ForegroundColor Yellow
    Write-Host "(e.g., C:\Users\YourName\Documents\MyVault)" -ForegroundColor Gray
    $VaultPath = Read-Host "Vault Path"
}

if (-not (Test-Path $VaultPath)) {
    Write-Host "ERROR: Vault path does not exist: $VaultPath" -ForegroundColor Red
    exit 1
}

$PluginPath = Join-Path $VaultPath ".obsidian\plugins\arete"

Write-Host ""
Write-Host "Installing to:" -ForegroundColor Green
Write-Host "  Obsidian Plugin: $PluginPath"
Write-Host "  Anki Add-ons:    $AnkiAddonsPath"
Write-Host ""

# --- Create directories ---
New-Item -ItemType Directory -Force -Path $PluginPath | Out-Null
New-Item -ItemType Directory -Force -Path "$AnkiAddonsPath\arete_sync" | Out-Null
New-Item -ItemType Directory -Force -Path "$AnkiAddonsPath\ankiconnect_fsrs" | Out-Null

# --- Download and Extract ---
$TempDir = Join-Path $env:TEMP "arete_install_$(Get-Random)"
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

try {
    # Obsidian Plugin
    Write-Host "Downloading Obsidian Plugin..." -ForegroundColor Cyan
    $PluginZip = Join-Path $TempDir "plugin.zip"
    Invoke-WebRequest -Uri "$RELEASE_URL/arete-obsidian-plugin-1.2.0.zip" -OutFile $PluginZip
    Expand-Archive -Path $PluginZip -DestinationPath $PluginPath -Force
    Write-Host "  [OK] Obsidian Plugin installed" -ForegroundColor Green

    # Arete Anki Add-on (Source Navigation)
    Write-Host "Downloading Arete Anki Add-on..." -ForegroundColor Cyan
    $AddonZip = Join-Path $TempDir "addon.zip"
    Invoke-WebRequest -Uri "$RELEASE_URL/arete-anki-addon-1.2.0.zip" -OutFile $AddonZip
    Expand-Archive -Path $AddonZip -DestinationPath "$AnkiAddonsPath\arete_sync" -Force
    Write-Host "  [OK] Arete Anki Add-on installed" -ForegroundColor Green

    # AnkiConnect with FSRS
    Write-Host "Downloading AnkiConnect (FSRS-enabled)..." -ForegroundColor Cyan
    $AcZip = Join-Path $TempDir "ankiconnect.zip"
    Invoke-WebRequest -Uri "$RELEASE_URL/ankiconnect-fsrs-1.2.0.zip" -OutFile $AcZip
    Expand-Archive -Path $AcZip -DestinationPath "$AnkiAddonsPath\ankiconnect_fsrs" -Force
    Write-Host "  [OK] AnkiConnect (FSRS) installed" -ForegroundColor Green

    # Python CLI
    Write-Host "Installing Python CLI..." -ForegroundColor Cyan
    pip install arete --upgrade --quiet
    Write-Host "  [OK] Python CLI installed (arete)" -ForegroundColor Green

} finally {
    Remove-Item -Recurse -Force $TempDir -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "=== Installation Complete! ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "  1. Restart Obsidian"
Write-Host "  2. Enable 'Arete' in Settings > Community Plugins"
Write-Host "  3. Restart Anki"
Write-Host "  4. You're ready to sync!"
Write-Host ""
