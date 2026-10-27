from django.test import TestCase
from planner.models import Place


class LocationDropdownTests(TestCase):
    def test_city_choices_only_include_the_selected_state(self):
        Place.objects.create(key='dallas, TX', latitude=32.78, longitude=-96.8)
        Place.objects.create(key='austin, TX', latitude=30.27, longitude=-97.74)
        Place.objects.create(key='los angeles, CA', latitude=34.05, longitude=-118.24)
        response = self.client.get('/api/places/', {'state': 'TX'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cities'], [
            {'value': 'austin, TX', 'label': 'Austin, TX'},
            {'value': 'dallas, TX', 'label': 'Dallas, TX'},
        ])

    def test_home_page_provides_state_and_city_dropdowns(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<select id="start" name="start" required disabled>')
        self.assertContains(response, '<select id="finish" name="finish" required disabled>')
        self.assertContains(response, '<option value="TX">TX</option>', count=2)
        self.assertNotContains(response, '<input')

    def test_unknown_state_is_rejected(self):
        for state in ('', 'ON', 'invalid'):
            with self.subTest(state=state):
                self.assertEqual(self.client.get('/api/places/', {'state': state}).status_code, 400)

    def test_state_with_no_loaded_cities_returns_empty_choices(self):
        self.assertEqual(self.client.get('/api/places/', {'state': 'TX'}).json(), {'cities': []})
