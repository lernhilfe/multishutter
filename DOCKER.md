# MultiShutter WebUI mit Docker Compose

Die App läuft vollständig in Docker unter WSL2. USB-Kameras werden einmal von
Windows an WSL weitergereicht. Fotos und Einstellungen bleiben im Projektordner.

## Auf diesem PC starten

1. Kameras einschalten, USB-Verbindung **PC Remote**, Einzelbild; Menüs und
   Wiedergabe schließen. Für gemeinsame Belichtung das Moduswahlrad auf **M** stellen.
2. Falls noch nicht an WSL angeschlossen: `Connect-Cameras.cmd` zeigt die
   USB-Liste. Danach die eingeschalteten Kameras gezielt anhängen, zum Beispiel
   `Connect-Cameras.cmd -BusId 8-3,8-4` für die zuletzt angeschlossenen A6000/A6300.
   Nach einem Portwechsel stets die aktuell angezeigten BUSIDs verwenden.
   Die A6700 an `7-2` kann nach dem Laden separat angehängt werden.
   Die A6300 benötigt hier USB-LUN **Single** und die bereits eingerichtete
   erzwungene Bindung an Port `8-4`. Details und Rücknahme stehen in [WINDOWS.md](WINDOWS.md).
3. `Start-WebUI.cmd` doppelklicken. Es baut/aktualisiert den Container und öffnet
   [http://localhost:8787](http://localhost:8787). Docker ist bereits installiert.
   Auf einem neuen WSL-Setup zuerst `Setup-Docker.ps1` ausführen.
4. Zum Stoppen `Start-WebUI.cmd -Stop` ausführen. Fotos bleiben erhalten.

`Start-WebUI.cmd -Demo` öffnet eine unabhängige Simulation auf Port **8788**, ohne
USB-Zugriff. Beenden mit `Start-WebUI.cmd -Demo -Stop`.
`-NoBrowser` unterdrückt das Öffnen des Browsers, `-LocalPort 8790` ändert den
Windows-Port. Node.js stellt die lokale Weiterleitung bereit. Ohne Node oder bei
belegtem Port zeigt das Startskript stattdessen die aktuelle WSL-IP-Adresse an.
Windows reserviert auf diesem Rechner die Ports um 8000; deshalb verwendet der
Starter 8787. Nach WSL-Neustart den Starter erneut ausführen, damit die IP stimmt.

## Bedienung

- **Aufnahme:** Anzahl, Countdown und Pause zwischen abgeschlossenen Takes;
  Auslösen auch per Leertaste. Stop sichert laufende Bilder und verhindert den
  nächsten Take. Eine unklare Auslösung wird niemals automatisch wiederholt.
- **Aufnahmen:** Originale herunterladen, JPEG- und ARW-Vorschauen ansehen,
  Manifest mit Dateigröße und SHA-256 herunterladen. Bestehende CLI-Sitzungen
  bleiben auch nach dem Wechsel in den Container sichtbar.
- **Kameraverwaltung:** USB-Kameras suchen und mit eindeutiger Kamera-ID koppeln.
  Neue Kameras werden ohne Auslösung geöffnet, anhand ihrer Seriennummer
  identifiziert und rechts angefügt. Mit **← / →** die Reihenfolge von links nach
  rechts ändern. Diese Reihenfolge gilt für neue Aufnahmen und ihre Galerie;
  alte Sitzungen behalten ihre ursprüngliche Reihenfolge.
  **Deaktivieren** pausiert eine Kamera, behält Platz und Wunschwerte und nimmt
  sie aus Verbindungsprüfung, gemeinsamer Steuerung und Aufnahmen heraus.
  **Aktivieren** verbindet sie wieder. **Entfernen** löscht ihren Rig-Eintrag und
  ihre gespeicherten Wunschwerte, während sämtliche Fotos und Protokolle erhalten
  bleiben. Auch ein leeres Rig kann anschließend neu bestückt werden.
  Nach einer Rig-Änderung ist die Zeitkorrektur aus; das aktive Rig muss einmal
  mit **Rig prüfen & entsperren** freigegeben werden.
- **Kameraeinstellungen:** Kameras auswählen und ISO, Zeit, Blende, Weißabgleich
  und Fokus gemeinsam setzen. Die Auswahl enthält die
  Schnittmenge der gemeldeten Fähigkeiten. Vorabprüfung aller Ziele, paralleles
  Schreiben und Rücklesen; bei Fehlern Rücksetzen mit Ergebnis pro Kamera.
  Dieses Rücksetzen ist bestmöglich, keine garantierte Hardware-Transaktion.
  A6000/A6300 benötigen M am physischen Rad. Ein offenes Kameramenü kann
  Schreibbefehle verhindern; die App meldet dann einen Fehler statt Erfolg.
- **Gemeinsames Aufnahmeformat** unter Kameraeinstellungen gilt immer für alle
  aktiven Kameras, unabhängig von der Auswahl für Belichtungswerte. Standard ist
  **JPEG**: A6000/A6300 melden dafür „Fine“, A6700 „JPEG“. Die A6700 wird auch
  auf die passende PC-Übertragung gestellt. RAW und RAW+JPEG können bei Bedarf
  ebenfalls nur für das gesamte aktive Rig gewählt werden. Vorabprüfung,
  Rücklesen und bestmögliches Rücksetzen gelten auch hier. Das gespeicherte
  Format wird beim Wiederverbinden angewendet, auch wenn die übrigen Wunschwerte
  nicht wiederhergestellt werden sollen. Eine manuell abweichend eingestellte
  Kamera sperrt neue Auslösungen, bis das Format wieder angeglichen ist.
- **Systemeinstellungen:** Auto-Reconnect, Wiederholabstand und Zeitlimits,
  Sperre oder Warnung bei Verbindungsverlust. Standard ist eine auch über
  Container-Neustarts bleibende
  Aufnahmesperre: nach Rückkehr aller Kameras **Rig prüfen & entsperren**.
  Im Warnmodus muss eine Teilaufnahme mit fehlenden Kameras ausdrücklich
  bestätigt werden; Ausschlüsse stehen im Manifest.

Das Reconnect der App öffnet eine wieder verfügbare Kamera anhand Modell und
Seriennummer, auch bei geänderter Linux-USB-Adresse. Nach physischem Abziehen
oder Ausschalten kann zusätzlich `Connect-Cameras.cmd` erforderlich sein, um
das Gerät erneut von Windows an WSL zu hängen. Die App ändert keine Windows-Treiber.
Erfolgreich gesetzte Wunschwerte werden bei Wiederverbindung erneut angewendet.

## Verarbeitungseinstellungen: MP4 und GIF

Die neue Seite **Verarbeitungseinstellungen** steuert die automatische Erstellung
nach einer vollständigen Foto-Sitzung. **Je Aufnahmerunde** entstehen ein MP4 und
ein GIF aus den Kameraperspektiven in der im Aufnahmeprotokoll gespeicherten
Links-rechts-Reihenfolge. Kalibrieraufnahmen und unvollständige Sitzungen werden
nicht automatisch verarbeitet. Bei nur einer Perspektive bleibt der Export ein
Standbild; es werden keine zusätzlichen Perspektiven erfunden.

Einstellbar sind automatische Erstellung, Video/GIF einzeln, Dauer pro Kamerabild,
Links→rechts / Rechts→links / Hin und zurück, Video-Wiederholungen, Breiten und
Seitenverhältnis. Standard: **150 ms je Bild**, Hin und zurück, sechs
Video-Wiederholungen, **1280 px MP4** und **640 px GIF**, 16:9. Bei drei Kameras
ergibt das eine Kamerafahrt von 0,6 s und ein Video von 3,6 s. GIFs laufen endlos.
MP4 ist H.264 / yuv420p mit 30 Ausgabebildern pro Sekunde und ohne Ton.

Unter **Aufnahmen** stehen Video und GIF direkt unter den Originalbildern;
beide lassen sich abspielen bzw. herunterladen. **Mit aktuellen Einstellungen
neu erstellen** verarbeitet die jeweilige Sitzung erneut. Änderungen gelten für
neu eingereihte Aufträge. **Fehlende Videos & GIFs erstellen** auf der
Verarbeitungsseite ergänzt die letzten bis zu 50 vollständigen Sitzungen ohne
fertige Exporte. Alte RAW-Fotos werden dabei über ihre eingebettete JPEG-Vorschau
gelesen; neue JPEG-Fotos werden direkt verwendet.

Eine eigene Warteschlange rendert nacheinander ohne USB-Zugriff, mit höchstens
zwei Encoder-Threads. Die Kamerasteuerung bleibt unabhängig. FFmpeg ist im
Docker-Image enthalten; es wird keine GPU benötigt. Exporte sind auf 32
Perspektiven, 120 s Videolänge und drei Minuten Encoder-Laufzeit je Runde
begrenzt. Bestehende Originale und Manifeste werden nicht verändert. Jeder Export
erhält eine neue Generation unter `captures/<Sitzung>/renders/`; die Galerie
zeigt die zuletzt erfolgreich erzeugten Ergebnisse. Bei einem fehlgeschlagenen
Neulauf bleiben die bisherigen Ergebnisse erhalten. Nach Container-Neustart
werden unterbrochene Aufträge als fehlgeschlagen angezeigt und können erneut
gestartet werden.

Wenn **Verbinden** keine Kamera findet: `usbipd list` unterscheidet **Shared**
(unter Windows freigegeben) von **Attached** (tatsächlich an WSL angehängt).
Shared allein reicht für den Docker-Container nicht. Nach Ausschalten, Abziehen
oder Portwechsel die aktuellen Kamera-BUSIDs mit `Connect-Cameras.cmd -BusId ...`
erneut anhängen. Der WebUI-Button prüft die Kameraverbindungen im Container;
er führt keine Windows-USB-Weiterleitung aus. Ein Auftrag mit fehlenden Kameras
wird nun als unvollständig mit betroffenen Kameras und Hinweis gemeldet,
anstatt lediglich „Auftrag abgeschlossen“ anzuzeigen.

## Automatischer Delay-Tester

Unter **Synchronisation** das optische Testmuster öffnen. Alle Kameras müssen
denselben Bildschirm einschließlich der vier farbigen Ecken aufnehmen; das Muster
sichtbar und möglichst im Vollbild lassen. Manuellen Fokus, feste ISO/Blende und
eine kurze Zeit wie 1/250 s verwenden. Dann **5 Testaufnahmen** starten und nach
der Auswertung die vorgeschlagene Korrektur übernehmen.

Das wechselnde, fehlergeprüfte Zeitmuster wird direkt aus den Fotos gelesen.
Mindestens drei vollständige, lesbare Runden sind erforderlich. Schnellere
Kameras erhalten eine zusätzliche Auslöseverzögerung; Streuung und geschätztes
Bildschirmraster werden angezeigt. Unlesbare oder während des Bildwechsels
gemischte Codes werden verworfen. Das Verfahren misst relative Aufnahmezeiten;
USB-Antwortzeiten allein werden nicht als Verschlussverzögerung ausgegeben.

Bei 60 Hz beträgt das Bildschirmraster etwa **16,7 ms**. Monitor-Scanout,
Rolling Shutter und wechselnder Autofokus begrenzen die Genauigkeit. Es gibt
keine Garantie für Sub-Millisekunden-Synchronität. Nach Korrektur eine weitere
Aufnahme des Musters zur Gegenprobe machen. Belichtungsänderungen und, im
Standardprofil, Verbindungsverlust deaktivieren die bisherige Korrektur.
Manuelle Zusatzverzögerungen von 0 bis 1000 ms pro Kamera sind ebenfalls möglich.

## Dateien und Betrieb

`config/rig.local.toml` liefert die anfängliche Kameraliste für die WebUI.
Sobald gespeichert, ist deren eigene Liste in `data/settings.json` maßgeblich,
einschließlich Reihenfolge und Aktivierungsstatus. Die Datei speichert auch
technische Einstellungen, Wunschwerte und Kalibrierprofil. Entfernte Kameras
werden beim Container-Neustart nicht aus der TOML-Datei zurückimportiert.
Die separate CLI verwendet weiterhin ihr angegebenes TOML-Profil.
`captures/<Sitzung>/` enthält Originale und Manifest;
`.preview.jpg` sind erzeugte Vorschauen. Demo verwendet separate Ordner
`demo-data` und `demo-captures`.

Ein Service mit einem Uvicorn-Prozess besitzt das gesamte Rig. Ein gemeinsamer
Dateilock verhindert parallele CLI-Aufnahmen. Zum Wechsel zur Konsole zuerst
die WebUI stoppen. Der Container läuft mit UID 1000, ohne zusätzliche Linux-
Capabilities, ohne Docker-Socket und mit schreibgeschütztem Root-Dateisystem.
USB-Hotplug wird über `/dev/bus/usb` und die Gerätegruppe erlaubt; kein
`privileged: true`. Die API prüft bei Steuerbefehlen Origin und eigenen Header.
Es gibt keine Anmeldung; die konfigurierte Adresse ist für lokale Bedienung gedacht.

Auf Linux im Projektordner:

```bash
sudo bash scripts/compose-start.sh up
sudo bash scripts/compose-start.sh down
sudo bash scripts/compose-start.sh demo
sudo bash scripts/compose-start.sh demo-down
```

Linux bindet standardmäßig an 127.0.0.1:8000 (Demo 8001); der WSL-Starter nutzt
die private WSL-IP für die Windows-Verbindung. `MULTISHUTTER_BIND`,
`MULTISHUTTER_PORT`, `USB_GID` und `MULTISHUTTER_RIG_FILE` lassen sich über die
Compose-Umgebung anpassen. Für andere Linux-Rechner die USB-Gerätegruppe und
UID/Besitzrechte prüfen. Der Starter erteilt keine dauerhafte Docker-Gruppenmitgliedschaft.

Protokoll: `sudo docker compose logs --tail 100 app`.
Tests: `python -m unittest discover -s tests` mit den Web-Abhängigkeiten;
`python scripts/smoke-web.py --url http://<WSL-IP>:8001` prüft ausschließlich eine
laufende Demo. Echte Hardwareabnahme: [VALIDATION.md](VALIDATION.md).
