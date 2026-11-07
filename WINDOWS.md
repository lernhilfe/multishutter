# MultiShutter als Prozess unter Windows 11

MultiShutter wird aus dem entpackten Projektordner gestartet. Es gibt keinen
MultiShutter-Installer, Windows-Dienst, Autostart oder Eintrag im Startmenü.
Der Prozess endet mit `quit`; Fotos und Konfigurationen bleiben im Projektordner.

Die kostenlose Kamera-Anbindung verwendet **WSL2 + Ubuntu 24.04 + libgphoto2**.
Windows stellt die ausgewählten USB-Kameras mit **usbipd-win** bereit.
WSL, Ubuntu und usbipd sind installierte Systemvoraussetzungen, die nach dem
Beenden der Konsole erhalten bleiben. usbipd bringt einen USB-Hilfsdienst mit.
Ein ausschließlich natives Windows-USB-Backend ist in dieser Version nicht enthalten.

## 1. Voraussetzungen einrichten

ZIP in einen lokalen, beschreibbaren Ordner entpacken, beispielsweise unter
Downloads. Alle Dateien und Unterordner zusammenlassen. Dort **als normaler
Windows-Benutzer** `Setup-Windows.cmd` starten. Es fragt bei Bedarf über UAC nach
Administratorzugriff für die Systemkomponenten; der ursprüngliche Benutzer behält
seine eigene WSL-Distribution. Nicht von Beginn an unter einem anderen Admin-Konto starten.

Das Setup:

1. aktiviert WSL und Virtual Machine Platform;
2. installiert usbipd-win über winget (interaktiver Installer);
3. installiert die WSL-Laufzeit und Ubuntu 24.04 unter deinem Windows-Konto;
4. installiert Python, gphoto2 und USB-Werkzeuge **innerhalb von Ubuntu** sowie
   `gphoto2==2.6.4` mit libgphoto2 2.5.34 in einer eigenen Python-Umgebung;
5. legt dort einen normalen Benutzer `multishutter` an und erlaubt ihm den Zugriff
   auf die explizit weitergereichten Sony-USB-Geräte;
6. prüft die Laufzeit und führt die automatisierten Tests aus.

Der WSL-Benutzer braucht für diesen Startweg kein Passwort. Vorhandene Benutzer
werden nicht gelöscht oder mit einem neuen Passwort versehen. Für diesen Aufbau
wird kein Raspberry-Pi-Passwort verwendet.

Die Kamera-Python-Umgebung liegt unter
`/home/multishutter/.local/share/multishutter/venv`. Die Windows-Starter wählen sie
automatisch aus. Das ältere Ubuntu-Systempaket allein erkennt die A6700 nicht korrekt.
MultiShutter selbst bleibt Quellcode im Projektordner.

Falls das Setup einen Neustart meldet: Windows bei Gelegenheit neu starten und
`Setup-Windows.cmd` erneut ausführen. Es erfolgt **kein automatischer Neustart**.
Virtualisierung muss im BIOS/UEFI aktiviert sein. Das Setup zeigt Fehler von Windows
oder winget an; eine Unternehmensrichtlinie oder fehlende Virtualisierung kann es
nicht selbst beheben. Fehlt winget, Microsofts „App Installer“ oder usbipd-win zuerst
bereitstellen. Eine vorhandene WSL1-Distribution wird nicht stillschweigend konvertiert.

Die CMD-Starter setzen die PowerShell-Ausführungsrichtlinie nur für ihren eigenen
Prozess. Sie verändern keine permanente Execution Policy. Vor Ausführung kannst du
die danebenliegenden `.ps1`- und `.sh`-Dateien lesen.

## 2. Ohne Kameras ausprobieren

PowerShell im entpackten Projektordner öffnen:

```powershell
.\Start-MultiShutter.cmd -Simulated
```

In der gestarteten Konsole:

