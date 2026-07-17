import unittest
from unittest.mock import patch

import grok_register_ttk as app


class FakeStates:
    def __init__(self, alive=True):
        self.is_alive = alive


class FakeTab:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, tabs=None, alive=True, process_id=None):
        self.tabs = list(tabs or [])
        self.states = FakeStates(alive)
        self.quit_count = 0
        self.quit_kwargs = []
        self.user_data_path = None
        self.process_id = process_id

    def get_tabs(self):
        if not self.states.is_alive:
            raise RuntimeError("browser closed")
        return self.tabs

    def new_tab(self):
        tab = FakeTab()
        self.tabs.append(tab)
        return tab

    def quit(self, timeout=5, force=False, del_data=True):
        self.quit_count += 1
        self.quit_kwargs.append(
            {
                "timeout": timeout,
                "force": force,
                "del_data": del_data,
            }
        )
        if force:
            self.states.is_alive = False


class StickyBrowser(FakeBrowser):
    """quit() never flips is_alive; requires force-kill by pid."""

    def quit(self, timeout=5, force=False, del_data=True):
        self.quit_count += 1
        self.quit_kwargs.append(
            {
                "timeout": timeout,
                "force": force,
                "del_data": del_data,
            }
        )


class BrowserLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.original_browser = app._get_browser()
        self.original_page = app._get_page()
        self.original_wait = app.config.get("browser_shutdown_wait_sec")
        app.config["browser_shutdown_wait_sec"] = 0.05
        app._set_browser(None)
        app._set_page(None)

    def tearDown(self):
        app._set_browser(self.original_browser)
        app._set_page(self.original_page)
        if self.original_wait is None:
            app.config.pop("browser_shutdown_wait_sec", None)
        else:
            app.config["browser_shutdown_wait_sec"] = self.original_wait

    def test_start_closes_existing_browser_and_creates_a_fresh_instance(self):
        old = FakeBrowser([FakeTab()])
        new = FakeBrowser([FakeTab()])
        app._set_browser(old)

        with patch.object(app, "create_browser_options", return_value=object()), patch.object(app, "Chromium", return_value=new) as chromium:
            result_browser, _ = app.start_browser()

        self.assertEqual(old.quit_count, 1)
        self.assertTrue(old.quit_kwargs[0]["force"])
        chromium.assert_called_once()
        self.assertIs(result_browser, new)

    def test_restart_quits_old_browser_and_starts_exactly_one_new_instance(self):
        old = FakeBrowser([FakeTab()])
        new = FakeBrowser([FakeTab()])
        app._set_browser(old)

        with patch.object(app, "create_browser_options", return_value=object()), patch.object(app, "Chromium", return_value=new) as chromium:
            result_browser, _ = app.restart_browser()

        self.assertEqual(old.quit_count, 1)
        self.assertTrue(old.quit_kwargs[0]["force"])
        chromium.assert_called_once()
        self.assertIs(result_browser, new)

    def test_after_attempt_only_closes_and_does_not_prestart_next_browser(self):
        app._set_browser(FakeBrowser([FakeTab()]))
        with patch.object(app, "stop_browser") as stop, patch.object(app, "start_browser") as start:
            app._close_browser_after_attempt(attempts=3, restart_every=1)
        stop.assert_called_once()
        start.assert_not_called()

    def test_stop_force_kills_when_browser_process_stays_alive(self):
        old = StickyBrowser([FakeTab()], process_id=424242)
        app._set_browser(old)
        with patch.object(app, "_pid_is_running", side_effect=[True, False]), patch.object(app, "_force_kill_pid_tree", return_value=True) as kill, patch.object(app, "cleanup_orphan_browsers", return_value={"ok": True, "killed_count": 0}):
            app.stop_browser()
        self.assertEqual(old.quit_count, 1)
        self.assertTrue(old.quit_kwargs[0]["force"])
        kill.assert_called_once()
        self.assertEqual(kill.call_args.args[0], 424242)
        self.assertIsNone(app._get_browser())

    def test_cleanup_runtime_memory_shuts_down_mint_browsers(self):
        with patch.object(app, "stop_browser") as stop, patch("cpa_xai.browser_confirm.shutdown_mint_browsers") as shutdown, patch.object(app, "cleanup_orphan_browsers", return_value={"ok": True}) as orphan, patch.object(app.gc, "collect", return_value=0):
            app.cleanup_runtime_memory()
        stop.assert_called_once()
        shutdown.assert_called_once()
        orphan.assert_called_once()


    def test_finalize_all_browsers_cleans_orphans(self):
        with patch.object(app, "stop_browser") as stop, patch("cpa_xai.browser_confirm.shutdown_mint_browsers") as shutdown, patch.object(app, "cleanup_orphan_browsers", return_value={"ok": True, "killed_count": 1}) as orphan:
            app.finalize_all_browsers()
        stop.assert_called_once()
        shutdown.assert_called_once()
        orphan.assert_called_once()


if __name__ == "__main__":

    unittest.main()
