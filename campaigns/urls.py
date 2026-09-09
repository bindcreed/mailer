from django.urls import path

from campaigns import views

urlpatterns = [
    path("", views.session_list, name="session_list"),

    path("smtp/", views.smtp_list, name="smtp_list"),
    path("smtp/add/", views.smtp_create, name="smtp_create"),
    path("smtp/<int:pk>/edit/", views.smtp_edit, name="smtp_edit"),
    path("smtp/<int:pk>/delete/", views.smtp_delete, name="smtp_delete"),
    path("smtp/<int:pk>/test/", views.smtp_test, name="smtp_test"),

    path("sessions/new/", views.session_create, name="session_create"),
    path("sessions/<int:pk>/", views.session_detail, name="session_detail"),
    path("sessions/<int:pk>/start/", views.session_start, name="session_start"),
    path("sessions/<int:pk>/stop/", views.session_stop, name="session_stop"),
    path("sessions/<int:pk>/status.json", views.session_status_json, name="session_status_json"),
    path("sessions/<int:pk>/upload/", views.session_upload, name="session_upload"),
    path("sessions/<int:pk>/upload/mapped/", views.session_upload_mapped, name="session_upload_mapped"),
    path("sessions/<int:pk>/upload/confirm/", views.session_upload_confirm, name="session_upload_confirm"),
]
