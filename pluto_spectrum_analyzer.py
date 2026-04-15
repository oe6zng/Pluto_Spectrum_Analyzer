#!/usr/bin/env python3
"""
ADALM-PLUTO Wideband Spectrum Analyzer
=======================================
Digitalisiert lückenlos grosse Frequenzbereiche durch sequentielles
Scannen mit maximaler Abtastrate und anschliessender Zusammensetzung.

Abhängigkeiten:
    pip install pyadi-iio adi numpy scipy matplotlib

Verwendung:
    python pluto_spectrum_analyzer.py [--uri ip:192.168.2.1] [--fmin 100e6] [--fmax 2100e6]
"""

import numpy as np
import argparse
import sys
import time
import json
import threading
import queue
from dataclasses import dataclass, field
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────────
# Konstanten & Konfiguration
# ──────────────────────────────────────────────────────────────────────────────

# ADALM-PLUTO Gerätegrenzen
PLUTO_SAMPLE_RATE_MAX   = 61_440_000   # 61.44 MSPS (theoretisch; stabil: ~56 MSPS)
PLUTO_SAMPLE_RATE_USE   = 56_000_000   # Stabile maximale Samplingrate
PLUTO_LO_MIN            = 70_000_000   # 70 MHz
PLUTO_LO_MAX            = 6_000_000_000  # 6 GHz
PLUTO_BANDWIDTH_MAX     = 56_000_000   # Analoge Filterbandbreite

# Nutzbare Basisbandbandbreite
# Durch Nyquist und Filterrolloff ca. 80% der Samplingrate nutzbar
USABLE_BW_FACTOR        = 0.80
USABLE_BW               = int(PLUTO_SAMPLE_RATE_USE * USABLE_BW_FACTOR)  # ~44.8 MHz

# FFT
FFT_SIZE                = 8192        # Hohe Auflösung
AVERAGES                = 4           # Mittelwert-Mittelung pro Segment
WINDOW_FUNC             = "blackman"  # Gut für Spektrumanalyse

# Überlappung zwischen Segmenten (verhindert Lücken durch Filterabfall)
OVERLAP_FACTOR          = 0.10        # 10% Überlappung
OVERLAP_HZ              = int(USABLE_BW * OVERLAP_FACTOR)


@dataclass
class ScanConfig:
    """Konfiguration für einen Scan-Durchlauf."""
    freq_min:    float = 100e6
    freq_max:    float = 2100e6
    sample_rate: int   = PLUTO_SAMPLE_RATE_USE
    usable_bw:   int   = USABLE_BW
    overlap_hz:  int   = OVERLAP_HZ
    fft_size:    int   = FFT_SIZE
    averages:    int   = AVERAGES
    gain_db:     int   = 40          # Manueller Gewinn (AGC deaktiviert)
    uri:         str   = "ip:192.168.2.1"

    @property
    def step_hz(self) -> int:
        """Schrittweite zwischen zwei LO-Positionen."""
        return self.usable_bw - self.overlap_hz

    @property
    def center_freqs(self) -> list:
        """Liste aller benötigten LO-Frequenzen."""
        freqs = []
        f = self.freq_min + self.usable_bw / 2
        while f - self.usable_bw / 2 < self.freq_max:
            freqs.append(f)
            f += self.step_hz
        return freqs

    @property
    def num_segments(self) -> int:
        return len(self.center_freqs)

    @property
    def freq_resolution_hz(self) -> float:
        return self.sample_rate / self.fft_size


# ──────────────────────────────────────────────────────────────────────────────
# PlutoSDR Schnittstelle
# ──────────────────────────────────────────────────────────────────────────────

