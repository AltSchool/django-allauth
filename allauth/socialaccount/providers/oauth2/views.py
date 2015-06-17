from __future__ import absolute_import

import json
from datetime import timedelta
from requests import RequestException
from urllib.parse import urljoin, urlparse

from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone

from allauth.exceptions import ImmediateHttpResponse
from allauth.socialaccount import providers
from allauth.socialaccount.helpers import (
    complete_social_login,
    render_authentication_error,
)
from allauth.socialaccount.models import SocialLogin, SocialToken
from allauth.socialaccount.providers.base import ProviderException
from allauth.socialaccount.providers.base.constants import (
    AuthAction,
    AuthError,
)
from allauth.socialaccount.providers.base.mixins import OAuthLoginMixin
from allauth.socialaccount.providers.oauth2.client import (
    OAuth2Client,
    OAuth2Error,
)
from allauth.account import app_settings
from allauth.utils import build_absolute_uri, get_request_param


class MissingParameter(Exception):
    pass


class OAuth2Adapter(object):
    expires_in_key = "expires_in"
    client_class = OAuth2Client
    supports_state = True
    redirect_uri_protocol = None
    access_token_method = "POST"
    login_cancelled_error = "access_denied"
    scope_delimiter = " "
    basic_auth = False
    headers = None

    def __init__(self, request):
        self.request = request

    def get_provider(self):
        return providers.registry.by_id(self.provider_id, self.request)

    def complete_login(self, request, app, access_token, **kwargs):
        """
        Returns a SocialLogin instance
        """
        raise NotImplementedError

    def get_callback_url(self, request, app):
        callback_url = reverse(self.provider_id + "_callback")
        protocol = self.redirect_uri_protocol
        return build_absolute_uri(request, callback_url, protocol)

    def parse_token(self, data):
        token = SocialToken(token=data["access_token"])
        token.token_secret = data.get("refresh_token", "")
        expires_in = data.get(self.expires_in_key, None)
        if expires_in:
            token.expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        return token

    def get_access_token_data(self, request, app, client):
        code = get_request_param(self.request, "code")
        return client.get_access_token(code)


class OAuth2View(object):
    @classmethod
    def adapter_view(cls, adapter):
        def view(request, *args, **kwargs):
            self = cls()
            self.request = request
            self.adapter = adapter(request)
            try:
                return self.dispatch(request, *args, **kwargs)
            except ImmediateHttpResponse as e:
                return e.response

        return view

    def get_client(self, request, app):
        if app_settings.LOGIN_CALLBACK_PROXY:
            callback_url = reverse(self.adapter.provider_id + "_callback")
            callback_url = urljoin(app_settings.LOGIN_CALLBACK_PROXY, callback_url)
            callback_url = "%s/proxy/" % callback_url.rstrip("/")
        else:
            callback_url = self.adapter.get_callback_url(request, app)
        provider = self.adapter.get_provider()
        scope = provider.get_scope(request)
        client = self.adapter.client_class(
            self.request,
            app.client_id,
            app.secret,
            self.adapter.access_token_method,
            self.adapter.access_token_url,
            callback_url,
            scope,
            scope_delimiter=self.adapter.scope_delimiter,
            headers=self.adapter.headers,
            basic_auth=self.adapter.basic_auth,
        )
        return client


class OAuth2LoginView(OAuthLoginMixin, OAuth2View):
    def dispatch(self, request, *args, **kwargs):
        provider = self.adapter.get_provider()
        app = provider.get_app(self.request)
        client = self.get_client(request, app)
        action = request.GET.get("action", AuthAction.AUTHENTICATE)
        auth_url = self.adapter.authorize_url
        auth_params = provider.get_auth_params(request, action)
        client.state = SocialLogin.stash_state(request)
        try:
            return HttpResponseRedirect(client.get_redirect_url(auth_url, auth_params))
        except OAuth2Error as e:
            return render_authentication_error(request, provider.id, exception=e)

    def login(self, request, *args, **kwargs):
        provider = self.adapter.get_provider()
        app = provider.get_app(self.request)
        client = self.get_client(request, app)
        action = request.GET.get("action", AuthAction.AUTHENTICATE)
        auth_url = self.adapter.authorize_url
        auth_params = provider.get_auth_params(request, action)
        client.state = SocialLogin.stash_state(request)
        try:
            return HttpResponseRedirect(client.get_redirect_url(auth_url, auth_params))
        except OAuth2Error as e:
            return render_authentication_error(request, provider.id, exception=e)


class OAuth2CallbackView(OAuth2View):
    def dispatch(self, request, *args, **kwargs):
        if "error" in request.GET or "code" not in request.GET:
            # Distinguish cancel from error
            auth_error = request.GET.get("error", None)
            if auth_error == self.adapter.login_cancelled_error:
                error = AuthError.CANCELLED
            else:
                error = AuthError.UNKNOWN
            return render_authentication_error(
                request, self.adapter.provider_id, error=error
            )
        app = self.adapter.get_provider().get_app(self.request)
        client = self.get_client(self.request, app)

        try:
            access_token = self.adapter.get_access_token_data(request, app, client)
            token = self.adapter.parse_token(access_token)
            token.app = app
            login = self.adapter.complete_login(
                request, app, token, response=access_token
            )
            login.token = token
            if self.adapter.supports_state:
                login.state = SocialLogin.verify_and_unstash_state(
                    request, get_request_param(request, "state")
                )
            else:
                login.state = SocialLogin.unstash_state(request)

            return complete_social_login(request, login)
        except (
            PermissionDenied,
            OAuth2Error,
            RequestException,
            ProviderException,
        ) as e:
            return render_authentication_error(
                request, self.adapter.provider_id, exception=e
            )


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
        if not allowed.startswith("http"):
            allowed = f"https://{allowed}"  # scheme doesnt patter to us, but is required for urlparse
        parsed_allowed = urlparse(allowed)
        allowed_loc = parsed_allowed.netloc
        if allowed_loc and target_loc.endswith(allowed_loc):
            return True
    return False


def proxy_login_callback(request, **kwargs):
    unverified_state = get_request_param(request, "state")
    unverified_state = json.loads(unverified_state) if unverified_state else {}

    if "host" not in unverified_state:
        raise MissingParameter()

    parsed_target = urlparse(unverified_state["host"])
    if not target_in_whitelist(parsed_target):
        raise PermissionDenied()

    relative_callback = reverse(kwargs.get("callback_view_name"))
    redirect = urljoin(unverified_state["host"], relative_callback)

    # URLUnparse would be ideal here, but it's buggy.
    # It used a semicolon instead of a question mark, which neither Django nor I
    # understand. Neither of us have time for that nonsense, so add params
    # manually.
    redirect_with_params = "%s?%s" % (redirect, request.GET.urlencode())
    return HttpResponseRedirect(redirect_with_params)
