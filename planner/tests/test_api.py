import json
from decimal import Decimal
from unittest.mock import patch
from django.core.cache import cache
from django.test import TestCase, override_settings
from planner.models import Place, Station


def route_response(miles=200, legs=None):
    return json.dumps({'code': 'Ok', 'routes': [{
        'distance': miles * 1609.344, 'duration': 10800,
        'geometry': {'type': 'LineString', 'coordinates': [[-96.8, 32.78], [-96.0, 32.78]]},
        'legs': [{'distance': distance * 1609.344} for distance in (legs or [miles])],
    }]}).encode()


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   OSRM_MIN_INTERVAL=0,
                   STATIONS_CSV='/__fuel_route_tests__/stations.csv',
                   STATION_PRICES_CSV='/__fuel_route_tests__/prices.csv',
                   STATION_COORDINATES_CSV='/__fuel_route_tests__/coordinates.csv')
class RouteApiTests(TestCase):
    def setUp(self):
        cache.clear()
        Place.objects.create(key='dallas, TX', latitude=32.78, longitude=-96.8)
        Place.objects.create(key='tyler, TX', latitude=32.78, longitude=-96.0)
        Station.objects.create(opis_id=7, name='Start station', address='1 Main', city='Dallas',
                               state='TX', price=Decimal('3'), latitude=32.78, longitude=-96.8)

    def post(self, **extra):
        return self.client.post('/api/routes/', data=json.dumps(
            {'start': 'Dallas, TX', 'finish': 'Tyler, TX', **extra}), content_type='application/json')

    @patch('urllib.request.urlopen')
    def test_short_route_returns_map_and_total_fuel_cost_with_one_call(self, network):
        network.return_value.__enter__.return_value.read.return_value = route_response()
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['fuel']['total_cost_usd'], '60.00')
        self.assertEqual(body['fuel']['gallons_consumed'], '20.000000')
        self.assertEqual(body['fuel_stops'], [])
        self.assertEqual(body['route']['geometry']['type'], 'LineString')
        self.assertEqual(self.client.get(body['map_url']).status_code, 200)
        self.post()
        self.assertEqual(network.call_count, 1)

    @patch('urllib.request.urlopen')
    def test_long_route_uses_verified_road_legs_and_never_exceeds_tank_range(self, network):
        Station.objects.create(opis_id=8, name='Cheap', address='2 Main', city='Test', state='TX',
                               price=Decimal('2'), latitude=32.78, longitude=-96.4)
        network.return_value.__enter__.return_value.read.side_effect = [
            route_response(800), route_response(820, [410, 410])]
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['fuel']['total_cost_usd'], '214.00')
        self.assertEqual(body['fuel']['money_spent_en_route_usd'], '64.00')
        self.assertEqual(body['fuel_stops'][0]['opis_id'], 8)
        self.assertEqual(body['fuel_stops'][0]['gallons'], '32.000000')
        self.assertEqual(network.call_count, 2)

    @patch('urllib.request.urlopen')
    def test_road_detour_that_breaks_range_is_rejected(self, network):
        Station.objects.create(opis_id=8, name='Cheap', address='2 Main', city='Test', state='TX',
                               price=Decimal('2'), latitude=32.78, longitude=-96.4)
        network.return_value.__enter__.return_value.read.side_effect = [
            route_response(800), route_response(1010, [505, 505])]
        self.assertEqual(self.post().status_code, 422)

    @patch('urllib.request.urlopen')
    def test_invalid_or_non_us_locations_are_rejected_before_network(self, network):
        for payload in ({'start': 'Toronto, ON'}, {'start': ''}, {'initial_gallons': 51},
                        {'initial_gallons': True}, {'initial_fuel_price': 'NaN'}, {'start': ['Dallas']}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(**payload).status_code, 400)
        network.assert_not_called()

    def test_bad_json_and_unsupported_methods(self):
        self.assertEqual(self.client.post('/api/routes/', '{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.get('/api/routes/').status_code, 405)
        self.assertEqual(self.client.get('/maps/00000000-0000-0000-0000-000000000000/').status_code, 404)

    @patch('urllib.request.urlopen', side_effect=TimeoutError)
    def test_provider_failure_returns_service_unavailable(self, network):
        self.assertEqual(self.post().status_code, 503)

    @patch('urllib.request.urlopen')
    def test_unroutable_locations_return_422(self, network):
        network.return_value.__enter__.return_value.read.return_value = b'{"code":"NoRoute"}'
        self.assertEqual(self.post().status_code, 422)

    @patch('urllib.request.urlopen')
    def test_malformed_provider_response_returns_503(self, network):
        network.return_value.__enter__.return_value.read.return_value = b'{"code":"Ok","routes":[]}'
        self.assertEqual(self.post().status_code, 503)

    @patch('urllib.request.urlopen')
    def test_missing_station_coordinates_are_reported_without_network_calls(self, network):
        Station.objects.update(latitude=None, longitude=None)
        self.assertEqual(self.post().status_code, 503)
        network.assert_not_called()

    @patch('urllib.request.urlopen')
    def test_customer_can_specify_initial_fuel_valuation(self, network):
        network.return_value.__enter__.return_value.read.return_value = route_response()
        response = self.post(initial_fuel_price='4.125')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fuel']['total_cost_usd'], '82.50')

    @patch('urllib.request.urlopen')
    def test_updated_database_backup_prices_are_used_with_cached_geometry(self, network):
        network.return_value.__enter__.return_value.read.return_value = route_response()
        self.post()
        Station.objects.update(price=Decimal('4'))
        self.assertEqual(self.post().json()['fuel']['total_cost_usd'], '80.00')
        self.assertEqual(network.call_count, 1)


    @patch('urllib.request.urlopen')
    def test_adak_to_arkansas_no_route_http_error_is_not_a_service_outage(self, network):
        from io import BytesIO
        from urllib.error import HTTPError
        Place.objects.create(key='adak, AK', latitude=51.874, longitude=-176.634)
        Place.objects.create(key='alleene, AR', latitude=33.7944, longitude=-94.2681)
        network.side_effect = HTTPError('https://routing.example/route', 400, 'Bad Request', {},
                                       BytesIO(b'{"code":"NoRoute","message":"Impossible route between points"}'))
        response = self.post(start='adak, AK', finish='alleene, AR')
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(response.json()['error']['code'], 'no_verified_plan')
        self.assertIn('No driving route', response.json()['error']['message'])
        self.assertNotIn('Retry-After', response)
        self.assertEqual(network.call_count, 1)


    @patch('urllib.request.urlopen')
    def test_no_road_near_city_is_a_422_with_actionable_message(self, network):
        from io import BytesIO
        from urllib.error import HTTPError
        network.side_effect = HTTPError('https://routing.example/route', 400, 'Bad Request', {},
                                       BytesIO(b'{"code":"NoSegment","message":"Could not find a matching segment"}'))
        response = self.post()
        self.assertEqual(response.status_code, 422)
        self.assertIn('200 meters', response.json()['error']['message'])
        self.assertEqual(network.call_count, 1)

    @patch('urllib.request.urlopen')
    def test_http_server_failure_remains_503_even_with_no_route_body(self, network):
        from io import BytesIO
        from urllib.error import HTTPError
        network.side_effect = HTTPError('https://routing.example/route', 503, 'Unavailable', {},
                                       BytesIO(b'{"code":"NoRoute"}'))
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertIn('HTTP 503', response.json()['error']['message'])
        self.assertEqual(response['Retry-After'], '5')

    @patch('urllib.request.urlopen')
    def test_non_json_http_400_is_not_mistaken_for_a_disconnected_route(self, network):
        from io import BytesIO
        from urllib.error import HTTPError
        network.side_effect = HTTPError('https://routing.example/route', 400, 'Bad Request', {},
                                       BytesIO(b'<html>Proxy error</html>'))
        self.assertEqual(self.post().status_code, 503)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   OSRM_MIN_INTERVAL=0)
class CsvStationApiTests(TestCase):
    def setUp(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'stations.csv'
        self.settings_override = override_settings(
            STATIONS_CSV=str(self.path), STATION_PRICES_CSV=str(self.path),
            STATION_COORDINATES_CSV=str(Path(self.directory.name) / 'coordinates.csv'))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.fields = ['OPIS Truckstop ID', 'Truckstop Name', 'Address', 'City', 'State',
                       'Rack ID', 'Retail Price', 'latitude', 'longitude', 'coordinate_source']
        cache.clear()
        Place.objects.create(key='dallas, TX', latitude=32.78, longitude=-96.8)
        Place.objects.create(key='tyler, TX', latitude=32.78, longitude=-96.0)
        Station.objects.create(opis_id=7, name='Database backup', address='1 Main', city='Dallas',
                               state='TX', price='3', latitude=32.78, longitude=-96.8)
        self.write_csv('2.50')

    def write_csv(self, price):
        import csv
        with self.path.open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(self.fields)
            writer.writerow([7, 'CSV station', '1 Main', 'Dallas', 'TX', '', price,
                             32.78, -96.8, 'verified-coordinate-file'])

    def post(self):
        return self.client.post('/api/routes/', data={'start': 'Dallas, TX', 'finish': 'Tyler, TX'},
                                content_type='application/json')

    @patch('urllib.request.urlopen')
    def test_csv_is_primary_and_station_database_is_not_queried(self, network):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        network.return_value.__enter__.return_value.read.return_value = route_response()
        with CaptureQueriesContext(connection) as queries:
            response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['fuel']['total_cost_usd'], '50.00')
        self.assertEqual(response.json()['coverage']['station_source'], 'csv')
        self.assertFalse(any('planner_station' in query['sql'] for query in queries))

    @patch('urllib.request.urlopen')
    def test_missing_or_corrupt_csv_uses_database_backup(self, network):
        network.return_value.__enter__.return_value.read.return_value = route_response()
        for content in (None, 'not,a,station,file\n'):
            with self.subTest(content=content):
                if content is None:
                    self.path.unlink(missing_ok=True)
                else:
                    self.path.write_text(content)
                response = self.post()
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()['fuel']['total_cost_usd'], '60.00')
                self.assertEqual(response.json()['coverage']['station_source'], 'database')

    @patch('urllib.request.urlopen')
    def test_csv_changes_are_reloaded_without_restarting_server(self, network):
        network.return_value.__enter__.return_value.read.return_value = route_response()
        self.assertEqual(self.post().json()['fuel']['total_cost_usd'], '50.00')
        self.write_csv('4.1250')
        self.assertEqual(self.post().json()['fuel']['total_cost_usd'], '82.50')
        self.assertEqual(network.call_count, 1)

    @patch('urllib.request.urlopen')
    def test_original_csv_can_use_coordinate_sidecar_without_station_import(self, network):
        import csv
        from pathlib import Path
        Station.objects.all().delete()
        with self.path.open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(self.fields[:7])
            writer.writerow([7, 'CSV station', '1 Main', 'Dallas', 'TX', '', '2.50'])
        Path(self.settings_override.options['STATION_COORDINATES_CSV']).write_text(
            'opis_id,latitude,longitude\n7,32.78,-96.8\n')
        network.return_value.__enter__.return_value.read.return_value = route_response()
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['coverage']['station_source'], 'csv')
        self.assertEqual(response.json()['fuel']['total_cost_usd'], '50.00')

    @patch('urllib.request.urlopen')
    def test_unlocated_csv_uses_located_database_backup(self, network):
        self.path.write_text('OPIS Truckstop ID,Truckstop Name,Address,City,State,Retail Price\n'
                             '7,Unlocated,1 Main,Dallas,TX,2.5\n')
        network.return_value.__enter__.return_value.read.return_value = route_response()
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['coverage']['station_source'], 'database')

    @patch('urllib.request.urlopen')
    def test_unchanged_csv_is_read_once_across_requests(self, network):
        from pathlib import Path
        network.return_value.__enter__.return_value.read.return_value = route_response()
        original_open = Path.open
        reads = []
        def track_open(path, *args, **kwargs):
            if path == self.path:
                reads.append(path)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', autospec=True, side_effect=track_open):
            self.assertEqual(self.post().status_code, 200)
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual(len(reads), 1)

    @patch('urllib.request.urlopen')
    def test_database_export_becomes_usable_primary_csv(self, network):
        from io import StringIO
        from django.core.management import call_command
        call_command('export_stations', output=str(self.path), stdout=StringIO())
        Station.objects.all().delete()
        network.return_value.__enter__.return_value.read.return_value = route_response()
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['fuel']['total_cost_usd'], '60.00')
        self.assertEqual(response.json()['coverage']['station_source'], 'csv')