class PlutoReceiver:
    """
    Verwaltet die Verbindung zum ADALM-PLUTO und konfiguriert
    ihn für den Betrieb ohne AGC.
    """

    def __init__(self, cfg: ScanConfig):
        self.cfg = cfg
        self.sdr = None
        self._connected = False

    def connect(self):
        """Verbindet mit dem ADALM-PLUTO und konfiguriert Grundeinstellungen."""
        try:
            import adi
        except ImportError:
            raise ImportError(
                "pyadi-iio nicht installiert.\n"
                "Bitte ausführen: pip install pyadi-iio"
            )

        print(f"[PlutoRX] Verbinde mit {self.cfg.uri} ...")
        self.sdr = adi.Pluto(uri=self.cfg.uri)

        # ── Samplingrate & Bandbreite ──────────────────────────────────────
        self.sdr.sample_rate = self.cfg.sample_rate
        self.sdr.rx_rf_bandwidth = PLUTO_BANDWIDTH_MAX
        print(f"[PlutoRX] Samplingrate:    {self.cfg.sample_rate / 1e6:.2f} MSPS")
        print(f"[PlutoRX] HF-Bandbreite:   {PLUTO_BANDWIDTH_MAX / 1e6:.2f} MHz")

        # ── AGC DEAKTIVIEREN – kritisch für reproduzierbare Messungen ──────
        #   'manual' = AGC vollständig aus; Gain wird direkt gesetzt
        self.sdr.gain_control_mode_chan0 = "manual"
        self.sdr.rx_hardwaregain_chan0   = self.cfg.gain_db
        print(f"[PlutoRX] AGC:             DEAKTIVIERT (manual)")
        print(f"[PlutoRX] HW-Gewinn:       {self.cfg.gain_db} dB")

        # ── Puffer-Grösse ──────────────────────────────────────────────────
        #   Mindestens fft_size * averages Samples für stabile Mittelung
        buf_samples = self.cfg.fft_size * (self.cfg.averages + 2)
        self.sdr.rx_buffer_size = buf_samples
        print(f"[PlutoRX] Puffer:          {buf_samples} Samples")

        self._connected = True
        print(f"[PlutoRX] ✓ Verbunden.")

    def tune(self, center_freq_hz: float):
        """Stellt den LO auf die angegebene Frequenz."""
        if not self._connected:
            raise RuntimeError("Nicht verbunden. connect() aufrufen.")
        # ADALM-PLUTO akzeptiert ganze Zahlen
        self.sdr.rx_lo = int(center_freq_hz)

    def capture(self) -> np.ndarray:
        """
        Liest einen Puffer ein und gibt das IQ-Signal zurück.
        Durch zweimaliges Einlesen wird der erste (ggf. inkohärente)
        Puffer nach dem Umschalten verworfen.
        """
        _ = self.sdr.rx()      # Ersten Puffer nach Frequenzwechsel verwerfen
        return self.sdr.rx()   # Nutzdaten

    def close(self):
        """Gibt die Geräteverbindung frei."""
        if self.sdr is not None:
            try:
                del self.sdr
            except Exception:
                pass
        self._connected = False
        print("[PlutoRX] Verbindung getrennt.")


# ──────────────────────────────────────────────────────────────────────────────
# Spektrum-Prozessor
# ──────────────────────────────────────────────────────────────────────────────

