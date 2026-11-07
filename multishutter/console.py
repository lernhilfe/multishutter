"""Foreground console; no service, installer, network listener or autostart."""

import argparse
from pathlib import Path
import shlex


HELP = """Commands:
  discover                     List connected cameras (no capture)
  configure                    Map cameras explicitly from left to right
  doctor                       Check runtime/dependencies (no capture)
  check                        Open cameras and check the configured rig
  capture [--takes N] [--countdown S] [--interval S]
                               Trigger and download a new session
  help                         Show this help
  quit                         Exit the process
"""


def run_console(config: Path, output: Path, backend="gphoto2", read=None, emit=print, execute=None) -> int:
    if execute is None:
        from .cli import main
        execute = main
    read = read or input
    config, output = config.resolve(), output.resolve()
    emit(f"MultiShutter console | backend: {backend}")
    emit(f"Rig: {config}\nPhotos: {output}")
    emit(HELP)
    if not config.exists():
        emit("No rig configuration yet. Run discover, then configure before capturing.")
    while True:
        try:
            line = read("multishutter> ")
        except EOFError:
            return 0
        except KeyboardInterrupt:
            emit("\nStopped.")
            return 130
        try:
            words = shlex.split(line)
            if not words:
                continue
            command, *arguments = words
            if command in {"quit", "exit"}:
                return 0
            if command == "help":
                emit(HELP)
                continue
            if command in {"discover", "doctor"} and not arguments:
                code = execute([command, "--backend", backend])
            elif command == "configure" and not arguments:
                code = execute([command, "--backend", backend, "--output", str(config)])
            elif command == "check" and not arguments:
                code = execute([command, "--config", str(config)])
            elif command == "capture":
                parser = argparse.ArgumentParser(prog="capture")
                parser.add_argument("--takes", type=int, default=1)
                parser.add_argument("--countdown", type=float, default=3)
                parser.add_argument("--interval", type=float, default=0)
                options = parser.parse_args(arguments)
                code = execute(["capture", "--config", str(config), "--output", str(output),
                                "--takes", str(options.takes), "--countdown", str(options.countdown),
                                "--interval", str(options.interval)])
            else:
                emit("Unknown command or arguments. Type help.")
                continue
            if code:
                emit(f"Command ended with status {code}. No automatic retry.")
        except SystemExit:
            # argparse errors/help affect this command, not the foreground console.
            continue
        except (ValueError, OSError) as error:
            emit(str(error))
