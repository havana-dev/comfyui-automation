import tempfile
import unittest
from pathlib import Path
from character_store import CharacterStore
from comfy_client import ROOT, character_folder, generate_image, load_workflow
from unittest.mock import Mock, patch


class CharacterTests(unittest.TestCase):
    def test_save_resolves_supplied_names_and_rejects_conflicts(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            store = CharacterStore(Path(tmp) / 'characters', Path(tmp) / 'images')
            original = store.save('예시 캐릭터', name_ko='예시 캐릭터', name_en='Example Character')
            updated = store.save('예시', name_en='Example Character', aliases=['예시', 'Example'])
            self.assertEqual(original['id'], updated['id'])
            for name in ['예시', '예시 캐릭터', 'Example', 'Example Character', original['id']]:
                self.assertEqual(store.get(name)['id'], original['id'])
            store.save('Other')
            with self.assertRaises(ValueError):
                store.save('Other', name_en='Example Character')
            self.assertEqual(store.get('Other')['aliases'], ['Other'])

    def test_saved_aliases_and_generation_destination_without_submission(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp, patch('comfy_client.ROOT', Path(tmp)):
            store = CharacterStore(Path(tmp) / 'characters', Path(tmp) / 'images')
            profile = store.save('예시 캐릭터', name_ko='예시 캐릭터', name_en='Example Character', aliases=['예시', 'Example'])
            expected_id = profile['id']
            workflow = load_workflow(ROOT / 'workflows/z_image_turbo_api.json')
            for name in ['예시', '예시 캐릭터', 'Example', 'Example Character', expected_id]:
                with self.subTest(name=name):
                    self.assertEqual(store.get(name)['id'], expected_id)
                    expected = Path(store.output_dir) / expected_id
                    self.assertEqual(character_folder(store.output_dir, name), expected)
                    client = Mock()
                    with patch('comfy_client.prepare_output', side_effect=RuntimeError('stop before submission')) as prepare:
                        with self.assertRaisesRegex(RuntimeError, 'stop before submission'):
                            generate_image(client, workflow, 'test', store.output_dir, seed=42, character=name)
                    prepare.assert_called_once_with(expected)
                    client.request.assert_not_called()
                    client.save.assert_not_called()
            self.assertEqual(character_folder(store.output_dir), Path(store.output_dir))
            self.assertEqual(character_folder(store.output_dir, None, 'trpg/london'), Path(store.output_dir) / 'trpg/london')
            self.assertEqual(character_folder(store.output_dir, 'Unregistered', 'trpg/london'), Path(store.output_dir) / 'trpg/london/Unregistered')
            self.assertEqual(character_folder(store.output_dir, 'Example', 'trpg/london'), Path(store.output_dir) / 'trpg/london' / expected_id)

    def test_memory_and_partial_update(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            store = CharacterStore(Path(tmp) / 'characters', Path(tmp) / 'images')
            self.assertIsNone(store.get('예시 캐릭터'))
            p = store.save('예시 캐릭터', name_ko='예시 캐릭터', name_en='Example Character', aliases=['Sample'])
            self.assertIsNone(p['appearance'])
            self.assertIsNone(p['age'])
            self.assertTrue(Path(p['image_dir']).is_dir())
            self.assertEqual(store.get(' EXAMPLE CHARACTER '), p)
            updated = store.save('Sample', appearance='User supplied appearance', reference_images=['missing.png'])
            self.assertEqual(updated['id'], p['id'])
            self.assertEqual(store.get('예시 캐릭터')['reference_images'], ['missing.png'])
            self.assertEqual(len(list(store.root.glob('*.json'))), 1)
            self.assertEqual(list(Path(p['image_dir']).iterdir()), [])

    def test_collisions_and_safe_paths(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            store = CharacterStore(Path(tmp) / 'characters', Path(tmp) / 'images')
            store.save('First', aliases=['one'])
            store.save('Second')
            with self.assertRaises(ValueError):
                store.save('Second', aliases=['ONE'])
            p = store.save('../outside')
            self.assertTrue(Path(p['image_dir']).is_relative_to(Path(tmp) / 'images'))
            with self.assertRaises(ValueError):
                store.save('Invalid', aliases='string')
            with self.assertRaises(ValueError):
                store.get(' ')

if __name__ == '__main__':
    unittest.main()