```text
doctor
check
capture --takes 3 --countdown 3 --interval 1
quit
```

Das erzeugt neun kleine PNG-Testbilder und ein Manifest unter
`captures/<session-id>/`. Die Simulation ist ausdrücklich markiert.
Windows-Python muss dafür nicht installiert sein; der Starter verwendet Ubuntu-Python.

## 3. Kameras verbinden und an WSL weiterreichen

An jeder Kamera **PC Remote**, Einzelbild und zunächst JPEG einstellen.
Manuellen Fokus, feste Belichtung und festen Weißabgleich verwenden. Andere
Kamera-Programme schließen. Dann die Kameras per USB verbinden.

Zuerst die Konsole öffnen und geöffnet lassen; sie hält WSL aktiv:

```powershell
.\Start-MultiShutter.cmd
```

In einer **zweiten PowerShell im Projektordner**:

```powershell
.\Connect-Cameras.cmd
```

Die Tabelle zeigt die Windows-USB-**BUSIDs**. Wähle nur die Ports deiner Sony-Kameras.
`1-2,1-3` in den folgenden Befehlen sind **Beispiele**, keine automatisch ermittelten
Werte. Das Hilfsskript akzeptiert ausschließlich explizit ausgewählte angeschlossene
Sony-Geräte (USB-Vendor `054c`) und übernimmt keine ganzen Hubs.

Einmal zum Freigeben (fragt über UAC nach Admin-Rechten):

```powershell
.\Connect-Cameras.cmd -BusId 1-2,1-3 -Bind
```

Anschließend in WSL einhängen:

```powershell
.\Connect-Cameras.cmd -BusId 1-2,1-3
```

Solange sie an WSL hängen, stehen diese Kameras Windows-Kameraanwendungen nicht zur
Verfügung. Nach Abziehen, Neustart oder Wiederverbinden kann ein erneutes Einhängen
und eine neue Portzuordnung erforderlich sein. Es wird nichts automatisch ausgelöst.

## 4. Physische Kamerareihenfolge festlegen

Zur ersten, weiterhin geöffneten MultiShutter-Konsole wechseln:

```text
discover
configure
```

`configure` zeigt die erkannten Kameras nummeriert an. Gib die Nummern in ihrer
**physischen Reihenfolge von links nach rechts** ein, beispielsweise `2,1,3`.
Die USB-Erkennungsreihenfolge wird nicht als Position angenommen. Falls die Zuordnung
unklar ist, zunächst Kameras einzeln verbinden und ihre Ports notieren.

Es entsteht `config/rig.local.toml`; eine vorhandene Datei wird vorher gesichert.
Der Assistent erzeugt ein JPEG-Einzelbildprofil mit Standard-Zeitlimits und neuen IDs.
Bei erneuter Einrichtung individuelle Einstellungen/Seriennummern aus der Sicherung
übernehmen. Die von Windows angezeigte BUSID unterscheidet sich von der Linux-Adresse
wie `usb:001,003`; der Assistent verwendet die tatsächlich in WSL ermittelte Adresse.

Danach:

```text
check
capture --countdown 3
capture --takes 3 --countdown 3 --interval 1
```

`check` löst nicht aus. `capture` löst alle konfigurierten Kameras möglichst gleichzeitig
aus und lädt die Bilder herunter. `--interval` ist die Pause nach Übertragung und
Vorbereitung, kein festes Belichtungsraster. Bei unvollständigen Ergebnissen stoppt die
Serie; eine Auslösung wird nicht automatisch wiederholt.

Du kannst danach weitere Aufnahmen eingeben, bis du `quit` eingibst. Der Konsolenprozess
bleibt offen; Kamera-Worker/Verbindungen werden für jeden Aufnahmeauftrag geöffnet,
über dessen Takes gehalten und anschließend geschlossen. Das lässt eine erneute
Zuordnung nach USB-Neuverbindungen zu. Es ist kein Hintergrunddienst.

