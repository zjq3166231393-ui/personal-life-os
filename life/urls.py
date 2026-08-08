from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("api/parse/", views.parse_entry, name="parse_entry"),
    path("api/save/", views.save_entry, name="save_entry"),
]

