from django.urls import path
from planner import views
urlpatterns = [path('', views.index, name='index'),
               path('api/places/', views.places, name='places'),
               path('api/routes/', views.route_plan, name='route-plan'),
               path('maps/<uuid:plan_id>/', views.route_map, name='route-map')]
