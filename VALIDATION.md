# Lokaler WSL-Test, 6. November 2026

Ubuntu 24.04 läuft unter WSL2. usbipd-win 5.3.0 ist installiert. MultiShutter wird
als Konsolenprozess direkt aus diesem Projektordner ausgeführt.

Die eigene Python-Umgebung unter `/home/multishutter/.local/share/multishutter/venv`
verwendet Python 3.12.3, python-gphoto2 2.6.4 und libgphoto2 2.5.34. Der CMD-Starter
wurde auch mit Windows PowerShell erfolgreich geprüft. Alle 26 Tests bestehen.

## Erfolgreiche Aufnahme

Sitzung `captures/118454c69f064c2db4daadf895807a81` enthält einen vollständigen
gemeinsamen Take mit zwei Kameras:

| Kamera | Datei | Auflösung | Dateigröße |
| --- | --- | --- | --- |
| A6000 | `takes/001/a6000/01_capt_DSC00256.JPG` | 6000 × 4000 | 5.696.940 Bytes |
| A6700 | `takes/001/a6700/01_capt_DSC08449.JPG` | 6192 × 4128 | 6.507.497 Bytes |

Beide JPEGs wurden mit einem Bilddecoder geöffnet. Die Manifest-Prüfsummen stimmen.
Die Befehlsstarts lagen rund 0,48 ms auseinander; die Belichtungssynchronität wurde
nicht gemessen. Die physische Kamerareihenfolge ist noch nicht kalibriert.

Die A6000 wurde von AF-A auf manuellen Fokus gestellt, nachdem sie beim ersten
Auslösetest keine Datei geliefert hatte. Mit manuellem Fokus gelang der neue Take.
Die A6700 speichert RAW+JPEG auf der Kamera, überträgt laut `pcsaveimgformat` aber
„JPEG Only“ an den PC. Ihr Profil erwartet daher eine Datei pro Take.

## A6300: Verbindung repariert und mit allen Kameras getestet

Die A6300 wurde anfangs erkannt, beantwortete aber schon die USB-/PTP-Initialisierung
nicht. Das unabhängige gphoto2-Systemprogramm zeigte denselben Timeout wie MultiShutter.
Einzelverbindung, Portwechsel, normale Neuverbindung und USB-Endpunkt-Reset halfen nicht.

Funktionierende Kombination:

1. Kamera: `USB-Verbindung = PC Remote`, `USB-LUN-Einstlg. = Einzeln/Single`.
2. Windows: erzwungene usbipd-Bindung ausschließlich für die A6300 über
   `Connect-Cameras.cmd -BusId 8-4 -Bind -Force`.
3. Vollständiger Stromreset: Kamera ausschalten, USB abziehen, Akku kurz entnehmen,
   wieder einsetzen, einschalten und USB verbinden. Danach erneut an WSL hängen.
4. A6300 auf manuellen Fokus und Einzelbild gestellt. Das vorhandene RAW-Format blieb
   erhalten; erwartet wird eine ARW-Datei pro Take.

Mit Single und alternativer Bindung gelang zunächst eine Initialisierung. Nach der
Gegenprobe mit normalem Windows-Treiber trat der Timeout wieder auf. Der vollständige
Stromreset mit anschließend erzwungener Bindung stellte den funktionierenden Zustand
her. Die genaue interne Ursache in Kamera, Treiber oder USB/IP wurde nicht isoliert.
Der angeforderte Windows-Geräteneustart meldete Exitcode 3010; der anschließende
physische Stromreset reichte für die hier erfolgreich geprüfte Verbindung aus.

Einzeltest `captures/7dd5fdf50d564163acfaa81632cd7be9`: vollständige Aufnahme und
Download von `01_capt_DSC00001.ARW` mit 24.780.544 Bytes.
Danach ließen sich alle drei Kameras erneut mit dem normalen MultiShutter-Backend
öffnen; Seriennummern und Auslösefähigkeiten wurden erfolgreich geprüft.

