#!/usr/bin/env python3
"""Preparación y corrida CPU en Colab; el lanzador antepone CARGA.

Mantiene intacto el repositorio del script común. Cada minuto publica un ZIP
de resultados que el lanzador descarga a la laptop mientras continúa la corrida.
"""
import base64
import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback
import zipfile


ROOT = Path("/content/estudioIA")
RESULTS = ROOT / "pruebas" / "resultados"
ARCHIVE = Path("/content/estudioIA-resultados.zip")
STATE = ROOT / "estado.json"
SNAPSHOT_LOCK = threading.Lock()
# Tras terminar, el kernel sigue activo este tiempo para que la VM no se libere
# por inactividad antes de que el lanzador descargue el respaldo final.
ESPERA_RECOGIDA = 12 * 3600


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_state(status, **details):
    data = json.loads(STATE.read_text()) if STATE.exists() else {}
    data.update(status=status, actualizado=now(), **details)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(STATE)


def snapshot():
    with SNAPSHOT_LOCK:
        tmp = ARCHIVE.with_suffix(".zip.part")
        paths = [ROOT / "hardware.json", ROOT / "procedencia.json"]
        paths += list(RESULTS.rglob("*")) if RESULTS.exists() else []
        paths += list((ROOT / "registros").glob("*.log"))
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            # El latido indica al lanzador que la corrida sigue viva.
            state = json.loads(STATE.read_text()) if STATE.exists() else {}
            archive.writestr("estado.json", json.dumps({**state, "latido": now()},
                                                       indent=2, ensure_ascii=False))
            for path in paths:
                if path.is_file() and not path.name.endswith(".part"):
                    archive.write(path, path.relative_to(ROOT))
        tmp.replace(ARCHIVE)
        print("ESTUDIOIA_CHECKPOINT " + str(ARCHIVE), flush=True)


def run(command, cwd=None, env=None):
    # ROOT se asigna al iniciar cada corrida; resolverlo al llamar, no al
    # definir la función (los valores predeterminados de Python se fijan una vez).
    if cwd is None:
        cwd = ROOT
    print("EJECUTANDO " + " ".join(map(str, command)), flush=True)
    process = subprocess.Popen(list(map(str, command)), cwd=cwd, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1)
    try:
        with (ROOT / "registros" / "ejecucion.log").open("a") as log:
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
                log.flush()
        if process.wait():
            raise subprocess.CalledProcessError(process.returncode, command)
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def clone(url, revision, destination):
    if not (destination / ".git").is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        run(["git", "init", destination])
        run(["git", "-C", destination, "remote", "add", "origin", url])
    head = subprocess.run(
        ["git", "-C", str(destination), "rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
        capture_output=True, text=True,
    )
    if head.returncode not in (0, 1):
        raise RuntimeError(f"No se pudo comprobar el repositorio {destination}: {head.stderr.strip()}")
    current = head.stdout.strip() if head.returncode == 0 else ""
    if current != revision:
        if current:
            raise RuntimeError(f"Revisión inesperada en {destination}: {current}")
        run(["git", "-C", destination, "fetch", "--depth", "1", "origin", revision])
        run(["git", "-C", destination, "checkout", "--detach", "FETCH_HEAD"])


def restore(path, payload):
    # Se llama antes del primer respaldo: así un ZIP descargado mientras se
    # prepara una VM nueva conserva las respuestas y el hardware anteriores.
    if not path.exists():
        return
    with zipfile.ZipFile(path) as archive:
        previous = json.loads(archive.read("procedencia.json"))
        for key in ("ejecucion_id", "laboratorio_commit", "archivos_comunes", "modelos", "motores"):
            if previous.get(key) != payload.get(key):
                raise RuntimeError("El respaldo usa otro código o modelos. Usa --salida para otra corrida.")
        for entry in archive.infolist():
            name = Path(entry.filename)
            allowed = name.parts[:2] == ("pruebas", "resultados") or name == Path("hardware.json")
            if not allowed or name.is_absolute() or ".." in name.parts:
                continue
            destination = ROOT / name
            if not entry.is_dir() and not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(entry))


