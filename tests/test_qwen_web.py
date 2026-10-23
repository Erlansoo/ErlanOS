"""Contract and network-boundary checks for the read-only local web tools."""
import importlib.util
import json
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "qwen_web.py"
spec = importlib.util.spec_from_file_location("qwen_web", SOURCE)
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class WebToolsTests(unittest.TestCase):
    def test_private_and_mixed_dns_are_blocked(self):
        for addresses in (["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"],
                          ["::1"], ["::ffff:127.0.0.1"], ["8.8.8.8", "192.168.0.1"]):
            answers = [(socket.AF_INET6 if ":" in ip else socket.AF_INET,
                        socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses]
            with self.subTest(addresses=addresses), patch.object(web.socket, "getaddrinfo", return_value=answers):
                with self.assertRaises(ValueError):
                    web.public_target("https://example.com/")

    def test_non_web_and_credential_urls_are_blocked_before_dns(self):
        for url in ("file:///C:/secret", "http://example.com", "https://u:p@example.com/",
                    "https://example.com:8088/", "https://example.com/\nheader"):
            with self.subTest(url=url), patch.object(web.socket, "getaddrinfo") as dns:
                with self.assertRaises(ValueError):
                    web.public_target(url)
                dns.assert_not_called()

    def test_search_preserves_evidence_and_sends_only_query(self):
        evidence = 'Title: Reference\nURL: https://example.com/reference\nHighlights: Actual source text.'
        event = 'event: message\ndata: ' + json.dumps({"id": 1, "result": {"content": [{"type": "text", "text": evidence}]}}) + '\n\n'
        with patch.object(web, "fetch", return_value=(web.EXA_URL, "text/event-stream", event)) as fetch:
            result = web.search("Arduino UNO")
        self.assertEqual(result["results_text"], evidence)
        args = fetch.call_args.kwargs["rpc"]["params"]["arguments"]
        self.assertEqual(set(args), {"query", "numResults", "objective"})
        self.assertEqual(args["query"], "Arduino UNO")

    def test_provider_error_does_not_become_evidence(self):
        raw = json.dumps({"id": 1, "result": {"isError": True, "content": [{"type": "text", "text": "Rate limit"}]}})
        with patch.object(web, "fetch", return_value=(web.EXA_URL, "application/json", raw)):
            result = web.dispatch({"method": "tools/call", "params": {"name": "search", "arguments": {"query": "test"}}})
        self.assertTrue(result["isError"])

    def test_reader_removes_scripts_and_marks_truncation(self):
        html = '<title>Example</title><nav>menu</nav><script>evil()</script><p>Hello</p><pre>int a = 1;</pre><p>' + "x" * 2000 + '</p><a href="/reference">Reference</a>'
        with patch.object(web, "fetch", return_value=("https://example.com/", "text/html", html)):
            result = web.read("https://example.com/", max_chars=1000)
        self.assertEqual(result["title"], "Example")
        self.assertEqual(result["links"], ["https://example.com/reference"])
        self.assertTrue(result["truncated"])
        self.assertIn("int a = 1;", result["text"])
        self.assertNotIn("evil", result["text"])
        self.assertNotIn("menu", result["text"])

    def test_redirect_to_private_address_is_revalidated(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch.object(web.socket, "getaddrinfo", side_effect=[public, private]), patch.object(web, "PinnedHTTPSConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 302
            response.getheader.return_value = "https://localhost/"
            with self.assertRaises(ValueError):
                web.fetch("https://example.com/")
            self.assertEqual(connection.call_count, 1)

    def test_stdio_handshake_and_tools_contract(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        result = subprocess.run([sys.executable, str(SOURCE)], input="\n".join(map(json.dumps, messages)) + "\n",
                                text=True, encoding="utf-8", capture_output=True, timeout=10, check=True)
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([line["id"] for line in lines], [1, 2])
        self.assertEqual([tool["name"] for tool in lines[1]["result"]["tools"]], ["search", "read"])
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in lines[1]["result"]["tools"]))


if __name__ == "__main__":
    unittest.main()
