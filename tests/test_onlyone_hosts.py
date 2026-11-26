import unittest

from onlyone_hosts import ONLYONE_HOST, is_onlyone_host


class OnlyOneHostTests(unittest.TestCase):
    def test_hub_and_subdomains_are_trusted(self):
        for host in (ONLYONE_HOST, 'S2.' + ONLYONE_HOST, 'a.b.' + ONLYONE_HOST):
            with self.subTest(host=host):
                self.assertTrue(is_onlyone_host(host))

    def test_lookalikes_and_old_domain_are_rejected(self):
        for host in ('onlyoneseedbox.opik.net', ONLYONE_HOST + '.evil.test', 'evil.example', ''):
            with self.subTest(host=host):
                self.assertFalse(is_onlyone_host(host))


if __name__ == '__main__':
    unittest.main()
