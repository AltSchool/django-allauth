from django.conf.urls import url, include
from allauth.utils import import_attribute

from allauth.account import app_settings
from allauth.socialaccount.providers.oauth2.views import proxy_login_callback

def default_urlpatterns(provider):
    login_view = import_attribute(
        provider.get_package() + '.views.oauth2_login')
    callback_view = import_attribute(
        provider.get_package() + '.views.oauth2_callback')

    urlpatterns = [
        url('^login/$',
            login_view, name=provider.id + "_login"),
        url('^login/callback/$',
            callback_view, name=provider.id + "_callback"),
    ]
    if (
        app_settings.LOGIN_PROXY_REDIRECT_WHITELIST or
        app_settings.LOGIN_PROXY_REDIRECT_DOMAIN_WHITELIST
    ):
        urlpatterns += patterns('',
            url('^login/callback/proxy/$',
                proxy_login_callback,
                {'callback_view_name': provider.id + '_callback'},
                name=provider.id + '_proxy')
        )

    return [url('^' + provider.get_slug() + '/', include(urlpatterns))]
