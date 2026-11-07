import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from transc_service.config import ServerConfig
from transc_service.model_health import model_availability


class ModelHealthTests(unittest.TestCase):
    def test_configured_available_missing_and_uncached(self):
        with tempfile.TemporaryDirectory() as directory:
            weights = Path(directory) / 'model.safetensors'
            weights.write_bytes(b'weights')
            weights.with_name('config.json').write_text(json.dumps({
                'dim': 1024, 'num_heads': 16, 'num_layers': 24, 'card': 1395,
            }))
            config = ServerConfig(weights_medium=str(weights), weights_small='',
                                  weights_large=str(Path(directory) / 'missing.safetensors'))
            with patch('transc_service.model_health._cached_file', return_value=None):
                result = model_availability(config)
            models = {model['size']: model for model in result['models']}
            self.assertEqual(models['medium']['status'], 'available')
            self.assertEqual(models['medium']['source'], 'configured')
            self.assertEqual(models['large']['status'], 'unavailable')
            self.assertEqual(models['small']['status'], 'download_required')

    def test_corrupt_config_or_empty_weights_are_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            weights = Path(directory) / 'model.safetensors'
            weights.write_bytes(b'weights')
            config_file = weights.with_name('config.json')
            settings = ServerConfig(weights_medium=str(weights))
            for content in ('not json', '{}', '[]'):
                config_file.write_text(content)
                with patch('transc_service.model_health._cached_file', return_value=None):
                    result = model_availability(settings)
                self.assertEqual(result['models'][1]['status'], 'unavailable')
            weights.write_bytes(b'')
            with patch('transc_service.model_health._cached_file', return_value=None):
                result = model_availability(settings)
            self.assertIn('empty', result['models'][1]['message'])

    def test_cached_files_are_checked_without_downloading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model.safetensors').write_bytes(b'weights')
            (root / 'config.json').write_text(json.dumps({'dim': 1, 'num_heads': 1, 'num_layers': 1, 'card': 1}))
            with patch('transc_service.model_health._cached_file', side_effect=lambda size, name: root / name):
                result = model_availability(ServerConfig(weights_small='', weights_medium='', weights_large=''))
            self.assertTrue(all(model['status'] == 'available' for model in result['models']))
            self.assertTrue(all(model['source'] == 'huggingface_cache' for model in result['models']))
