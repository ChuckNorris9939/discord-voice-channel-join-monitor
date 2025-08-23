# Discord Voice Aligned Recording System

Dieses System erstellt zeitlich exakt ausgerichtete Audio-Aufnahmen pro User für Discord Voice-Channels. Jede User-Spur hat die **identische Länge** der gesamten Session und kann direkt in einer DAW übereinander gelegt werden, um die Session in der ursprünglichen zeitlichen Reihenfolge zu hören.

## Features

✅ **Echte zeitliche Ausrichtung**: Alle User-Spuren haben exakt die gleiche Länge  
✅ **Automatisches Stille-Padding**: Füllt Gaps und Vor-/Nach-Stille automatisch  
✅ **Thread-Safe**: Unterstützt gleichzeitige Aufnahme mehrerer User  
✅ **Gap-Detection**: Erkennt und füllt fehlende Audio-Frames automatisch  
✅ **Standardformat**: PCM 16-bit, 48kHz, mono WAV-Dateien  
✅ **Timeline-Tracking**: JSON-Metadaten für Session-Informationen  
✅ **Validierung**: Automatische Überprüfung der zeitlichen Konsistenz  

## Architektur

### AlignedPerUserSink
- Implementiert `voice_recv.AudioSink` Interface
- Verwaltet pro User einen separaten WAV-Writer
- Verwendet Sample-Cursor für präzise Zeitpositionierung
- Erkennt Gaps via Frame-Timing und füllt automatisch Stille

### UserWavWriter
- Thread-Safe WAV-Datei-Writer pro User
- Schreibt direkt PCM 16-bit, 48kHz, mono
- Unterstützt Stille-Padding für exakte Sample-Positionierung

### GarminVoiceManager Integration
- Verwendet `MultiSink` für parallele STT- und Aufnahme-Verarbeitung
- Automatische Cleanup bei Session-Ende
- Erhält bestehende Kompatibilität mit main.py

## Dateiformate

### Audio-Dateien
```
YYYYMMDD_HHMMSSZ_userId_username.wav
```
- **Format**: WAV, PCM 16-bit, 48kHz, mono
- **Länge**: Exakt Session-Dauer (mit Stille-Padding)
- **Pfad**: `data/aligned-recordings/`

### Timeline-Datei
```
timeline_YYYYMMDD_HHMMSSZ.json
```
```json
{
  "session_start": "20250102_143045Z",
  "session_duration_ms": 137480.5,
  "session_samples": 6599304,
  "sample_rate": 48000,
  "users": {
    "12345": {
      "username": "Alice",
      "file_path": "/path/to/20250102_143045Z_12345_Alice.wav",
      "final_samples": 6599304
    }
  }
}
```

## Installation & Setup

### Abhängigkeiten
```bash
# Core-Abhängigkeiten
pip install discord.py pydub

# discord-ext-voice-recv (falls nicht installiert)
pip install discord-ext-voice-recv

# FFmpeg für pydub
# Ubuntu/Debian:
sudo apt install ffmpeg
# oder Windows: Download von https://ffmpeg.org/
```

### Konfiguration
Keine spezielle Konfiguration nötig. Das System wird automatisch mit `GarminVoiceManager` initialisiert.

## Nutzung

### Automatische Aufnahme
Das aligned Recording läuft automatisch, wenn der Bot einem Voice-Channel beitritt:

```python
# In main.py - keine Änderungen nötig
await garmin_manager.join_channel(voice_channel)
# -> Startet automatisch aligned Recording

await garmin_manager.leave_channel()
# -> Finalisiert automatisch alle User-Spuren
```

### Manuelle Session-Speicherung
```python
# Speichert aktuelle Session und startet neue
garmin_manager.save_recording()
```

### Validierung
```bash
# Automatisch erstelltes Validierungs-Script
python validate_recordings.py data/aligned-recordings/timeline_20250102_143045Z.json

# Test-Script für Simulation
python test_aligned_recording.py
```

## Verwendung in DAW

### Audacity
1. **File** → **Import** → **Audio**
2. Alle User-WAV-Dateien auswählen
3. ✅ **Jede Spur startet automatisch bei 0:00**
4. Play → Hört sich an wie die originale Session

### Logic Pro / Pro Tools / Reaper
1. Neues Projekt erstellen (48kHz Sample-Rate)
2. Alle WAV-Dateien gleichzeitig importieren
3. **Timeline-Position**: 0:00:00.000 für alle Spuren
4. ✅ Automatisch korrekte zeitliche Ausrichtung

### Beispiel-Timeline
```
User Alice:   |████░░░░████░░░░░░| (spricht 0-2s und 6-8s)
User Bob:     |░░░░████░░░░████░░| (spricht 2-4s und 8-10s)  
User Charlie: |░░████░░░░░░████░░| (spricht 1-3s und 9-11s)
Session:      |________________| (0-12s, alle Spuren gleich lang)
```

