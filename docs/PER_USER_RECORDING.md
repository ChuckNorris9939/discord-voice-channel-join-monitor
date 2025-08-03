# Per-User Audio Recording System

## Übersicht

Das Discord Voice Bot wurde erweitert, um Audio-Glitches und Stottern zu verhindern, die auftreten, wenn mehrere Benutzer gleichzeitig sprechen. Das neue System nimmt jeden Benutzer separat auf und kombiniert die Streams beim Speichern.

## Problem

Das ursprüngliche System mischte alle Audio-Streams in einem einzigen Buffer, was zu folgenden Problemen führte:

- **Audio-Glitches**: Hochfrequente vertikale Spikes im Spektrogramm
- **Stottern**: Unterbrechungen und Verzerrungen bei gleichzeitigem Sprechen
- **Synchronisationsprobleme**: Verschiedene Latenzen zwischen Benutzern
- **Buffer-Überlauf**: Unkontrolliertes Wachstum des Audio-Buffers

## Lösung

### 1. Separate User-Buffer

Jeder Benutzer erhält seinen eigenen Audio-Buffer:

```python
self.user_buffers: dict[int, bytearray] = {}  # user_id -> audio buffer
self.user_buffer_locks: dict[int, threading.Lock] = {}  # user_id -> lock
self.user_last_activity: dict[int, float] = {}  # user_id -> last activity timestamp
```

### 2. Mindestaufnahmezeit

Aufnahmen werden nur gespeichert, wenn mindestens 10 Minuten aufgenommen wurden:

```python
MIN_RECORDING_DURATION: Final[int] = int(os.getenv("GARMIN_MIN_RECORDING_DURATION", "600"))  # 10 minutes minimum
```

### 3. Intelligente Audio-Kombination

Beim Speichern werden die User-Streams intelligent kombiniert:

#### FFmpeg-basierte Mischung (empfohlen)
- Verwendet FFmpeg's `amix` Filter für professionelle Audio-Qualität
- Automatischer Fallback auf einfache Mischung, wenn FFmpeg nicht verfügbar ist

#### Einfache Byte-Level-Mischung (Fallback)
- Durchschnittliche Mischung der Audio-Samples
- Funktioniert ohne externe Abhängigkeiten

### 4. Automatische Bereinigung

Inaktive User-Buffer werden automatisch bereinigt:

```python
USER_BUFFER_CLEANUP_INTERVAL: Final[float] = float(os.getenv("GARMIN_USER_BUFFER_CLEANUP_INTERVAL", "300.0"))  # 5 minutes
```

## Konfiguration

### Umgebungsvariablen

```bash
# Mindestaufnahmezeit (Sekunden)
GARMIN_MIN_RECORDING_DURATION=600

# Maximale User-Buffer-Größe (Bytes)
GARMIN_USER_BUFFER_MAX_SIZE=48000000

# User-Buffer-Bereinigungsintervall (Sekunden)
GARMIN_USER_BUFFER_CLEANUP_INTERVAL=300.0
```

### Datenbank-Einstellungen

Die Einstellungen können über die Web-Oberfläche oder direkt in der Datenbank geändert werden:

```sql
-- Mindestaufnahmezeit auf 15 Minuten setzen
UPDATE bot_settings SET setting_value = '900' WHERE setting_name = 'GARMIN_MIN_RECORDING_DURATION';
```

## Verwendung

### 1. Bot starten

```powershell
# Test-Skript ausführen
.\scripts\test_bot.ps1

# Bot starten
python main.py
```

### 2. Audio-Mixing testen

```powershell
# Audio-Mixing-Funktionalität testen
.\scripts\test_audio_mixing.ps1
```

### 3. Aufnahme speichern

1. Bot tritt einem Voice-Channel bei
2. Mindestens 10 Minuten aufnehmen lassen
3. "Okay Garmin video speichern" sagen oder "video speichern" nach dem Wake-Word
4. Aufnahme wird automatisch gespeichert

## Monitoring

### Health-Status

Der Bot liefert detaillierte Informationen über die Aufnahme-Gesundheit:

```python
health = garmin_manager.get_recording_health()
print(f"Aktive Benutzer: {health['active_users']}")
print(f"Gesamt-Buffer-Größe: {health['total_user_buffer_size']} bytes")
print(f"Aufnahme-Dauer: {health['recording_duration']:.1f}s")
```

### Logging

Detaillierte Logs zeigen den Status der User-Buffer:

```
DEBUG: User buffer status: 3 active users, 24000000 total bytes
INFO: Successfully mixed 3 user streams using FFmpeg
INFO: Recording saved: recording_02.08.2025_02-56.wav (duration: 600.1s, users: 3)
```

## Technische Details

### Audio-Pipeline

1. **Empfang**: Discord sendet Audio-Pakete pro Benutzer
2. **Routing**: Jeder Benutzer erhält seinen eigenen Buffer
3. **Buffer-Management**: Automatische Größenkontrolle und Bereinigung
4. **Kombination**: FFmpeg oder einfache Mischung beim Speichern
5. **Speicherung**: Synchronisierte WAV-Datei mit allen Benutzern

### Speicherverwaltung

- **User-Buffer**: Maximal 10 Minuten pro Benutzer
- **Automatische Bereinigung**: Inaktive Benutzer nach 5 Minuten
- **Memory-Effizienz**: Sliding-Window-Ansatz für große Aufnahmen

### Fehlerbehandlung

- **FFmpeg-Fallback**: Automatischer Wechsel zu einfacher Mischung
- **Buffer-Überlauf**: Verhindert durch Größenkontrolle
- **Timeout-Handling**: Automatische Neustarts bei Problemen

## Vorteile

1. **Keine Audio-Glitches**: Separate Streams verhindern Interferenzen
2. **Bessere Qualität**: FFmpeg-basierte professionelle Mischung
3. **Zuverlässigkeit**: Mindestaufnahmezeit und automatische Bereinigung
4. **Skalierbarkeit**: Unterstützt beliebig viele gleichzeitige Benutzer
5. **Monitoring**: Detaillierte Einblicke in die Aufnahme-Gesundheit

## Troubleshooting

### FFmpeg nicht verfügbar

```powershell
# FFmpeg installieren (Windows)
winget install FFmpeg
# oder
choco install ffmpeg
```

### Speicherprobleme

- Reduzieren Sie `GARMIN_USER_BUFFER_MAX_SIZE`
- Verkürzen Sie `GARMIN_USER_BUFFER_CLEANUP_INTERVAL`

### Audio-Qualität

- Stellen Sie sicher, dass FFmpeg installiert ist
- Überprüfen Sie die Logs auf "FFmpeg mixing failed"
- Verwenden Sie das Audio-Mixing-Test-Skript

## Zukünftige Verbesserungen

- **Intelligente Lautstärkeanpassung**: Automatische Normalisierung
- **Noise-Reduction**: Rauschunterdrückung pro Benutzer
- **Echo-Cancellation**: Echo-Unterdrückung für bessere Qualität
- **Streaming**: Echtzeit-Audio-Streaming ohne Speicherung 