import inspect
import unittest

from upload_sites import BrowserSite


class DuplicateBudgetTests(unittest.TestCase):
    def test_default_budget_is_high_enough_for_full_search(self):
        params = inspect.signature(BrowserSite.check_duplicates).parameters
        self.assertEqual(params['max_pages'].default, 100)
        self.assertEqual(params['max_candidates'].default, 200)
