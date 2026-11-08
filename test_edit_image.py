"""Mock-only edit coverage. Never connects to ComfyUI."""
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import mcp_server
from comfy_client import ComfyClient, ComfyError, ROOT, edit_image

PNG = b'\x89PNG\r\n\x1a\nmock'
GRAPH = {
    'image': {'class_type': 'LoadImage', 'inputs': {'image': 'placeholder.png'}},
    'text': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'old'}},
    'noise': {'class_type': 'RandomNoise', 'inputs': {'noise_seed': 0}},
    'model': {'class_type': 'UNETLoader', 'inputs': {'unet_name': 'klein-fp8.safetensors'}},
}
BINDINGS = {'reference_image': ['image', 'image'], 'instruction': ['text', 'text'],
            'seed': ['noise', 'noise_seed']}


class EditTests(unittest.TestCase):
    def test_edit_preserves_reference_and_records_graph(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            source = Path(folder) / 'reference_v1.png'
            source.write_bytes(PNG)
            client = ComfyClient('http://127.0.0.1:8188')
            entry = {'outputs': {'save': {'images': [{'filename': 'edited.png', 'type': 'output'}]}}}
            with patch.object(client, 'check') as check, patch.object(client, 'upload_image', return_value='unique.png'), patch.object(client, 'wait', return_value=entry), patch.object(client, 'request', side_effect=[{'prompt_id': 'edit-id'}, PNG]) as request:
                result = edit_image(client, GRAPH, BINDINGS, source, 'Shorten only the hair', folder, seed=42)
            sent = request.call_args_list[0].args[1]['prompt']
            self.assertNotIn('image', check.call_args.args[0]['image']['inputs'])
            self.assertEqual(sent['image']['inputs']['image'], 'unique.png')
            self.assertEqual(sent['noise']['inputs']['noise_seed'], 42)
            self.assertEqual(GRAPH['text']['inputs']['text'], 'old')
            metadata = json.loads(Path(result['metadata_file']).read_text())
            self.assertEqual(metadata['reference_image'], str(source.resolve()))
            self.assertEqual(metadata['reference_sha256'], hashlib.sha256(PNG).hexdigest())
            self.assertEqual(metadata['operation'], 'edit_image')
            self.assertEqual(metadata['workflow'], sent)
            self.assertEqual(metadata['seed'], 42)
            self.assertEqual(source.read_bytes(), PNG)
            self.assertNotEqual(Path(result['files'][0]), source)

    def test_model_error_prevents_upload_and_submit(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            source = Path(folder) / 'ref.png'; source.write_bytes(PNG)
            client = Mock()
            client.check.side_effect = ComfyError('model missing')
            with self.assertRaisesRegex(ComfyError, 'model missing'):
                edit_image(client, GRAPH, BINDINGS, source, 'hair', folder)
            client.upload_image.assert_not_called()
            client.request.assert_not_called()

    def test_invalid_inputs_do_not_contact_server(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            source = Path(folder) / 'ref.png'; source.write_bytes(PNG)
            for ref, instruction, seed, bindings in [(source, '', 0, BINDINGS), (source, 'hair', -1, BINDINGS), (source, 'hair', 0, {}), (Path(folder)/'missing.png', 'hair', 0, BINDINGS)]:
                client = Mock()
                with self.assertRaises(ComfyError):
                    edit_image(client, GRAPH, bindings, ref, instruction, folder, seed=seed)
                client.check.assert_not_called()
                client.upload_image.assert_not_called()

    def test_timeout_submits_once(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            source = Path(folder) / 'ref.png'; source.write_bytes(PNG)
            client = Mock()
            client.upload_image.return_value = 'unique.png'
            client.request.return_value = {'prompt_id': 'pending'}
            client.wait.side_effect = ComfyError('timeout pending')
            with self.assertRaisesRegex(ComfyError, 'timeout pending'):
                edit_image(client, GRAPH, BINDINGS, source, 'hair', folder)
            client.request.assert_called_once()
            client.save.assert_not_called()

    def test_adapter_resolves_first_reference_and_releases_lock(self):
        config = {'base_url': 'http://127.0.0.1:8188', 'output_dir': 'out',
                  'edit_engine': 'klein',
                  'edit_workflow': {'path': 'official.json', 'bindings': BINDINGS}}
        store = Mock()
        store.get.return_value = {'folder_name': 'existing-id', 'reference_images': ['reference_v1.png', 'other.png']}
        with patch.object(mcp_server.Path, 'read_text', return_value=json.dumps(config)), patch.object(mcp_server.Path, 'is_file', return_value=True), patch.object(mcp_server, 'load_workflow', return_value=GRAPH), patch.object(mcp_server, 'character_store', return_value=store), patch.object(mcp_server, 'ensure_backend'), patch.object(mcp_server, 'edit_local_image', return_value={'ok': True}) as edit:
            self.assertEqual(mcp_server.edit_sync(None, 'hair', 'Example', 42), {'ok': True})
            self.assertEqual(edit.call_args.args[3], 'reference_v1.png')
            self.assertEqual(edit.call_args.kwargs['character'], 'existing-id')
        self.assertFalse(mcp_server.generation_lock.locked())

    def test_default_qwen_uses_explicit_source_and_preserves_previous_results(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            root = Path(folder)
            source = root / 'previous-scene.png'; source.write_bytes(PNG)
            official = root / 'reference_v1.png'; official.write_bytes(PNG + b'official')
            config = {'base_url': 'http://127.0.0.1:8188', 'output_dir': folder,
                      'edit_workflow': {'path': 'legacy-klein.json', 'bindings': BINDINGS}}
            (root / 'config.json').write_text(json.dumps(config), encoding='utf-8')
            store = Mock()
            store.get.return_value = {'folder_name': 'example-id', 'reference_images': [str(official)]}
            client = ComfyClient(config['base_url'])
            entry = {'outputs': {'461': {'images': [{'filename': 'edited.png', 'type': 'output'}]}}}
            instruction = 'Change only the clothing to an ivory linen day dress.'
            with patch.object(mcp_server, 'ROOT', root), patch.object(mcp_server, 'character_store', return_value=store), patch.object(mcp_server, 'ensure_backend'), patch.object(mcp_server, 'ComfyClient', return_value=client), patch.object(client, 'check'), patch.object(client, 'upload_image', return_value='uploaded-source.png') as upload, patch.object(client, 'wait', return_value=entry), patch.object(client, 'request', side_effect=[{'prompt_id': 'qwen-edit'}, PNG, {'prompt_id': 'qwen-edit'}, PNG]) as request:
                first = mcp_server.edit_sync(str(source), instruction, 'alias', 42)
                old_png = Path(first['files'][0]).read_bytes()
                old_json = Path(first['metadata_file']).read_bytes()
                second = mcp_server.edit_sync(str(source), instruction, 'alias', 42)
            self.assertEqual(set(first), {'prompt_id', 'seed', 'files', 'metadata_file', 'character', 'reference_image'})
            self.assertEqual(first['reference_image'], str(source.resolve()))
            self.assertNotEqual(first['files'], second['files'])
            self.assertNotEqual(first['metadata_file'], second['metadata_file'])
            self.assertEqual(Path(first['files'][0]).read_bytes(), old_png)
            self.assertEqual(Path(first['metadata_file']).read_bytes(), old_json)
            self.assertEqual(source.read_bytes(), PNG)
            self.assertEqual(official.read_bytes(), PNG + b'official')
            self.assertEqual(upload.call_count, 2)
            for call in upload.call_args_list:
                self.assertEqual(call.args[0], source.resolve())
                self.assertEqual(call.kwargs['image_bytes'], PNG)
            submitted = [call.args[1]['prompt'] for call in request.call_args_list if call.args[0] == '/prompt']
            self.assertEqual(len(submitted), 2)
            graph = submitted[0]
            self.assertEqual(graph['470']['inputs']['image'], 'uploaded-source.png')
            self.assertEqual(graph['459:474']['inputs']['images.image_1'], ['470', 0])
            self.assertEqual(graph['459:474']['inputs']['prompt'], instruction)
            self.assertEqual(graph['459:458']['inputs']['positive'], ['459:474', 0])
            self.assertNotIn('images.image_2', graph['459:474']['inputs'])
            self.assertNotIn('459:500', graph)
            metadata = json.loads(old_json)
            self.assertEqual(metadata['engine'], 'Qwen Image 2.1')
            self.assertEqual(metadata['operation'], 'edit_image')
            self.assertEqual(metadata['reference_image'], str(source.resolve()))
            self.assertEqual(metadata['reference_sha256'], hashlib.sha256(PNG).hexdigest())
            self.assertEqual(metadata['prompt'], instruction)
            self.assertEqual(metadata['seed'], 42)
            self.assertEqual(metadata['workflow'], graph)
            self.assertEqual({m['filename'] for m in metadata['models']}, {
                'qwen_image_2.1_int8_convrot.safetensors',
                'qwen3vl_8b_int8_convrot.safetensors',
                'qwen_image_2.1_vae_bf16.safetensors'})
        self.assertFalse(mcp_server.generation_lock.locked())

    def test_default_qwen_retains_omitted_reference_fallback(self):
        config = {'base_url': 'http://127.0.0.1:8188', 'output_dir': 'out'}
        store = Mock()
        store.get.return_value = {'folder_name': 'example-id', 'reference_images': ['fallback.png']}
        with patch.object(mcp_server.Path, 'read_text', return_value=json.dumps(config)), patch.object(mcp_server, 'prepare_qwen_graph', return_value={'461': {'inputs': {}}}), patch.object(mcp_server, 'character_store', return_value=store), patch.object(mcp_server, 'ensure_backend'), patch.object(mcp_server, 'edit_local_image', return_value={'ok': True}) as edit:
            self.assertEqual(mcp_server.edit_sync(None, 'Change clothes', 'alias', 42), {'ok': True})
            self.assertEqual(edit.call_args.args[3], 'fallback.png')
            self.assertEqual(edit.call_args.args[2], mcp_server.QWEN_BINDINGS)
        self.assertFalse(mcp_server.generation_lock.locked())

    def test_missing_workflow_fails_without_startup(self):
        with patch.object(mcp_server.Path, 'is_file', return_value=False), patch.object(mcp_server, 'ensure_backend') as start:
            with self.assertRaisesRegex(ComfyError, 'workflow not ready'):
                mcp_server.edit_sync('reference.png', 'hair', None, 0)
            start.assert_not_called()
        self.assertFalse(mcp_server.generation_lock.locked())

    def test_upload_unique_name_no_overwrite(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            source = Path(folder) / 'reference_v1.png'; source.write_bytes(PNG)
            client = ComfyClient('http://127.0.0.1:8188')
            response = Mock(); response.read.return_value = b'{"name":"unique.png","subfolder":""}'
            with patch.object(client.opener, 'open') as opened:
                opened.return_value.__enter__.return_value = response
                self.assertEqual(client.upload_image(source), 'unique.png')
            req = opened.call_args.args[0]
            self.assertEqual(req.full_url, client.base_url + '/upload/image')
            self.assertIn(b'edit_reference_', req.data)
            self.assertNotIn(b'name="overwrite"', req.data)
            self.assertEqual(source.read_bytes(), PNG)


class KleinWorkflowTests(unittest.TestCase):
    def test_configured_official_graph_submits_bound_inputs_once(self):
        config = json.loads((ROOT / 'config.example.json').read_text(encoding='utf-8-sig'))
        settings = config['edit_workflow']
        self.assertEqual(settings['path'], 'workflows/flux2_klein_edit_api.json')
        self.assertEqual(settings['bindings'], {
            'reference_image': ['76', 'image'], 'instruction': ['75:74', 'text'],
            'seed': ['75:73', 'noise_seed']})
        workflow_path = ROOT / settings['path']
        original = workflow_path.read_bytes()
        graph = json.loads(original)
        self.assertEqual(graph['76']['class_type'], 'LoadImage')
        self.assertEqual(graph['75:74']['class_type'], 'CLIPTextEncode')
        self.assertEqual(graph['75:73']['class_type'], 'RandomNoise')
        instruction = 'Keep the same character, face, hairstyle, clothing and illustration style. Change only her expression to a gentle smile.'
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            reference = Path(folder) / 'reference_v1.png'
            reference.write_bytes(PNG)
            client = ComfyClient('http://127.0.0.1:8188')
            entry = {'outputs': {'9': {'images': [{'filename': 'smile.png', 'type': 'output'}]}}}
            with patch.object(client, 'check'), patch.object(client, 'upload_image', return_value='uploaded.png'), patch.object(client, 'wait', return_value=entry), patch.object(client, 'request', side_effect=[{'prompt_id': 'smile-test'}, PNG]) as request:
                result = edit_image(client, graph, settings['bindings'], reference, instruction, folder, seed=42)
            submitted = request.call_args_list[0].args[1]['prompt']
            expected = json.loads(original)
            expected['76']['inputs']['image'] = 'uploaded.png'
            expected['75:74']['inputs']['text'] = instruction
            expected['75:73']['inputs']['noise_seed'] = 42
            self.assertEqual(submitted, expected)
            self.assertEqual(sum(call.args[0] == '/prompt' for call in request.call_args_list), 1)
            metadata = json.loads(Path(result['metadata_file']).read_text(encoding='utf-8'))
            self.assertEqual(metadata['prompt'], instruction)
            self.assertEqual(metadata['workflow'], submitted)
            self.assertEqual(reference.read_bytes(), PNG)
        self.assertEqual(workflow_path.read_bytes(), original)
