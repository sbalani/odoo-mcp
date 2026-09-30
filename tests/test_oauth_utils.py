import base64
import hashlib
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('oauth_utils', Path(__file__).parents[1] / 'odoo_mcp/oauth_utils.py')
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class OAuthPrimitiveTests(unittest.TestCase):
    def test_pkce_rfc7636_vector(self):
        verifier = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'
        challenge = 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM'
        self.assertTrue(u.check_pkce(verifier, challenge))
        for bad in (None, '', 'too-short', 'a' * 129, '!' * 43, 'x' * 43):
            self.assertFalse(u.check_pkce(bad, challenge))

    def test_callbacks_and_origin_reject_unsafe_urls(self):
        for bad in ('http://chatgpt.com/callback', '//chatgpt.com/callback', 'javascript:alert(1)',
                    'https://user:pass@chatgpt.com/callback', 'https://chatgpt.com/callback#fragment',
                    'https://chatgpt.com/\ncallback'):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                u.redirect_uri(bad)
        for bad in ('https://odoo.example/path', 'https://odoo.example?key=secret', 'http://odoo.example'):
            with self.subTest(url=bad), self.assertRaises(u.OAuthError):
                u.canonical_origin(bad)
        self.assertEqual(u.canonical_origin('https://odoo.example/'), 'https://odoo.example')

    def test_unknown_or_missing_read_scope_denied(self):
        for bad in ('', 'mcp.crm.write', 'mcp.read admin', None):
            with self.assertRaises(u.OAuthError):
                u.scopes(bad)
        self.assertEqual(u.scopes('offline_access mcp.read mcp.read'), ['mcp.read', 'offline_access'])

    def test_tokens_are_high_entropy_and_hashes_are_one_way(self):
        values = {u.new_secret('test_') for _ in range(100)}
        self.assertEqual(len(values), 100)
        for value in values:
            self.assertEqual(len(value), 69)
            self.assertEqual(len(u.digest(value)), 64)
            self.assertNotEqual(value, u.digest(value))
