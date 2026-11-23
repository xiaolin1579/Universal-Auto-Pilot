import asyncio
import socket
import unittest
from unittest.mock import patch

import telebot.asyncio_helper as telebot_helper

import remote_control as remote


class FakeConnector:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeSession:
    def __init__(self, connector=None):
        self.connector = connector


class TelegramTransportTests(unittest.TestCase):
    def setUp(self):
        self.manager = telebot_helper.session_manager
        self.original_create = self.manager.create_session
        self.original_retry = (telebot_helper.RETRY_ON_ERROR,
                               telebot_helper.MAX_RETRIES,
                               telebot_helper.RETRY_TIMEOUT)
        self.addCleanup(setattr, self.manager, 'create_session', self.original_create)
        self.addCleanup(self.restore_retry)

    def restore_retry(self):
        (telebot_helper.RETRY_ON_ERROR,
         telebot_helper.MAX_RETRIES,
         telebot_helper.RETRY_TIMEOUT) = self.original_retry

    def test_configures_ipv4_only_session_and_transient_retries(self):
        remote.configure_telegram_transport()
        self.assertTrue(telebot_helper.RETRY_ON_ERROR)
        self.assertEqual(telebot_helper.MAX_RETRIES, 3)
        self.assertEqual(telebot_helper.RETRY_TIMEOUT, 2)

        with patch.object(remote.aiohttp, 'TCPConnector', FakeConnector), \
                patch.object(remote.aiohttp, 'ClientSession', FakeSession):
            session = asyncio.run(self.manager.create_session())

        self.assertEqual(session.connector.kwargs['family'], socket.AF_INET)
        self.assertEqual(session.connector.kwargs['limit'], telebot_helper.REQUEST_LIMIT)
        self.assertIs(session.connector.kwargs['ssl'], self.manager.ssl_context)
        self.assertIs(self.manager.session, session)


if __name__ == '__main__':
    unittest.main()