class SpectrumProcessor:
    """
    Berechnet das gemittelte Leistungsdichtespektrum aus IQ-Rohdaten.
    """

    def __init__(self, cfg: ScanConfig):
        self.cfg = cfg
        # Fensterfunktion vorberechnen
        self.window = np.blackman(cfg.fft_size)
        self.window_power = np.sum(self.window ** 2)

    def compute_psd(self, iq_samples: np.ndarray) -> np.ndarray:
        """
        Berechnet das gemittelte PSD in dBFS.

        Args:
            iq_samples: Komplexe IQ-Samples

        Returns:
            PSD in dBFS, Länge = fft_size (Frequenzachse: DC-zentriert)
        """
        n = self.cfg.fft_size
        avgs = self.cfg.averages

        # Sicherstellen dass genug Samples vorhanden
        needed = n * avgs
        if len(iq_samples) < needed:
            # Auffüllen falls nötig
            iq_samples = np.tile(iq_samples, int(np.ceil(needed / len(iq_samples))))

        # Samples normalisieren (Pluto liefert int16, adi normalisiert zu float)
        # Mittelwert entfernen (DC-Offset)
        iq_samples = iq_samples - np.mean(iq_samples)

        # Gemitteltes Spektrum berechnen
        psd_sum = np.zeros(n)
        for i in range(avgs):
            chunk = iq_samples[i * n: (i + 1) * n]
            if len(chunk) < n:
                break
            spec = np.fft.fft(chunk * self.window, n=n)
            psd_sum += np.abs(spec) ** 2

        psd = psd_sum / avgs / self.window_power

        # FFT-Shift: Negative Frequenzen links
        psd = np.fft.fftshift(psd)

        # In dBFS umrechnen (normiert auf Vollaussteuerung)
        psd_db = 10 * np.log10(psd + 1e-20)

        return psd_db

    def freq_axis(self, center_hz: float) -> np.ndarray:
        """Frequenzachse für ein Segment in Hz."""
        n   = self.cfg.fft_size
        fs  = self.cfg.sample_rate
        f   = np.fft.fftshift(np.fft.fftfreq(n, d=1.0 / fs))
        return center_hz + f


# ──────────────────────────────────────────────────────────────────────────────
# Sweep-Engine
# ──────────────────────────────────────────────────────────────────────────────

class SweepEngine:
    """
    Koordiniert den lückenlosen Sweep über den gesamten Frequenzbereich.
    """

    def __init__(self, cfg: ScanConfig):
        self.cfg       = cfg
        self.rx        = PlutoReceiver(cfg)
        self.proc      = SpectrumProcessor(cfg)
        self._running  = False

    def connect(self):
        self.rx.connect()

    def close(self):
        self.rx.close()

    def sweep_once(self) -> tuple[np.ndarray, np.ndarray]:
        """
        Führt einen vollständigen Sweep durch und gibt
        (frequencies_hz, power_dbfs) zurück.
        """
        cfg         = self.cfg
        centers     = cfg.center_freqs
        usable_bw   = cfg.usable_bw
        overlap     = cfg.overlap_hz
        fft_size    = cfg.fft_size
        fs          = cfg.sample_rate

        # Frequenzauflösung
        df = fs / fft_size

        # Anzahl nutzbarer Bins (Mitte des Spektrums, ohne Randabfall)
        half_usable = usable_bw / 2
        usable_bins = int(half_usable / df)   # Bins auf jeder Seite

        # Überlappungs-Bins
        overlap_bins = int(overlap / 2 / df)

        # Innere nutzbare Bins (ohne Rand)
        inner_low  = fft_size // 2 - usable_bins + overlap_bins
        inner_high = fft_size // 2 + usable_bins - overlap_bins

        all_freqs = []
        all_power = []

        t_start = time.time()

        for idx, fc in enumerate(centers):
            # LO einstellen
            self.rx.tune(fc)

            # Samples einlesen
            iq = self.capture_averaged()

            # PSD berechnen
            psd = self.proc.compute_psd(iq)
            fa  = self.proc.freq_axis(fc)

            # Nur nutzbaren Mittelteil verwenden
            seg_f = fa[inner_low:inner_high]
            seg_p = psd[inner_low:inner_high]

            # Frequenzbereich beschneiden
            mask = (seg_f >= cfg.freq_min) & (seg_f <= cfg.freq_max)
            all_freqs.append(seg_f[mask])
            all_power.append(seg_p[mask])

            elapsed = time.time() - t_start
            print(f"\r[Sweep] Segment {idx+1}/{len(centers)} | "
                  f"LO={fc/1e6:.1f} MHz | "
                  f"{elapsed:.2f}s", end="", flush=True)

        print()  # Newline nach Progress

        # Zusammensetzen
        freqs = np.concatenate(all_freqs)
        power = np.concatenate(all_power)

        # Sortieren (sollte schon sortiert sein)
        sort_idx = np.argsort(freqs)
        return freqs[sort_idx], power[sort_idx]

    def capture_averaged(self) -> np.ndarray:
        """Liest Samples ein. Erster Puffer nach LO-Wechsel wird verworfen."""
        return self.rx.capture()

    def continuous_sweep(self, callback, stop_event: threading.Event):
        """
        Führt kontinuierlich Sweeps durch und ruft callback(freqs, power) auf.
        """
        sweep_count = 0
        while not stop_event.is_set():
            try:
                t0 = time.time()
                freqs, power = self.sweep_once()
                dt = time.time() - t0
                sweep_count += 1
                print(f"[Sweep] #{sweep_count} abgeschlossen in {dt:.2f}s "
                      f"({len(freqs)} Punkte)")
                callback(freqs, power)
            except Exception as e:
                print(f"[Sweep] Fehler: {e}", file=sys.stderr)
                if not stop_event.is_set():
                    time.sleep(1)


