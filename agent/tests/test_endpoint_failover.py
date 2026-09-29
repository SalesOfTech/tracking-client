import unittest
from unittest.mock import Mock
import requests
from agent_tracker.core.endpoints import EndpointTransport


class EndpointTests(unittest.TestCase):
    def test_network_fallback_and_sticky_origin(self):
        session = Mock()
        response = Mock(status_code=200)
        session.post.side_effect = [requests.ConnectionError(), response, response]
        transport = EndpointTransport(session)
        url = 'https://tracking.salesof.tech/client/v3/events'
        transport.request('post', url, json={'secret': 'fixture'}, timeout=3)
        transport.request('post', url, json={}, timeout=3)
        self.assertEqual([c.args[0] for c in session.post.call_args_list], [url,
            url.replace('salesof.tech', 'salesoftech.com'), url.replace('salesof.tech', 'salesoftech.com')])
        self.assertFalse(session.post.call_args.kwargs['allow_redirects'])

    def test_auth_failure_is_not_retried(self):
        for status in (401, 403, 409, 422):
            session = Mock()
            session.post.return_value = Mock(status_code=status, headers={'Content-Type': 'application/json'})
            EndpointTransport(session).request('post', 'https://tracking.salesof.tech/test')
            self.assertEqual(session.post.call_count, 1)

    def test_only_exact_trusted_origins_fail_over(self):
        for url in ('https://evil.example/test', 'http://tracking.salesof.tech/test',
                    'https://tracking.salesof.tech:444/test', 'https://user@tracking.salesof.tech/test'):
            session = Mock()
            session.get.side_effect = requests.ConnectionError()
            with self.assertRaises(requests.ConnectionError):
                EndpointTransport(session).request('get', url)
            self.assertEqual(session.get.call_count, 1)

    def test_gateway_failure_closes_response(self):
        session = Mock()
        unavailable = Mock(status_code=503)
        session.get.side_effect = [unavailable, Mock(status_code=200)]
        EndpointTransport(session).request('get', 'https://tracking.salesoftech.com/test', stream=True)
        unavailable.close.assert_called_once()
