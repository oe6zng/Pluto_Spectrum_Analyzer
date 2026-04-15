# Pluto_Spectrum_Analyzer
Main PlutoSDR spectrum analyzer Python scriptMain PlutoSDR spectrum analyzer Python scriptNow the web UI:2 files created, file read2 files created, file readHere is the complete package — three files:
pluto_spectrum_analyzer.py— the Python backend with:

AGC reliably disabled via gain_control_mode_chan0 = "manual"— the AD9363 chip does not adjust anything, the gain remains constant throughout the entire sweep.
56 MSPS Samplingrate (stabiles Maximum des PLUTO)
~44.8 MHz usable baseband bandwidth per segment (80% of the sampling rate, filter rolloff subtracted)
10% overlap between segments — prevents visible transitions at the filter waste.
The first buffer after each LO change is discarded (incoherent settling phase)
HTTP server on port 8765 for the web UI

pluto_spectrum_web_ui.html— the browser interface with real-time spectrum, waterfall, cursor, MaxHold and averaging.
Quick start:
bash
pip install pyadi-iio numpy matplotlib

# Mit Web-UI:
python pluto_spectrum_analyzer.py --uri ip:192.168.2.1 --gain 40 --no-gui
# → HTML im Browser öffnen, "Verbinden" klicken

# Oder mit Matplotlib direkt:
python pluto_spectrum_analyzer.py --uri ip:192.168.2.1 --gain 40

# Konfiguration prüfen (kein Gerät nötig):
python pluto_spectrum_analyzer.py --info
For 100–2100 MHz, this results in approximately 50 segments with a 40 MHz step size. The sweep time depends on the buffer size and the USB/Ethernet latency, but is typically 5–15 seconds per pass.
