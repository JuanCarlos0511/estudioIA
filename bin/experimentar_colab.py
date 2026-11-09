#!/usr/bin/env python3
"""Ejecuta el laboratorio completo en una VM CPU mediante el CLI oficial."""
import argparse
import base64
import copy
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "colab"
SESSION_FILE = ROOT / "runtimes" / "colab-estado" / "sessions.json"
REFERENCE = ROOT / "colab" / "referencia.json"
FINAL_STATES = ("completo", "fallido")
# La VM renueva el latido cada minuto; sin cambios en este lapso se da por detenida.
LATIDO_MAXIMO = 5 * 60


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(directory, *args):
    return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()


def pinned(cfg, reference):
    """GGUF y commits que la VM debe reproducir byte a byte.

    Con la instalación local se fijan sus GGUF y commits, para comparar con esa
    laptop; lo que falte se toma de colab/referencia.json (laptop de referencia).
    """
    models = {}
    for name, expected in reference["modelos"].items():
        path = Path(cfg.get("modelos", {}).get(name, ""))
        if path.is_file():
            print(f"Verificando pesos locales: {name}", flush=True)
            models[name] = {"archivo": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
        else:
            print(f"Sin pesos locales de {name}: se usa la referencia.", flush=True)
            models[name] = expected
    engines = {}
    for name, spec in reference["motores"].items():
        local = ROOT / "runtimes" / name
        commit = git(local, "rev-parse", "HEAD") if (local / ".git").exists() else spec["commit"]
        engines[name] = {"url": spec["url"], "commit": commit}
    return models, engines


def make_payload():
    lab = ROOT / "pruebas"
    if not (lab / ".git").exists():
        raise RuntimeError("Falta pruebas/: clona ahí testing-modelos-terniarios (ver README.md).")
    if git(lab, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("El repositorio común tiene cambios locales; hay que conservar una revisión reproducible.")
    if not (lab / "config.json").is_file():
        raise RuntimeError("Falta pruebas/config.json: copia pruebas/config.ejemplo.json y pon tu apellido.")
    cfg = json.loads((lab / "config.json").read_text())
    if cfg.get("apellido", "apellido") == "apellido":
        raise RuntimeError("Pon tu apellido en pruebas/config.json; nombra la carpeta de entrega.")
    manifest = copy.deepcopy(json.loads((ROOT / "registros" / "descargas.json").read_text()))
    models, engines = pinned(cfg, json.loads(REFERENCE.read_text()))
    for entry in manifest:
        if entry["name"] == "bonsai2-2b-ternario":
            model = models[entry["name"]]
            entry["files"] = [{
                "file": model["archivo"], "size": model["bytes"], "sha256": model["sha256"],
                "url": f"https://huggingface.co/{entry['repo']}/resolve/{entry['revision']}/{model['archivo']}",
            }]
    common = ["laboratorio.py", "requirements.txt", "plantillas/bitnet-2b.jinja",
              "plantillas/bitacora.md", "parches/bitnet-llamacpp.patch"]
    return {
        "laboratorio_url": git(lab, "remote", "get-url", "origin"),
        "laboratorio_commit": git(lab, "rev-parse", "HEAD"),
        "archivos_comunes": {name: sha256(lab / name) for name in common},
        "apellido": cfg["apellido"], "modelos": models, "motores": engines,
        "descargas": manifest,
        "descargador": (ROOT / "bin" / "descargar_modelos.py").read_text(),
    }


def remote_code(payload):
    encoded = base64.b64encode(json.dumps(payload).encode()).decode("ascii")
    # Solo código, manifiestos públicos y hashes: nunca se envían credenciales.
    prefix = "import json, base64\nCARGA = json.loads(base64.b64decode(" + repr(encoded) + "))\n"
    return prefix + (ROOT / "colab" / "ejecutar_remoto.py").read_text()


def read_checkpoint(path):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip():
            raise RuntimeError("El ZIP de resultados está dañado.")
        for name in archive.namelist():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError("El ZIP contiene una ruta inválida.")
        return json.loads(archive.read("estado.json"))


def extract_checkpoint(path, destination):
    read_checkpoint(path)
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            relative = Path(entry.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError("Ruta inválida en el respaldo.")
            if not entry.is_dir():
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))


def cli(*args, capture=False):
    return subprocess.run([str(CLI), *map(str, args)], text=True, check=True,
                          stdout=subprocess.PIPE if capture else None)


def download_snapshot(session, output, remote_archive):
    partial = output / "ultimo.zip.part"
    cli("download", "-s", session, remote_archive, partial)
    status = read_checkpoint(partial)
    partial.replace(output / "ultimo.zip")
    extract_checkpoint(output / "ultimo.zip", output / "resultados")
    (output / "estado.json").write_text(json.dumps(status, indent=2, ensure_ascii=False))
    return status


def check_backup(path, payload):
    # Antes de conectar: un respaldo de otro código nunca debe sustituirse por
    # el ZIP vacío que produciría la VM al rechazarlo.
    if not path.exists():
        return
    with zipfile.ZipFile(path) as archive:
        previous = json.loads(archive.read("procedencia.json"))
    for key in ("ejecucion_id", "laboratorio_commit", "archivos_comunes", "modelos", "motores"):
        if previous.get(key) != payload.get(key):
            raise RuntimeError("El respaldo usa otro código o modelos. Usa --salida para otra corrida.")


def alive(status):
    try:
        beat = datetime.datetime.fromisoformat(status["latido"])
    except (KeyError, TypeError, ValueError):
        return False
    age = datetime.datetime.now(datetime.timezone.utc) - beat
    return age.total_seconds() < LATIDO_MAXIMO


def remote_status(session, output, remote_archive):
    """Respaldo de la corrida que ya está en la VM, o None si no hay."""
    try:
        return download_snapshot(session, output, remote_archive)
    except (subprocess.CalledProcessError, RuntimeError, OSError, KeyError, ValueError,
            zipfile.BadZipFile):
        return None


def follow(session, output, remote_archive):
    """Descarga cada minuto los respaldos de una corrida que siguió sin la laptop."""
    print("La corrida sigue en la VM. Descargando respaldos cada minuto.", flush=True)
    last_ok = time.monotonic()
    while True:
        time.sleep(60)
        try:
            status = download_snapshot(session, output, remote_archive)
        except (subprocess.CalledProcessError, RuntimeError, OSError, KeyError, ValueError,
                zipfile.BadZipFile) as exc:
            print(f"AVISO: no se pudo recuperar este respaldo: {exc}", flush=True)
            if time.monotonic() - last_ok > LATIDO_MAXIMO * 2:
                raise RuntimeError("No se pudo descargar ningún respaldo en 10 minutos.") from None
            continue
        last_ok = time.monotonic()
        print(f"RESPALDO LOCAL: {status.get('status')} → {output}", flush=True)
        if status.get("status") in FINAL_STATES:
            return
        if not alive(status):
            raise RuntimeError("La VM dejó de actualizar sus respaldos.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sesion", default="laboratorio-cpu")
    parser.add_argument("--salida", type=Path, default=ROOT / "registros" / "colab" / "actual")
    parser.add_argument("--preparar", action="store_true", help="Generar y validar la carga sin conectar ni crear una VM")
    parser.add_argument("--estandar", action="store_true",
                        help="Crear la VM CPU estándar (2 vCPU) en lugar de la High-RAM")
    args = parser.parse_args()
    output = args.salida.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "lanzador.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Ya hay un lanzador activo en esa carpeta.") from None
        return execute(args, output)


