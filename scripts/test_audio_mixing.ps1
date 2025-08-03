# Test Audio Mixing Script for Discord Voice Bot
# This script helps test and improve the audio mixing functionality

param(
    [string]$TestDir = "data\garmin-output",
    [string]$OutputDir = "data\test-output",
    [int]$Duration = 30
)

Write-Host "=== Discord Voice Bot Audio Mixing Test ===" -ForegroundColor Green
Write-Host "Testing audio mixing functionality..." -ForegroundColor Yellow

# Create output directory if it doesn't exist
if (!(Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force
    Write-Host "Created output directory: $OutputDir" -ForegroundColor Green
}

# Check if ffmpeg is available
try {
    $ffmpegVersion = ffmpeg -version 2>&1 | Select-Object -First 1
    Write-Host "FFmpeg found: $ffmpegVersion" -ForegroundColor Green
} catch {
    Write-Host "FFmpeg not found. Please install FFmpeg for audio processing." -ForegroundColor Red
    Write-Host "Download from: https://ffmpeg.org/download.html" -ForegroundColor Yellow
    exit 1
}

# Function to analyze audio file
function Analyze-AudioFile {
    param([string]$FilePath)
    
    if (!(Test-Path $FilePath)) {
        Write-Host "File not found: $FilePath" -ForegroundColor Red
        return $null
    }
    
    try {
        $audioInfo = ffprobe -v quiet -print_format json -show_format -show_streams $FilePath | ConvertFrom-Json
        $duration = [double]$audioInfo.format.duration
        $sampleRate = $audioInfo.streams[0].sample_rate
        $channels = $audioInfo.streams[0].channels
        $bitRate = $audioInfo.format.bit_rate
        
        return @{
            Duration = $duration
            SampleRate = $sampleRate
            Channels = $channels
            BitRate = $bitRate
            FileSize = (Get-Item $FilePath).Length
        }
    } catch {
        Write-Host "Error analyzing audio file: $FilePath" -ForegroundColor Red
        return $null
    }
}

# Function to create test audio files
function Create-TestAudio {
    param([string]$OutputPath, [string]$Frequency, [int]$Duration)
    
    try {
        # Generate sine wave at specified frequency
        ffmpeg -f lavfi -i "sine=frequency=$Frequency:duration=$Duration" -ar 48000 -ac 2 -y $OutputPath 2>$null
        Write-Host "Created test audio: $OutputPath (${Frequency}Hz, ${Duration}s)" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "Error creating test audio: $OutputPath" -ForegroundColor Red
        return $false
    }
}

# Function to mix audio files
function Mix-AudioFiles {
    param([string[]]$InputFiles, [string]$OutputFile)
    
    try {
        $inputArgs = $InputFiles | ForEach-Object { "-i", $_ }
        $filterComplex = "amix=inputs=$($InputFiles.Count):duration=longest"
        
        ffmpeg @inputArgs -filter_complex $filterComplex -ar 48000 -ac 2 -y $OutputFile 2>$null
        
        if (Test-Path $OutputFile) {
            Write-Host "Successfully mixed audio files to: $OutputFile" -ForegroundColor Green
            return $true
        } else {
            Write-Host "Failed to create mixed audio file" -ForegroundColor Red
            return $false
        }
    } catch {
        Write-Host "Error mixing audio files" -ForegroundColor Red
        return $false
    }
}

# Create test audio files
Write-Host "`nCreating test audio files..." -ForegroundColor Yellow

$testFiles = @()
$frequencies = @(440, 880, 1320)  # A4, A5, E6

for ($i = 0; $i -lt $frequencies.Count; $i++) {
    $testFile = Join-Path $OutputDir "test_audio_$($frequencies[$i])Hz.wav"
    if (Create-TestAudio -OutputPath $testFile -Frequency $frequencies[$i] -Duration $Duration) {
        $testFiles += $testFile
    }
}

if ($testFiles.Count -eq 0) {
    Write-Host "No test files created. Exiting." -ForegroundColor Red
    exit 1
}

# Mix test audio files
Write-Host "`nMixing test audio files..." -ForegroundColor Yellow
$mixedFile = Join-Path $OutputDir "mixed_test_audio.wav"

if (Mix-AudioFiles -InputFiles $testFiles -OutputFile $mixedFile) {
    # Analyze the mixed file
    $mixedInfo = Analyze-AudioFile -FilePath $mixedFile
    if ($mixedInfo) {
        Write-Host "`nMixed audio analysis:" -ForegroundColor Green
        Write-Host "  Duration: $($mixedInfo.Duration)s" -ForegroundColor White
        Write-Host "  Sample Rate: $($mixedInfo.SampleRate) Hz" -ForegroundColor White
        Write-Host "  Channels: $($mixedInfo.Channels)" -ForegroundColor White
        Write-Host "  File Size: $([math]::Round($mixedInfo.FileSize / 1MB, 2)) MB" -ForegroundColor White
    }
}

# Analyze existing recordings if available
Write-Host "`nAnalyzing existing recordings..." -ForegroundColor Yellow
$existingRecordings = Get-ChildItem -Path $TestDir -Filter "*.wav" | Sort-Object LastWriteTime -Descending | Select-Object -First 3

if ($existingRecordings.Count -gt 0) {
    Write-Host "Found $($existingRecordings.Count) recent recordings:" -ForegroundColor Green
    
    foreach ($recording in $existingRecordings) {
        $info = Analyze-AudioFile -FilePath $recording.FullName
        if ($info) {
            Write-Host "  $($recording.Name):" -ForegroundColor Cyan
            Write-Host "    Duration: $($info.Duration)s" -ForegroundColor White
            Write-Host "    Sample Rate: $($info.SampleRate) Hz" -ForegroundColor White
            Write-Host "    Channels: $($info.Channels)" -ForegroundColor White
            Write-Host "    File Size: $([math]::Round($info.FileSize / 1MB, 2)) MB" -ForegroundColor White
        }
    }
} else {
    Write-Host "No existing recordings found in: $TestDir" -ForegroundColor Yellow
}

Write-Host "`n=== Test Complete ===" -ForegroundColor Green
Write-Host "Test files created in: $OutputDir" -ForegroundColor Cyan
Write-Host "You can now test the audio mixing functionality with these files." -ForegroundColor Yellow 