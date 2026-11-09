"""Pruebas locales de respaldos y control de la corrida; no alquilan una VM."""
import contextlib
import datetime
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


PROJECT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


local = load("local_colab_test", PROJECT / "bin" / "experimentar_colab.py")
remote = load("remote_colab_test", PROJECT / "colab" / "ejecutar_remoto.py")


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_commands_use_current_run_directory(self):
        # Ejecuta un subproceso real tras cambiar ROOT. Cubre el fallo de
        # arranque observado en Colab al usar una carpeta con id de corrida.
        current = self.root / "estudioIA-corrida"
        (current / "registros").mkdir(parents=True)
        with patch.object(remote, "ROOT", current), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            remote.run([sys.executable, "-c", "from pathlib import Path; print(Path.cwd())"])
        self.assertIn(str(current), output.getvalue().splitlines())
        self.assertIn(str(current), (current / "registros/ejecucion.log").read_text())

    def archive(self, entries):
        path = self.root / "checkpoint.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, json.dumps(content) if not isinstance(content, str) else content)
        return path

    def test_blocks_paths_outside_destination(self):
        path = self.archive({"estado.json": {"status": "corriendo"}, "../fuera.txt": "no"})
        with self.assertRaises(RuntimeError):
            local.extract_checkpoint(path, self.root / "extraido")
        self.assertFalse((self.root / "fuera.txt").exists())

    def test_preserves_responses_in_snapshot(self):
        result = self.root / "vm" / "pruebas" / "resultados"
        result.mkdir(parents=True)
        (self.root / "vm" / "registros").mkdir()
        state = self.root / "vm" / "estado.json"
        state.write_text('{"status":"corriendo"}')
        (result / "m_es.jsonl").write_text('{"id":1,"respuesta_modelo":"Respuesta: 7"}\n')
        (self.root / "vm" / "secreto.json").write_text('"no enviar"')
        with patch.multiple(remote, ROOT=self.root / "vm", RESULTS=result,
                            STATE=state, ARCHIVE=self.root / "respaldo.zip"):
            with contextlib.redirect_stdout(io.StringIO()):
                remote.snapshot()
        local.extract_checkpoint(self.root / "respaldo.zip", self.root / "local")
        self.assertTrue((self.root / "local" / "pruebas/resultados/m_es.jsonl").is_file())
        self.assertFalse((self.root / "local" / "secreto.json").exists())

    def test_restore_rejects_different_models(self):
        payload = {"ejecucion_id": "run", "modelos": {"a": "hash"}}
        path = self.archive({"procedencia.json": payload,
                             "pruebas/resultados/una.jsonl": '{"id":1}\n'})
        with patch.object(remote, "ROOT", self.root / "destino"):
            with self.assertRaisesRegex(RuntimeError, "otro código"):
                remote.restore(path, {**payload, "modelos": {}})
        self.assertFalse((self.root / "destino").exists())

    def test_rejects_different_hardware_after_restore(self):
        payload = {"ejecucion_id": "run"}
        hardware = {"hilos": 2, "ram_bytes": 100, "modelo_cpu": "CPU1"}
        path = self.archive({"procedencia.json": payload, "hardware.json": hardware})
        with patch.object(remote, "ROOT", self.root / "destino"):
            remote.restore(path, payload)
            remote.check_hardware(hardware)
            with self.assertRaisesRegex(RuntimeError, "hardware"):
                remote.check_hardware({**hardware, "hilos": 8})

    def test_restore_never_replaces_code(self):
        payload = {"ejecucion_id": "run"}
        path = self.archive({"procedencia.json": payload,
                             "pruebas/laboratorio.py": "codigo alterado",
                             "pruebas/resultados/m_es.jsonl": '{"id":1}\n'})
        with patch.object(remote, "ROOT", self.root / "destino"):
            remote.restore(path, payload)
        self.assertFalse((self.root / "destino/pruebas/laboratorio.py").exists())
        self.assertTrue((self.root / "destino/pruebas/resultados/m_es.jsonl").is_file())

    def test_first_snapshot_of_new_vm_keeps_previous_responses(self):
        # Al reanudar en otra VM, el primer respaldo (aún preparando) reemplaza
        # el ZIP local; debe conservar las respuestas y el hardware anteriores.
        payload = {"ejecucion_id": "run"}
        path = self.archive({"procedencia.json": payload,
                             "hardware.json": {"hilos": 4},
                             "pruebas/resultados/m_es.jsonl": '{"id":1}\n'})
        vm = self.root / "vm"
        (vm / "registros").mkdir(parents=True)
        (vm / "estado.json").write_text('{"status":"preparando"}')
        with patch.multiple(remote, ROOT=vm, RESULTS=vm / "pruebas/resultados",
                            STATE=vm / "estado.json", ARCHIVE=self.root / "respaldo.zip"):
            remote.restore(path, payload)
            with contextlib.redirect_stdout(io.StringIO()):
                remote.snapshot()
        with zipfile.ZipFile(self.root / "respaldo.zip") as archive:
            names = archive.namelist()
            state = json.loads(archive.read("estado.json"))
        self.assertIn("pruebas/resultados/m_es.jsonl", names)
        self.assertIn("hardware.json", names)
        self.assertEqual(state["status"], "preparando")
        self.assertTrue(local.alive(state))