# ──────────────────────────────────────────────────────────────────────────────
# Matplotlib Live-Anzeige (falls kein Web-Server benötigt)
# ──────────────────────────────────────────────────────────────────────────────

class MatplotlibDisplay:
    """Einfache Live-Anzeige mit Matplotlib."""

    def __init__(self, cfg: ScanConfig):
        self.cfg  = cfg
        self.fig  = None
        self.ax   = None
        self.line = None
        self._latest_data = None

    def setup(self):
        import matplotlib.pyplot as plt
        import matplotlib.animation as animation

        self.plt  = plt
        self.anim_mod = animation

        self.fig, self.ax = plt.subplots(figsize=(16, 6))
        self.fig.patch.set_facecolor('#0d1117')
        self.ax.set_facecolor('#161b22')
        self.ax.set_xlabel("Frequenz (MHz)", color='#8b949e')
        self.ax.set_ylabel("Leistung (dBFS)", color='#8b949e')
        self.ax.set_title(
            f"ADALM-PLUTO Spektrumanalyzer | "
            f"{self.cfg.freq_min/1e6:.0f}–{self.cfg.freq_max/1e6:.0f} MHz | "
            f"AGC: AUS | Gewinn: {self.cfg.gain_db} dB",
            color='#58a6ff', fontsize=12
        )
        self.ax.tick_params(colors='#8b949e')
        self.ax.spines[:].set_color('#30363d')
        self.ax.grid(True, color='#21262d', linewidth=0.5)

        self.line, = self.ax.plot([], [], color='#58a6ff',
                                  linewidth=0.7, alpha=0.9)
        self.ax.set_xlim(self.cfg.freq_min / 1e6, self.cfg.freq_max / 1e6)
        self.ax.set_ylim(-100, 0)

        plt.tight_layout()

    def update_callback(self, freqs: np.ndarray, power: np.ndarray):
        self._latest_data = (freqs / 1e6, power)

    def _animate(self, frame):
        if self._latest_data is not None:
            f, p = self._latest_data
            self.line.set_data(f, p)
        return (self.line,)

    def run(self, sweep_engine: SweepEngine):
        self.setup()
        stop_event = threading.Event()

        sweep_thread = threading.Thread(
            target=sweep_engine.continuous_sweep,
            args=(self.update_callback, stop_event),
            daemon=True
        )
        sweep_thread.start()

        anim = self.anim_mod.FuncAnimation(
            self.fig, self._animate,
            interval=500, blit=True, cache_frame_data=False
        )

        try:
            self.plt.show()
        except KeyboardInterrupt:
            pass
        finally:
            stop_event.set()
            sweep_engine.close()