Sitzung `captures/ace793119f1944c1990a46d6ca723122`: **zwei vollständige gemeinsame
Takes mit A6000, A6300 und A6700**, insgesamt vier JPEGs und zwei ARW-Dateien.
Alle sechs SHA-256-Prüfsummen stimmen. Damit sind auch wiederholte Aufnahmen bei
offenen Verbindungen und erneutes Öffnen nach dem Schließen getestet.

Beide lokalen Profile enthalten jetzt alle drei Kameras mit geprüften Seriennummern.
Die A6300 hängt aktuell am Windows-Port `8-4` mit erzwungener Bindung. Ihre normale
Windows-Treiberzuordnung lässt sich mit `Connect-Cameras.cmd -BusId 8-4 -Unbind`
wiederherstellen; `-Detach` allein entfernt die erzwungene Bindung nicht.
USB-Adressen können sich beim Neuverbinden ändern und müssen danach über `discover`
überprüft werden. Die physische Kamerareihenfolge ist weiterhin nicht kalibriert.

## Bedienung

`Start-MultiShutter.cmd` öffnet die Konsole. `check` prüft ohne Auslösen;
`capture --countdown 3` startet einen neuen Take; `quit` beendet den Prozess.
Fotos und Manifeste bleiben im Unterordner `captures` erhalten.

## Docker Compose und WebUI – 06.11.2026

Die WebUI läuft auf diesem PC in Ubuntu 24.04 / WSL2, Docker 29.1.3 und
Compose 2.40.3. Container: Python 3.12, python-gphoto2 2.6.4 mit libgphoto2
2.5.34. Windows-Adresse: `http://localhost:8787`; der Windows-Starter
überbrückt fehlende WSL-Localhost-Weiterleitung mit einem lokalen Node-Prozess.
Die Demo wurde nach den Prüfungen beendet; der Hardware-Container läuft weiter.

Geprüft:

- **48 automatisierte Tests erfolgreich** im isolierten Container ohne USB
  und Netzwerk. Darunter verzögerte Sony-Einstellungsrückmeldung, Fehler und
  Rücksetzen gemeinsamer Einstellungen, Reconnect/Wunschwerte, Sperre über
  Dienstneustart, Warnmodus, Stop vor Auslösung, Dateipfade, optischer Decoder,
  Perspektive/JPEG, gemischte Bildschirmcodes und Kompensationsberechnung.
- `scripts/smoke-web.py --url http://172.20.106.249:8001` vollständig erfolgreich:
  CSRF/Origin, Pfadbegrenzung, ungültige Einstellungen, parallele Aufträge,
  Stop, Lockout/Warnung/Teilaufnahme, Wiederkehr, gemeinsame Einstellungen,
  sechs Originale/Vorschauen und tatsächliche geplante Auslöseabstände.
  Letzte Demo-Sitzung: `9818c2de13e845f6882717fd82ab8f4b`.
- Gemeinsame manuelle Einstellung **ISO 800, 1/125 s, f/5.6** von A6000,
  A6300 und A6700 erfolgreich geschrieben und zurückgelesen. A6000/A6300
  standen am physischen Moduswahlrad auf M. Die A6000 ignorierte zuvor auch
  im offiziellen gphoto2-Client Schreibbefehle; nach dem Schließen von Menü/
  Wiedergabe durch den Nutzer funktionierten alle drei Werte. Sony liefert
  teilweise verzögerte Rückmeldungen; diese werden begrenzt abgewartet.
- Aus dem echten WebUI gestartete Sitzung **`92a644124c4d4161afb462475cb135d8`**:
  zwei vollständige Takes mit allen drei Kameras, vier JPEGs und zwei ARWs.
  Alle sechs SHA-256-Prüfsummen stimmen; alle sechs Vorschauen liefern HTTP 200.
- USB-Trennung der A6700 erkannt, Aufnahme gesperrt. Ihre physische Rückkehr
  konnte wegen anschließend leerem Akku nicht geprüft werden; sie bleibt als
  fehlende Kamera eingetragen. Die A6300 wurde erneut an WSL angehängt und
  automatisch wieder geöffnet. Die A6000 wurde über die API neu verbunden;
  gespeicherte ISO/Zeit/Blende wurden erfolgreich wieder angewendet.
