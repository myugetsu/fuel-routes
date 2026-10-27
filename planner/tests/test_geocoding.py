import csv
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.core.management import call_command, CommandError
from django.test import TestCase
from planner.models import Station


def census_response(*rows):
    output = io.StringIO()
    csv.writer(output).writerows(rows)
    return output.getvalue().encode()


class GeocodingCommandTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.report = str(Path(self.directory.name) / 'unresolved.csv')
        self.output = io.StringIO()
        for station_id in (7, 9):
            Station.objects.create(opis_id=station_id, name=f'Station {station_id}',
                                   address='123 Main St', city='Dallas', state='TX', price='3.25')

    def run_command(self, **options):
        call_command('geocode_stations', unresolved=self.report, stdout=self.output, **options)

    @patch('urllib.request.urlopen')
    def test_progress_is_printed_before_network_wait_and_matches_are_saved(self, network):
        def respond(*args, **kwargs):
            self.assertIn('Batch 1/2: sending 1 station', self.output.getvalue())
            return network.return_value
        network.side_effect = respond
        network.return_value.__enter__.return_value.read.side_effect = [
            census_response([7, '123 Main St', 'Match', 'Exact', '123 MAIN ST', '-96.8,32.78']),
            census_response([9, '123 Main St', 'No_Match'])]
        self.run_command(batch_size=1, timeout=10)
        self.assertEqual(Station.objects.get(opis_id=7).latitude, 32.78)
        self.assertIsNotNone(Station.objects.get(opis_id=9).geocoding_attempted_at)
        self.assertIn('Batch 2/2 complete', self.output.getvalue())
        self.assertIn('Matched 1/2', self.output.getvalue())
        self.assertEqual(network.call_count, 2)
        self.assertEqual(network.call_args.kwargs['timeout'], 10)

    @patch('urllib.request.urlopen')
    def test_failed_later_batch_preserves_completed_work_and_can_resume(self, network):
        network.return_value.__enter__.return_value.read.side_effect = [
            census_response([7, '123 Main St', 'No_Match']), TimeoutError('timed out')]
        with self.assertRaisesMessage(CommandError, 'batch 2/2'):
            self.run_command(batch_size=1)
        self.assertIsNotNone(Station.objects.get(opis_id=7).geocoding_attempted_at)
        self.assertIsNone(Station.objects.get(opis_id=9).geocoding_attempted_at)
        self.assertTrue(Path(self.report).exists())
        network.reset_mock()
        network.return_value.__enter__.return_value.read.side_effect = None
        network.return_value.__enter__.return_value.read.return_value = census_response(
            [9, '123 Main St', 'Match', 'Exact', '123 MAIN ST', '-96.8,32.78'])
        self.run_command(batch_size=1)
        submitted = network.call_args.args[0].data.decode()
        self.assertNotIn('7,123 Main St', submitted)
        self.assertIn('9,123 Main St', submitted)
        self.assertEqual(Station.objects.get(opis_id=9).latitude, 32.78)

    @patch('urllib.request.urlopen')
    def test_invalid_response_does_not_mark_batch_processed(self, network):
        network.return_value.__enter__.return_value.read.return_value = b'<html>Service unavailable</html>'
        with self.assertRaisesMessage(CommandError, 'Invalid Census response'):
            self.run_command()
        self.assertFalse(Station.objects.filter(geocoding_attempted_at__isnull=False).exists())

    @patch('urllib.request.urlopen')
    def test_no_matches_produces_actionable_warning(self, network):
        network.return_value.__enter__.return_value.read.return_value = census_response(
            [7, '123 Main St', 'No_Match'], [9, '123 Main St', 'No_Match'])
        self.run_command()
        self.assertIn('No stations were located', self.output.getvalue())
        self.assertIn('--coordinates', self.output.getvalue())
