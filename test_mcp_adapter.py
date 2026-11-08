"""Adapter tests with the real SDK and mocked ComfyUI calls; no GPU work."""
import contextlib
import importlib.util
import io
import unittest
from unittest.mock import patch

SDK_AVAILABLE = importlib.util.find_spec("mcp") is not None
if SDK_AVAILABLE:
    import mcp_server
    from mcp.server.fastmcp.exceptions import ToolError


@unittest.skipUnless(SDK_AVAILABLE, "Install requirements-mcp.txt in .venv-mcp first")
class AdapterTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