- Bestätigter Warnmodus mit A6000/A6300: Sitzung
  **`c0e6153052da419fa523029603260b44`** vollständig, zwei Dateien und
  `excluded_cameras: ["a6700"]`. Eine vorherige zulässige Teilaufnahme
  `26d87e2b1f4444ff9cb91badcf3125dd` hatte nur A6000, weil die A6300 zu
  diesem Zeitpunkt zwar in Windows verfügbar, aber nicht an WSL angehängt war.
  Beide Auslassungen wurden korrekt im Manifest vermerkt.
- Browserprüfung: echte Gruppensteuerung und Aufnahme, Galerie inklusive
  alter CLI-Sitzungen und ARW-Vorschau; in der Demo außerdem Trennung,
  Wiederkehr und bewusstes Entsperren. JavaScript- und PowerShell-Syntax geprüft.
- Das tatsächlich im Browser gerenderte optische Muster wurde aus einem
  Screenshot korrekt decodiert (Counter 2416, Confidence 1.0). Die echte
  optische Messreihe mit auf den Bildschirm gerichteten Kameras steht noch aus;
  **keine optische Hardware-Kalibrierung oder Sub-Millisekunden-Synchronität
  wird behauptet**. Kompensation bleibt deaktiviert.

Abschlusszustand: A6000 und A6300 verbunden, A6700 wegen leerem Akku offline.
Der Standard **Lockout** ist wieder aktiv und bleibt bis zur bewussten Freigabe
des vollständigen Rigs bestehen. Für weitere Zweikameraaufnahmen im WebUI
Warnmodus wählen und die Teilaufnahme ausdrücklich bestätigen.
Start und Bedienung: [DOCKER.md](DOCKER.md).

## Kameraverwaltung im WebUI – 06.11.2026

- **60 Tests erfolgreich**, einschließlich Koppeln anhand ausgelesener
  Seriennummer, zwei Kameras desselben Modells, Ablehnung fehlender oder
  doppelter Identitäten, Aktivieren/Deaktivieren, gültiger leerer Liste,
  Reihenfolge, Neustart und Erhalt vorhandener Fotos beim Entfernen.
- Der vollständige API-Smoke-Test ist erfolgreich; zusätzliche Prüfung der
  Kamerareihenfolge in neuen Manifesten und Galerie, Aufnahme ohne deaktivierte
  Kamera, Entfernen ohne Löschen von Fotos und erneutes Koppeln. Bereits
  vorhandene Manifeste bleiben unverändert. Demo-Prüfsitzung:
  `f750eca8953b4e96955d9c6df2ef1741`.
- Browserprüfung in der Demo: Verschieben von `center` nach links,
  Deaktivieren und Entfernen von `right`, USB-Suche und erneutes Koppeln über
  das Formular. Die neue Verwaltung ist auch im Hardware-Container verfügbar.
- Liste, Aktivierungsstatus und Reihenfolge liegen mit den übrigen Einstellungen
  atomar in `data/settings.json`. Eine entfernte Kamera wird beim Neustart
  nicht erneut aus dem anfänglichen TOML-Profil importiert.
- Rig-Änderungen stoppen die bisherige Kompensation und setzen die bewusste
  Aufnahmesperre. Deaktivierte Geräte zählen nicht zu fehlenden aktiven Kameras.
  Entfernen verändert ausschließlich Rig-Eintrag, Wunschwerte und Delay-Wert.
- Zu diesem Zeitpunkt melden sich A6000/A6300 unter Windows nur im Ladezustand
  (054c:0994), A6700 ist weiterhin nicht am USB sichtbar. Daher wurde das neue
  Kopplungsformular vollständig mit der Simulation geprüft; eine erneute
  physische Kopplung einer eingeschalteten Kamera wurde hier nicht behauptet.
  Die bestehenden Einträge und ihre Reihenfolge bleiben erhalten.

## Verbindung nach USB-Neuanschluss – 06.11.2026

