# ADALM-PLUTO Wideband Spektrumanalyzer

## Übersicht

Dieses Paket ermöglicht eine lückenlose Spektrumanalyse von z. B. 100 bis 2100 MHz
(oder beliebigem Bereich) mit dem ADALM-PLUTO SDR.

### Schlüsselprinzipien

| Parameter | Wert | Begründung |
|-----------|------|------------|
| Samplingrate | 56 MSPS | Stabiles Maximum des PLUTO |
| Nutzbare BW/Segment | ~44.8 MHz | 80 % der Abtastrate (Filterrolloff) |
| Überlappung | ~4.5 MHz | Verhindert Lücken am Segmentrand |
| Schrittweite | ~40.3 MHz | Nutzbare BW minus Überlappung |
| Segmente (100–2100 MHz) | ~50 | Automatisch berechnet |
| AGC | **DEAKTIVIERT** | `gain_control_mode_chan0 = "manual"` |

---

## Voraussetzungen

```bash
# System (Ubuntu/Debian)
sudo apt install libiio-dev libiio-utils

# Python
pip install pyadi-iio numpy scipy matplotlib

# Verbindung testen
iio_info -n 192.168.2.1
```

---

## Schnellstart

### 1. Matplotlib-GUI (lokal, einfachste Option)

```bash
python pluto_spectrum_analyzer.py \
  --uri ip:192.168.2.1 \
  --fmin 100e6 \
  --fmax 2100e6 \
  --gain 40
```

### 2. Mit Web-UI (empfohlen)

```bash
# Python-Backend starten (öffnet HTTP-Server auf Port 8765)
python pluto_spectrum_analyzer.py \
  --uri ip:192.168.2.1 \
  --fmin 100e6 \
  --fmax 2100e6 \
  --gain 40 \
  --no-gui

# Dann pluto_spectrum_web_ui.html im Browser öffnen
# → "Verbinden" klicken
```

### 3. Nur Infos anzeigen (kein Gerät nötig)

```bash
python pluto_spectrum_analyzer.py --info
```

### 4. Einzel-Sweep als CSV

```bash
python pluto_spectrum_analyzer.py --once > spectrum.csv
```

---

## Alle Optionen

```
--uri       PlutoSDR URI (Standard: ip:192.168.2.1)
              USB:         usb:
              Netzwerk:    ip:192.168.2.1
--fmin      Startfrequenz in Hz  (Standard: 100e6)
--fmax      Stopfrequenz in Hz   (Standard: 2100e6)
--gain      HF-Gewinn 0–73 dB   (Standard: 40)
--fft       FFT-Grösse           (Standard: 8192)
--avg       Mittelwerte/Segment  (Standard: 4)
--port      HTTP-Port Web-UI     (Standard: 8765, 0=aus)
--no-gui    Matplotlib deaktivieren
--once      Nur einen Sweep, dann beenden
--info      Konfiguration anzeigen, dann beenden
```

---

## AGC-Abschaltung – Details

Der ADALM-PLUTO besitzt drei AGC-Modi (über `libiio` / `pyadi-iio`):

```python
# FALSCH – AGC aktiv (Standard-Einstellung!):
sdr.gain_control_mode_chan0 = "slow_attack"   # oder "fast_attack"

# RICHTIG – AGC vollständig deaktiviert:
sdr.gain_control_mode_chan0 = "manual"
sdr.rx_hardwaregain_chan0   = 40              # 0…73 dB, ganze Zahlen
```

Mit `"manual"` regelt der AD9363-Chip den Gain **nicht** selbst nach.
Der eingestellte Wert bleibt über den gesamten Sweep konstant – Voraussetzung
für reproduzierbare Pegelvergleiche zwischen den Segmenten.

**Empfohlene Gainwerte:**
- Schwache Signale / Empfindlichkeit: 60–73 dB
- Normalbetrieb: 40–50 dB  
- Starke Signale / kein Overdrive: 20–35 dB

---

## Lückenlose Sweep-Strategie

```
Segment 1:  FC = 122.4 MHz  → 100.0 … 144.8 MHz (nutzbar)
Segment 2:  FC = 162.7 MHz  → 140.3 … 185.1 MHz (nutzbar)
Segment 3:  FC = 203.0 MHz  → 180.6 … 225.4 MHz (nutzbar)
  ...             (Überlappung ~4.5 MHz verhindert Lücken)
Segment N:  FC = xxx MHz    → … 2100 MHz
```

Jedes Segment verwendet nur den mittleren Teil der FFT (80 % der Bandbreite),
wo der analoge Tiefpassfilter des PLUTO flach ist. Die Randbereiche mit
Filterabfall werden verworfen. Dank der Überlappung entstehen keine
sichtbaren Unstetigkeiten im zusammengesetzten Spektrum.

---

## Web-UI Funktionen

- **Live-Spektrum** mit Gitter, Peak-Markierung und Cursor
- **Wasserfall** (zeitlicher Verlauf)
- **Mittelung**: 3, 6 oder 10 Sweeps
- **MaxHold**: Maximale Einhüllende speichern
- **Referenzpegel** und **Skalierung** einstellbar
- Alle Parameter über Browser, kein Neustart nötig

---

## Fehlerbehebung

| Symptom | Lösung |
|---------|--------|
| `ImportError: adi` | `pip install pyadi-iio` |
| Verbindung schlägt fehl | `ping 192.168.2.1`, USB-Treiber prüfen |
| Verzerrtes Spektrum | Gain reduzieren (Overdrive) |
| Lücken im Spektrum | Overlap erhöhen (Code: `OVERLAP_FACTOR`) |
| Sehr langsamer Sweep | FFT-Grösse reduzieren (`--fft 2048`) |
