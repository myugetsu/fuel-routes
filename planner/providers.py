"""Bounded, cached OSRM HTTP adapter; free endpoint configurable for self hosting."""
import hashlib
import json
import logging
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class ProviderUnavailable(Exception):
    pass


class RouteNotFound(Exception):
    pass


def download(url, *, data=None, headers=None, timeout=15, allowed_error_statuses=()):
    request = urllib.request.Request(url, data=data, headers={
        'User-Agent': settings.PROVIDER_USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        # OSRM documents NoRoute and NoSegment as HTTP 400 JSON responses.
        if exc.code in allowed_error_statuses:
            with exc:
                return exc.read()
        exc.close()
        logger.warning('External service returned HTTP %s', exc.code)
        if exc.code == 429:
            raise ProviderUnavailable('External service rate limit reached; retry shortly.') from exc
        raise ProviderUnavailable(f'External service returned HTTP {exc.code}; retry shortly.') from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning('External connection failed (%s): %s', type(exc).__name__, exc)
        raise ProviderUnavailable('External service unavailable; retry shortly.') from exc


def driving_route(coordinates):
    encoded = ';'.join(f'{lon:.6f},{lat:.6f}' for lon, lat in coordinates)
    key = 'osrm:' + hashlib.sha256((settings.OSRM_URL + encoded).encode()).hexdigest()
    existing = cache.get(key)
    if existing is not None:
        return existing
    lock = key + ':pending'
    if not cache.add(lock, True, timeout=25):
        raise ProviderUnavailable('This route is being calculated; retry shortly.')
    try:
        interval = getattr(settings, 'OSRM_MIN_INTERVAL', 2)
        if interval:
            deadline = time.monotonic() + 5
            while not cache.add('osrm:rate-gate', True, timeout=interval):
                if time.monotonic() >= deadline:
                    raise ProviderUnavailable('Routing service busy; retry shortly.')
                time.sleep(0.1)
        url = settings.OSRM_URL.rstrip('/') + '/route/v1/driving/' + encoded
        raw = download(url + '?overview=full&geometries=geojson&steps=false&radiuses=' + ';'.join(['200'] * len(coordinates)),
                       allowed_error_statuses=(400,))
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('Invalid route payload')
            if payload.get('code') == 'NoRoute':
                raise RouteNotFound('No driving route is available between these locations. '
                                    'They may be on disconnected road networks, such as an island and the mainland.')
            if payload.get('code') == 'NoSegment':
                raise RouteNotFound('One or more locations are not within 200 meters of a mapped driving road. '
                                    'City centers are approximate; choose another city.')
            route = payload['routes'][0]
            points = route['geometry']['coordinates']
            numbers = [route['distance'], route['duration']] + [leg['distance'] for leg in route['legs']]
            if (payload.get('code') != 'Ok' or route['geometry']['type'] != 'LineString'
                    or len(points) < 2 or len(route['legs']) != len(coordinates) - 1
                    or any(isinstance(n, bool) or not math.isfinite(n) or n < 0 for n in numbers)
                    or any(len(p) != 2 or not -180 <= p[0] <= 180 or not -90 <= p[1] <= 90 for p in points)
                    or not math.isclose(sum(leg['distance'] for leg in route['legs']), route['distance'], abs_tol=1)):
                raise ValueError('Invalid route payload')
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailable('Routing service returned an invalid response.') from exc
        cache.set(key, route, timeout=settings.ROUTE_CACHE_SECONDS)
        return route
    finally:
        cache.delete(lock)
