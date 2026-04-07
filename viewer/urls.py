from django.urls import path
from . import views

urlpatterns = [
    # Pages
    path("",                                views.search_page,     name="search"),
    path("viewer/<str:study_id>/",          views.viewer_page,     name="viewer"),

    # API
    path("api/search/",                     views.api_search,          name="api_search"),
    path("api/load/<str:study_id>/",        views.api_load_study,      name="api_load"),
    path("api/status/<str:study_id>/",      views.api_study_status,    name="api_status"),
    path("api/preview/<str:study_id>/<str:plane>/", views.api_preview,  name="api_preview"),
    path("api/instances/<str:study_id>/",   views.api_instances,       name="api_instances"),
    path("api/frames/<str:study_id>/<int:frame_idx>/<str:plane>/",
         views.api_frame, name="api_frame"),
]
