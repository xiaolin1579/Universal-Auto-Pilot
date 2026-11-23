import contextlib
import io
import os
import shutil
import unittest
from unittest.mock import AsyncMock, patch

import main as app


class FakeConfig:
    def __init__(self):
        self.args = []
        self.user_data_dir = None
        self.headless = None
        self.sandbox = None
        self.browser_executable_path = None

    def add_argument(self, arg):
        self.args.append(arg)


class FakeBrowser:
    def __init__(self, config):
        self.config = config
        self.started = False

    async def start(self):
        self.started = True


class BrowserMemoryTests(unittest.TestCase):
    def test_default_args_are_tuned_for_low_memory_and_no_images(self):
        args = app.build_browser_performance_args()
        self.assertIn('--blink-settings=imagesEnabled=false', args)
        self.assertIn('--js-flags=--max-old-space-size=256', args)
        self.assertIn('--process-per-site', args)
        self.assertIn('--renderer-process-limit=2', args)
        self.assertIn('--disable-accelerated-2d-canvas', args)
        self.assertIn('--disable-accelerated-video-decode', args)
        disable_features = [a for a in args if a.startswith('--disable-features=')]
        self.assertEqual(len(disable_features), 1)
        for feature in ('VizDisplayCompositor', 'IsolateOrigins',
                        'site-per-process', 'SiteIsolationTrials'):
            self.assertIn(feature, disable_features[0])
        self.assertNotIn('--js-flags=--max-old-space-size=512', args)

    def test_upload_args_keep_images_enabled(self):
        args = app.build_browser_performance_args(enable_images=True)
        self.assertIn('--blink-settings=imagesEnabled=true', args)
        self.assertNotIn('--blink-settings=imagesEnabled=false', args)

    def test_settings_can_raise_limits_when_a_site_needs_headroom(self):
        config = {'SETTING': {'browser_js_heap_mb': 384,
                              'browser_renderer_process_limit': 3}}
        with patch.object(app, 'CFG', config):
            args = app.build_browser_performance_args()
        self.assertIn('--js-flags=--max-old-space-size=384', args)
        self.assertIn('--renderer-process-limit=3', args)


class BrowserLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_launch_applies_memory_args_before_custom_args(self):
        profile = os.path.abspath('./profiles/test_browser_memory_uc_profile')
        shutil.rmtree(profile, ignore_errors=True)
        self.addCleanup(lambda: shutil.rmtree(profile, ignore_errors=True))

        patches = (
            patch.object(app, '_active_browser_instance', None),
            patch.object(app, '_current_profile_path', None),
            patch.object(app, '_global_display', None),
            patch.object(app, 'kill_specific_browser', return_value=True),
            patch.object(app, 'kill_xvfb'),
            patch.object(app, 'cleanup_profile', new=AsyncMock(return_value=True)),
            patch.object(app, 'load_xvfb_config', return_value=False),
            patch.object(app, 'get_browser_path_or_fail', return_value='/usr/bin/chrome'),
            patch.object(app.uc, 'Config', FakeConfig),
            patch('nodriver.core.browser.Browser', FakeBrowser),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        with contextlib.redirect_stdout(io.StringIO()):
            browser = await app.launch_any_browser('test_browser_memory', ['--custom=1'])
        self.assertTrue(browser.started)
        args = browser.config.args
        self.assertEqual(args[-1], '--custom=1')
        self.assertIn('--js-flags=--max-old-space-size=256', args)
        self.assertIn('--process-per-site', args)
        self.assertIn('--renderer-process-limit=2', args)


if __name__ == '__main__':
    unittest.main()
