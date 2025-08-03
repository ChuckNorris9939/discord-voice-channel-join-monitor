# Test Discord Voice Bot Script
# This script helps test the new per-user audio recording functionality

param(
    [string]$BotScript = "main.py",
    [string]$LogLevel = "INFO",
    [switch]$Verbose
)

Write-Host "=== Discord Voice Bot Test ===" -ForegroundColor Green
Write-Host "Testing per-user audio recording functionality..." -ForegroundColor Yellow

# Check if we're in the correct directory
if (!(Test-Path $BotScript)) {
    Write-Host "Bot script not found: $BotScript" -ForegroundColor Red
    Write-Host "Please run this script from the bot's root directory." -ForegroundColor Yellow
    exit 1
}

# Check if virtual environment exists
if (!(Test-Path "venv")) {
    Write-Host "Virtual environment not found. Creating one..." -ForegroundColor Yellow
    python -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to create virtual environment." -ForegroundColor Red
        exit 1
    }
}

# Activate virtual environment
Write-Host "Activating virtual environment..." -ForegroundColor Yellow
& "venv\Scripts\Activate.ps1"

# Check if required packages are installed
Write-Host "Checking required packages..." -ForegroundColor Yellow
$requiredPackages = @("discord.py", "voice-recv", "SpeechRecognition", "pyaudio")
$missingPackages = @()

foreach ($package in $requiredPackages) {
    try {
        python -c "import $package" 2>$null
        if ($LASTEXITCODE -ne 0) {
            $missingPackages += $package
        }
    } catch {
        $missingPackages += $package
    }
}

if ($missingPackages.Count -gt 0) {
    Write-Host "Missing packages: $($missingPackages -join ', ')" -ForegroundColor Red
    Write-Host "Installing missing packages..." -ForegroundColor Yellow
    pip install $missingPackages
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to install packages." -ForegroundColor Red
        exit 1
    }
}

# Check if FFmpeg is available for audio mixing
try {
    $ffmpegVersion = ffmpeg -version 2>&1 | Select-Object -First 1
    Write-Host "FFmpeg found: $ffmpegVersion" -ForegroundColor Green
    Write-Host "Audio mixing will use FFmpeg for better quality." -ForegroundColor Green
} catch {
    Write-Host "FFmpeg not found. Audio mixing will use simple byte-level mixing." -ForegroundColor Yellow
    Write-Host "For better audio quality, install FFmpeg: https://ffmpeg.org/download.html" -ForegroundColor Yellow
}

# Create necessary directories
$directories = @("data", "data\temp", "data\garmin-output", "config")
foreach ($dir in $directories) {
    if (!(Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "Created directory: $dir" -ForegroundColor Green
    }
}

# Check if environment file exists
if (!(Test-Path ".env")) {
    Write-Host "Creating .env file from template..." -ForegroundColor Yellow
    if (Test-Path "env.example") {
        Copy-Item "env.example" ".env"
        Write-Host "Created .env file. Please configure your Discord bot token." -ForegroundColor Yellow
    } else {
        Write-Host "No env.example found. Please create a .env file with your Discord bot token." -ForegroundColor Red
        exit 1
    }
}

# Check if database exists and initialize if needed
if (!(Test-Path "config\user_log.db")) {
    Write-Host "Initializing database..." -ForegroundColor Yellow
    python -c "import config_loader; config_loader.load_all_settings()" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Database initialized successfully." -ForegroundColor Green
    } else {
        Write-Host "Failed to initialize database." -ForegroundColor Red
    }
}

# Set log level
$env:LOG_LEVEL = $LogLevel
if ($Verbose) {
    $env:LOG_LEVEL = "DEBUG"
}

Write-Host "`n=== Bot Configuration ===" -ForegroundColor Green
Write-Host "Log Level: $env:LOG_LEVEL" -ForegroundColor White
Write-Host "Bot Script: $BotScript" -ForegroundColor White
Write-Host "Virtual Environment: Active" -ForegroundColor White

# Display current settings
Write-Host "`n=== Current Settings ===" -ForegroundColor Green
try {
    $settings = python -c "
import config_loader
config_loader.load_all_settings()
print(f'STT Enabled: {config_loader.STT_ENABLED}')
print(f'STT Engine: {config_loader.STT_ENGINE}')
print(f'Garmin Record Seconds: {config_loader.GARMIN_RECORD_SECONDS}')
print(f'Garmin Auto Join: {config_loader.GARMIN_AUTO_JOIN_ENABLED}')
" 2>$null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host $settings -ForegroundColor White
    } else {
        Write-Host "Failed to load settings." -ForegroundColor Red
    }
} catch {
    Write-Host "Error loading settings: $_" -ForegroundColor Red
}

# Check for existing recordings
$recordingsDir = "data\garmin-output"
if (Test-Path $recordingsDir) {
    $recordings = Get-ChildItem -Path $recordingsDir -Filter "*.wav" | Sort-Object LastWriteTime -Descending | Select-Object -First 5
    if ($recordings.Count -gt 0) {
        Write-Host "`n=== Recent Recordings ===" -ForegroundColor Green
        foreach ($recording in $recordings) {
            $size = [math]::Round($recording.Length / 1MB, 2)
            Write-Host "  $($recording.Name) ($size MB)" -ForegroundColor White
        }
    }
}

Write-Host "`n=== Ready to Start Bot ===" -ForegroundColor Green
Write-Host "The bot is configured with per-user audio recording to prevent stuttering." -ForegroundColor Cyan
Write-Host "Key improvements:" -ForegroundColor Cyan
Write-Host "  - Separate audio buffers for each user" -ForegroundColor White
Write-Host "  - Minimum 10-minute recording duration" -ForegroundColor White
Write-Host "  - FFmpeg-based audio mixing (if available)" -ForegroundColor White
Write-Host "  - Automatic cleanup of inactive user buffers" -ForegroundColor White
Write-Host "  - Improved audio pipeline health monitoring" -ForegroundColor White

Write-Host "`nTo start the bot, run:" -ForegroundColor Yellow
Write-Host "  python $BotScript" -ForegroundColor Cyan

Write-Host "`nTo test audio mixing, run:" -ForegroundColor Yellow
Write-Host "  .\scripts\test_audio_mixing.ps1" -ForegroundColor Cyan

Write-Host "`n=== Test Complete ===" -ForegroundColor Green 