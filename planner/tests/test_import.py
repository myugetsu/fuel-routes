import csv
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from django.core.management import call_command, CommandError
from django.test import TestCase
from planner.models import Station


class ImportTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'prices.csv'

    def write_rows(self, rows):
        with self.path.open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['OPIS Truckstop ID', 'Truckstop Name', 'Address', 'City',
                             'State', 'Rack ID', 'Retail Price'])
            writer.writerows(rows)

    def test_import_is_repeatable_and_preserves_lowest_duplicate_price(self):
        self.write_rows([[7, 'A', '123 Main St', 'Dallas', 'TX', 1, '3.25'],
                         [7, 'A duplicate', '123 Main St', 'Dallas', 'TX', 1, '3.50']])
        for _ in range(2):
            call_command('import_stations', str(self.path), stdout=StringIO())
        station = Station.objects.get(opis_id=7)
        self.assertEqual(Station.objects.count(), 1)
        self.assertEqual(str(station.price), '3.25000000')
        self.assertIsNone(station.latitude)

    def test_invalid_row_rolls_back_entire_import(self):
        self.write_rows([[7, 'A', '123 Main St', 'Dallas', 'TX', 1, '3.25'],
                         [8, 'B', '999 Main St', 'Dallas', 'TX', 1, 'NaN']])
        with self.assertRaises(CommandError):
            call_command('import_stations', str(self.path), stdout=StringIO())
        self.assertEqual(Station.objects.count(), 0)

    def test_coordinate_sidecar_sets_real_coordinates(self):
        self.write_rows([[7, 'A', '123 Main St', 'Dallas', 'TX', 1, '3.25']])
        coordinates = Path(self.directory.name) / 'coords.csv'
        coordinates.write_text('opis_id,latitude,longitude\n7,32.78,-96.80\n')
        call_command('import_stations', str(self.path), coordinates=str(coordinates), stdout=StringIO())
        self.assertEqual(Station.objects.get(opis_id=7).longitude, -96.8)

    def test_canadian_stations_are_excluded_from_us_planner(self):
        self.write_rows([[7, 'US', '123 Main St', 'Dallas', 'TX', 1, '3.25'],
                         [8, 'Canada', '1 Main St', 'Toronto', 'ON', 1, '1.50']])
        call_command('import_stations', str(self.path), stdout=StringIO())
        self.assertEqual(list(Station.objects.values_list('opis_id', flat=True)), [7])

    def test_enriched_csv_can_restore_database_backup_with_coordinates(self):
        self.path.write_text('OPIS Truckstop ID,Truckstop Name,Address,City,State,Retail Price,latitude,longitude,coordinate_source\n'
                             '7,Enriched station,123 Main St,Dallas,TX,2.50,32.78,-96.8,verified-coordinate-file\n')
        call_command('import_stations', str(self.path), stdout=StringIO())
        station = Station.objects.get(opis_id=7)
        self.assertEqual(station.latitude, 32.78)
        self.assertEqual(station.longitude, -96.8)
        self.assertEqual(station.coordinate_source, 'verified-coordinate-file')
