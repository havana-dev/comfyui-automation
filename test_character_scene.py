"""Mock-only scene tests. No network, backend startup or model execution."""
import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

from character_scene import prepare_scene, create_scene, prepare_qwen_graph
from comfy_client import ComfyClient, ComfyError, ROOT
import mcp_server
from trpg_style import TRPG_STYLE_PRESET


def fixture_png():
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\x00\x00\x00')) + chunk(b'IEND', b''))


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'example-id'
        self.folder.mkdir()
        self.reference = self.folder / 'reference_v1.png'
        self.data = fixture_png()
        self.reference.write_bytes(self.data)
        self.profile = {'id': 'example-id', 'folder_name': 'example-id',
                        'reference_images': [str(self.reference)], 'appearance': 'example'}

    def test_official_reference_is_not_list_order(self):
        previous = self.folder / 'previous.png'; previous.write_bytes(self.data)
        self.profile['reference_images'].insert(0, str(previous))
        prepared = prepare_scene(self.profile, self.root, 'Sitting on a terrace')
        self.assertEqual(prepared[1], self.reference)
        self.assertEqual(prepared[5]['official_reference_sha256'], hashlib.sha256(self.data).hexdigest())
        self.assertNotIn('scene:pose', prepared[0])
        self.assertEqual(prepared[0]['459:456']['inputs']['width'], 1024)
        self.assertNotIn('images.image_2', prepared[0]['459:474']['inputs'])
        self.assertEqual(prepared[0]['459:474']['inputs']['images.image_1'], ['470', 0])

    def test_explicit_scene_style_overrides_reference_style_in_prompt(self):
        request = 'At a harbor. Use a photorealistic 3D render style.'
        prepared = prepare_scene(self.profile, self.root, request)
        self.assertIn(request, prepared[4])
        self.assertIn('including its exclusions and any reference-image style preservation', prepared[4])
        self.assertEqual(prepared[5]['scene_description'], request)
        self.assertEqual(self.reference.read_bytes(), self.data)
        self.assertNotIn('default_style', self.profile)

    def test_reject_unregistered_or_wrong_folder_or_invalid_png(self):
        outside = self.root / 'reference_v1.png'; outside.write_bytes(self.data)
        for refs in ([], [str(outside)], [str(self.folder / 'previous.png')]):
            with self.subTest(refs=refs):
                self.profile['reference_images'] = refs
                with self.assertRaises(ComfyError):
                    prepare_scene(self.profile, self.root, 'Scene')
        self.profile['reference_images'] = [str(self.reference)]
        self.reference.write_bytes(b'not png')
        with self.assertRaisesRegex(ComfyError, 'Invalid reference PNG'):
            prepare_scene(self.profile, self.root, 'Scene')

    def test_pose_is_connected_as_second_qwen_image_only_when_given(self):
        pose = self.root / 'pose.png'; pose.write_bytes(self.data)
        graph, *rest = prepare_scene(self.profile, self.root, 'Seated', pose)
        self.assertEqual(graph['459:474']['inputs']['images.image_1'], ['470', 0])
        self.assertEqual(graph['459:474']['inputs']['images.image_2'], ['scene:pose', 0])
        with self.assertRaisesRegex(ComfyError, 'not found'):
            prepare_scene(self.profile, self.root, 'Scene', self.root/'missing.png')

    def test_single_submission_and_png_json_provenance(self):
        prepared = prepare_scene(self.profile, self.root, 'A calm woman seated on a terrace')
        client = ComfyClient('http://127.0.0.1:8188')
        entry = {'outputs': {'9': {'images': [{'filename': 'scene.png', 'type': 'output'}]}}}
        with patch.object(client, 'check'), patch.object(client, 'upload_image', return_value='uploaded.png') as upload, patch.object(client, 'wait', return_value=entry), patch.object(client, 'request', side_effect=[{'prompt_id': 'scene-id'}, self.data]) as request:
            result = create_scene(client, prepared, self.root, 10, 42, 'example-id')
        self.assertEqual(upload.call_args.kwargs['image_bytes'], self.data)
        self.assertEqual(sum(call.args[0] == '/prompt' for call in request.call_args_list), 1)
        metadata = json.loads(Path(result['metadata_file']).read_text(encoding='utf-8'))
        self.assertEqual(metadata['operation'], 'create_character_scene')
        self.assertEqual(metadata['official_reference_image'], str(self.reference))
        self.assertEqual(metadata['engine'], 'Qwen Image 2.1')
        self.assertEqual(metadata['seed'], 42)
        sent = request.call_args_list[0].args[1]['prompt']
        self.assertEqual(sent['470']['inputs']['image'], 'uploaded.png')
        self.assertEqual(sent['459:474']['inputs']['prompt'], metadata['prompt'])
        self.assertIn(TRPG_STYLE_PRESET, metadata['prompt'])
        self.assertEqual(sent['459:458']['inputs']['seed'], 42)
        self.assertEqual(metadata['workflow'], sent)
        self.assertEqual({m['filename'] for m in metadata['models']}, {
            'qwen_image_2.1_int8_convrot.safetensors',
            'qwen3vl_8b_int8_convrot.safetensors',
            'qwen_image_2.1_vae_bf16.safetensors'})
        upload.assert_called_once()
        png = Path(result['files'][0]).read_bytes()
        offset, embedded = 8, None
        while offset < len(png):
            size = struct.unpack('>I', png[offset:offset+4])[0]
            if png[offset+4:offset+8] == b'iTXt':
                payload = png[offset+8:offset+8+size]
                if payload.startswith(b'character_scene\0'):
                    embedded = json.loads(payload.split(b'\0', 5)[5])
            offset += size + 12
        self.assertEqual(embedded['official_reference_sha256'], metadata['official_reference_sha256'])
        self.assertEqual(embedded['prompt'], metadata['prompt'])
        self.assertEqual(embedded['operation'], metadata['operation'])
        self.assertEqual(self.reference.read_bytes(), self.data)
        self.assertIn('scenes', Path(result['files'][0]).parts)

    def test_template_samples_and_enhancer_are_removed_without_changing_file(self):
        path = ROOT / 'workflows/qwen21_character_scene_api.json'
        original = path.read_bytes()
        template = json.loads(original)
        template['sample'] = {'class_type': 'LoadImage', 'inputs': {'image': 'sample.png'}}
        template['459:474']['inputs']['images.image_2'] = ['sample', 0]
        template['459:474']['inputs']['images.image_3'] = ['sample', 0]
        with patch('character_scene.load_workflow', return_value=template):
            graph = prepare_qwen_graph()
        self.assertNotIn('sample', graph)
        self.assertNotIn('459:500', graph)
        self.assertNotIn('459:477', graph)
        self.assertNotIn('472', graph)
        self.assertEqual(graph['459:474']['inputs']['prompt'], '')
        self.assertEqual([f for f in graph['459:474']['inputs'] if f.startswith('images.')], ['images.image_1'])
        self.assertEqual(path.read_bytes(), original)

    def test_wrong_qwen_node_fails_closed(self):
        template = json.loads((ROOT / 'workflows/qwen21_character_scene_api.json').read_text(encoding='utf-8'))
        template['459:474']['class_type'] = 'CLIPTextEncode'
        with patch('character_scene.load_workflow', return_value=template):
            with self.assertRaisesRegex(ComfyError, 'binding mismatch'):
                prepare_scene(self.profile, self.root, 'Scene')

    def test_pose_uploads_validated_bytes_and_timeout_does_not_resubmit(self):
        pose = self.root / 'pose.png'; pose.write_bytes(self.data)
        prepared = prepare_scene(self.profile, self.root, 'Seated', pose)
        client = Mock()
        client.upload_image.side_effect = ['official-upload.png', 'pose-upload.png']
        client.request.return_value = {'prompt_id': 'qwen-pending'}
        client.wait.side_effect = ComfyError('timeout qwen-pending')
        with self.assertRaisesRegex(ComfyError, 'qwen-pending'):
            create_scene(client, prepared, self.root, 10, 42, 'example-id')
        client.request.assert_called_once()
        client.save.assert_not_called()
        self.assertEqual(client.upload_image.call_count, 2)
        self.assertEqual(client.upload_image.call_args.kwargs['image_bytes'], self.data)
        sent = client.request.call_args.args[1]['prompt']
        self.assertEqual(sent['470']['inputs']['image'], 'official-upload.png')
        self.assertEqual(sent['scene:pose']['inputs']['image'], 'pose-upload.png')

    def test_missing_qwen_model_never_uploads_or_submits(self):
        prepared = prepare_scene(self.profile, self.root, 'Scene')
        client = Mock()
        client.check.side_effect = ComfyError('Qwen model missing')
        with self.assertRaisesRegex(ComfyError, 'Qwen model missing'):
            create_scene(client, prepared, self.root, 10, 42, 'example-id')
        client.upload_image.assert_not_called()
        client.request.assert_not_called()

    def test_adapter_alias_and_legacy_settings_without_scene_config(self):
        config = {'base_url': 'http://127.0.0.1:8188', 'output_dir': str(self.root)}
        store = Mock(); store.get.return_value = self.profile
        with patch.object(mcp_server.Path, 'read_text', return_value=json.dumps(config)), patch.object(mcp_server, 'character_store', return_value=store), patch.object(mcp_server, 'prepare_scene', return_value='prepared'), patch.object(mcp_server, 'ensure_backend') as start, patch.object(mcp_server, 'create_scene', return_value={'ok': True}) as create:
            self.assertEqual(mcp_server.create_character_scene_sync('alias', 'English scene', 42, None), {'ok': True})
            store.get.assert_called_once_with('alias')
            self.assertEqual(create.call_args.args[-1], 'example-id')
            start.assert_called_once()
        self.assertFalse(mcp_server.generation_lock.locked())

    def test_invalid_official_reference_never_starts_backend(self):
        config = {'output_dir': str(self.root)}
        store = Mock(); store.get.return_value = {**self.profile, 'reference_images': []}
        with patch.object(mcp_server.Path, 'read_text', return_value=json.dumps(config)), patch.object(mcp_server, 'character_store', return_value=store), patch.object(mcp_server, 'ensure_backend') as start:
            with self.assertRaises(ComfyError):
                mcp_server.create_character_scene_sync('alias', 'Scene', None, None)
            start.assert_not_called()
        self.assertFalse(mcp_server.generation_lock.locked())
