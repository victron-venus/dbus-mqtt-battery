"""Run release modules in isolated interpreters without test sys.path mutations."""

import os
import subprocess  # nosec B404 - isolated local Python interpreter only.
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULES = (
    "release_control",
    "release_state",
    "release_version_adapter",
    "prepare_version",
    "release_versioned",
    "version_receipt",
    "stage_release_assets",
    "version_plan",
)


class ReleaseImportTests(unittest.TestCase):
    def run_python(self, *arguments):
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        # Interpreter and arguments are fixed test inputs; no shell or external command.
        result = subprocess.run(  # nosec B603
            [sys.executable, *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_package_imports_without_scripts_on_path(self):
        script = "import importlib; " + "; ".join(
            f"importlib.import_module('scripts.{name}')" for name in MODULES
        )
        self.run_python("-c", script)

    def test_standalone_importlib_loads(self):
        script = """
import importlib.util
import sys
from pathlib import Path
sys.path.insert(0, str(Path('scripts').resolve()))
for name in MODULES:
    spec = importlib.util.spec_from_file_location(name, Path('scripts') / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
""".replace("MODULES", repr(MODULES))
        self.run_python("-c", script)

    def test_direct_cli_help(self):
        for name in MODULES:
            if name == "release_state":
                continue
            with self.subTest(module=name):
                self.run_python(f"scripts/{name}.py", "--help")

    def test_event_input_shapes(self):
        script = """
import json
import os
from pathlib import Path
import tempfile
from scripts import release_versioned as lifecycle
with tempfile.TemporaryDirectory() as directory:
    event = Path(directory) / 'event.json'
    os.environ['GITHUB_EVENT_PATH'] = str(event)
    for value in (None, [], 'event', *({'inputs': item} for item in (['untrusted'], [], '', 0, False))):
        event.write_text(json.dumps(value))
        try:
            lifecycle.event_inputs()
        except lifecycle.rc.ReleaseError:
            pass
        else:
            raise AssertionError('Malformed event accepted')
    for value in ({}, {'inputs': None}, {'inputs': {'channel': 'beta'}}):
        event.write_text(json.dumps(value))
        assert lifecycle.event_inputs() == (value.get('inputs') or {})
"""
        self.run_python("-c", script)
