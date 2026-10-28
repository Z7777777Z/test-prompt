"""根 URL 配置。"""
from django.urls import include, path

urlpatterns = [
    path("", include("tp.urls")),
]
