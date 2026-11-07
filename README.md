# MultiShutter

Erste Komponente einer Bullet-Time-Photobooth für **Windows 11 über WSL2, Linux und Raspberry Pi OS**:
Kameras vorbereiten, möglichst gleichzeitig auslösen und Originaldateien eindeutig
nach Sitzung, Take und Kameraposition lokal speichern. Kein Internet erforderlich.

**WebUI / Docker Compose:** `Start-WebUI.cmd` startet die App unter WSL und öffnet
[http://localhost:8787](http://localhost:8787). Aufnahme, Galerie, gemeinsame manuelle
Kameraeinstellungen, Kameraverwaltung mit Links-rechts-Reihenfolge,
Auto-Reconnect, Sperre/Warnung und optischer Delay-Tester sind
integriert. Ein gemeinsames Aufnahmeformat (Standard JPEG) gilt für alle aktiven
Kameras. Die neue Seite Verarbeitungseinstellungen steuert die automatische
MP4-/GIF-Erstellung; Ergebnisse stehen unter den Fotos in der Galerie.
Einrichtung und Bedienung: [DOCKER.md](DOCKER.md).

**Hardwarestatus:** Sony A6000, A6300 und A6700 wurden an diesem Windows-PC über
WSL2 gemeinsam ausgelöst. Ergebnisse und Grenzen stehen in [VALIDATION.md](VALIDATION.md).
Die optische Zeitkorrektur muss am jeweiligen Aufbau mit sichtbarem Testmuster
gemessen werden. Der Raspberry Pi wurde nicht geprüft.

## Auf dem Windows-PC als Prozess starten

Siehe [WINDOWS.md](WINDOWS.md) für das vollständige Setup und die USB-Weiterleitung.
MultiShutter selbst wird nicht als App, Dienst oder Autostart installiert.
WSL2, Ubuntu und usbipd sind separate Systemvoraussetzungen.

1. `Setup-Windows.cmd` lokal starten; bei gemeldetem Neustart danach erneut ausführen.
2. `Start-MultiShutter.cmd -Simulated` zum Testen ohne Kameras starten.
3. `Start-MultiShutter.cmd` starten und in einem zweiten Terminal mit
   `Connect-Cameras.cmd` die ausgewählten Sony-USB-Geräte an WSL weiterreichen.
4. In der Konsole `discover`, `configure`, `check` und `capture --countdown 3` eingeben.
5. `quit` beendet den Prozess.

Auf einem Linux-PC ist ebenfalls keine App-Installation erforderlich: nach Bereitstellen
der gphoto2-Python-Bindung einfach `/usr/bin/python3 -m multishutter console` im Projektordner
starten. Auch ohne Kameras ist `console --backend simulated --config config/rig.simulated.toml`
verfügbar. `doctor` prüft die Laufzeit ohne Kamerazugriff.

## Auf dem Raspberry Pi installieren (optional)

Benötigt: Raspberry Pi OS Lite **64 Bit**, Python **3.11 oder neuer**, geeignete
Kamerastromversorgung, aktive Kühlung und ein versorgter USB-Hub.

Nach dem Entpacken des Projekts **auf dem Pi**, als normaler Benutzer:

```bash
cd /pfad/zu/multishutter
bash scripts/install-pi.sh
```

Der Installer installiert die benötigten apt-Pakete, führt die automatisierten
Tests und drei simulierte Aufnahmerunden mit Dateiprüfung aus und legt einen
Starter unter `~/.local/bin/multishutter` an. Er löst keine echte Kamera aus.
Die Simulation liegt unter `captures/install-check/`. Vorhandene lokale
Kamerakonfigurationen bleiben erhalten; ein vorhandener Starter wird gesichert.
Passwörter werden gegebenenfalls von `sudo` abgefragt und nicht gespeichert.
Mit `bash scripts/install-pi.sh --check-only` lassen sich Betriebssystem,
Architektur und vorhandene Abhängigkeiten ohne Änderungen prüfen.

Für eine Installation über SSH muss der ausführende Rechner den Pi erreichen
können. Eine Cloud-Arbeitsumgebung hat ohne eingerichteten Netzwerkzugang keinen
Zugriff auf das Heimnetz; das gilt auch bei bekannter privater IP-Adresse.

Alternativ die Abhängigkeiten manuell installieren:

```bash
sudo apt update
sudo apt install python3 python3-gphoto2 gphoto2
cd /pfad/zu/multishutter
/usr/bin/python3 -m multishutter --version
gphoto2 --version
```

Das Programm selbst benötigt keine zusätzlichen Python-Pakete. Die Hardwarebindung
kommt aus `python3-gphoto2`. Verwende `/usr/bin/python3`, damit sie gefunden wird.
Eine normale venv sieht apt-Pakete nicht; falls benötigt, mit
`/usr/bin/python3 -m venv --system-site-packages .venv` anlegen.

Falls die Distribution eine ältere libgphoto2-Version liefert, die eine Kamera nicht
korrekt auslöst, zuerst deren Unterstützung mit einer aktuellen stabilen Version
prüfen. Die Installation einer beliebigen alten Version garantiert keine Unterstützung
der a6700. Version und Firmware bei der Hardwareabnahme notieren.

## Ohne Kameras ausprobieren

```bash
python3 -m multishutter discover --backend simulated
python3 -m multishutter check --config config/rig.simulated.toml
python3 -m multishutter capture --config config/rig.simulated.toml --output captures --takes 3 --countdown 3 --interval 1
```

Es entstehen neun kleine gültige PNG-Testbilder mit einem eindeutig als simuliert
gekennzeichneten Manifest. Es werden keine Kameras angesprochen. `--interval` ist die
Pause **nach abgeschlossener Übertragung und Vorbereitung**, kein festes Raster
zwischen Belichtungen. `--countdown` gilt nur vor dem ersten Take.

## Echte Kameras vorbereiten

1. Am jeweiligen Sony-Modell USB-**PC Remote** aktivieren; nicht Massenspeicher,
   MTP oder USB-Streaming. Die Menübezeichnung hängt vom Modell ab.
2. **Einzelbild**, JPEG, manuellen Fokus, feste Belichtungszeit, Blende, ISO und
   Weißabgleich einstellen. Auto-Sleep, Selbstauslöser und Bracketing deaktivieren.
3. Keine andere Kameraanwendung parallel öffnen. Unter Linux gegebenenfalls eine
   automatisch gestartete Fotoimport-Anwendung schließen. MultiShutter als normalen
   Benutzer betreiben; bei USB-Berechtigungsproblemen die libgphoto2/udev-Regeln prüfen.
4. Zunächst jede Kamera einzeln, anschließend den gesamten Aufbau testen.
   Während einer Sitzung die Kameras nicht zusätzlich manuell auslösen.

```bash
/usr/bin/python3 -m multishutter discover
cp config/rig.example.toml config/rig.local.toml
# rig.local.toml bearbeiten: nur vorhandene Kameras, exakte Modelle und aktuelle Ports
/usr/bin/python3 -m multishutter check --config config/rig.local.toml
/usr/bin/python3 -m multishutter capture --config config/rig.local.toml --output /pfad/zur/ssd/captures --countdown 3
```

`check` öffnet die Kameras, prüft Modell, optionale Seriennummer und angekündigte
Trigger-Unterstützung, löst aber **nicht** aus und bestätigt keine funktionierenden
Dateiereignisse. Die Kameraeinstellungen werden derzeit **nicht automatisch verändert
oder vollständig validiert**.

**Zuordnung:** `id` ist ein stabiler, selbst vergebener Name, `position` die räumliche
Reihenfolge. `port` ist die aktuelle USB-Adresse. Sie kann sich bei Neuverbindung
ändern. Modell und Port werden vor jeder Sitzung geprüft. Soweit die Firmware sie
bereitstellt, zeigt `check` die Seriennummer; diese anschließend als `serial` in die
Konfiguration übernehmen. Ein gesetzter, aber nicht lesbarer oder falscher Wert blockiert
die Aufnahme. Ohne Seriennummer muss der Betreiber die physische Zuordnung bestätigen,
insbesondere bei mehreren Exemplaren desselben Modells.

`files_per_take = 1` erwartet JPEG **oder** RAW, `2` RAW+JPEG. Der Wert muss zur
Kameraeinstellung passen. Zusätzliche Dateien machen einen Take mehrdeutig und stoppen
die Serie. RAW+JPEG erst nach erfolgreichem JPEG-Test verwenden. Datei-Ereignisse werden
bis zur erwarteten Zahl oder zum Timeout gesammelt, danach mit einer kurzen Ruhefrist.
Sehr spät eintreffende zusätzliche Dateien können mit dieser Strategie nicht sicher
erkannt werden; deshalb ist ein passendes Einzelbildprofil erforderlich.

## Ergebnis und Prüfung

Die CLI schreibt Fortschritt nach stderr und das abschließende JSON nach stdout.
Exitcodes: `0` vollständig, `1` fehlgeschlagen/unvollständig, `2` Bedien-/Startfehler,
`130` Benutzerabbruch. Bei einer unterbrochenen Sitzung können die tatsächlichen
Auslöseergebnisse unbekannt sein; es erfolgt keine automatische Fortsetzung.

```text
captures/<session-id>/
  manifest.json
  takes/001/left-a6000/01_DSC01234.JPG
  takes/001/right-zve10/01_DSC04567.JPG
  takes/002/...
```

```bash
python3 -m multishutter status captures/SESSION_ID --verify
```

Das Manifest enthält erwartete Kameras und Takes, Vorbereitung, Backendmeldungen,
Original-Dateireferenzen, Downloadversuche, Dateigröße, SHA-256 und Zustände. Downloads
erhalten zunächst `.part`-Dateien und werden erst nach erfolgreichem Schreiben und
Hashen atomar umbenannt. Kameraoriginale werden **nie aktiv gelöscht**. Je nach
Sony-Speicherziel können Aufnahmen jedoch ausschließlich im flüchtigen PC-Remote-Puffer
liegen; eine SD-Sicherung wird nicht garantiert.

`--verify` vergleicht vollständige lokale Dateien mit den aufgezeichneten Prüfsummen.
Das erkennt spätere Veränderungen; ohne kameraseitige Prüfsumme ist dies kein Beweis
für bitgenaue USB-Übertragung oder eine decodierbare Bilddatei. Pfade im Manifest sind
absolut: nach Verschieben einer Sitzung müssen sie angepasst werden.

## Architektur und Grenzen

```text
CLI → Capture Coordinator → ein persistenter Prozess pro Kamera
                         → CameraBackend (gphoto2 oder Simulation)
                         → Originaldateien + atomar geschriebenes JSON-Manifest
```

- Kameraobjekte und USB-Verbindungen bleiben über alle Takes bis nach den Downloads
  geöffnet. Kein Start eines neuen gphoto2-CLI-Prozesses pro Foto.
- Alle Worker bekommen denselben zukünftigen monotonen Host-Zeitpunkt. Danach nutzen
  sie `trigger_capture()` und `wait_for_event()`; ein nicht unterstützter Trigger wird
  nicht still auf einen anderen Aufnahmemechanismus umgestellt.
- Bilddownloads beginnen nach der Ereignissammlung und laufen mit begrenzter
  Parallelität. Fehlgeschlagene Downloads können innerhalb der offenen Verbindung
  erneut versucht werden. Auslösungen werden **nie automatisch wiederholt**.
- Bei einem unvollständigen Take werden bekannte Bilder trotzdem gesichert und die
  Serie gestoppt. Es wird keine Kamera einzeln nachträglich neu ausgelöst.
  Auch nach einer unklaren Trigger-Antwort wird versucht, bereits entstandene Dateien
  zu sichern; der Take bleibt vorsichtshalber unvollständig.
- Eine pro Benutzer gemeinsame Betriebssystemsperre verhindert parallele
  MultiShutter-Aufnahmen auch mit unterschiedlichen Ausgabeordnern. Andere Programme
  und andere OS-Benutzer werden dadurch nicht koordiniert; das Rig mit einem dedizierten
  Benutzer betreiben. Die Sperre wird beim Prozessende automatisch freigegeben.
- Vor dem Auslösen werden unbekannte Ergebnisse im Manifest festgehalten. Nach
  SIGKILL/Stromverlust kann der Status daher noch `capturing` sein. `status` wertet das
  nicht als Erfolg. Ein automatisches Wiederaufnehmen offener Kamera-Transfers nach
  Prozessende ist noch nicht implementiert; flüchtige Kamerapuffer können verloren gehen.
- SQLite, Weboberfläche, Bildbearbeitung, Druck und Cloud sind noch nicht enthalten.
  Das Manifest ist in dieser Ausbaustufe die persistente Quelle. Der Coordinator und die
  Backend-Schnittstelle können später aus einem lokalen API-Service aufgerufen werden.
- Mindestfreispeicher ist ein Sicherheitsabstand, keine Kapazitätsgarantie für einen
  ganzen Take. Schreibfehler werden protokolliert, Kameraoriginale nicht gelöscht.

`trigger_started_ns`, `command_lateness_ms` und `command_start_spread_ms` beschreiben
**Host-Befehlszeiten**, nicht die tatsächliche Belichtung. USB- und Verschlusslatenzen,
AF und Modellunterschiede bleiben bestehen. Zur Hardwareabnahme ein millisekundengenaues
LED-Zeitmuster mit allen Kameras aufnehmen und die sichtbaren Zeitwerte vergleichen.
EXIF-Zeitstempel sind dafür nicht präzise genug. Es gibt keine zugesicherte Synchronität.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Die Tests prüfen Prozesskoordination mit Simulation, sichere Dateizuordnung,
Downloadwiederholung ohne erneute Auslösung, unvollständige Takes, Worker-Timeouts,
Sperren und Dateiintegrität. Sie ersetzen keine Hardwaretests.

## Quellen

- [libgphoto2-Unterstützungsliste](https://gphoto.sourceforge.io/proj/libgphoto2/support.php)
- [Python-gphoto2](https://github.com/jim-easterbrook/python-gphoto2)
- [libgphoto2 API](https://gphoto.sourceforge.io/doc/api/)