## Validierung & Debugging

### Automatische Validierung
```bash
# Prüft alle Aspekte der Aufnahme
python validate_recordings.py timeline_file.json
```

**Prüft:**
- ✅ Identische Dauer aller User-Spuren (±10ms Toleranz)
- ✅ Korrekte Audio-Format (48kHz, mono, 16-bit)
- ✅ Lesbarkeit aller WAV-Dateien
- ✅ Konsistenz mit Timeline-Metadaten

### Test-Simulation
```bash
# Simuliert Voice-Session mit Gaps
python test_aligned_recording.py
```

**Testet:**
- 🎙️ 3 simulierte User mit verschiedenen Sprech-Mustern
- ⏰ 5-Sekunden Session mit 20ms Frames
- 🕳️ Verschiedene Gap-Patterns (User kommt/geht)
- ✅ Automatische Validierung der Ergebnisse

### Health Check
```python
health = garmin_manager.get_recording_health()
print(f"Aligned Recording: {health['aligned_recording_active']}")
print(f"Active Users: {health['aligned_users']}")
print(f"Session Duration: {health['aligned_session_duration_ms']:.1f}ms")
```

## Fehlerbehebung

### Häufige Probleme

#### "WAV-Dateien haben unterschiedliche Längen"
**Ursache**: Gap-Detection fehlgeschlagen oder Session nicht ordnungsgemäß beendet  
**Lösung**: Prüfe Logs auf Timing-Probleme, stelle sicher dass `cleanup()` aufgerufen wird

#### "Sample-Rate Mismatch"
**Ursache**: Discord sendet unerwartete Audio-Formate  
**Lösung**: Prüfe `_estimate_frame_duration()` und Stereo→Mono Konvertierung

#### "Gaps nicht korrekt gefüllt"
**Ursache**: Frame-Timing-Erkennung ungenau  
**Lösung**: Justiere `_handle_gaps_and_padding()` Parameter oder nutze RTP-Timestamps

### Logging
```python
# Debug-Level für detaillierte Gap-Info
logging.getLogger('garmin_voice').setLevel(logging.DEBUG)
```

### Performance-Optimierung

#### Speicher-Verbrauch
- Pro User ~1MB/Minute (16-bit mono @ 48kHz)
- Thread-Safe Writer verwenden minimale Locks
- Automatische Pufferung verhindert Speicher-Lecks

#### CPU-Last
- Direktes PCM-Schreiben (kein Re-Encoding)
- Minimale Kopier-Operationen
- Effiziente Gap-Detection

## Akzeptanzkriterien

### ✅ Session von 02:17.480
- **Alle User-WAVs**: Exakt 02:17.480 ± 10ms
- **Format**: 48kHz, mono, 16-bit PCM
- **DAW-Import**: Keine manuellen Timing-Korrekturen nötig

### ✅ Packet-Loss Simulation
- **Gaps werden gefüllt**: Hörbare Stille statt Timing-Verschiebungen
- **Korrekte Platzierung**: Gaps an den richtigen zeitlichen Positionen
- **Konsistente Länge**: Trotz Packet-Loss identische Spur-Längen

### ✅ Multi-User Scenarios
- **User Join/Leave**: Korrekte Vor-/Nach-Stille-Padding
- **Verschiedene Frame-Größen**: 2.5/5/10/20/40/60ms Opus-Frames
- **Jitter-Toleranz**: Robust gegen unregelmäßige Frame-Ankunft

## Technische Details

### Sample-Rechnung
```python
samples_per_ms = 48  # bei 48kHz
frame_samples = int(frame_ms * samples_per_ms)
# 20ms Frame = 960 Samples
```

### Gap-Erkennung
```python
# Primär: RTP-Timestamp (falls verfügbar)
# Sekundär: Erwartete vs. tatsächliche Frame-Zeit
if time_gap > 1.5 * expected_frame_duration:
    # Fülle Gap mit Stille
    missing_samples = gap_duration_ms * samples_per_ms
    write_silence(missing_samples)
```

### WAV-Format
```python
wave_writer.setnchannels(1)       # mono
wave_writer.setsampwidth(2)       # 16-bit
wave_writer.setframerate(48000)   # 48kHz
# Stille = b"\x00\x00" * num_samples
```

## Kompatibilität

- ✅ **main.py**: Keine Änderungen erforderlich
- ✅ **Bestehende STT**: Läuft parallel über MultiSink
- ✅ **Legacy Recording**: Fallback auf altes System bei Fehlern
- ✅ **Discord.py**: Kompatibel mit aktuellen Versionen
- ✅ **voice-recv**: Nutzt offizielle AudioSink-API

---

**🎯 Ziel erreicht**: Präzise zeitlich ausgerichtete Discord Voice-Aufnahmen für professionelle Audio-Nachbearbeitung.
