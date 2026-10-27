from django.db import models


class Station(models.Model):
    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    price = models.DecimalField(max_digits=12, decimal_places=8)
    latitude = models.FloatField(null=True)
    longitude = models.FloatField(null=True)
    coordinate_source = models.CharField(max_length=100, blank=True)
    geocoding_attempted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['latitude', 'longitude'], name='station_location_idx')]


class Place(models.Model):
    key = models.CharField(max_length=150, unique=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