# ──────────────────────────────────────────────────────────────────────────────
# JSON-Server für Web-UI (optional)
# ──────────────────────────────────────────────────────────────────────────────

class WebServer:
    """
    Minimaler HTTP-Server, der Spektrumdaten als JSON ausliefert.
    Kompatibel mit der beiliegenden Web-UI.
    """

    def __init__(self, cfg: ScanConfig, port: int = 8765):
        self.cfg   = cfg
        self.port  = port
        self._data_lock = threading.Lock()
        self._latest    = {"freqs": [], "power": [], "timestamp": 0}

    def update(self, freqs: np.ndarray, power: np.ndarray):
        with self._data_lock:
            self._latest = {
                "freqs":     (freqs / 1e6).tolist(),  # MHz
                "power":     power.tolist(),
                "timestamp": time.time(),
                "meta": {
                    "gain_db":    self.cfg.gain_db,
                    "agc":        "off",
                    "sample_rate_msps": self.cfg.sample_rate / 1e6,
                    "freq_min_mhz": self.cfg.freq_min / 1e6,
                    "freq_max_mhz": self.cfg.freq_max / 1e6,
                    "num_segments": self.cfg.num_segments,
                    "res_hz": self.cfg.freq_resolution_hz,
                }
            }

    def _handle(self, request_handler_class):
        """Gibt Handler-Klasse mit Zugriff auf self zurück."""
        server = self

        from http.server import BaseHTTPRequestHandler

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/spectrum":
                    with server._data_lock:
                        body = json.dumps(server._latest).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/":
                    self.send_response(302)
                    self.send_header("Location", "/spectrum")
                    self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, fmt, *args):
                pass  # Kein Log-Spam

        return Handler

    def start(self):
        from http.server import HTTPServer
        handler = self._handle(None)
        httpd   = HTTPServer(("0.0.0.0", self.port), handler)
        print(f"[WebServer] Lausche auf http://localhost:{self.port}/spectrum")
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()


# ──────────────────────────────────────────────────────────────────────────────
# Hauptprogramm
# ──────────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="ADALM-PLUTO Wideband Spektrumanalyzer",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    p.add_argument("--uri",      default="ip:192.168.2.1",
                   help="PlutoSDR URI (z.B. ip:192.168.2.1 oder usb:)")
    p.add_argument("--fmin",     type=float, default=100e6,
                   help="Startfrequenz in Hz")
    p.add_argument("--fmax",     type=float, default=2100e6,
                   help="Stopfrequenz in Hz")
    p.add_argument("--gain",     type=int,   default=40,
                   help="HF-Gewinn in dB (AGC deaktiviert, 0–73 dB)")
    p.add_argument("--fft",      type=int,   default=FFT_SIZE,
                   help="FFT-Grösse")
    p.add_argument("--avg",      type=int,   default=AVERAGES,
                   help="Anzahl Mittelwerte pro Segment")
    p.add_argument("--port",     type=int,   default=8765,
                   help="HTTP-Port für Web-UI (0 = deaktiviert)")
    p.add_argument("--no-gui",   action="store_true",
                   help="Matplotlib-GUI deaktivieren")
    p.add_argument("--once",     action="store_true",
                   help="Nur einen Sweep durchführen und beenden")
    p.add_argument("--info",     action="store_true",
                   help="Scan-Parameter anzeigen und beenden")
    return p.parse_args()


