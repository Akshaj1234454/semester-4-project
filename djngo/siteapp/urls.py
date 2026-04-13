from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/clusters/", views.api_clusters, name="api_clusters"),
    path("api/clusters/delete/", views.api_delete_cluster, name="api_delete_cluster"),
    path("api/geocode/", views.api_geocode, name="api_geocode"),
    path("api/roads/", views.api_roads, name="api_roads"),
]
