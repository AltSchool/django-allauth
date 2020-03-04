from django.urls import path, include
from allauth.socialaccount.providers.oauth2.urls import default_urlpatterns

from .provider import GoogleProvider
from .views import oauth2_callback_android


android_path = path(
    '%s/login/callback_android' % GoogleProvider.id,
    oauth2_callback_android,
    name=GoogleProvider.id + '_callback_android'
)
urlpatterns = default_urlpatterns(GoogleProvider, [android_path])
