#!/usr/bin/env python3
"""Ejecuta el CLI oficial con registros y estado dentro del proyecto.

No cambia HOME ni modifica el paquete instalado. La versión que guarda OAuth
en un archivo reutiliza la credencial creada por el usuario y renueva una copia
privada dentro de runtimes/, excluido de Git.
"""
import logging
import os
from pathlib import Path
import shutil
import sys

from colab_cli import auth, cli
from colab_cli.common import state
from colab_cli.history import HistoryLogger
from colab_cli.state import SettingsStore


def main():
    os.umask(0o077)
    directory = Path(__file__).resolve().parents[1] / "runtimes" / "colab-estado"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)

    # Compatibilidad con el CLI publicado que usa token.json. Nunca imprime
    # la credencial ni sobrescribe el archivo original del usuario.
    if hasattr(auth, "TOKEN_CONFIG_PATH"):
        original = Path(auth.TOKEN_CONFIG_PATH)
        private = directory / "token.json"
        if original.is_file() and (
            not private.exists() or original.stat().st_mtime > private.stat().st_mtime
        ):
            shutil.copy2(original, private)
        if private.exists():
            private.chmod(0o600)
        auth.TOKEN_CONFIG_PATH = str(private)

    def setup_logging(log_to_stderr):
        handlers = [logging.FileHandler(directory / "colab.log")]
        if log_to_stderr:
            handlers.append(logging.StreamHandler(sys.stderr))
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
            handlers=handlers,
            force=True,
        )

    cli.setup_logging = setup_logging
    state._settings_store = SettingsStore(str(directory / "settings.json"))
    state._history = HistoryLogger(str(directory / "history"))
    cli.main()


if __name__ == "__main__":
    main()
