import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'is_production/production/controllers'))
import hod_schedule_renderer as renderer


class RendererTests(unittest.TestCase):
    def test_asset_resolver_supports_installed_asset_symlinks_and_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            bench = Path(tmp)
            public = bench/'apps/is_production/is_production/public'
            public.mkdir(parents=True)
            assets = bench/'sites/assets'
            assets.mkdir(parents=True)
            (assets/'is_production').symlink_to(public, target_is_directory=True)
            image = public/'excavator (1).png'
            image.write_bytes(b'image')
            self.assertEqual(renderer.asset_path(bench, '/assets/is_production/excavator%20(1).png'), image)

    def test_external_or_traversal_assets_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for url in ['/private/files/report.png','/assets/../../etc/passwd','/assets/%2e%2e/etc/passwd']:
                with self.assertRaises(ValueError): renderer.asset_path(Path(tmp), url)

    def test_symlink_outside_bench_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            bench=Path(tmp)
            (bench/'sites/assets').mkdir(parents=True)
            secret=Path(outside)/'secret'
            secret.write_text('not an asset')
            (bench/'sites/assets/escape').symlink_to(secret)
            with self.assertRaises(ValueError): renderer.asset_path(bench,'/assets/escape')

    def test_empty_export_rejected(self):
        with self.assertRaises(ValueError): renderer.capture([],Path('/missing'),Path('/missing'))


if __name__ == '__main__': unittest.main()