def check_hardware(hardware):
    path = ROOT / "hardware.json"
    if path.exists():
        previous = json.loads(path.read_text())
        for key in ("hilos", "ram_bytes", "modelo_cpu"):
            if previous.get(key) != hardware.get(key):
                raise RuntimeError("El hardware de Colab cambió; usa --salida para separar las mediciones.")


def setup(payload):
    write_state("preparando", inicio=now(), fase="dependencias")
    # Pico estimado ~45 GB: descargas, motores y el F32 de Falcon más su copia temporal.
    if shutil.disk_usage(ROOT).free < 50 * 2**30:
        raise RuntimeError("Se requieren al menos 50 GiB libres para descargas y conversiones.")
    if os.geteuid() != 0:
        raise RuntimeError("Este instalador requiere el entorno administrado estándar de Colab (root).")
    run(["apt-get", "update", "-qq"])
    run(["apt-get", "install", "-y", "-qq", "git", "cmake", "ninja-build",
         "clang", "libomp-dev", "build-essential", "python3-venv", "ca-certificates"],
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
    lab = ROOT / "pruebas"
    clone(payload["laboratorio_url"], payload["laboratorio_commit"], lab)
    for name, expected in payload["archivos_comunes"].items():
        if sha256(lab / name) != expected:
            raise RuntimeError(f"El archivo común no coincide con la laptop: {name}")

    for name, spec in payload["motores"].items():
        destination = ROOT / "runtimes" / name
        clone(spec["url"], spec["commit"], destination)
        if name == "BitNet":
            run(["git", "-C", destination, "submodule", "update", "--init", "--depth", "1"])
            sub = destination / "3rdparty" / "llama.cpp"
            patch = lab / "parches" / "bitnet-llamacpp.patch"
            applied = subprocess.run(
                ["git", "-C", str(sub), "apply", "--reverse", "--check", str(patch)],
                capture_output=True,
            ).returncode == 0
            if not applied:
                run(["git", "-C", sub, "apply", patch])
        run(["cmake", "-S", destination, "-B", destination / "build", "-G", "Ninja",
             "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
             "-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang++",
             "-DGGML_NATIVE=ON", "-DGGML_CUDA=OFF", "-DGGML_VULKAN=OFF",
             "-DBITNET_X86_TL2=OFF", "-DLLAMA_CURL=OFF", "-DLLAMA_BUILD_COMMON=ON",
             "-DLLAMA_BUILD_SERVER=ON", "-DLLAMA_BUILD_TOOLS=ON", "-DLLAMA_BUILD_TESTS=OFF"])
        jobs = min(4, len(os.sched_getaffinity(0)))
        run(["cmake", "--build", destination / "build", "--parallel", jobs,
             "--target", "llama-server", "llama-bench", "llama-quantize"])

    # Los pesos se descargan directamente desde sus revisiones originales.
    (ROOT / "bin").mkdir(exist_ok=True)
    (ROOT / "registros" / "descargas.json").write_text(json.dumps(payload["descargas"]))
    downloader = ROOT / "bin" / "descargar_modelos.py"
    downloader.write_text(payload["descargador"])
    write_state("preparando", fase="descargas")
    run([sys.executable, "-u", downloader])
    write_state("preparando", fase="conversiones")

    def converter_env(name, requirements):
        directory = ROOT / "runtimes" / name
        if not (directory / "bin" / "python").exists():
            # Colab ya ofrece Python 3.13, pero Falcon necesita numpy<2,
            # cuyas ruedas requieren Python <=3.12. Igualar el Python local.
            uv = shutil.which("uv")
            if uv is None:
                run([sys.executable, "-m", "pip", "install", "uv"])
                uv_command = [sys.executable, "-m", "uv"]
            else:
                uv_command = [uv]
            run([*uv_command, "venv", "--python", "3.12", "--seed", directory],
                env={**os.environ,
                     "UV_PYTHON_INSTALL_DIR": str(ROOT / "runtimes" / "python-uv"),
                     "UV_CACHE_DIR": str(ROOT / "runtimes" / "uv-cache")})
        python = directory / "bin" / "python"
        run([python, "-m", "pip", "install", "torch==2.11.0",
             "--index-url", "https://download.pytorch.org/whl/cpu"])
        run([python, "-m", "pip", "install", *requirements])
        return python

    falcon = ROOT / "modelos" / "falcon3-3b-ternario"
    falcon_final = falcon / "ggml-model-i2_s.gguf"
    if not falcon_final.exists():
        bitnet = ROOT / "runtimes" / "BitNet"
        python = converter_env("convertir-falcon", ["numpy<2", "transformers==4.57.6",
                               "safetensors", "sentencepiece", "protobuf<5", "pyyaml",
                               "requests", "tqdm", "packaging"])
        run([python, "-m", "pip", "install", "-e", bitnet / "3rdparty" / "llama.cpp" / "gguf-py"])
        intermediate = falcon / "ggml-model-f32.gguf"
        # Sin --use-temp-file el conversor retiene en RAM los ~13 GB del F32 hasta
        # escribirlo; la VM CPU estándar tiene ~12.7 GB y el kernel lo mataría.
        # El GGUF resultante es idéntico byte a byte. El temporal va al disco de la corrida.
        scratch = ROOT / "tmp"
        scratch.mkdir(exist_ok=True)
        run([python, "-u", bitnet / "utils" / "convert-hf-to-gguf-bitnet.py", falcon,
             "--outtype", "f32", "--use-temp-file",
             "--outfile", intermediate.with_suffix(".gguf.part")],
            cwd=bitnet, env={**os.environ, "TMPDIR": str(scratch)})
        intermediate.with_suffix(".gguf.part").replace(intermediate)
        run([bitnet / "build" / "bin" / "llama-quantize", intermediate,
             falcon_final.with_suffix(".gguf.part"), "I2_S", "1"])
        falcon_final.with_suffix(".gguf.part").replace(falcon_final)

    qwen = ROOT / "modelos" / "qwen3.5-2b-q4"
    qwen_final = qwen / "Qwen3.5-2B-Q4_K_M.gguf"
    if not qwen_final.exists():
        llama = ROOT / "runtimes" / "llama.cpp"
        python = converter_env("convertir-qwen", ["-r", llama / "requirements" /
                               "requirements-convert_hf_to_gguf.txt"])
        run([python, "-m", "pip", "install", "-e", llama / "gguf-py"])
        intermediate = qwen / "Qwen3.5-2B-F16.gguf"
        run([python, "-u", llama / "convert_hf_to_gguf.py", qwen, "--outtype", "f16",
             "--outfile", intermediate.with_suffix(".gguf.part")])
        intermediate.with_suffix(".gguf.part").replace(intermediate)
        run([llama / "build" / "bin" / "llama-quantize", intermediate,
             qwen_final.with_suffix(".gguf.part"), "Q4_K_M"])
        qwen_final.with_suffix(".gguf.part").replace(qwen_final)

    run([sys.executable, "-m", "pip", "install", "psutil>=5.9"])
    import psutil
    physical = psutil.cpu_count(logical=False) or 1
    available = len(os.sched_getaffinity(0))
    threads = min(physical, available)
    quota = Path("/sys/fs/cgroup/cpu.max")
    if quota.exists():
        value, period = quota.read_text().split()
        if value != "max":
            threads = min(threads, max(1, math.floor(int(value) / int(period))))
    model_paths = {name: str(ROOT / "modelos" / name / spec["archivo"])
                   for name, spec in payload["modelos"].items()}
    for name, spec in payload["modelos"].items():
        path = Path(model_paths[name])
        if path.stat().st_size != spec["bytes"] or sha256(path) != spec["sha256"]:
            raise RuntimeError(f"GGUF de {name} distinto al de la laptop; no se mezclan mediciones.")
    cfg = {"apellido": payload["apellido"] + "_colab", "hilos": threads,
           "modelos": model_paths,
           "bitnet_dir": str(ROOT / "runtimes" / "BitNet"),
           "llamacpp_dir": str(ROOT / "runtimes" / "llama.cpp"),
           "prism_dir": str(ROOT / "runtimes" / "llama.cpp-prism")}
    (lab / "config.json").write_text(json.dumps(cfg, indent=2))
    hardware = {
        "fecha": now(), "entorno": "Google Colab CPU", "hilos": threads,
        "nucleos_fisicos_reportados": physical, "afinidad": sorted(os.sched_getaffinity(0)),
        "ram_bytes": psutil.virtual_memory().total,
        "lscpu": subprocess.check_output(["lscpu"], text=True),
        "python": sys.version,
        "modelo_cpu": next((line.split(":", 1)[1].strip()
                            for line in Path("/proc/cpuinfo").read_text().splitlines()
                            if line.startswith("model name")), "desconocido"),
    }
    check_hardware(hardware)
    (ROOT / "hardware.json").write_text(json.dumps(hardware, indent=2))
    return lab, cfg


def experiment(lab, cfg, payload):
    delivery = RESULTS / ("entrega_" + cfg["apellido"])
    pilot = RESULTS / "piloto_colab"
    command = [sys.executable, "-u", str(lab / "laboratorio.py"), "--config", str(lab / "config.json")]

    def lab_run(output, *args):
        run([*command, "--salida", output, *args])

    run([*command, "datos"])
    lab_run(delivery, "preparar")
    ficha = delivery / "ficha_laptop.txt"
    text = ficha.read_text().replace("Alumno:\n", "Alumno: " + payload["apellido"] + "\n")
    text = text.replace("Marca y modelo de la laptop:\n", "Marca y modelo de la laptop: Google Colab VM; ver hardware.json\n")
    ficha.write_text(text)
    write_state("piloto", fase="5 problemas por modelo e idioma")
    lab_run(pilot, "correr", "--piloto", "5")
    snapshot()
    write_state("corriendo", fase="3000 respuestas y benchmarks")
    for model in payload["modelos"]:
        write_state("corriendo", modelo=model)
        lab_run(delivery, "correr", "--modelos", model)
        lab_run(delivery, "velocidad", "--modelos", model)
        snapshot()
    lab_run(delivery, "versiones")
    data = json.loads(STATE.read_text())
    text = ficha.read_text()
    prefix = "Inicio y fin de la corrida completa (fecha y hora):"
    text = "\n".join(prefix + f" {data['inicio']} a {now()}" if line.startswith(prefix) else line
                     for line in text.splitlines()) + "\n"
    ficha.write_text(text)
    with (delivery / "bitacora.md").open("a") as output:
        output.write(f"\n## {now()}\n\nCorrida adicional en Google Colab CPU, "
                     f"{cfg['hilos']} hilos. Hardware y procedencia en el ZIP. "
                     "Resultados separados de la laptop.\n")
    lab_run(delivery, "verificar")


def main(payload):
    global ROOT, RESULTS, ARCHIVE, STATE
    ROOT = Path("/content") / ("estudioIA-" + payload["ejecucion_id"])
    RESULTS = ROOT / "pruebas" / "resultados"
    ARCHIVE = Path(str(ROOT) + "-resultados.zip")
    STATE = ROOT / "estado.json"
    ROOT.mkdir(exist_ok=True)
    (ROOT / "registros").mkdir(exist_ok=True)
    with (ROOT / "experimento.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run_experiment(payload)
    # Si la laptop está apagada, la VM espera con los resultados; el lanzador
    # la detiene en cuanto descarga el respaldo final.
    time.sleep(ESPERA_RECOGIDA)


def run_experiment(payload):
    (ROOT / "procedencia.json").write_text(json.dumps(
        {key: value for key, value in payload.items() if key != "descargador"}, indent=2))
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(60):
            try:
                snapshot()
            except Exception as exc:
                print(f"AVISO respaldo: {exc}", flush=True)

    thread = threading.Thread(target=heartbeat, daemon=True)
    code = 0
    try:
        restore(Path(f"/content/estudioIA-{payload['ejecucion_id']}-reanudar.zip"), payload)
        thread.start()
        lab, cfg = setup(payload)
        experiment(lab, cfg, payload)
        write_state("completo", fin=now())
    except BaseException as exc:
        code = 1
        write_state("fallido", error=f"{type(exc).__name__}: {exc}", fin=now())
        traceback.print_exc()
    finally:
        stop.set()
        if thread.is_alive():
            thread.join(timeout=65)
        snapshot()
    print("ESTUDIOIA_FINAL " + str(code), flush=True)


if __name__ == "__main__" and "CARGA" in globals():
    main(CARGA)