Windows erkannte alle drei eingeschalteten Kameras als **Shared**, WSL dagegen
keine USB-Kamera. Nach `usbipd attach --wsl` für die aktuellen BUSIDs `8-3`
(A6000), `8-4` (A6300, bestehende erzwungene Bindung) und `7-2` (A6700)
öffnete die App alle drei anhand ihrer Seriennummer erneut.

Der Reconnect-Auftrag meldet fehlende Ziele jetzt als Fehler mit genauerem
Hinweis und gibt nur bei tatsächlich verbundenen Zielen Erfolg zurück.
Die Vorprüfung aller angeforderten Kamera-IDs verhindert auch, dass eine
ungültige Auswahl bereits eine andere Kamera trennt. **62 Tests erfolgreich**.
Hardwareprüfung nach dem Update: alle drei Kameras bereit, Reconnect erfolgreich.
Die bestehende bewusste Aufnahmesperre wird beim Verbinden nicht aufgehoben.

## Gemeinsames Format und automatische Medien – 06.11.2026

- **74 Tests erfolgreich**. Neue Prüfungen umfassen unterschiedliche
  Sony-JPEG-Bezeichnungen, gemeinsames JPEG/RAW-Profil, gesperrte Einzeländerung,
  Wiederverbindung, Formatabweichung vor Auslösung und Rücksetzen bei Fehlern.
- Die Medienprüfungen erzeugen echte H.264-MP4s und GIFs aus farbigen Testbildern.
  Codec, Auflösung, Laufzeit, GIF-Schleifen und Farbreihenfolge wurden geprüft.
  Die Hintergrundwarteschlange verwendet die im Manifest gespeicherten Positionen,
  erhält Originale und alte Exporte und meldet fehlende Eingabedateien sowie
  unterbrochene Verarbeitung nach Neustart.
- Der erweiterte API-Smoke-Test ist erfolgreich: automatische MP4/GIFs für beide
  Takes, HTTP-Range/Seeking, Dateibegrenzung, globale Formatwahl und Neuberechnung
  mit geänderter Bilddauer. Das Originalmanifest blieb unverändert.
  Demo-Sitzung: `191ace5241bc494ea61964ff793d8bc5`.
- Auf Wunsch des Nutzers wurde ohne angeschlossene Kameras mit vorhandenen
  Aufnahmen geprüft. **16 vollständige Sitzungen / 20 Aufnahmerunden** wurden
  erfolgreich nachverarbeitet: **20 Videos und 20 GIFs**. Alle **54 veröffentlichten
  Originaldateien** stimmen weiterhin mit ihren SHA-256-Prüfsummen überein.
  Für sämtliche Exporte wurden Prüfsummen, MP4-Codec/-Dauer und GIF-Schleifendauer
  nochmals geprüft. Fehlgeschlagene/unvollständige Fotositzungen wurden übersprungen.
- Browserprüfung: neue Seite Verarbeitungseinstellungen, dynamische
  Zeitberechnung, Speichern, nachträgliche Sammelverarbeitung, Video/GIF unter
  den Originalbildern, geladenes 640-px-GIF und tatsächlich laufendes Video
  (`paused=false`, `readyState=4`, 3,6 s).
- **JPEG ist dauerhaft als gemeinsames Zielprofil gespeichert.** A6000/A6300
  werden dabei auf Fine, A6700 auf JPEG und JPEG Only für den PC gesetzt.
  Da die Kameras aktuell laut Nutzer nicht verfügbar sind, wird die physische
  Umstellung erst beim nächsten Verbinden vorgenommen; sie wurde hier nicht
  als neue Hardwareabnahme behauptet. Alte RAW-Originale bleiben erhalten.
- Die Laufzeit bleibt Docker/WSL. FFmpeg kommt aus dem Debian-Paket im Image;
  keine GPU erforderlich. ARM64-Wheels für Python 3.12 wurden auf PyPI für
  gphoto2, rawpy, OpenCV, NumPy und Pillow geprüft. Ein echter Raspberry-Pi-Lauf
  wurde weiterhin nicht durchgeführt.
