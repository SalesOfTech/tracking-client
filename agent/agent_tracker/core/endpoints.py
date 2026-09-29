"""Failover only between the two explicitly trusted Tracking origins."""
from urllib.parse import urlsplit, urlunsplit
import requests

HOSTS = ('tracking.salesoftech.com', 'tracking.salesof.tech')


def trusted_tracking_url(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.hostname in HOSTS and
            parsed.port in (None, 443) and not parsed.username and not parsed.password)


class EndpointTransport:
    def __init__(self, session):
        self.session = session
        self.preferred = None

    def request(self, method, url, **kwargs):
        parsed = urlsplit(url)
        hosts = [parsed.hostname]
        if trusted_tracking_url(url):
            first = self.preferred or parsed.hostname
            hosts = [first] + [host for host in HOSTS if host != first]
        kwargs['allow_redirects'] = False
        for index, host in enumerate(hosts):
            target = urlunsplit(parsed._replace(netloc=host)) if len(hosts) > 1 else url
            try:
                response = getattr(self.session, method)(target, **kwargs)
            except (requests.ConnectionError, requests.Timeout):
                if index + 1 == len(hosts):
                    raise
                continue
            blocked = response.status_code in (451, 502, 503, 504)
            if response.status_code == 403:
                blocked = 'text/html' in str(response.headers.get('Content-Type', '')).lower()
            if blocked and index + 1 < len(hosts):
                response.close()
                continue
            if response.status_code == 200 and len(hosts) > 1:
                self.preferred = host
            return response
