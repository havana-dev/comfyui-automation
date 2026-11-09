import contextlib
import io
import tempfile
import comfy_client
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from comfy_client import ComfyClient, ComfyError, ROOT, generate_image, prepare_generation_workflow
from result_store import output_folder


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = ComfyClient("http://127.0.0.1:8188")
        self.workflow = prepare_generation_workflow()

    def test_cli_check_and_generate_accept_removed_legacy_workflow(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            config = Path(folder) / 'config.json'
            config.write_text(json.dumps({'base_url': 'http://127.0.0.1:8188',
                                         'output_dir': folder,
                                         'workflow': 'workflows/z_image_turbo_api.json',
                                         'edit_engine': 'klein', 'edit_workflow': {}}))
            for command in ('check', 'generate'):
                with self.subTest(command=command), patch('sys.argv', ['client', command, '--config', str(config), '--prompt', 'library']), patch.object(comfy_client, 'ComfyClient') as factory, patch.object(comfy_client, 'generate_image', return_value={'files': []}) as generate, contextlib.redirect_stdout(io.StringIO()):
                    factory.return_value.base_url = "http://127.0.0.1:8188"
                    factory.return_value.check.return_value = {}
                    self.assertEqual(comfy_client.main(), 0)
                    graph = factory.return_value.check.call_args.args[0] if command == 'check' else generate.call_args.args[1]
                    self.assertEqual(graph['459:451']['inputs']['unet_name'], 'qwen_image_2.1_int8_convrot.safetensors')
                    self.assertEqual(graph['459:456']['inputs'], {'width': 1024, 'height': 1024, 'batch_size': 1})
                    self.assertNotIn('459:471', graph)
                    factory.return_value.request.assert_not_called()

    def test_non_square_dimensions_and_timeout_submit_once(self):
        from unittest.mock import Mock
        client = Mock()
        client.request.return_value = {'prompt_id': 'pending'}
        client.wait.side_effect = ComfyError('timeout pending')
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            with self.assertRaisesRegex(ComfyError, 'pending'):
                generate_image(client, self.workflow, 'library', folder, seed=42, width=768, height=1024)
        client.request.assert_called_once()
        graph = client.request.call_args.args[1]['prompt']
        self.assertEqual(graph['459:456']['inputs'], {'width': 768, 'height': 1024, 'batch_size': 1})
        self.assertEqual(set(graph), set(self.workflow))
        client.save.assert_not_called()

    def test_missing_model_blocks_before_submission(self):
        info = {node["class_type"]: {"input": {}} for node in self.workflow.values()}
        info["UNETLoader"]["input"] = {"required": {"unet_name": [[]]}}
        with patch.object(self.client, "request", side_effect=[{}, info]) as request:
            with self.assertRaisesRegex(ComfyError, "qwen_image_2.1"):
                self.client.check(self.workflow)
        self.assertEqual([call.args[0] for call in request.call_args_list], ["/system_stats", "/object_info"])

    def test_generation_failure_and_timeout(self):
        entry = {"status": {"messages": [["execution_error", {"exception_message": "CUDA out of memory"}]]}}
        with patch.object(self.client, "request", return_value={"id": entry}):
            with self.assertRaisesRegex(ComfyError, "CUDA out of memory"):
                self.client.wait("id", 1)
        with self.assertRaisesRegex(ComfyError, "fetch"):
            self.client.wait("id", 0)

    def test_generate_prompt_seed_and_save(self):
        entry = {"outputs": {"9": {"images": [{"filename": "../../server.png", "subfolder": "automation", "type": "output"}]}}}
        png = b"\x89PNG\r\n\x1a\nmock-payload"
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            with patch.object(self.client, "check"), patch.object(self.client, "wait", return_value=entry), patch.object(self.client, "request", side_effect=[{"prompt_id": "test-id"}, png]) as request:
                result = generate_image(self.client, self.workflow, "청록색 눈의 탐정", folder, seed=42)
            sent = request.call_args_list[0].args[1]["prompt"]
            self.assertEqual(sent["459:452"]["inputs"]["prompt"], "청록색 눈의 탐정")
            self.assertEqual(sent["459:458"]["inputs"]["seed"], 42)
            self.assertEqual(self.workflow["459:458"]["inputs"]["seed"], 0)
            target = Path(result["files"][0])
            self.assertEqual(target.parent, Path(folder))
            self.assertEqual(target.read_bytes(), png)
            metadata = json.loads(Path(result["metadata_file"]).read_text(encoding="utf-8"))
            self.assertEqual(metadata["prompt"], sent["459:452"]["inputs"]["prompt"])
            self.assertEqual(metadata["seed"], 42)
            self.assertEqual(metadata["workflow"], sent)
            self.assertEqual(metadata["files"], result["files"])
            self.assertEqual(metadata["generated_at_source"], "client_observed_completion")

    def test_character_subfolder_and_server_timestamp(self):
        entry = {"status": {"messages": [["execution_success", {"timestamp": 0}]]},
                 "outputs": {"9": {"images": [{"filename": "image.png", "type": "output"}]}}}
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            with patch.object(self.client, "check"), patch.object(self.client, "wait", return_value=entry), patch.object(self.client, "request", side_effect=[{"prompt_id": "test-id"}, b"\x89PNG\r\n\x1a\nmock"]):
                result = generate_image(self.client, self.workflow, "portrait", folder, seed=0,
                                        character="루시", output_subdir="trpg/london")
            target = Path(result["metadata_file"])
            self.assertEqual(target.parent, Path(folder) / "trpg" / "london" / "루시")
            metadata = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(metadata["character"], "루시")
            self.assertEqual(metadata["prompt"], "portrait")
            self.assertEqual(metadata["generated_at"], "1970-01-01T00:00:00+00:00")
            self.assertEqual(metadata["generated_at_source"], "server_execution_success")
            self.assertEqual({item["filename"] for item in metadata["models"]},
                             {"qwen_image_2.1_int8_convrot.safetensors", "qwen3vl_8b_int8_convrot.safetensors", "qwen_image_2.1_vae_bf16.safetensors"})

    def test_invalid_output_paths_never_call_api(self):
        for subdir in ("../escape", "E:\\outside", "\\outside", "bad:stream", "trpg/../escape", "CON", "name.", ""):
            with self.subTest(subdir=subdir), patch.object(self.client, "check") as check:
                with self.assertRaises(ValueError):
                    generate_image(self.client, self.workflow, "test", ROOT, output_subdir=subdir)
                check.assert_not_called()
        with self.assertRaises(ValueError):
            output_folder(ROOT, character="a/b")

    def test_metadata_failure_reports_saved_images(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            with patch.object(self.client, "check"), patch.object(self.client, "wait", return_value={}), patch.object(self.client, "save", return_value=["saved.png"]), patch.object(self.client, "request", return_value={"prompt_id": "test-id"}), patch("comfy_client.save_metadata", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(ComfyError, "saved.png"):
                    generate_image(self.client, self.workflow, "test", folder)

    def test_invalid_size_never_calls_api(self):
        with patch.object(self.client, "request") as request:
            with self.assertRaises(ComfyError):
                generate_image(self.client, self.workflow, "test", ROOT, width=513)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
