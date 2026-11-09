"""Mock-only T2I and MCP tests: no backend, network or GPU execution."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import mcp_server
from character_draft import prepare_draft, create_draft
from trpg_style import apply_trpg_style, TRPG_STYLE_PRESET
from comfy_client import ComfyClient, ComfyError, ROOT, load_workflow


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = {"name": "새 캐릭터", "appearance": "금발 남성"}
        self.prompt = "A blond man in a watercolor illustration."

    def prepared(self):
        return prepare_draft(self.settings, self.prompt, 42)

    def test_real_graph_bindings_and_template_preserved(self):
        path = ROOT / 'workflows/qwen21_text_to_image_api.json'
        original = path.read_bytes()
        graph, settings, prompt, seed = self.prepared()
        self.assertEqual(graph['459:452']['inputs']['prompt'], apply_trpg_style(self.prompt))
        self.assertEqual(graph['459:452']['inputs']['negative_prompt'], '')
        self.assertEqual(graph['459:458']['inputs']['seed'], 42)
        self.assertEqual(graph['459:456']['inputs']['batch_size'], 1)
        self.assertEqual(graph['13']['inputs'], json.loads(original)['13']['inputs'])
        self.assertEqual(len(graph), 10)
        self.assertFalse(any(n['class_type'] in ('LoadImage', 'TextGenerate', 'ComfySwitchNode', 'PreviewAny') for n in graph.values()))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(settings, self.settings)
        self.assertIsNot(settings, self.settings)

    def test_sparse_draft_submits_shared_style_without_changing_settings(self):
        settings = {'purpose': 'TRPG character'}
        graph, saved, prompt, _ = prepare_draft(settings, 'An unnamed character', 42)
        self.assertEqual(saved, settings)
        self.assertIn(TRPG_STYLE_PRESET, graph['459:452']['inputs']['prompt'])
        self.assertEqual(graph['459:452']['inputs']['prompt'], prompt)
        self.assertIn('photorealistic', prompt)
        self.assertIn('3D render', prompt)
        self.assertIn('realistic game character', prompt)
        self.assertIn('flat cel shading', prompt)
        for feature in ('fine intricate refined linework', 'finely rendered character detail',
                        'natural color transitions', 'natural painterly shading',
                        'dimensional lighting', 'delicate textures in hair, irises and fabric',
                        'thick black outlines', 'rough sketch textures'):
            self.assertIn(feature, prompt)


    def test_explicit_style_remains_authoritative_without_negative_conditioning(self):
        for style in ('watercolor', 'photorealistic', '3D render', 'flat cel shading'):
            request = 'Use ' + style + ' for this character.'
            graph, _, prompt, _ = prepare_draft(self.settings, request, 42)
            self.assertTrue(prompt.endswith(request))
            self.assertIn('takes priority over this entire default', prompt)
            self.assertEqual(graph['459:452']['inputs']['negative_prompt'], '')

    def test_invalid_input_or_graph_blocks(self):
        for settings, prompt, seed in [({}, self.prompt, 42), (self.settings, '', 42),
                                      (self.settings, self.prompt, -1), (self.settings, self.prompt, True),
                                      (self.settings, self.prompt, 2**64),
                                      ({'reference_images': ['old.png']}, self.prompt, 42)]:
            with self.subTest(settings=settings, seed=seed), self.assertRaises(ComfyError):
                prepare_draft(settings, prompt, seed)
        graph = load_workflow(ROOT / 'workflows/qwen21_text_to_image_api.json')
        graph['459:452']['class_type'] = 'WrongEncoder'
        with patch('character_draft.load_workflow', return_value=graph), self.assertRaisesRegex(ComfyError, 'binding mismatch'):
            self.prepared()

    def test_single_submission_real_storage_isolated_and_metadata(self):
        entry = {'outputs': {'461': {'images': [{'filename': 'candidate.png', 'type': 'output'}]}}}
        png = b'\x89PNG\r\n\x1a\nmock-image'
        existing = self.root / 'reference_v1.png'
        existing.write_bytes(b'original')
        client = ComfyClient('http://127.0.0.1:8188')
        results = []
        for _ in range(2):
            with patch.object(client, 'check'), patch.object(client, 'wait', return_value=entry), patch.object(client, 'request', side_effect=[{'prompt_id': 'same-server-id'}, png]) as request, patch.object(client, 'upload_image') as upload:
                result = create_draft(client, self.prepared(), self.root)
            results.append(result)
            self.assertEqual(sum(c.args[0] == '/prompt' for c in request.call_args_list), 1)
            upload.assert_not_called()
            self.assertEqual(len(result['files']), 1)
            image = Path(result['files'][0])
            self.assertEqual(image.read_bytes(), png)
            self.assertTrue(image.is_relative_to(self.root / 'drafts' / result['draft_id']))
            data = json.loads(Path(result['metadata_file']).read_text(encoding='utf-8'))
            self.assertEqual(data['character_settings'], self.settings)
            self.assertEqual(data['workflow'], request.call_args_list[0].args[1]['prompt'])
            self.assertEqual(data['prompt'], apply_trpg_style(self.prompt))
            self.assertEqual(data['seed'], 42)
            self.assertEqual(data['status'], 'pending_approval')
            self.assertIsNone(data['character'])
            self.assertEqual(len(data['models']), 3)
            self.assertEqual(data['engine'], 'Qwen Image 2.1 T2I')
        self.assertNotEqual(results[0]['draft_id'], results[1]['draft_id'])
        self.assertEqual(existing.read_bytes(), b'original')
        self.assertFalse((self.root / 'characters').exists())

    def test_collision_preserves_existing_files_before_submission(self):
        folder = self.root / 'drafts' / 'draft-collision'
        folder.mkdir(parents=True)
        sentinel = folder / 'old.json'
        sentinel.write_bytes(b'original')
        client = Mock()
        with patch('character_draft.uuid.uuid4', return_value=Mock(hex='collision')):
            with self.assertRaises(FileExistsError):
                create_draft(client, self.prepared(), self.root)
        client.request.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b'original')

    def test_preflight_timeout_and_submission_errors_never_retry(self):
        for stage in ('check', 'request', 'wait'):
            client = Mock()
            client.request.return_value = {'prompt_id': 'pending-id'}
            getattr(client, stage).side_effect = ComfyError('failure pending-id')
            with self.subTest(stage=stage), self.assertRaisesRegex(ComfyError, 'pending-id'):
                create_draft(client, self.prepared(), self.root)
            self.assertEqual(client.request.call_count, 0 if stage == 'check' else 1)
            client.save.assert_not_called()

    def test_adapter_registration_validation_and_lock(self):
        names = {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}
        self.assertTrue({'create_character_draft', 'create_character_scene', 'edit_image', 'get_character', 'save_character'} <= names)
        with patch.object(mcp_server, 'ensure_backend') as start, patch.object(mcp_server, 'create_draft', return_value={'status': 'pending_approval'}) as create, patch.object(mcp_server, 'character_store') as store:
            result = asyncio.run(mcp_server.create_character_draft(self.settings, self.prompt, 42))
            self.assertEqual(result['status'], 'pending_approval')
            start.assert_called_once()
            create.assert_called_once()
            store.assert_not_called()
        with patch.object(mcp_server, 'ensure_backend') as start:
            with self.assertRaises(ComfyError):
                mcp_server.create_character_draft_sync(self.settings, '', 42)
            start.assert_not_called()
        self.assertFalse(mcp_server.generation_lock.locked())
        mcp_server.generation_lock.acquire()
        try:
            with self.assertRaisesRegex(ComfyError, 'already generating'):
                mcp_server.create_character_draft_sync(self.settings, self.prompt, 42)
        finally:
            mcp_server.generation_lock.release()

    def test_random_seed_and_unexpected_output_count(self):
        with patch('character_draft.secrets.randbelow', return_value=123):
            self.assertEqual(prepare_draft(self.settings, self.prompt)[3], 123)
        client = Mock()
        client.request.return_value = {'prompt_id': 'empty-result'}
        client.wait.return_value = {'outputs': {}}
        with self.assertRaisesRegex(ComfyError, 'Expected one draft image'):
            create_draft(client, self.prepared(), self.root)
        client.request.assert_called_once()
        client.save.assert_not_called()
