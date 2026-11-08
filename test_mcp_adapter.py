"""Adapter tests with the real SDK and mocked ComfyUI calls; no GPU work."""
import contextlib
import importlib.util
import io
import unittest
from unittest.mock import patch, Mock, mock_open

SDK_AVAILABLE = importlib.util.find_spec("mcp") is not None
if SDK_AVAILABLE:
    import mcp_server
    from mcp.server.fastmcp.exceptions import ToolError


@unittest.skipUnless(SDK_AVAILABLE, "Install requirements-mcp.txt in .venv-mcp first")
class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.startup = patch.object(mcp_server, "ensure_backend")
        self.ensure = self.startup.start()
        self.addCleanup(self.startup.stop)

    async def test_generate_reuses_client_and_does_not_print_stdout(self):
        result = {"prompt_id": "test", "seed": 42, "files": ["image.png"], "metadata_file": "image.json", "character": "Lucy"}
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(mcp_server, "generate_local_image", return_value=result) as generate:
            actual = await mcp_server.generate_image("portrait", "Lucy", 42)
        self.assertEqual(actual, result)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(generate.call_args.args[2], "portrait")
        self.assertEqual(generate.call_args.kwargs["character"], "Lucy")
        self.assertEqual(generate.call_args.kwargs["seed"], 42)
        self.assertFalse(mcp_server.generation_lock.locked())

    async def test_status_only_checks_api(self):
        with patch.object(mcp_server.ComfyClient, "check", return_value={"devices": ["GPU"]}), patch.object(mcp_server, "generate_local_image") as generate:
            result = await mcp_server.comfyui_status()
        self.assertTrue(result["ready"])
        self.assertFalse(result["output_write_checked"])
        generate.assert_not_called()

    async def test_generation_error_is_tool_error_and_releases_lock(self):
        with patch.object(mcp_server, "generate_local_image", side_effect=mcp_server.ComfyError("model missing")):
            with self.assertRaisesRegex(ToolError, "model missing"):
                await mcp_server.generate_image("portrait")
        self.assertFalse(mcp_server.generation_lock.locked())


@unittest.skipUnless(SDK_AVAILABLE, "MCP SDK required")
class BackendTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.base_url = "http://127.0.0.1:8188"
        self.client.request.return_value = {"system": {}, "devices": []}
        self.patches = [
            patch.object(mcp_server.socket, "create_connection"),
            patch.object(mcp_server.subprocess, "Popen"),
            patch.object(mcp_server.Path, "is_file", return_value=True),
            patch.object(mcp_server.Path, "open", mock_open()),
            patch.object(mcp_server, "backend_process", None),
            patch.object(mcp_server.time, "sleep"),
            patch.object(mcp_server, "backend_settings", return_value={"base_dir": "test-backend", "models_config": "test-models.yaml", "input_dir": "test-input", "output_dir": "test-output"}),
        ]
        self.connection, self.spawn, *_ = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.spawn.return_value.poll.return_value = None

    def test_existing_listener_reused(self):
        mcp_server.ensure_backend(self.client)
        self.spawn.assert_not_called()

    def test_start_hidden_and_wait_for_api(self):
        self.connection.side_effect = ConnectionRefusedError()
        self.client.request.side_effect = [mcp_server.ComfyError("starting"), {"system": {}, "devices": []}]
        mcp_server.ensure_backend(self.client)
        self.assertEqual(self.client.request.call_count, 2)
        self.assertEqual(self.spawn.call_args.kwargs["creationflags"], mcp_server.subprocess.CREATE_NO_WINDOW)
        self.assertIn(".venv", self.spawn.call_args.args[0][0])
        mcp_server.ensure_backend(Mock(base_url=self.client.base_url, request=Mock(return_value={"system": {}, "devices": []})))
        self.spawn.assert_called_once()

    def test_port_access_denied_does_not_start(self):
        self.connection.side_effect = PermissionError("denied")
        with self.assertRaises(mcp_server.ComfyError):
            mcp_server.ensure_backend(self.client)
        self.spawn.assert_not_called()

    def test_missing_environment_does_not_start(self):
        self.connection.side_effect = ConnectionRefusedError()
        with patch.object(mcp_server.Path, "is_file", return_value=False):
            with self.assertRaisesRegex(mcp_server.ComfyError, "missing"):
                mcp_server.ensure_backend(self.client)
        self.spawn.assert_not_called()

    def test_child_exit(self):
        self.connection.side_effect = ConnectionRefusedError()
        self.spawn.return_value.poll.return_value = 1
        self.client.request.side_effect = mcp_server.ComfyError("offline")
        with self.assertRaisesRegex(mcp_server.ComfyError, "exited"):
            mcp_server.ensure_backend(self.client)

    def test_wrong_listener_times_out_without_start(self):
        self.client.request.return_value = {}
        with self.assertRaisesRegex(mcp_server.ComfyError, "timed out"):
            mcp_server.ensure_backend(self.client, timeout=0)
        self.spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