def execute(args, output):
    if not args.preparar and (output / "ultimo.zip").exists():
        previous = read_checkpoint(output / "ultimo.zip")
        if previous.get("status") == "completo":
            print(f"La corrida ya está completa en {output / 'resultados'}. Usa --salida para otra corrida.")
            return 0

    payload = make_payload()
    identity = output / "ejecucion_id.txt"
    if not identity.exists():
        identity.write_text(uuid.uuid4().hex[:16])
    payload["ejecucion_id"] = identity.read_text().strip()
    remote_archive = f"/content/estudioIA-{payload['ejecucion_id']}-resultados.zip"
    script = output / "ejecutar_remoto.py"
    code = remote_code(payload)
    compile(code, str(script), "exec")
    script.write_text(code)
    if args.preparar:
        print(f"Carga preparada: {script}. No se creó una máquina ni se ejecutaron modelos.")
        return 0

    check_backup(output / "ultimo.zip", payload)

    # Sin conexión se detiene aquí: no afirma que haya empezado una corrida.
    cli("sessions")
    known = json.loads(SESSION_FILE.read_text()) if SESSION_FILE.exists() else {}
    created = False
    started = False
    final_seen = False
    snapshot_ok = False
    proc = None
    try:
        if args.sesion not in known:
            cli("new", "-s", args.sesion, *([] if args.estandar else ["--high-mem"]))
            created = True
        cli("status", "-s", args.sesion)
        cli("usage")
        # Si se cerró la terminal o se apagó la laptop, la corrida pudo seguir
        # en la VM: se retoma su respaldo en lugar de ejecutarla otra vez.
        status = None if created else remote_status(args.sesion, output, remote_archive)
        if status and "latido" not in status and status.get("status") not in FINAL_STATES:
            # Una carga anterior no publica latido ni espera a ser recogida; una
            # ejecución nueva quedaría en cola detrás de ella en el mismo kernel.
            raise RuntimeError(f"La VM corre una versión anterior de la carga. Detenla con "
                               f"./bin/colab stop -s {args.sesion} y vuelve a lanzar.")
        if status and status.get("status") in FINAL_STATES:
            print(f"La corrida ya terminó en la VM: {status.get('status')}.", flush=True)
            final_seen = True
        elif status and alive(status):
            started = True
            follow(args.sesion, output, remote_archive)
            final_seen = True
        else:
            if (output / "ultimo.zip").exists():
                cli("upload", "-s", args.sesion, output / "ultimo.zip",
                    f"/content/estudioIA-{payload['ejecucion_id']}-reanudar.zip")
            print("Iniciando instalación y corrida remota. El avance y los respaldos aparecerán aquí.", flush=True)
            proc = subprocess.Popen(
                [str(CLI), "exec", "-s", args.sesion, "--timeout", "172800", "-f", str(script)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            started = True
            with (output / "consola.log").open("a") as log:
                for line in proc.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                    log.flush()
                    if line.startswith("ESTUDIOIA_CHECKPOINT "):
                        try:
                            status = download_snapshot(args.sesion, output, remote_archive)
                            print(f"RESPALDO LOCAL: {status.get('status')} → {output}", flush=True)
                        except Exception as exc:
                            print(f"AVISO: no se pudo recuperar este respaldo: {exc}", flush=True)
                    elif line.startswith("ESTUDIOIA_FINAL "):
                        # La VM se queda esperando a que se recojan los resultados.
                        final_seen = True
                        break
            if final_seen:
                proc.terminate()
            proc.wait()
        status = download_snapshot(args.sesion, output, remote_archive)
        snapshot_ok = True
        try:
            cli("log", "-s", args.sesion, "-o", output / "sesion.ipynb")
        except subprocess.CalledProcessError:
            print("AVISO: no se pudo exportar el historial; los resultados ya están descargados.", flush=True)
        if status.get("status") != "completo":
            raise RuntimeError(f"Corrida pendiente: {status.get('error', status.get('status'))}. Avances en {output}.")
        print(f"CORRIDA COMPLETA Y VERIFICADA: {output / 'resultados'}", flush=True)
        return 0
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=15)
        if proc and proc.stdout:
            proc.stdout.close()
        # Solo libera la VM después de recuperar los datos de una ejecución
        # finalizada. Si se corta la conexión, deja la tarea remota en ejecución.
        if (snapshot_ok and final_seen) or (created and not started):
            cli("stop", "-s", args.sesion)
        elif started:
            print(f"La VM puede seguir trabajando. Estado: ./bin/colab status -s {args.sesion}", flush=True)
            print("Vuelve a ejecutar el lanzador para recuperar o reanudar; no se descartan los respaldos.", flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nLanzador interrumpido; los respaldos locales se conservan.", file=sys.stderr)
        raise SystemExit(130)
    except (RuntimeError, subprocess.CalledProcessError, OSError) as exc:
        print(f"No se completó la ejecución en Colab: {exc}", file=sys.stderr)
        raise SystemExit(1)