def print_info(cfg: ScanConfig):
    bw_total = cfg.freq_max - cfg.freq_min
    print("=" * 60)
    print("  ADALM-PLUTO Spektrumanalyzer – Konfiguration")
    print("=" * 60)
    print(f"  URI:                {cfg.uri}")
    print(f"  Frequenzbereich:    {cfg.freq_min/1e6:.1f} – {cfg.freq_max/1e6:.1f} MHz")
    print(f"  Gesamtbandbreite:   {bw_total/1e6:.1f} MHz")
    print(f"  Samplingrate:       {cfg.sample_rate/1e6:.2f} MSPS")
    print(f"  Nutzb. BW/Segment:  {cfg.usable_bw/1e6:.2f} MHz")
    print(f"  Überlappung:        {cfg.overlap_hz/1e6:.2f} MHz")
    print(f"  Schrittweite:       {cfg.step_hz/1e6:.2f} MHz")
    print(f"  Anzahl Segmente:    {cfg.num_segments}")
    print(f"  FFT-Grösse:         {cfg.fft_size}")
    print(f"  Frequenzauflösung:  {cfg.freq_resolution_hz:.1f} Hz")
    print(f"  Mittelwerte:        {cfg.averages}")
    print(f"  AGC:                DEAKTIVIERT")
    print(f"  HW-Gewinn:          {cfg.gain_db} dB")
    print("=" * 60)


def main():
    args = parse_args()

    cfg = ScanConfig(
        freq_min    = args.fmin,
        freq_max    = args.fmax,
        sample_rate = PLUTO_SAMPLE_RATE_USE,
        usable_bw   = USABLE_BW,
        overlap_hz  = OVERLAP_HZ,
        fft_size    = args.fft,
        averages    = args.avg,
        gain_db     = args.gain,
        uri         = args.uri,
    )

    print_info(cfg)

    if args.info:
        return

    engine = SweepEngine(cfg)

    try:
        engine.connect()
    except Exception as e:
        print(f"\n[FEHLER] Verbindung fehlgeschlagen: {e}", file=sys.stderr)
        print("Tipps:", file=sys.stderr)
        print("  - IP-Adresse prüfen: ping 192.168.2.1", file=sys.stderr)
        print("  - USB-Verbindung: --uri usb:", file=sys.stderr)
        print("  - IIO-Kontext prüfen: iio_info -n 192.168.2.1", file=sys.stderr)
        sys.exit(1)

    # ── Einzel-Sweep ──────────────────────────────────────────────────────
    if args.once:
        freqs, power = engine.sweep_once()
        engine.close()
        # Ergebnis als CSV ausgeben
        print(f"\nFrequenz(MHz),Leistung(dBFS)")
        for f, p in zip(freqs / 1e6, power):
            print(f"{f:.4f},{p:.2f}")
        return

    # ── Web-Server ────────────────────────────────────────────────────────
    web = None
    if args.port > 0:
        web = WebServer(cfg, port=args.port)
        web.start()

    # ── Callbacks zusammenstellen ─────────────────────────────────────────
    def on_sweep(freqs, power):
        if web:
            web.update(freqs, power)

    # ── GUI oder Headless ─────────────────────────────────────────────────
    if not args.no_gui:
        disp = MatplotlibDisplay(cfg)

        def combined_callback(freqs, power):
            disp.update_callback(freqs, power)
            on_sweep(freqs, power)

        # Sweep in eigenem Thread
        stop_event = threading.Event()
        sweep_thread = threading.Thread(
            target=engine.continuous_sweep,
            args=(combined_callback, stop_event),
            daemon=True
        )
        sweep_thread.start()

        disp.setup()
        import matplotlib.pyplot as plt
        import matplotlib.animation as animation

        def animate(frame):
            if disp._latest_data is not None:
                f, p = disp._latest_data
                disp.line.set_data(f, p)
            return (disp.line,)

        anim = animation.FuncAnimation(
            disp.fig, animate,
            interval=200, blit=True, cache_frame_data=False
        )
        try:
            plt.show()
        except KeyboardInterrupt:
            pass
        finally:
            stop_event.set()
            engine.close()
    else:
        # Headless: nur sweepen
        stop_event = threading.Event()
        try:
            engine.continuous_sweep(on_sweep, stop_event)
        except KeyboardInterrupt:
            print("\n[Info] Beendet durch Benutzer.")
        finally:
            stop_event.set()
            engine.close()


if __name__ == "__main__":
    main()