class PinnedTests(unittest.TestCase):
    def test_uses_reference_for_missing_local_files(self):
        # Quien no instaló los modelos ni los motores reproduce la laptop de referencia.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / "a.gguf"
            model.write_bytes(b"pesos locales")
            reference = {
                "modelos": {"a": {"archivo": "a.gguf", "bytes": 1, "sha256": "ref-a"},
                            "b": {"archivo": "b.gguf", "bytes": 2, "sha256": "ref-b"}},
                "motores": {"motor": {"url": "https://example.invalid/motor.git", "commit": "abc"}},
            }
            cfg = {"modelos": {"a": str(model), "b": str(root / "falta.gguf")}}
            with patch.object(local, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
                models, engines = local.pinned(cfg, reference)
        self.assertEqual(models["a"]["bytes"], len(b"pesos locales"))
        self.assertNotEqual(models["a"]["sha256"], "ref-a")
        self.assertEqual(models["b"], reference["modelos"]["b"])
        self.assertEqual(engines, reference["motores"])

    def test_reference_matches_local_installation(self):
        # La referencia publicada debe coincidir con los GGUF de la laptop que la generó.
        reference = json.loads((PROJECT / "colab" / "referencia.json").read_text())
        config = PROJECT / "pruebas" / "config.json"
        if not config.is_file():
            self.skipTest("sin instalación local")
        paths = json.loads(config.read_text())["modelos"]
        for name, expected in reference["modelos"].items():
            path = Path(paths[name])
            if not path.is_file():
                self.skipTest("sin instalación local")
            self.assertEqual((path.name, path.stat().st_size), (expected["archivo"], expected["bytes"]))


class CloneTests(unittest.TestCase):
    """Clonaciones reales desde un repositorio local, sin red ni una VM."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "registros").mkdir()
        self.origin = self.root / "origen"
        self.git("init", self.origin)
        (self.origin / "archivo.txt").write_text("primero")
        self.git("-C", self.origin, "add", "archivo.txt")
        self.git("-C", self.origin, "-c", "user.name=Prueba", "-c", "user.email=prueba@example.invalid",
                 "commit", "-m", "primero")
        self.first = self.git("-C", self.origin, "rev-parse", "HEAD")
        (self.origin / "archivo.txt").write_text("segundo")
        self.git("-C", self.origin, "-c", "user.name=Prueba", "-c", "user.email=prueba@example.invalid",
                 "commit", "-am", "segundo")
        self.second = self.git("-C", self.origin, "rev-parse", "HEAD")
        self.destination = self.root / "destino"

    def git(self, *args):
        return subprocess.run(["git", *map(str, args)], capture_output=True,
                              text=True, check=True).stdout.strip()

    def clone(self, revision):
        with patch.object(remote, "ROOT", self.root), \
             contextlib.redirect_stdout(io.StringIO()):
            remote.clone(str(self.origin), revision, self.destination)

    def test_fresh_clone_fetches_requested_commit(self):
        self.clone(self.first)
        self.assertEqual(self.git("-C", self.destination, "rev-parse", "HEAD"), self.first)
        self.assertEqual((self.destination / "archivo.txt").read_text(), "primero")

    def test_initialized_empty_repository_can_resume_fetch(self):
        self.git("init", self.destination)
        self.git("-C", self.destination, "remote", "add", "origin", self.origin)
        self.clone(self.second)
        self.assertEqual(self.git("-C", self.destination, "rev-parse", "HEAD"), self.second)

    def test_repeated_clone_keeps_existing_files(self):
        self.clone(self.first)
        (self.destination / "local.txt").write_text("conservar")
        self.clone(self.first)
        self.assertEqual((self.destination / "local.txt").read_text(), "conservar")

    def test_unexpected_existing_commit_is_preserved(self):
        self.clone(self.first)
        with self.assertRaisesRegex(RuntimeError, "Revisión inesperada"):
            self.clone(self.second)
        self.assertEqual(self.git("-C", self.destination, "rev-parse", "HEAD"), self.first)


class LifecycleTests(unittest.TestCase):
    def test_prepare_never_connects(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(sys, "argv", ["runner", "--preparar", "--salida", temp]), \
                 patch.object(local, "make_payload", return_value={}), \
                 patch.object(local, "cli") as cli, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(local.main(), 0)
                cli.assert_not_called()
                compile((Path(temp) / "ejecutar_remoto.py").read_text(), "carga", "exec")

    def run_fake(self, remote_status, exec_return=0, known=None, states=None, extra_args=(),
                 shape=None):
        """Simula el CLI; ``states`` son los estado.json que devuelve cada descarga."""
        fresh = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        states = list(states or [])
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            sessions = directory / "sesiones.json"
            if known:
                sessions.write_text(json.dumps(known))
            if shape:
                (directory / "forma_vm.txt").write_text(shape)
            calls = []
            def fake_cli(*args, **kwargs):
                calls.append(args)
                if args[0] == "download":
                    state = states.pop(0) if states else {"status": remote_status, "latido": fresh}
                    with zipfile.ZipFile(args[-1], "w") as archive:
                        archive.writestr("estado.json", json.dumps(state))
                return subprocess.CompletedProcess(args, 0)
            class FakeProcess:
                stdout = io.StringIO("ESTUDIOIA_CHECKPOINT /content/resultados.zip\nESTUDIOIA_FINAL 0\n")
                def wait(self, **kwargs):
                    return exec_return
                def poll(self):
                    return exec_return
                def terminate(self):
                    pass
            with patch.object(sys, "argv", ["runner", "--salida", str(directory), *extra_args]), \
                 patch.object(local, "make_payload", return_value={}), \
                 patch.object(local, "SESSION_FILE", sessions), \
                 patch.object(local, "cli", side_effect=fake_cli), \
                 patch.object(local.subprocess, "Popen", return_value=FakeProcess()) as popen, \
                 patch.object(local.time, "sleep"), \
                 contextlib.redirect_stdout(io.StringIO()):
                try:
                    result = local.main()
                except RuntimeError:
                    result = "error"
            self.popen = popen
            return result, calls

    def test_remote_failure_is_not_success_even_if_cli_returns_zero(self):
        result, calls = self.run_fake("fallido")
        self.assertEqual(result, "error")
        self.assertEqual(calls[-1][0], "stop")
        self.assertTrue(any(call[0] == "download" for call in calls))

    def test_downloads_results_before_releasing_completed_vm(self):
        result, calls = self.run_fake("completo")
        self.assertEqual(result, 0)
        self.assertEqual(calls[-1][0], "stop")
        downloads = [i for i, call in enumerate(calls) if call[0] == "download"]
        self.assertTrue(downloads and max(downloads) < len(calls) - 1)

    def test_requests_high_ram_unless_standard(self):
        _, calls = self.run_fake("completo")
        self.assertIn(("new", "-s", "laboratorio-cpu", "--high-mem"), calls)
        _, calls = self.run_fake("completo", extra_args=["--estandar"])
        self.assertIn(("new", "-s", "laboratorio-cpu"), calls)

    def test_resumed_run_keeps_its_vm_shape(self):
        # La corrida empezó en estándar: una VM nueva para reanudar también lo es.
        _, calls = self.run_fake("completo", shape="estandar")
        self.assertIn(("new", "-s", "laboratorio-cpu"), calls)
        result, calls = self.run_fake("completo", shape="high-ram", extra_args=["--estandar"])
        self.assertEqual(result, "error")
        self.assertEqual(calls, [])

    def test_reattaches_to_run_that_continued_without_laptop(self):
        # La terminal se cerró: la sesión quedó marcada como ocupada y la VM
        # sigue corriendo. No se ejecuta de nuevo; se descargan sus respaldos.
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        known = {"laboratorio-cpu": {"running": "exec(ejecutar_remoto.py)"}}
        result, calls = self.run_fake("completo", known=known, states=[
            {"status": "corriendo", "latido": now},
            {"status": "corriendo", "latido": now},
        ])
        self.assertEqual(result, 0)
        self.popen.assert_not_called()
        self.assertNotIn("new", [call[0] for call in calls])
        self.assertNotIn("upload", [call[0] for call in calls])
        self.assertEqual(calls[-1][0], "stop")

    def test_collects_run_that_finished_without_laptop(self):
        result, calls = self.run_fake("completo", known={"laboratorio-cpu": {}})
        self.assertEqual(result, 0)
        self.popen.assert_not_called()
        self.assertEqual(calls[-1][0], "stop")

    def test_resumes_when_remote_run_stopped(self):
        # Latido viejo: el kernel ya no corre la tarea; se reanuda en la misma VM.
        result, calls = self.run_fake("completo", known={"laboratorio-cpu": {}}, states=[
            {"status": "corriendo", "latido": "2026-01-01T00:00:00+00:00"},
        ])
        self.assertEqual(result, 0)
        self.popen.assert_called_once()
        self.assertIn("upload", [call[0] for call in calls])
        self.assertEqual(calls[-1][0], "stop")

    def test_refuses_vm_running_previous_payload(self):
        result, calls = self.run_fake("completo", known={"laboratorio-cpu": {}},
                                      states=[{"status": "preparando"}])
        self.assertEqual(result, "error")
        self.popen.assert_not_called()
        self.assertNotIn("stop", [call[0] for call in calls])

    def test_keeps_vm_when_final_download_fails(self):
        def failing_download(*args, **kwargs):
            if args[0] == "download":
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0)
        with tempfile.TemporaryDirectory() as temp:
            class FakeProcess:
                stdout = io.StringIO("ESTUDIOIA_CHECKPOINT x\nESTUDIOIA_FINAL 0\n")
                def wait(self, **kwargs):
                    return 0
                def poll(self):
                    return 0
                def terminate(self):
                    pass
            with patch.object(sys, "argv", ["runner", "--salida", temp]), \
                 patch.object(local, "make_payload", return_value={}), \
                 patch.object(local, "SESSION_FILE", Path(temp) / "sesiones.json"), \
                 patch.object(local, "cli", side_effect=failing_download) as cli, \
                 patch.object(local.subprocess, "Popen", return_value=FakeProcess()), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(subprocess.CalledProcessError):
                    local.main()
            self.assertNotIn("stop", [call.args[0] for call in cli.call_args_list])

    def test_backup_of_other_code_is_rejected_before_connecting(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "ejecucion_id.txt").write_text("run")
            with zipfile.ZipFile(Path(temp) / "ultimo.zip", "w") as archive:
                archive.writestr("estado.json", json.dumps({"status": "fallido"}))
                archive.writestr("procedencia.json", json.dumps({"ejecucion_id": "run", "modelos": {"a": 1}}))
            with patch.object(sys, "argv", ["runner", "--salida", temp]), \
                 patch.object(local, "make_payload", return_value={"modelos": {"a": 2}}), \
                 patch.object(local, "cli") as cli, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "otro código"):
                    local.main()
                cli.assert_not_called()

    def test_connection_failure_never_allocates_vm(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(sys, "argv", ["runner", "--salida", temp]), \
                 patch.object(local, "make_payload", return_value={}), \
                 patch.object(local, "cli", side_effect=OSError("DNS")) as cli, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(OSError):
                    local.main()
                self.assertEqual([call.args[0] for call in cli.call_args_list], ["sessions"])


if __name__ == "__main__":
    unittest.main()
