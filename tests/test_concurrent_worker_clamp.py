# -*- coding: utf-8 -*-
import ast
import os
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "grok_register_ttk.py")


def _extract_function_source(path, func_name):
    src = open(path, "r", encoding="utf-8").read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(src, node)
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == func_name:
                    return ast.get_source_segment(src, child)
    raise AssertionError(f"function not found: {func_name}")


class ConcurrentWorkerClampTests(unittest.TestCase):
    def test_source_clamps_workers_to_target_count(self):
        src = open(MAIN, "r", encoding="utf-8").read()
        self.assertIn(
            "concurrent = min(concurrent, max(1, int(count or 1)))",
            src,
        )
        self.assertIn(
            "worker_count = min(worker_count, max(1, int(count or 1)))",
            src,
        )
        self.assertIn(
            "worker_count = min(max(1, int(worker_count or 1)), max(1, int(total_count or 1)))",
            src,
        )

    def test_clamp_math(self):
        # Mirror production formula used by GUI/CLI/worker launcher.
        def clamp(configured, target):
            n = max(1, int(configured or 1))
            return min(n, max(1, int(target or 1)))

        self.assertEqual(clamp(10, 1), 1)
        self.assertEqual(clamp(10, 3), 3)
        self.assertEqual(clamp(2, 10), 2)
        self.assertEqual(clamp(0, 5), 1)
        self.assertEqual(clamp(5, 0), 1)

    def test_run_concurrent_workers_has_internal_clamp(self):
        fn = _extract_function_source(MAIN, "_run_concurrent_workers")
        self.assertIn("worker_count = min(max(1, int(worker_count or 1)), max(1, int(total_count or 1)))", fn)




class FinalizeBrowsersHookTests(unittest.TestCase):
    def test_gui_and_helper_call_finalize(self):
        src = open(MAIN, "r", encoding="utf-8").read()
        self.assertIn("def finalize_all_browsers(", src)
        self.assertIn("finalize_all_browsers(log_callback=self.log", src)
        # mint cleanup must be invoked from finalize helper
        helper_start = src.find("def finalize_all_browsers(")
        helper_end = src.find("\ndef _wait_cpa_async_threads", helper_start)
        helper = src[helper_start:helper_end]
        self.assertIn("shutdown_mint_browsers", helper)
        self.assertIn("stop_browser", helper)


if __name__ == "__main__":
    unittest.main()
