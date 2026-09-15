from django.urls import path

from . import views

# Mounted under /api/ by config.urls. Flat and small, as S7 specifies.
urlpatterns = [
    path('health/', views.health, name='health'),
    path('availability/', views.availability, name='availability'),
    path('bookings/', views.create_booking, name='create-booking'),
    # token_urlsafe uses [A-Za-z0-9_-], all of which <str:> accepts.
    path('bookings/<str:reference>/confirm/', views.confirm_booking, name='confirm-booking'),
    path('bookings/<str:reference>/songs/', views.song_requests, name='song-requests'),
]
