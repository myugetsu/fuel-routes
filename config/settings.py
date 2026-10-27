import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'local-development-only-change-before-deployment')
DEBUG = os.environ.get('DJANGO_DEBUG', '0') == '1'
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
ROOT_URLCONF = 'config.urls'
INSTALLED_APPS = ['django.contrib.contenttypes', 'django.contrib.staticfiles', 'planner']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.middleware.common.CommonMiddleware']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'APP_DIRS': True}]
STATIC_URL = 'static/'
WSGI_APPLICATION = 'config.wsgi.application'
USE_TZ = True
# Database cache shares results and the provider rate gate between WSGI workers.
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.db.DatabaseCache', 'LOCATION': 'api_cache'}}
OSRM_URL = os.environ.get('OSRM_URL', 'https://routing.openstreetmap.de/routed-car')
PROVIDER_USER_AGENT = os.environ.get('PROVIDER_USER_AGENT', 'FuelStationRouteAssessment/1.0')
OSRM_MIN_INTERVAL = max(0, int(os.environ.get('OSRM_MIN_INTERVAL', '2')))
ROUTE_CACHE_SECONDS = 86400
CORRIDOR_MILES = 1.0
MAX_ROUTE_WAYPOINTS = 25
DATA_UPLOAD_MAX_MEMORY_SIZE = 16384

# An enriched snapshot is preferred; original prices + a coordinate sidecar also work.
STATIONS_CSV = Path(os.environ.get('STATIONS_CSV', BASE_DIR / 'data/stations.csv'))
STATION_PRICES_CSV = Path(os.environ.get('STATION_PRICES_CSV', BASE_DIR / 'fuel-prices-for-be-assessment.csv'))
STATION_COORDINATES_CSV = Path(os.environ.get('STATION_COORDINATES_CSV', BASE_DIR / 'data/station-coordinates.csv'))
