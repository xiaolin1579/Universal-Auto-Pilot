import unittest
from http.cookiejar import Cookie
from urllib.parse import urlsplit

import main
from onlyone_hosts import ONLYONE_HOST

HUB = "https://" + ONLYONE_HOST
NODE = "https://s2." + ONLYONE_HOST
ACCOUNT = "/xiaolin1579"
QBIT = ACCOUNT + "/qbittorrent"
LOGIN_HTML = (
    '<html><form method="post" action="/login.php">'
    '<input name="_csrf" value="test-csrf">'
    '<input name="next" value="/xiaolin1579/">'
    "</form></html>"
)


def add_cookie(jar, name, domain):
    jar.set_cookie(Cookie(0, name, "", None, False, domain, True, False,
                          "/", True, False, None, False, None, None, {}))


class Response:
    def __init__(self, url, status_code=200, location=None, text="", content_type=None):
        self.url = url
        self.status_code = status_code
        self.headers = {}
        if location:
            self.headers["Location"] = location
        if content_type:
            self.headers["Content-Type"] = content_type
        self.text = text


class ProviderFlow:
    def __init__(self, node, hub_first):
        self.node = node
        self.hub_first = hub_first
        self.logged_in = False
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url))
        p = urlsplit(url)
        host = p.hostname or ""
        path = p.path

        if method == "GET" and host.endswith(ONLYONE_HOST) and path == "/login.php":
            if host == ONLYONE_HOST and self.logged_in:
                return Response(
                    url, 303,
                    NODE + "/_sso?t=test-token&next=%2Fxiaolin1579%2F",
                )
            if host == ONLYONE_HOST:
                add_cookie(self.node.s.cookies, "oos_sid", ONLYONE_HOST)
                return Response(url, text=LOGIN_HTML,
                                content_type="text/html; charset=utf-8")
            return Response(
                url, 302,
                HUB + "/login.php?next=%2Fxiaolin1579%2F",
            )

        if method == "GET" and host.endswith(ONLYONE_HOST) and path == "/_hub/login.php":
            return Response(url, 302, HUB + "/login.php?next=%2Fxiaolin1579%2F")

        if method == "POST" and host == ONLYONE_HOST and path == "/login.php":
            self.logged_in = True
            if self.hub_first:
                return Response(url, 303, "/account.php")
            return Response(url, 303, NODE + ACCOUNT + "/")

        if method == "GET" and host == ONLYONE_HOST and path == "/account.php":
            return Response(url, text="<html>account</html>")

        if method == "GET" and host.endswith(ONLYONE_HOST) and path == ACCOUNT + "/":
            if (not self.hub_first
                    or any(c.name == "oos_node" for c in self.node.s.cookies)):
                return Response(url, 200, text="<html>dashboard</html>")
            return Response(
                url, 302,
                HUB + "/login.php?next=%2Fxiaolin1579%2F",
            )

        if method == "GET" and host.endswith(ONLYONE_HOST) and path == "/_sso":
            add_cookie(self.node.s.cookies, "oos_node", "s2." + ONLYONE_HOST)
            return Response(url, 302, ACCOUNT + "/")

        if method == "GET" and host.endswith(ONLYONE_HOST) and path == ACCOUNT + "/go/qbittorrent":
            add_cookie(self.node.s.cookies, "SID", "s2." + ONLYONE_HOST)
            return Response(url, 302, QBIT + "/")

        if method == "GET" and host.endswith(ONLYONE_HOST) and path.startswith(QBIT):
            if path.endswith("/api/v2/app/version"):
                return Response(url, text="v5.0.5")
            return Response(url, text="<html>qBittorrent</html>")

        raise AssertionError("Unexpected SSO request: " + method + " " + url)


class OnlyOneSSOTests(unittest.TestCase):
    def node(self, hub_first):
        node = main.QbitNode({
            "name": "OnlyOneSeedbox",
            "url": NODE + QBIT + "/",
            "qb_user": "",
            "qb_pass": "",
            "provider_sso": True,
            "provider_user": "test-user",
            "provider_pass": "test-pass",
            "provider_auth_origin": HUB,
        })
        node._execute_request = ProviderFlow(node, hub_first)
        return node

    def test_central_hub_hands_off_to_node(self):
        node = self.node(hub_first=True)

        self.assertTrue(node.login())

        cookies = {c.name for c in node.s.cookies}
        self.assertTrue({"oos_sid", "oos_node", "SID"}.issubset(cookies))
        urls = [url for _, url in node._execute_request.calls]
        self.assertTrue(any(url.startswith(HUB + "/login.php") for url in urls))
        self.assertTrue(any(url.startswith(NODE + ACCOUNT + "/") for url in urls))
        self.assertTrue(any(url.startswith(NODE + ACCOUNT + "/go/qbittorrent") for url in urls))

    def test_legacy_node_login_still_works(self):
        node = self.node(hub_first=False)

        self.assertTrue(node.login())

        cookies = {c.name for c in node.s.cookies}
        self.assertTrue({"oos_sid", "SID"}.issubset(cookies))
        self.assertFalse(any(c.name == "oos_node" for c in node.s.cookies))


if __name__ == "__main__":
    unittest.main()
