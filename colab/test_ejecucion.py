"""Pruebas locales de respaldos y control de la corrida; no alquilan una VM."""
import contextlib
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

    def test_restore_rejects_different_models_and_hardware(self):
        payload = {"ejecucion_id": "run", "modelos": {"a": "hash"}}
        hardware = {"hilos": 2, "ram_bytes": 100, "modelo_cpu": "CPU1"}
        different = {**hardware, "hilos": 8}
        path = self.archive({"procedencia.json": payload, "hardware.json": different,
                             "pruebas/resultados/una.jsonl": '{"id":1}\n'})
        with patch.object(remote, "ROOT", self.root / "destino"):
            with self.assertRaisesRegex(RuntimeError, "hardware"):
                remote.restore(path, payload, hardware)
            with self.assertRaisesRegex(RuntimeError, "otro código"):
                remote.restore(path, {**payload, "modelos": {}}, different)
        self.assertFalse((self.root / "destino").exists())

    def test_restore_never_replaces_code(self):
        payload = {"ejecucion_id": "run"}
        path = self.archive({"procedencia.json": payload,
                             "pruebas/laboratorio.py": "codigo alterado",
                             "pruebas/resultados/m_es.jsonl": '{"id":1}\n'})
        with patch.object(remote, "ROOT", self.root / "destino"):
            remote.restore(path, payload, {})
        self.assertFalse((self.root / "destino/pruebas/laboratorio.py").exists())
        self.assertTrue((self.root / "destino/pruebas/resultados/m_es.jsonl").is_file())


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

    def run_fake(self, remote_status, exec_return=0):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            calls = []
            def fake_cli(*args, **kwargs):
                calls.append(args)
                if args[0] == "download":
                    with zipfile.ZipFile(args[-1], "w") as archive:
                        archive.writestr("estado.json", json.dumps({"status": remote_status}))
                return subprocess.CompletedProcess(args, 0)
            class FakeProcess:
                stdout = io.StringIO("ESTUDIOIA_CHECKPOINT /content/resultados.zip\nESTUDIOIA_FINAL 0\n")
                def wait(self, **kwargs):
                    return exec_return
                def poll(self):
                    return exec_return
            with patch.object(sys, "argv", ["runner", "--salida", str(directory)]), \
                 patch.object(local, "make_payload", return_value={}), \
                 patch.object(local, "SESSION_FILE", directory / "sesiones.json"), \
                 patch.object(local, "cli", side_effect=fake_cli), \
                 patch.object(local.subprocess, "Popen", return_value=FakeProcess()), \
                 contextlib.redirect_stdout(io.StringIO()):
                try:
                    result = local.main()
                except RuntimeError:
                    result = "error"
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
