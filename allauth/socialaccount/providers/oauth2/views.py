from __future__ import absolute_import

from datetime import timedelta
from urlparse import urljoin
from urlparse import urlparse

from django.core.exceptions import PermissionDenied
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect
from django.utils import timezone

from allauth.utils import build_absolute_uri
from allauth.account import app_settings
from allauth.socialaccount.helpers import render_authentication_error
from allauth.socialaccount import providers
from allauth.socialaccount.providers.oauth2.client import (OAuth2Client,
                                                           OAuth2Error)
from allauth.socialaccount.helpers import complete_social_login
from allauth.socialaccount.models import SocialToken, SocialLogin
from allauth.utils import get_request_param
from ..base import AuthAction, AuthError


class MissingParameter(Exception):
    pass

class OAuth2Adapter(object):
    expires_in_key = 'expires_in'
    supports_state = True
    redirect_uri_protocol = None  # None: use ACCOUNT_DEFAULT_HTTP_PROTOCOL
    access_token_method = 'POST'
    login_cancelled_error = 'access_denied'

    def get_provider(self):
        return providers.registry.by_id(self.provider_id)

    def complete_login(self, request, app, access_token, **kwargs):
        """
        Returns a SocialLogin instance
        """
        raise NotImplementedError

    def parse_token(self, data):
        token = SocialToken(token=data['access_token'])
        token.token_secret = data.get('refresh_token', '')
        expires_in = data.get(self.expires_in_key, None)
        if expires_in:
            token.expires_at = timezone.now() + timedelta(
                seconds=int(expires_in))
        return token


class OAuth2View(object):
    @classmethod
    def adapter_view(cls, adapter):
        def view(request, *args, **kwargs):
            self = cls()
            self.request = request
            self.adapter = adapter()
            return self.dispatch(request, *args, **kwargs)
        return view

    def get_client(self, request, app):
        callback_url = reverse(self.adapter.provider_id + "_callback")
        if app_settings.LOGIN_CALLBACK_PROXY:
            callback_url = urljoin(app_settings.LOGIN_CALLBACK_PROXY, callback_url)
            callback_url = '%s/proxy/' % callback_url.rstrip('/')
        else:
            protocol = (self.adapter.redirect_uri_protocol
                        or app_settings.DEFAULT_HTTP_PROTOCOL)
            callback_url = build_absolute_uri(
                request, callback_url, protocol=protocol)
        provider = self.adapter.get_provider()
        scope = provider.get_scope(request)
        client = OAuth2Client(self.request, app.client_id, app.secret,
                              self.adapter.access_token_method,
                              self.adapter.access_token_url,
                              callback_url,
                              scope)
        return client


class OAuth2LoginView(OAuth2View):
    def dispatch(self, request):
        provider = self.adapter.get_provider()
        app = provider.get_app(self.request)
        client = self.get_client(request, app)
        action = request.GET.get('action', AuthAction.AUTHENTICATE)
        auth_url = self.adapter.authorize_url
        auth_params = provider.get_auth_params(request, action)
        print('### stashing state')
        from django.conf import settings
        print('### DB URL:')
        from pprint import pprint
        pprint(settings.DATABASES)
        client.state = SocialLogin.stash_state(request)
        print('### state stashed')

        print('### login session:')
        pprint(request.session.__dict__)
        try:
            return HttpResponseRedirect(client.get_redirect_url(
                auth_url, auth_params))
        except OAuth2Error as e:
            return render_authentication_error(
                request,
                provider.id,
                exception=e)


class OAuth2CallbackView(OAuth2View):
    def dispatch(self, request):
        from pprint import pprint
        print('### request:')
        pprint(request)
        print('### callback session:')
        pprint(request.session.__dict__)
        if 'error' in request.GET or 'code' not in request.GET:
            # Distinguish cancel from error
            auth_error = request.GET.get('error', None)
            if auth_error == self.adapter.login_cancelled_error:
                error = AuthError.CANCELLED
            else:
                error = AuthError.UNKNOWN
            print('### auth error:')
            pprint(error)
            return render_authentication_error(
                request,
                self.adapter.provider_id,
                error=error)
        app = self.adapter.get_provider().get_app(self.request)
        client = self.get_client(request, app)
        try:
            access_token = client.get_access_token(request.GET['code'])
            token = self.adapter.parse_token(access_token)
            token.app = app
            print '### pre-adapter-complete-login'
            print '### token: %s' % token
            login = self.adapter.complete_login(request,
                                                app,
                                                token,
                                                response=access_token)
            print '### post-adapter-complete login'
            from django.conf import settings
            pprint(settings.DATABASES)
            login.token = token
            if self.adapter.supports_state:
                print '### pre-parse-and-verify'
                login.state = SocialLogin.parse_and_verify_url_state(request)
                print '### post-parse-and-verify'
            else:
                print '### pre unstash'
                login.state = SocialLogin.unstash_state(request)
                print '### post unstash'
            print '### pre-complete-social-login'
            return complete_social_login(request, login)
            print '### post-complete-login'
        except (PermissionDenied, OAuth2Error) as e:
            print '### ERROR!!!'
            import traceback
            traceback.print_exc(e)
            return render_authentication_error(
                request,
                self.adapter.provider_id,
                exception=e)


def target_in_whitelist(parsed_target):
    target_loc = parsed_target.netloc
    target_scheme = parsed_target.scheme
    for allowed in app_settings.LOGIN_PROXY_REDIRECT_WHITELIST:
        parsed_allowed = urlparse(allowed)
        allowed_loc = parsed_allowed.netloc
        allowed_scheme = parsed_allowed.scheme
        if target_loc == allowed_loc and target_scheme == allowed_scheme:
            return True
    for allowed in app_settings.LOGIN_PROXY_REDIRECT_DOMAIN_WHITELIST:
        parsed_allowed = urlparse(allowed)
        allowed_loc = parsed_allowed.netloc
        if target_loc.endswith(allowed_loc):
            return True
    return False


def proxy_login_callback(request, **kwargs):
    unverified_state = SocialLogin.parse_url_state(request)
    if 'host' not in unverified_state:
        raise MissingParameter()

    parsed_target = urlparse(unverified_state['host'])
    if not target_in_whitelist(parsed_target):
        raise PermissionDenied()

    relative_callback = reverse(kwargs.get('callback_view_name'))
    redirect = urljoin(unverified_state['host'], relative_callback)

    # URLUnparse would be ideal here, but it's buggy.
    # It used a semicolon instead of a question mark, which neither Django nor I
    # understand. Neither of us have time for that nonsense, so add params
    # manually.
    redirect_with_params = '%s?%s' % (redirect, request.GET.urlencode())
    return HttpResponseRedirect(redirect_with_params)
