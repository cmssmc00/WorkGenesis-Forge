"""Offline security and provenance tests; no network or paid API requests."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from wgforge import sources


class Response:
    def __init__(self, body=b'a,b\n1,2\n', mime='text/csv', status=200, **headers):
        self.body, self.status = body, status
        self.headers = {'Content-Type': mime, **headers}
    def getheader(self, key, default=None):
        return self.headers.get(key, default)
    def read1(self, size):
        result, self.body = self.body[:size], self.body[size:]
        return result
    def close(self):
        pass


class Connection:
    responses = []
    requests = []
    def __init__(self, host, address, timeout):
        self.host, self.address, self.timeout, self.sock = host, address, timeout, None
    def request(self, *args, **kwargs):
        self.requests.append((self.host, self.address, args, kwargs))
    def getresponse(self):
        return self.responses.pop(0)
    def close(self):
        pass


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.config = {'allowed_hosts': ['example.org'], 'max_bytes': 1024, 'timeout_seconds': 5}
        self.record = {'id': 'fixture', 'url': 'https://example.org/data.csv', 'title': 'Mock test fixture'}
    def tearDown(self):
        self.temp.cleanup()
    def fetch(self, responses, config=None):
        Connection.responses, Connection.requests = list(responses), []
        with patch.object(sources, '_public_addresses', return_value=['93.184.216.34']), patch.object(sources, '_PinnedHTTPSConnection', Connection):
            return sources._fetch_https(self.record['url'], config or self.config)
    def materialize(self):
        with patch.object(sources, '_fetch_https', return_value=(b'a,b\n1,2\n', {'mime_type': 'text/csv', 'final_url': self.record['url']})):
            return sources.download_source(self.record, self.base, self.config)
    def test_direct_discovery_defaults_pending(self):
        result = sources.discover_sources('', {**self.config, 'direct_urls': [self.record['url']]})
        self.assertEqual(result[0]['rights_status'], 'pending')
        self.assertIs(result[0]['synthetic'], False)
    def test_direct_discovery_rejects_unapproved_hosts(self):
        with self.assertRaises(sources.SourceError):
            sources.discover_sources('https://evil.example.org/data', self.config)
    def test_url_rejects_ssrf_credentials_ports_protocols(self):
        urls = ['http://example.org/a', 'file:///etc/passwd', 'https://u:p@example.org/a', 'https://example.org:8080/a',
                'https://example.org.evil.com/a', 'https://127.0.0.1/a', 'https://[::1]/a', 'https://169.254.169.254/a',
                'https://example.org/\nfoo', 'https://example.org/a#fragment', 'https://example.org\\evil/a']
        for url in urls:
            with self.subTest(url=url), self.assertRaises(sources.SourceError):
                sources.validate_url(url, ['example.org', '127.0.0.1', '::1', '169.254.169.254'], resolve=False)
    def test_mixed_public_private_dns_fails_closed(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
                     (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.4', 443))]
        with patch.object(socket, 'getaddrinfo', return_value=addresses), self.assertRaises(sources.SourceError):
            sources.validate_url(self.record['url'], ['example.org'])
    def test_dns_pinning_uses_checked_numeric_address(self):
        body, meta = self.fetch([Response()])
        self.assertEqual(body, b'a,b\n1,2\n')
        self.assertEqual(Connection.requests[0][1], '93.184.216.34')
        self.assertEqual(meta['retrieval_method'], 'pinned_https')
    def test_redirect_denied_by_default(self):
        with self.assertRaisesRegex(sources.SourceError, 'Redirect denied'):
            self.fetch([Response(status=302, Location='https://example.org/new.csv')])
    def test_redirect_target_revalidated(self):
        with self.assertRaises(sources.SourceError):
            self.fetch([Response(status=302, Location='https://127.0.0.1/secret')], {**self.config, 'max_redirects': 1})
    def test_allowed_redirect_keeps_original_origin(self):
        body, meta = self.fetch([Response(status=302, Location='/next.csv'), Response()], {**self.config, 'max_redirects': 1})
        self.assertEqual(meta['url'], self.record['url'])
        self.assertEqual(meta['final_url'], 'https://example.org/next.csv')
        self.assertEqual(len(Connection.requests), 2)
    def test_html_header_and_disguised_html_fail(self):
        for response in [Response(b'<html>Error</html>', 'text/html'), Response(b'<!doctype html><html>Denied</html>', 'text/csv'), Response(b'%PDF-no', 'application/json')]:
            with self.subTest(mime=response.headers), self.assertRaises(sources.SourceError):
                self.fetch([response])
    def test_size_bound_headers_stream_and_truncation(self):
        for response in [Response(**{'Content-Length': '2000'}), Response(b'x' * 1025), Response(**{'Content-Length': '99'}), Response(**{'Content-Length': '-1'})]:
            with self.subTest(response=response), self.assertRaises(sources.SourceError):
                self.fetch([response])
    def test_invalid_type_encoding_and_empty_fail(self):
        for response in [Response(b''), Response(b'x', 'application/octet-stream'), Response(b'x', **{'Content-Encoding': 'gzip'}), Response(b'\x00x')]:
            with self.subTest(response=response), self.assertRaises(sources.SourceError):
                self.fetch([response])
    def test_bad_limits_rejected(self):
        for key, value in [('max_bytes', 0), ('timeout_seconds', .5), ('max_redirects', 6)]:
            with self.subTest(key=key), self.assertRaises(sources.SourceError):
                self.fetch([Response()], {**self.config, key: value})

    def test_watchdog_disconnect_is_reported_as_timeout(self):
        class DeadlineTimer:
            def __init__(self, delay, callback):
                self.callback = callback
            def start(self):
                self.callback()
            def cancel(self):
                pass
        with patch.object(sources.threading, 'Timer', DeadlineTimer), \
             patch.object(Connection, 'getresponse', side_effect=http.client.RemoteDisconnected('closed')), \
             self.assertRaises(sources.SourceTimeoutError) as caught:
            self.fetch([])
        self.assertEqual(sources._search_failure(caught.exception).diagnostic['code'], 'network_timeout')

    def test_disconnect_before_deadline_remains_connection_error(self):
        with patch.object(Connection, 'getresponse', side_effect=http.client.RemoteDisconnected('closed')), \
             self.assertRaises(sources.SourceError) as caught:
            self.fetch([])
        self.assertEqual(sources._search_failure(caught.exception).diagnostic['code'], 'network_connection_error')
    def test_pending_source_cached_immutable_and_not_production(self):
        r = self.materialize()
        sources.verify_source_record(r, self.base)
        self.assertEqual((self.base / r['path']).name, r['sha256'] + '.csv')
        self.assertEqual((self.base / r['path']).stat().st_mode & 0o222, 0)
        with self.assertRaises(sources.SourceError):
            sources.require_production_rights(r)
    def test_production_requires_all_rights_scopes(self):
        r = {**self.materialize(), 'rights_status': 'approved', 'license': 'CC0-1.0', 'rights_basis': 'Test-only licensed fixture', 'rights_evidence': ['https://example.org/license']}
        with self.assertRaises(sources.SourceError):
            sources.require_production_rights(r)
        r['rights_permissions'] = {'training': True, 'redistribution': True, 'public_access': True}
        sources.require_production_rights(r)
        r['rights_permissions']['training'] = False
        with self.assertRaises(sources.SourceError):
            sources.require_production_rights(r)
    def test_path_traversal_symlink_and_hardlink_fail(self):
        r = self.materialize()
        for path in ['../secret.csv', '/etc/passwd', '..\\secret.csv']:
            with self.subTest(path=path), self.assertRaises(sources.SourceError):
                sources.verify_source_record({**r, 'path': path}, self.base)
        (self.base / 'link.csv').symlink_to(self.base / r['path'])
        with self.assertRaises(sources.SourceError):
            sources.verify_source_record({**r, 'path': 'link.csv'}, self.base)
        os.link(self.base / r['path'], self.base / 'hard.csv')
        with self.assertRaises(sources.SourceError):
            sources.verify_source_record(r, self.base)
    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'FIFO test is POSIX-only')
    def test_fifo_rejected_without_open_block(self):
        r = self.materialize()
        os.mkfifo(self.base / 'pipe')
        with self.assertRaises(sources.SourceError):
            sources.verify_source_record({**r, 'path': 'pipe'}, self.base)
    def test_sha_byte_and_provenance_tampering_fail(self):
        r = self.materialize()
        for change in [{'sha256': '0' * 64}, {'bytes': r['bytes'] + 1}, {'synthetic': 'false'}, {'provenance_kind': 'synthetic'}, {'retrieved_at': '2026-01-01'}]:
            with self.subTest(change=change), self.assertRaises(sources.SourceError):
                sources.verify_source_record({**r, **change}, self.base)
        p = self.base / r['path']; p.chmod(0o644); p.write_bytes(b'a,b\n9,2\n')
        with self.assertRaises(sources.SourceError):
            sources.verify_source_record(r, self.base)
    def test_existing_corrupted_content_address_fails(self):
        r = self.materialize()
        p = self.base / r['path']; p.chmod(0o644); p.write_bytes(b'tampered')
        with self.assertRaises(sources.SourceError):
            self.materialize()
    def test_record_path_cannot_control_download_target(self):
        self.record['path'] = '../escape.csv'
        r = self.materialize()
        self.assertTrue(r['path'].startswith('raw/'))
    def test_manifest_duplicate_and_bad_source_fail(self):
        r = self.materialize(); path = self.base / 'manifest.json'
        path.write_text(json.dumps({'sources': [r]})); self.assertEqual(sources.load_source_manifest(path)['sources'][0], r)
        path.write_text(json.dumps({'sources': [r, r]}))
        with self.assertRaises(sources.SourceError):
            sources.load_source_manifest(path)
    def test_paid_search_never_runs_without_opt_in(self):
        with patch.object(sources, '_fetch_https') as mock, self.assertRaises(sources.SourceError):
            sources.discover_sources('official data', {**self.config, 'adapter': 'serpapi'})
        mock.assert_not_called()
    def test_serpapi_real_request_contract_mocked(self):
        payload = {'organic_results': [{'title': 'Data', 'link': self.record['url'], 'position': 1}, {'title': 'Other', 'link': 'https://evil.org/data'}]}
        with patch.dict(os.environ, {'SERPAPI_API_KEY': 'test-secret'}), patch.object(sources, '_fetch_https', return_value=(json.dumps(payload).encode(), {})) as fetch:
            found = sources.discover_sources('public CSV', {**self.config, 'adapter': 'serpapi', 'enable_paid_search': True})
        self.assertIn('https://serpapi.com/search.json?', fetch.call_args[0][0])
        self.assertEqual(fetch.call_args[0][1]['allowed_hosts'], ['serpapi.com'])
        self.assertEqual(fetch.call_args[0][1]['timeout_seconds'], 120)
        self.assertEqual(len(found), 1)
        self.assertNotIn('test-secret', json.dumps(found))
        self.assertEqual(found[0]['rights_status'], 'pending')

    def test_serpapi_search_deadline_can_be_configured_separately(self):
        with patch.dict(os.environ, {'SERPAPI_API_KEY': 'test-secret'}), \
             patch.object(sources, '_fetch_https', return_value=(b'{}', {})) as fetch:
            sources.discover_sources('public CSV', {**self.config, 'adapter': 'serpapi',
                'enable_paid_search': True, 'serpapi_search_timeout_seconds': 90})
        self.assertEqual(fetch.call_args[0][1]['timeout_seconds'], 90)
        self.assertEqual(self.config['timeout_seconds'], 5)
    def test_serpapi_error_never_exposes_credential_url(self):
        with patch.dict(os.environ, {'SERPAPI_API_KEY': 'test-secret'}), patch.object(sources, '_fetch_https', side_effect=RuntimeError('test-secret')):
            with self.assertRaises(sources.SourceError) as caught:
                sources.discover_sources('public CSV', {**self.config, 'adapter': 'serpapi', 'enable_paid_search': True})
        self.assertNotIn('test-secret', str(caught.exception))

    def test_serpapi_diagnostics_keep_status_without_credentials_or_retries(self):
        cases = [(sources.SourceHTTPError(401), 'authentication_or_access_denied', 401),
                 (sources.SourceHTTPError(429), 'quota_or_rate_limit', 429),
                 (sources.SourceHTTPError(503), 'provider_server_error', 503),
                 (TimeoutError('https://serpapi.com/?api_key=test-secret'), 'network_timeout', None),
                 (ConnectionResetError('test-secret'), 'network_connection_error', None)]
        for error, code, status in cases:
            with self.subTest(code=code), patch.dict(os.environ, {'SERPAPI_API_KEY': 'test-secret'}), \
                 patch.object(sources, '_fetch_https', side_effect=error) as fetch:
                with self.assertRaises(sources.SearchError) as caught:
                    sources.discover_sources('public CSV', {**self.config, 'adapter': 'serpapi', 'enable_paid_search': True})
                self.assertEqual(caught.exception.diagnostic, {'code': code, 'http_status': status})
                self.assertNotIn('test-secret', str(caught.exception))
                self.assertEqual(fetch.call_count, 1)

    def test_account_preflight_uses_only_account_endpoint_and_redacts_response(self):
        payload = {'account_status': 'Active', 'total_searches_left': 8, 'this_hour_searches': 2,
                   'account_rate_limit_per_hour': 100, 'api_key': 'test-secret', 'account_email': 'private@example.com'}
        with patch.dict(os.environ, {'SERPAPI_API_KEY': 'test-secret'}), \
             patch.object(sources, '_fetch_https', return_value=(json.dumps(payload).encode(), {})) as fetch:
            result = sources.serpapi_account_status(self.config)
        self.assertTrue(result['ready'])
        self.assertIn('/account.json?', fetch.call_args[0][0])
        self.assertNotIn('/search.json', fetch.call_args[0][0])
        self.assertNotIn('test-secret', json.dumps(result))
        self.assertNotIn('private@example.com', json.dumps(result))
        self.assertEqual(fetch.call_args[0][1]['max_redirects'], 0)
        self.assertEqual(fetch.call_args[0][1]['timeout_seconds'], 15)

    def test_account_preflight_blocks_empty_unknown_quota_inactive_and_rate_limit(self):
        for payload in ({'account_status': 'Active', 'total_searches_left': 0},
                        {'account_status': 'Active'},
                        {'account_status': 'Disabled', 'total_searches_left': 10},
                        {'account_status': 'Active', 'total_searches_left': 10,
                         'this_hour_searches': 10, 'account_rate_limit_per_hour': 10}):
            with self.subTest(payload=payload), patch.dict(os.environ, {'SERPAPI_API_KEY': 'test-secret'}), \
                 patch.object(sources, '_fetch_https', return_value=(json.dumps(payload).encode(), {})):
                self.assertFalse(sources.serpapi_account_status(self.config)['ready'])
    def test_bundled_public_pilot_is_real_and_verifies(self):
        path = Path(__file__).resolve().parents[1] / 'examples/anchors/usgs_manifest.json'
        manifest = sources.load_source_manifest(path)
        r = manifest['sources'][0]
        sources.require_production_rights(r)
        self.assertEqual(r['row_count'], 200)
        self.assertIn('catalog=us', r['url'])
        self.assertFalse(r['synthetic'])
        self.assertGreater(r['bytes'], 10000)


if __name__ == '__main__':
    unittest.main()