## Fotos, Ende und Diagnose

Fotos liegen unter `captures` direkt neben den Startern, somit auch im Windows-Explorer.
Manifestpfade sind Linux-Pfade (`/mnt/c/...`), die zu demselben Windows-Ordner gehören.
Den Projektordner während einer Aufnahme nicht verschieben. `Config` am Windows-Starter
erwartet einen relativen Projektpfad oder Linux-Pfad, keinen `C:\...`-Pfad.

Beenden und an Windows zurückgeben:

```text
quit
```

```powershell
.\Connect-Cameras.cmd -BusId 1-2,1-3 -Detach
```

Die USB-Freigabe kann administrativ mit `-Unbind` aufgehoben werden. WSL/Ubuntu und
usbipd bleiben als Voraussetzungen installiert. `wsl --shutdown` ist für MultiShutter
nicht nötig und würde auch andere laufende WSL-Prozesse beenden.

Diagnose ohne Kamerazugriff:

```powershell
.\Start-MultiShutter.cmd -Command doctor
```

Bei leerem `discover`: PC-Remote-Modus, `Connect-Cameras.cmd`-Tabelle und USB-Weiterleitung
prüfen. Bei Berechtigungsfehlern nach dem Setup die Geräte ab-/wieder einhängen, damit
die neue udev-Regel angewendet wird. Bei einer veralteten gphoto2-Version die ausgegebene
Bibliotheksversion prüfen. USB/IP bringt zusätzliche Latenz; Host-Befehlszeiten sind
weiterhin kein Nachweis gleichzeitig belichteter Fotos.

Bei einer A6300, die erkannt wird, aber beim Öffnen mit USB-Timeout abbricht:
USB-Kabel abziehen und im Kamera-Setup `USB-LUN-Einstlg.` auf `Einzeln/Single`
stellen; `USB-Verbindung` bleibt `PC Remote`. Sony nennt `Single` ausdrücklich als
Kompatibilitätsoption bei Verbindungsproblemen. Einen vollständigen Stromreset mit
abgezogenem USB-Kabel und kurz entnommenem Akku durchführen, falls sie weiter hängt.
Danach erneut an WSL anschließen und mit `discover` die aktuelle Adresse prüfen.

Für gezielte Treiberdiagnose unterstützt `Connect-Cameras.cmd -BusId BUSID -Bind -Force`
die alternative usbipd-Treiberbindung. Sie ersetzt den normalen Windows-Gerätetreiber
bis zum Aufheben mit `-Unbind`; `-Detach` allein hebt diese Treiberbindung nicht auf.
Die Option gilt nur zusammen mit `-Bind` und für die explizit ausgewählten Sony-Geräte.
Auf diesem PC ist die A6300 damit nach USB-LUN Single und vollständigem Stromreset
erfolgreich angebunden; zwei gemeinsame Aufnahmerunden aller drei Kameras mit sechs
übertragenen Dateien und gültigen Prüfsummen sind in `VALIDATION.md` dokumentiert.

**Validierung auf dem PC (6. November 2026):** Ubuntu 24.04 unter WSL2 und usbipd-win
5.3.0 sind installiert. Alle 26 automatisierten Tests bestehen. Der Windows-Starter
hat drei simulierte Aufnahmerunden mit neun Bildern erfolgreich ausgeführt; sämtliche
Prüfsummen stimmen. A6000, A6300 und A6700 werden über USB/IP in WSL erkannt.
Den aktuellen Hardware-Teststand dokumentiert `VALIDATION.md`.

## Referenzen

- [Microsoft: USB-Geräte in WSL](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)
- [Microsoft: WSL installieren](https://learn.microsoft.com/en-us/windows/wsl/install)
- [usbipd-win](https://github.com/dorssel/usbipd-win)
- [Python-gphoto2](https://github.com/jim-easterbrook/python-gphoto2)
