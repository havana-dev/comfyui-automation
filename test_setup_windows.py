"""Installer tests use temporary homes/projects and mock every installation/CLI/API call."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import setup_windows as setup
import mcp_server

SOURCE = Path(__file__).resolve().parent


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mcp 설치 테스트 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "ZIP 프로젝트"
        self.root.mkdir()
        (self.root / "workflows").mkdir()
        for filename in ("qwen21_text_to_image_api.json", "qwen21_character_scene_api.json"):
            shutil.copyfile(SOURCE / "workflows" / filename, self.root / "workflows" / filename)
        shutil.copyfile(SOURCE / "config.example.json", self.root / "config.example.json")
        self.python = self.root / ".venv-mcp" / "Scripts" / "python.exe"
        self.entry = setup.launcher(self.root, self.python)
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.desktop = self.home / "Claude" / "claude_desktop_config.json"
        self.code = self.home / ".claude.json"
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))

    def put(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path.read_bytes()

    def code_runner(self, args, **kwargs):
        self.assertEqual(args[:3], ["claude.exe", "mcp", "add"])
        self.assertEqual(kwargs["cwd"], self.root)
        self.assertIn("COMFYUI_MCP_AUTO_START=0", args)
        self.assertEqual(args[args.index(setup.NAME) - 2:args.index(setup.NAME)], ["--transport", "stdio"])
        env_args = args[args.index("--env") + 1:args.index("--transport")]
        self.assertEqual(set(env_args), {"PYTHONUTF8=1", "PYTHONDONTWRITEBYTECODE=1", "COMFYUI_MCP_AUTO_START=0"})
        self.assertEqual(args[args.index("--") + 1:], [self.entry["command"], *self.entry["args"]])
        data = setup.read_json(self.code) if self.code.exists() else {}
        scope = args[args.index("--scope") + 1]
        selected = data if scope == "user" else data.setdefault("projects", {}).setdefault(self.root.as_posix(), {})
        selected.setdefault("mcpServers", {})[setup.NAME] = {"type": "stdio", **self.entry}
        self.put(self.code, data)
        return "Added"

    def test_desktop_merge_backup_and_idempotent_rerun(self):
        original = self.put(self.desktop, {"preferences": {"theme": "dark"}, "mcpServers": {"other": {"env": {"SECRET": "kept"}}}, "Case": 1, "case": 2})
        setup.register_desktop(self.desktop, self.entry)
        actual = setup.read_json(self.desktop)
        self.assertEqual(actual["preferences"], {"theme": "dark"})
        self.assertEqual(actual["mcpServers"]["other"]["env"]["SECRET"], "kept")
        self.assertEqual((actual["Case"], actual["case"]), (1, 2))
        backups = list(self.desktop.parent.glob("*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        saved = self.desktop.read_bytes()
        self.assertIn("이미", setup.register_desktop(self.desktop, self.entry))
        self.assertEqual(self.desktop.read_bytes(), saved)
        self.assertEqual(list(self.desktop.parent.glob("*.bak")), backups)

    def test_desktop_new_file_and_conflict_preserved(self):
        setup.register_desktop(self.desktop, self.entry)
        before = self.desktop.read_bytes()
        changed = {**self.entry, "command": "other-python"}
        with self.assertRaises(setup.SetupError):
            setup.register_desktop(self.desktop, changed)
        self.assertEqual(self.desktop.read_bytes(), before)

    def test_wrong_transport_is_not_reported_as_registered(self):
        original = self.put(self.desktop, {"mcpServers": {setup.NAME: {"type": "http", **self.entry}}})
        with self.assertRaises(setup.SetupError):
            setup.register_desktop(self.desktop, self.entry)
        self.assertEqual(self.desktop.read_bytes(), original)

    def test_bad_json_and_wrong_servers_never_overwritten(self):
        self.desktop.parent.mkdir()
        for contents in ('{"broken"', '[]', '{"mcpServers": []}', '{"x":1,"x":2}'):
            self.desktop.write_text(contents, encoding="utf-8")
            original = self.desktop.read_bytes()
            with self.assertRaises(setup.SetupError):
                setup.register_desktop(self.desktop, self.entry)
            self.assertEqual(self.desktop.read_bytes(), original)
        self.assertFalse(list(self.desktop.parent.glob("*.bak")))

    def test_compare_before_write_preserves_concurrent_change(self):
        original = self.put(self.desktop, {"old": True})
        changed = self.put(self.desktop, {"new": True})
        with self.assertRaises(setup.SetupError):
            setup.write_json(self.desktop, {}, original)
        self.assertEqual(self.desktop.read_bytes(), changed)

    def test_code_local_cli_backup_preservation_and_rerun(self):
        original = self.put(self.code, {"oauth": "private", "projects": {self.root.as_posix(): {"trust": True}}, "mcpServers": {"other": {"command": "kept"}}})
        runner = Mock(side_effect=self.code_runner)
        setup.register_code(self.root, self.code, self.entry, cli="claude.exe", runner=runner)
        self.assertEqual(next(self.home.glob("*.bak")).read_bytes(), original)
        self.assertTrue(setup.local_project(setup.read_json(self.code), self.root)["trust"])
        self.assertIn("이미", setup.register_code(self.root, self.code, self.entry, cli="claude.exe", runner=runner))
        runner.assert_called_once()

    def test_code_user_scope(self):
        setup.register_code(self.root, self.code, self.entry, "user", cli="claude.exe", runner=self.code_runner)
        self.assertTrue(setup.matches(setup.read_json(self.code)["mcpServers"][setup.NAME], self.entry))

    def test_code_scope_conflict_does_not_invoke_cli(self):
        original = self.put(self.code, {"mcpServers": {setup.NAME: {"command": "old"}}})
        runner = Mock()
        with self.assertRaises(setup.SetupError):
            setup.register_code(self.root, self.code, self.entry, cli="claude.exe", runner=runner)
        runner.assert_not_called()
        self.assertEqual(self.code.read_bytes(), original)

    def test_code_project_scope_conflict_preserved(self):
        shared = self.root / ".mcp.json"
        before = self.put(shared, {"mcpServers": {setup.NAME: {"command": "shared"}}})
        runner = Mock()
        with self.assertRaises(setup.SetupError):
            setup.register_code(self.root, self.code, self.entry, cli="claude.exe", runner=runner)
        runner.assert_not_called()
        self.assertEqual(shared.read_bytes(), before)

    def test_code_cli_failure_keeps_backup(self):
        original = self.put(self.code, {"other": 1})
        with self.assertRaisesRegex(setup.SetupError, "백업"):
            setup.register_code(self.root, self.code, self.entry, cli="claude.exe", runner=Mock(side_effect=setup.SetupError("offline")))
        self.assertEqual(self.code.read_bytes(), original)
        self.assertEqual(next(self.home.glob("*.bak")).read_bytes(), original)

    def test_code_cli_zero_exit_without_persisting_is_failure(self):
        self.put(self.code, {})
        with self.assertRaises(setup.SetupError):
            setup.register_code(self.root, self.code, self.entry, cli="claude.exe", runner=Mock(return_value="Added"))

    def test_code_cli_detects_unrelated_settings_loss(self):
        self.put(self.code, {"keep": 1})
        def broken(args, **kwargs):
            self.code_runner(args, **kwargs)
            after = setup.read_json(self.code)
            del after["keep"]
            self.put(self.code, after)
        with self.assertRaises(setup.SetupError):
            setup.register_code(self.root, self.code, self.entry, cli="claude.exe", runner=broken)
        self.assertEqual(setup.read_json(next(self.home.glob("*.bak"))), {"keep": 1})

    def test_existing_claude_md_is_not_overwritten(self):
        self.assertIn("생성", setup.link_instructions(self.root))
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), "@AGENTS.md\n")
        (self.root / "CLAUDE.md").write_text("my instructions", encoding="utf-8")
        setup.link_instructions(self.root)
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), "my instructions")

    def test_config_creation_confirm_and_rerun_preserves_bytes(self):
        instance = self.root / "설치 폴더"
        instance.mkdir()
        (instance / "main.py").touch()
        answers = iter([str(instance), "http://127.0.0.1:8000", str(self.root / "결과 폴더"), "y"])
        config = setup.prepare_config(self.root, lambda _: next(answers))
        self.assertFalse(config["backend"]["auto_start"])
        self.assertTrue(Path(config["output_dir"]).is_absolute())
        original = (self.root / "config.json").read_bytes()
        setup.prepare_config(self.root, Mock(side_effect=AssertionError("must not prompt")))
        self.assertEqual((self.root / "config.json").read_bytes(), original)
        self.assertFalse(Path(config["output_dir"]).exists())

    def test_config_cancellation_creates_no_file(self):
        (self.root / "main.py").touch()
        answers = iter([str(self.root), "", "", "n"])
        with self.assertRaises(setup.SetupError):
            setup.prepare_config(self.root, lambda _: next(answers))
        self.assertFalse((self.root / "config.json").exists())

    def test_urls_reject_remote_credentials_paths_and_invalid_ports(self):
        for url in ("https://127.0.0.1", "http://example.org", "http://u:p@localhost", "http://localhost/api", "http://localhost:0", "http://localhost:abc", None):
            with self.subTest(url=url), self.assertRaises(setup.SetupError):
                setup.validate_url(url)
        self.assertEqual(setup.validate_url("http://[::1]:8000/"), "http://[::1]:8000")

    def test_existing_environment_no_install(self):
        self.python.parent.mkdir(parents=True)
        self.python.touch()
        runner = Mock(return_value=json.dumps({"version": [3, 13], "venv": True, "mcp": "1.30.0"}))
        self.assertEqual(setup.prepare_environment(self.root, "base", runner), self.python)
        runner.assert_called_once()

    def test_new_environment_creation_and_missing_mcp_install(self):
        def fake(args, **kwargs):
            if args[2:4] == ["-m", "venv"]:
                self.python.parent.mkdir(parents=True)
                self.python.touch()
            if "-c" in args:
                return json.dumps({"version": [3, 13], "venv": True, "mcp": None})
            return ""
        runner = Mock(side_effect=fake)
        setup.prepare_environment(self.root, "base", runner)
        self.assertEqual(runner.call_count, 3)
        pip_args = runner.call_args_list[-1].args[0]
        self.assertEqual(pip_args[0], self.python)
        self.assertIn("pip", pip_args)
        self.assertNotIn("--upgrade", pip_args)

    def test_broken_or_incompatible_environment_not_replaced(self):
        self.python.parent.mkdir(parents=True)
        runner = Mock()
        with self.assertRaises(setup.SetupError):
            setup.prepare_environment(self.root, "base", runner)
        runner.assert_not_called()
        self.python.touch()
        for info in ({"version": [3, 12], "venv": True, "mcp": None}, {"version": [3, 13], "venv": False}, {"version": [3, 13], "venv": True, "mcp": "2.0.0"}):
            runner = Mock(return_value=json.dumps(info))
            with self.assertRaises(setup.SetupError):
                setup.prepare_environment(self.root, "base", runner)
            runner.assert_called_once()

    def ready_info(self):
        info = {node: {} for node in setup.active_requirements(self.root)}
        for node, (field, filename, _) in setup.MODELS.items():
            info[node] = {"input": {"required": {field: [[filename]]}}}
        return info

    def test_diagnostics_only_gets_two_endpoints_and_ignores_enhancers(self):
        request = Mock(side_effect=[{"system": {}, "devices": []}, self.ready_info()])
        result = setup.diagnose(self.root, {"base_url": "http://localhost:8000"}, request=request)
        self.assertTrue(result["models_ready"])
        self.assertEqual([c.args[0] for c in request.call_args_list], ["http://localhost:8000/system_stats", "http://localhost:8000/object_info"])
        self.assertNotIn("TextGenerate", setup.active_requirements(self.root))

    def test_diagnostics_reports_missing_models_nodes_and_offline(self):
        info = self.ready_info()
        del info["TextEncodeQwenImage21"]
        info["UNETLoader"]["input"]["required"]["unet_name"] = [["another.safetensors"]]
        result = setup.diagnose(self.root, {"base_url": "http://localhost:8000"}, request=Mock(side_effect=[{"system": {}, "devices": []}, info]))
        self.assertTrue(result["connected"])
        self.assertFalse(result["models_ready"])
        self.assertIn("TextEncodeQwenImage21", result["missing_nodes"])
        self.assertEqual(result["missing_models"], [(setup.MODELS["UNETLoader"][1], "diffusion_models")])
        offline = setup.diagnose(self.root, {"base_url": "http://localhost:8000"}, request=Mock(side_effect=OSError("offline")))
        self.assertFalse(offline["connected"])

    def test_mock_install_both_and_rerun_without_real_home_or_cli(self):
        self.put(self.root / "config.json", {"base_url": "http://localhost:8000", "output_dir": str(self.root / "outputs")})
        self.put(self.desktop, {"preferences": {"keep": True}})
        self.python.parent.mkdir(parents=True)
        self.python.touch()
        def command(args, **kwargs):
            if "-c" in args:
                return json.dumps({"version": [3, 13], "venv": True, "mcp": "1.30.0"})
            if "mcp" in args:
                return self.code_runner(args, **kwargs)
            return json.dumps({"ok": True, "image_generation_requested": False, "tools": [{}] * 7})
        prepare = setup.prepare_environment
        register = setup.register_code
        ready = {"connected": True, "models_ready": True, "missing_models": [], "missing_nodes": [], "error": None}
        with patch.object(setup, "diagnose", return_value=ready), patch.object(setup, "__file__", str(self.root / "setup_windows.py")), patch.object(setup.Path, "home", return_value=self.home), patch.dict(os.environ, {"APPDATA": str(self.home), "CLAUDE_CONFIG_DIR": ""}), patch.object(setup, "run_command", side_effect=command), patch.object(setup, "prepare_environment", side_effect=lambda root, base: prepare(root, base, command)), patch.object(setup, "register_code", side_effect=lambda root, path, entry, scope: register(root, path, entry, scope, cli="claude.exe", runner=command)), patch.object(setup, "ask", side_effect=["3", "1", "3", "1"]):
            self.assertEqual(setup.main(), 0)
            desktop_before, code_before = self.desktop.read_bytes(), self.code.read_bytes()
            self.assertEqual(setup.main(), 0)
        self.assertEqual(self.desktop.read_bytes(), desktop_before)
        self.assertEqual(self.code.read_bytes(), code_before)
        self.assertEqual(setup.read_json(self.desktop)["preferences"], {"keep": True})
        self.assertFalse((self.root / "outputs").exists())

    def test_diagnostic_failure_still_registers_and_reports_partial_success(self):
        config_path = self.root / "config.json"
        original = self.put(config_path, {"base_url": "http://localhost:8000", "output_dir": str(self.root / "outputs")})
        self.desktop.parent.mkdir()
        listed = json.dumps({"ok": True, "image_generation_requested": False, "tools": [{}] * 7})
        offline = {"connected": False, "models_ready": False, "missing_nodes": [], "missing_models": [], "error": "offline"}
        with patch.object(setup, "diagnose", return_value=offline), patch.object(setup, "__file__", str(self.root / "setup_windows.py")), patch.dict(os.environ, {"APPDATA": str(self.home)}), patch.object(setup, "ask", return_value="1"), patch.object(setup, "prepare_environment", return_value=self.python), patch.object(setup, "run_command", return_value=listed):
            self.assertEqual(setup.main(), 2)
        self.assertTrue(self.desktop.exists())
        self.assertEqual(config_path.read_bytes(), original)
        printed = self.output.getvalue()
        self.assertIn("Claude Desktop MCP 등록: 등록 완료", printed)
        self.assertIn("ComfyUI 연결: 미확인", printed)
        self.assertIn("Qwen 모델·노드 준비: 미완료 또는 미확인", printed)

    def test_tools_list_failure_never_registers_apps(self):
        self.put(self.root / "config.json", {"base_url": "http://localhost:8000", "output_dir": str(self.root / "outputs")})
        with patch.object(setup, "__file__", str(self.root / "setup_windows.py")), patch.object(setup, "ask", return_value="3"), patch.object(setup, "prepare_environment", return_value=self.python), patch.object(setup, "run_command", side_effect=setup.SetupError("missing dependency")), patch.object(setup, "register_desktop") as desktop, patch.object(setup, "register_code") as code:
            with self.assertRaisesRegex(setup.SetupError, "도구 목록"):
                setup.main()
        desktop.assert_not_called()
        code.assert_not_called()

    def tree_snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() if path.is_file() else None
                for path in self.root.rglob("*")}

    def test_diagnose_missing_config_and_venv_creates_nothing(self):
        before = self.tree_snapshot()
        with patch.object(setup, "main", side_effect=AssertionError("must not install")), patch.object(setup, "run_command") as command, patch.object(setup, "ask", side_effect=AssertionError("must not prompt")), patch.object(setup, "__file__", str(self.root / "setup_windows.py")):
            self.assertEqual(setup.cli(["--diagnose"]), 1)
        command.assert_not_called()
        self.assertEqual(self.tree_snapshot(), before)
        result = json.loads(self.output.getvalue())
        self.assertFalse(result["mcp_connected"])
        self.assertFalse(result["models_ready"])
        self.assertIn("설정", result["error"])

    def test_diagnose_preserves_all_files_and_never_calls_mutations(self):
        self.put(self.root / "config.json", {"base_url": "http://localhost:8000"})
        self.put(self.root / "CLAUDE.md", {"keep": True})
        self.python.parent.mkdir(parents=True)
        self.python.touch()
        before = self.tree_snapshot()
        api = {"connected": True, "models_ready": False, "missing_models": [["missing", "vae"]], "missing_nodes": [], "error": None}
        listed = json.dumps({"ok": True, "image_generation_requested": False, "tools": [{}] * 7})
        with contextlib.ExitStack() as stack:
            for name in ("main", "prepare_config", "prepare_environment", "register_desktop", "register_code", "link_instructions", "backup", "write_json", "ask"):
                stack.enter_context(patch.object(setup, name, side_effect=AssertionError(f"must not call {name}")))
            stack.enter_context(patch.object(setup, "diagnose", return_value=api))
            command = stack.enter_context(patch.object(setup, "run_command", return_value=listed))
            result = setup.read_only_diagnosis(self.root)
        command.assert_called_once_with([self.python, "-B", self.root / "check_mcp_tools.py"], cwd=self.root, timeout=30)
        self.assertTrue(result["mcp_connected"])
        self.assertFalse(result["models_ready"])
        self.assertIn("MCP 연결", result)
        self.assertIn("Qwen 모델·노드 준비", result)
        self.assertEqual(self.tree_snapshot(), before)

    def test_diagnose_mcp_failure_keeps_independent_qwen_status(self):
        self.put(self.root / "config.json", {"base_url": "http://localhost:8000"})
        self.python.parent.mkdir(parents=True)
        self.python.touch()
        api = {"connected": True, "models_ready": True, "missing_models": [], "missing_nodes": [], "error": None}
        with patch.object(setup, "diagnose", return_value=api), patch.object(setup, "run_command", side_effect=setup.SetupError("SDK missing")):
            result = setup.read_only_diagnosis(self.root)
        self.assertFalse(result["mcp_connected"])
        self.assertTrue(result["models_ready"])
        self.assertIn("SDK missing", result["mcp_error"])

    def test_diagnose_exit_codes_reflect_each_required_check(self):
        for mcp, api, models, error in ((True, True, True, None), (False, True, True, None), (True, False, False, "offline"), (True, True, False, None), (True, True, True, "invalid schema")):
            result = {"mcp_connected": mcp, "connected": api, "models_ready": models, "error": error}
            with self.subTest(result=result), patch.object(setup, "read_only_diagnosis", return_value=result), patch.object(setup, "main", side_effect=AssertionError("must not install")):
                self.assertEqual(setup.cli(["--diagnose"]), 0 if all((mcp, api, models)) and not error else 1)

    def test_python_help_and_unknown_options_never_install(self):
        for args, code in ((["--help"], 0), (["-h"], 0), (["--unknown"], 2), (["--diagnose", "--unknown"], 2), (["--diag"], 2), (["unexpected"], 2)):
            with self.subTest(args=args), patch.object(setup, "main", side_effect=AssertionError("must not install")), patch.object(setup, "read_only_diagnosis", side_effect=AssertionError("must not diagnose")), contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit) as stopped:
                    setup.cli(args)
                self.assertEqual(stopped.exception.code, code)
                self.assertIn("usage:", self.output.getvalue() if code == 0 else errors.getvalue())

    def test_python_default_still_enters_installer(self):
        with patch.object(setup, "main", return_value=7) as install:
            self.assertEqual(setup.cli([]), 7)
        install.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows option handling")
    def test_powershell_help_and_unknown_options_never_start_python(self):
        shutil.copyfile(SOURCE / "setup.ps1", self.root / "setup.ps1")
        (self.root / "setup_windows.py").write_text('raise RuntimeError("INSTALLER_MUST_NOT_START")\n', encoding="utf-8")
        before = self.tree_snapshot()
        for args, code in ((["--help"], 0), (["-Help"], 0), (["-h"], 0), (["-Unknown"], 2), (["-CheckOnly", "-Unknown"], 2), (["-Check"], 2), (["-PythonPath"], 2), (["-PythonPath", "--help"], 2), (["positional"], 2)):
            with self.subTest(args=args):
                result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.root / "setup.ps1"), *args], capture_output=True, timeout=30)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn(b"setup.ps1", result.stdout)
                self.assertNotIn(b"INSTALLER_MUST_NOT_START", result.stderr)
                self.assertEqual(self.tree_snapshot(), before)

    @unittest.skipUnless(os.name == "nt", "Windows check-only routing")
    def test_powershell_checkonly_forwards_diagnose_and_exit_status(self):
        shutil.copyfile(SOURCE / "setup.ps1", self.root / "setup.ps1")
        for code in (0, 1):
            (self.root / "setup_windows.py").write_text('import sys\nassert sys.argv[1:] == ["--diagnose"]\nprint("READ_ONLY")\n' + f'sys.exit({code})\n', encoding="utf-8")
            before = self.tree_snapshot()
            result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.root / "setup.ps1"), "-CheckOnly", "-PythonPath", sys.executable], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertIn(b"READ_ONLY", result.stdout)
            self.assertEqual(self.tree_snapshot(), before)

    @unittest.skipUnless(os.name == "nt", "Windows read-only Python discovery")
    def test_checkonly_never_invokes_python_launcher_when_runtime_missing(self):
        shutil.copyfile(SOURCE / "setup.ps1", self.root / "setup.ps1")
        trap = self.root / "launcher.ps1"
        sentinel = self.root / "launcher-invoked.txt"
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        trap.write_text(f"Set-Content -LiteralPath {quote(sentinel)} -Value invoked\n", encoding="utf-8-sig")
        wrapper = self.root / "probe.ps1"
        wrapper.write_text(
            f"$env:LOCALAPPDATA = {quote(self.home)}\n"
            f"$env:APPDATA = {quote(self.home)}\n"
            "function Get-Command { param($Name, $ErrorAction)\n"
            f"if ($Name -eq 'py.exe') {{ [pscustomobject]@{{Source={quote(trap)}}} }}\n"
            "}\n"
            f"& {quote(self.root / 'setup.ps1')} -CheckOnly\nexit $LASTEXITCODE\n", encoding="utf-8-sig")
        before = self.tree_snapshot()
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.tree_snapshot(), before)

    def test_real_python_diagnose_with_no_config_or_venv_writes_nothing(self):
        shutil.copyfile(SOURCE / "setup_windows.py", self.root / "setup_windows.py")
        before = self.tree_snapshot()
        result = subprocess.run([sys.executable, "-B", str(self.root / "setup_windows.py"), "--diagnose"], capture_output=True, timeout=30, env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(result.returncode, 1, result.stderr)
        data = json.loads(result.stdout.decode("utf-8"))
        self.assertFalse(data["mcp_connected"])
        self.assertFalse(data["models_ready"])
        self.assertEqual(self.tree_snapshot(), before)

    @unittest.skipUnless(os.name == "nt", "Windows bootstrap")
    def test_powershell_bootstrap_uses_only_temporary_helper(self):
        shutil.copyfile(SOURCE / "setup.ps1", self.root / "setup.ps1")
        (self.root / "setup_windows.py").write_text('print("BOOTSTRAP_OK 한글 안내")\n', encoding="utf-8")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.root / "setup.ps1"), "-PythonPath", sys.executable], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"BOOTSTRAP_OK", result.stdout)
        self.assertIn("한글 안내", result.stdout.decode("utf-8"))

    def desktop_list(self, entries):
        path = self.home / "Comfy Desktop" / "installations.json"
        return path, self.put(path, entries)

    def comfy_install(self, name):
        folder = self.home / name
        (folder / "ComfyUI").mkdir(parents=True)
        (folder / "ComfyUI" / "main.py").touch()
        return folder

    def test_desktop_installs_reads_only_installed_comfyui_folders(self):
        good = self.comfy_install("설치 A")
        no_main = self.home / "main.py 없음"
        no_main.mkdir()
        path, original = self.desktop_list([
            {"status": "installed", "installPath": str(good)},
            {"status": "installed", "installPath": str(no_main)},
            {"status": "installing", "installPath": str(good)},
            {"status": "installed", "name": "Comfy Cloud", "remoteUrl": "https://cloud.comfy.org/"},
            "unexpected"])
        self.assertEqual(setup.desktop_installs(str(self.home)), [good])
        self.assertEqual(path.read_bytes(), original)

    def test_desktop_installs_missing_or_invalid_list_is_empty(self):
        self.assertEqual(setup.desktop_installs(str(self.home)), [])
        self.assertEqual(setup.desktop_installs(""), [])
        for content in ("{not json", '{"installPath": "C:/x"}'):
            with self.subTest(content=content):
                path = self.home / "Comfy Desktop" / "installations.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                self.assertEqual(setup.desktop_installs(str(self.home)), [])

    def test_config_folder_defaults_to_detected_desktop_install(self):
        install = self.comfy_install("Desktop 설치")
        self.desktop_list([{"status": "installed", "installPath": str(install)}])
        prompts = []
        answers = iter(["", "", str(self.root / "결과"), "y"])
        def answer(prompt):
            prompts.append(prompt)
            return next(answers)
        config = setup.prepare_config(self.root, answer, appdata=str(self.home))
        self.assertEqual(config["backend"]["base_dir"], str(install.resolve()))
        self.assertIn(str(install), prompts[0])
        self.assertIn("Enter", self.output.getvalue())

    @unittest.skipUnless(os.name == "nt", "Windows ComfyUI Desktop Python discovery")
    def test_powershell_prefers_comfyui_desktop_python_over_launcher(self):
        install = self.home / "Desktop 설치"
        install.mkdir()
        environment = install / "standalone-env"
        # A junction (no admin rights needed) stands in for Desktop's bundled Python 3.13.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(environment), sys.base_prefix], check=True, capture_output=True)
        self.addCleanup(os.rmdir, environment)
        self.desktop_list([{"status": "installed", "name": "Comfy Cloud", "remoteUrl": "https://cloud.comfy.org/"},
                           {"status": "installed", "installPath": str(install)}])
        shutil.copyfile(SOURCE / "setup.ps1", self.root / "setup.ps1")
        (self.root / "setup_windows.py").write_text("import sys\nprint('PY=' + sys.executable)\n", encoding="utf-8")
        trap = self.root / "launcher.ps1"
        sentinel = self.root / "launcher-invoked.txt"
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        trap.write_text(f"Set-Content -LiteralPath {quote(sentinel)} -Value invoked\n", encoding="utf-8-sig")
        wrapper = self.root / "probe.ps1"
        wrapper.write_text(
            f"$env:LOCALAPPDATA = {quote(self.home)}\n"
            f"$env:APPDATA = {quote(self.home)}\n"
            "function Get-Command { param($Name, $ErrorAction)\n"
            f"if ($Name -eq 'py.exe') {{ [pscustomobject]@{{Source={quote(trap)}}} }}\n"
            "}\n"
            f"& {quote(self.root / 'setup.ps1')}\nexit $LASTEXITCODE\n", encoding="utf-8-sig")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)], capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        # The launcher stub may be listed as a later candidate; Desktop's Python must be selected first.
        self.assertIn(str(environment / "python.exe"), result.stdout.decode("utf-8"))

    def junction_to_python(self, link):
        """A directory junction (no admin rights needed) whose python.exe is a real Python 3.13."""
        link.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), sys.base_prefix], check=True, capture_output=True)
        self.addCleanup(os.rmdir, link)  # Remove the link itself before the temporary tree is deleted.
        return link / "python.exe"

    def run_cli_wrapper(self, *args):
        shutil.copyfile(SOURCE / "run.ps1", self.root / "run.ps1")
        (self.root / "comfy_client.py").write_text("import sys\nprint('PY=' + sys.executable)\nprint('ARGS=' + '|'.join(sys.argv[1:]))\nsys.exit(3)\n", encoding="utf-8")
        return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.root / "run.ps1"), *args],
                              capture_output=True, timeout=60, env={**os.environ, "APPDATA": str(self.home), "PYTHONUTF8": "1"})

    def test_run_ps1_has_no_machine_specific_path(self):
        content = (SOURCE / "run.ps1").read_text(encoding="utf-8-sig")
        self.assertNotRegex(content, r"[A-Za-z]:\\")
        self.assertFalse((SOURCE / "setup_mcp.ps1").exists())

    @unittest.skipUnless(os.name == "nt", "Windows CLI wrapper")
    def test_run_ps1_without_any_python_explains_install_and_fails(self):
        result = self.run_cli_wrapper("check")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("install.cmd", (result.stdout + result.stderr).decode("utf-8", "replace"))
        self.assertNotIn(b"PY=", result.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows CLI wrapper")
    def test_run_ps1_uses_desktop_python_and_forwards_arguments(self):
        install = self.home / "Desktop 설치"
        desktop_python = self.junction_to_python(install / "standalone-env")
        # Real Desktop lists hold several entries (e.g. a Comfy Cloud entry without installPath).
        self.desktop_list([{"status": "installed", "installPath": str(install)},
                           {"status": "installed", "name": "Comfy Cloud", "remoteUrl": "https://cloud.comfy.org/"}])
        result = self.run_cli_wrapper("generate", "--prompt", "a cozy 서재", "--seed", "42")
        output = result.stdout.decode("utf-8")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("PY=" + str(desktop_python), output)
        self.assertIn("ARGS=generate|--prompt|a cozy 서재|--seed|42", output)

    @unittest.skipUnless(os.name == "nt", "Windows CLI wrapper")
    def test_run_ps1_prefers_installed_venv_over_desktop_python(self):
        install = self.home / "Desktop 설치"
        self.junction_to_python(install / "standalone-env")
        self.desktop_list([{"status": "installed", "installPath": str(install)}])
        venv_python = self.junction_to_python(self.root / ".venv-mcp" / "Scripts")
        result = self.run_cli_wrapper("check")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("PY=" + str(venv_python), result.stdout.decode("utf-8"))

    def test_install_cmd_is_ascii_crlf_and_calls_setup_from_its_folder(self):
        content = (SOURCE / "install.cmd").read_bytes()
        content.decode("ascii")
        self.assertNotIn(b"\n", content.replace(b"\r\n", b""))
        self.assertIn(b'-File "%~dp0setup.ps1" %*', content)
        self.assertIn(b"exit /b %SETUP_EXIT%", content)

    @unittest.skipUnless(os.name == "nt", "Windows double-click wrapper")
    def test_install_cmd_forwards_arguments_and_exit_status(self):
        for name in ("install.cmd", "setup.ps1"):
            shutil.copyfile(SOURCE / name, self.root / name)
        (self.root / "setup_windows.py").write_text('import sys\nassert sys.argv[1:] == ["--diagnose"]\nprint("READ_ONLY")\nsys.exit(1)\n', encoding="utf-8")
        before = self.tree_snapshot()
        # Run from another directory: the wrapper must locate setup.ps1 next to itself.
        result = subprocess.run(["cmd", "/c", str(self.root / "install.cmd"), "-CheckOnly", "-PythonPath", sys.executable],
                                cwd=self.home, input=b"\r\n", capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(b"READ_ONLY", result.stdout)
        self.assertEqual(self.tree_snapshot(), before)


class ExternalBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config.json").write_text(json.dumps({"backend": {"auto_start": False}}), encoding="utf-8")
        self.client = Mock(base_url="http://localhost:8000")
        self.client.request.return_value = {"system": {}, "devices": []}
        self.env = patch.dict(os.environ)
        self.env.start()
        os.environ.pop("COMFYUI_MCP_AUTO_START", None)
        self.addCleanup(self.env.stop)

    def test_external_api_never_spawns_or_probes_8188(self):
        with patch.object(mcp_server, "ROOT", self.root), patch.object(mcp_server.subprocess, "Popen") as spawn, patch.object(mcp_server.socket, "create_connection") as connection:
            mcp_server.ensure_backend(self.client)
        spawn.assert_not_called()
        connection.assert_not_called()
        self.client.request.assert_called_once_with("/system_stats")
        self.assertFalse((self.root / "backend.log").exists())

    def test_environment_opt_out_preserves_legacy_config_bytes(self):
        path = self.root / "config.json"
        path.write_text('{"backend":{"auto_start":true}}', encoding="utf-8")
        original = path.read_bytes()
        os.environ["COMFYUI_MCP_AUTO_START"] = "0"
        with patch.object(mcp_server, "ROOT", self.root), patch.object(mcp_server.subprocess, "Popen") as spawn:
            mcp_server.ensure_backend(self.client)
        spawn.assert_not_called()
        self.assertEqual(path.read_bytes(), original)

    def test_offline_external_does_not_start_or_retry(self):
        self.client.request.side_effect = mcp_server.ComfyError("offline")
        with patch.object(mcp_server, "ROOT", self.root), patch.object(mcp_server.subprocess, "Popen") as spawn:
            with self.assertRaises(mcp_server.ComfyError):
                mcp_server.ensure_backend(self.client)
        spawn.assert_not_called()
        self.client.request.assert_called_once()

    def test_external_wrong_listener_does_not_spawn(self):
        self.client.request.return_value = {"unrelated": True}
        with patch.object(mcp_server, "ROOT", self.root), patch.object(mcp_server.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(mcp_server.ComfyError, "API"):
                mcp_server.ensure_backend(self.client)
        spawn.assert_not_called()

    def test_legacy_default_reuses_existing_8188_listener(self):
        (self.root / "config.json").write_text('{"backend":{}}', encoding="utf-8")
        self.client.base_url = "http://127.0.0.1:8188"
        with patch.object(mcp_server, "ROOT", self.root), patch.object(mcp_server.socket, "create_connection"), patch.object(mcp_server.subprocess, "Popen") as spawn:
            mcp_server.ensure_backend(self.client)
        spawn.assert_not_called()
        self.assertFalse((self.root / "backend.log").exists())

    def test_legacy_default_still_starts_hidden_on_8188(self):
        base = self.root / "instance"
        for relative in ("ComfyUI/.venv/Scripts/python.exe", "ComfyUI/main.py", "models.yaml"):
            path = base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        (self.root / "config.json").write_text(json.dumps({"backend": {"base_dir": str(base), "models_config": str(base / "models.yaml"), "input_dir": str(base / "input"), "output_dir": str(base / "output")}}), encoding="utf-8")
        self.client.base_url = "http://127.0.0.1:8188"
        with patch.object(mcp_server, "ROOT", self.root), patch.object(mcp_server, "backend_process", None), patch.object(mcp_server.socket, "create_connection", side_effect=ConnectionRefusedError()), patch.object(mcp_server.subprocess, "Popen") as spawn:
            spawn.return_value.poll.return_value = None
            mcp_server.ensure_backend(self.client)
            spawn.assert_called_once()
            self.assertEqual(spawn.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertTrue((self.root / "backend.log").exists())


if __name__ == "__main__":
    unittest.main()
