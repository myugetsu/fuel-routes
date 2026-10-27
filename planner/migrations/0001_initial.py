from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(name='Station', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('opis_id', models.PositiveIntegerField(unique=True)),
            ('name', models.CharField(max_length=200)),
            ('address', models.CharField(max_length=300)),
            ('city', models.CharField(max_length=100)),
            ('state', models.CharField(max_length=2)),
            ('price', models.DecimalField(max_digits=12, decimal_places=8)),
            ('latitude', models.FloatField(null=True)),
            ('longitude', models.FloatField(null=True)),
            ('coordinate_source', models.CharField(blank=True, max_length=100)),
        ], options={'indexes': [models.Index(fields=['latitude', 'longitude'], name='station_location_idx')]}),
        migrations.CreateModel(name='Place', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('key', models.CharField(max_length=150, unique=True)),
            ('latitude', models.FloatField()), ('longitude', models.FloatField()),
        ]),
    ]
