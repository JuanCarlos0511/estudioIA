#!/usr/bin/env python3
"""Descargas reanudables con progreso y verificación SHA-256."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import signal
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CANCEL = threading.Event()
PRINT_LOCK = threading.RLock()


def log(message):
    with PRINT_LOCK:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def size(value):
    return f"{value / 1024 ** 2:.1f} MiB"


def duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class Progress:
    def __init__(self, interval=5):
        self.interval = interval
        self.lock = threading.Lock()
        self.items = {}
        self.stop = threading.Event()
        self.thread = None

    def set(self, key, stage, done, total):
        now = time.monotonic()
        with self.lock:
            self.items[key] = {
                "stage": stage, "done": done, "total": total,
                "base": done, "since": now, "last": now,
            }

    def update(self, key, done):
        with self.lock:
            if key in self.items:
                self.items[key]["done"] = done
                self.items[key]["last"] = time.monotonic()

    def remove(self, key):
        with self.lock:
            self.items.pop(key, None)

    def report(self):
        now = time.monotonic()
        with self.lock:
            items = [(key, dict(value)) for key, value in self.items.items()]
        for key, state in items:
            done, total = state["done"], state["total"]
            rate = max(0, done - state["base"]) / max(now - state["since"], .001)
            amount = f"{size(done)} / {size(total)} ({done / total * 100:.1f}%)" if total else size(done)
            eta = duration((total - done) / rate) if total and rate else "--:--:--"
            idle = now - state["last"]
            waiting = f" | sin datos nuevos hace {int(idle)} s" if idle >= 10 else ""
            log(f"{key} | {state['stage']} | {amount} | {size(rate)}/s | ETA {eta}{waiting}")

    def start(self):
        def monitor():
            while not self.stop.wait(self.interval):
                self.report()
        self.thread = threading.Thread(target=monitor, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join()


PROGRESS = Progress()


class IntegrityError(ValueError):
    pass


def verify(path, item, key):
    expected = item.get("size")
    if expected is not None and path.stat().st_size != expected:
        raise IntegrityError(f"Tamaño incorrecto: {path}")
    if item.get("sha256"):
        PROGRESS.set(key, "verificando SHA-256", 0, path.stat().st_size)
        log(f"VERIFICANDO {key}: SHA-256")
        digest = hashlib.sha256()
        done = 0
        with path.open("rb") as source:
            while chunk := source.read(8 * 1024 * 1024):
                if CANCEL.is_set():
                    raise concurrent.futures.CancelledError()
                digest.update(chunk)
                done += len(chunk)
                PROGRESS.update(key, done)
        if digest.hexdigest() != item["sha256"]:
            raise IntegrityError(f"SHA-256 incorrecto: {path}")


def preserve_invalid(path, key):
    backup = path.with_name(path.name + f".invalid-{time.time_ns()}")
    path.rename(backup)
    log(f"ARCHIVO INVÁLIDO {key}: conservado en {backup.name}; se descargará otra vez")


def download(job):
    name, item = job
    key = f"{name}/{item['file']}"
    target = ROOT / "modelos" / name / item["file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = item.get("size")
    part = target.with_name(target.name + ".part")
    try:
        if CANCEL.is_set():
            raise concurrent.futures.CancelledError()
        if target.exists():
            try:
                verify(target, item, key)
            except IntegrityError:
                if expected is not None and target.stat().st_size < expected and not part.exists():
                    target.rename(part)
                    log(f"REANUDAR {key}: archivo incompleto conservado como .part")
                else:
                    preserve_invalid(target, key)
            else:
                log(f"OMITIDO {key}: ya descargado y verificado")
                return "omitted"
        for attempt in range(1, 9):
            if CANCEL.is_set():
                raise concurrent.futures.CancelledError()
            try:
                start = part.stat().st_size if part.exists() else 0
                if expected is not None and start > expected:
                    preserve_invalid(part, key)
                    start = 0
                if part.exists() and expected is not None and start == expected:
                    verify(part, item, key)
                    part.replace(target)
                    log(f"COMPLETO {key}: .part verificado, sin volver a descargar")
                    return "downloaded"
                PROGRESS.set(key, "conectando / esperando respuesta", start, expected)
                log(f"{'REANUDANDO' if start else 'DESCARGANDO'} {key}: {size(start)} / "
                    f"{size(expected) if expected is not None else '?'}; intento {attempt}/8")
                headers = {"Range": f"bytes={start}-"} if start else {}
                request = urllib.request.Request(item["url"], headers=headers)
                with urllib.request.urlopen(request, timeout=30) as response:
                    append = bool(start and response.status == 206)
                    if response.status == 206:
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)",
                                             response.headers.get("Content-Range", ""))
                        if not match or int(match[1]) != start:
                            raise ValueError("El servidor devolvió un rango incorrecto; se conserva el parcial")
                        if expected is not None and match[3] != "*" and int(match[3]) != expected:
                            raise ValueError("El tamaño anunciado por el servidor no coincide con el manifiesto")
                    elif start:
                        log(f"AVISO {key}: el servidor no acepta reanudación; reiniciando este archivo")
                    received = start if append else 0
                    PROGRESS.set(key, "descargando", received, expected)
                    with part.open("ab" if append else "wb") as output:
                        while chunk := response.read1(1024 * 1024):
                            output.write(chunk)
                            output.flush()
                            received += len(chunk)
                            PROGRESS.update(key, received)
                            if CANCEL.is_set():
                                raise concurrent.futures.CancelledError()
                verify(part, item, key)
                part.replace(target)
                log(f"COMPLETO {key}: descargado y verificado")
                return "downloaded"
            except IntegrityError as exc:
                log(f"ERROR {key}: {exc}")
                # Un archivo corto se conserva para reanudar; un hash incorrecto
                # de un archivo completo exige empezar de nuevo.
                if part.exists() and expected is not None and part.stat().st_size >= expected:
                    preserve_invalid(part, key)
            except concurrent.futures.CancelledError:
                raise
            except Exception as exc:
                log(f"REINTENTO {key} ({attempt}/8): {exc}")
            if attempt < 8:
                wait = min(2 ** (attempt - 1), 30)
                current = part.stat().st_size if part.exists() else 0
                PROGRESS.set(key, f"esperando {wait} s para reintentar", current, expected)
                if CANCEL.wait(wait):
                    raise concurrent.futures.CancelledError()
        raise RuntimeError(f"No se pudo descargar {key}; el parcial queda guardado")
    finally:
        PROGRESS.remove(key)


def main():
    manifest = json.loads((ROOT / "registros/descargas.json").read_text())
    jobs = [(model["name"], item) for model in manifest for item in model["files"]]
    def interrupt(_signum, _frame):
        if not CANCEL.is_set():
            log("DETENIENDO: se conservarán los parciales; esperando cerrar conexiones (hasta 30 s).")
        CANCEL.set()
    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    log(f"Archivos: {len(jobs)} | conexiones simultáneas: 3 | progreso cada 5 s")
    results = {"omitted": 0, "downloaded": 0}
    errors = []
    PROGRESS.start()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(download, job) for job in jobs]
            for future in concurrent.futures.as_completed(futures):
                try:
                    results[future.result()] += 1
                except concurrent.futures.CancelledError:
                    pass
                except Exception as exc:
                    errors.append(str(exc))
                    log(f"ERROR FINAL: {exc}")
    finally:
        PROGRESS.close()
    log(f"RESUMEN: {results['omitted']} omitidos por estar verificados; "
        f"{results['downloaded']} completados; {len(errors)} errores")
    if CANCEL.is_set():
        log("PAUSADO. Ejecuta el mismo comando para reanudar.")
        return 130
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
