# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import json
import sys

from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sites.models import Site
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings
from django.urls import NoReverseMatch, clear_url_caches, reverse
from django.utils import six
from django.utils.http import urlunquote_plus as urlunquote

from allauth.compat import parse_qs, urlparse
from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers.fake.views import FakeOAuth2Adapter

from .views import MissingParameter, OAuth2LoginView, proxy_login_callback


class OAuth2TestsMixin(object):
    def param(self, param, url):
        # Look for a redirect uri
        parsed_url = urlparse(urlunquote(url))
        queries = parse_qs(parsed_url.query)
        return queries.get(param, ['']).pop()

    def init_request(self, endpoint, params):
        request = RequestFactory().get(reverse(endpoint), params)
        SessionMiddleware().process_request(request)
        return request

    def setUp(self):
        app = SocialApp.objects.create(
            provider=FakeOAuth2Adapter.provider_id,
            name=FakeOAuth2Adapter.provider_id,
            client_id='app123id',
            key=FakeOAuth2Adapter.provider_id,
            secret='dummy',
        )
        app.sites.add(Site.objects.get_current())
        super(OAuth2TestsMixin, self).setUp()


class OAuth2TestsNoProxying(OAuth2TestsMixin, TestCase):
    def test_proxyless_login(self):
        request = self.init_request('fake_login', dict(process='login'))
        login_view = OAuth2LoginView.adapter_view(FakeOAuth2Adapter)
        login_response = login_view(request)
        self.assertEqual(login_response.status_code, 302)  # Redirect
        self.assertEqual(
            self.param('redirect_uri', login_response['location']),
            'http://testserver/fake/login/callback/',
        )

    def test_is_not_login_proxy(self):
        with self.assertRaises(NoReverseMatch):
            reverse('fake_proxy')


@override_settings(ACCOUNT_LOGIN_CALLBACK_PROXY='https://loginproxy')
class OAuth2TestsUsesProxy(OAuth2TestsMixin, TestCase):
    def test_login_by_proxy(self):
        request = self.init_request('fake_login', dict(process='login'))
        login_view = OAuth2LoginView.adapter_view(FakeOAuth2Adapter)
        login_response = login_view(request)
        self.assertEqual(login_response.status_code, 302)  # Redirect
        self.assertEqual(
            self.param('redirect_uri', login_response['location']),
            'https://loginproxy/fake/login/callback/proxy/'
        )
        state = json.loads(self.param('state', login_response['location']))
        self.assertEqual(state['host'], 'http://testserver/fake/login/')

    def test_is_not_login_proxy(self):
        with self.assertRaises(NoReverseMatch):
            reverse('fake_proxy')


@override_settings(
    ACCOUNT_LOGIN_PROXY_REDIRECT_WHITELIST=(
        'https://cheshirecat,https://tweedledee,'
    ),
    ACCOUNT_LOGIN_PROXY_REDIRECT_DOMAIN_WHITELIST='sub.domain.com,'
)
class OAuth2TestsIsProxy(OAuth2TestsMixin, TestCase):
    def reload_urls(self):
        for module in sys.modules:
            if module.endswith('urls'):
                six.moves.reload_module(sys.modules[module])
        clear_url_caches()

    def setUp(self):
        super(OAuth2TestsIsProxy, self).setUp()
        self.reload_urls()

    @override_settings(
        ACCOUNT_LOGIN_PROXY_REDIRECT_WHITELIST='',
        ACCOUNT_LOGIN_PROXY_REDIRECT_DOMAIN_WHITELIST='',
    )
    def tearDown(self):
        self.reload_urls()
        super(OAuth2TestsIsProxy, self).tearDown()

    def tests_is_login_proxy(self):
        self.assertIsNotNone(reverse('fake_proxy'))

    def test_rejects_request_with_no_host_in_state(self):
        request = self.init_request('fake_proxy', dict(process='login'))
        with self.assertRaises(MissingParameter):
            proxy_login_callback(request, callback_view_name='fake_callback')

    def test_rejects_request_with_unwhitelisted_host(self):
        state = {'host': 'https://bar.domain.com'}
        request = self.init_request(
            'fake_proxy', dict(process='login', state=json.dumps(state)))
        with self.assertRaises(PermissionDenied):
            proxy_login_callback(request, callback_view_name='fake_callback')

    def tests_redirects_request_with_whitelisted_host(self):
        state = {'host': 'https://tweedledee'}
        serialized_state = json.dumps(state)
        request = self.init_request(
            'fake_proxy',
            dict(process='login', state=serialized_state)
        )
        proxy_response = proxy_login_callback(
            request,
            callback_view_name='fake_callback',
        )
        self.assertEqual(proxy_response.status_code, 302)  # Redirect
        expected_url = 'https://tweedledee/fake/login/callback/'
        actual_url = proxy_response['location']
        self.assertTrue(actual_url.startswith(expected_url))
        self.assertEqual(self.param('process', actual_url), 'login')
        self.assertEqual(json.loads(self.param('state', actual_url)), state)

    def tests_redirects_request_with_domain_whitelisted_host(self):
        state = {'host': 'https://foo.sub.domain.com'}
        serialized_state = json.dumps(state)
        request = self.init_request(
            'fake_proxy',
            dict(process='login', state=serialized_state),
        )
        proxy_response = proxy_login_callback(
            request,
            callback_view_name='fake_callback',
        )
        self.assertEqual(proxy_response.status_code, 302)  # Redirect
        expected_url = 'https://foo.sub.domain.com/fake/login/callback/'
        actual_url = proxy_response['location']
        self.assertTrue(actual_url.startswith(expected_url))
        self.assertEqual(self.param('process', actual_url), 'login')
        self.assertEqual(json.loads(self.param('state', actual_url)), state)

    def test_rejects_request_with_scheme_mismatch(self):
        state = {'host': 'ftp://tweedledee'}
        request = self.init_request(
            'fake_proxy',
            dict(process='login', state=json.dumps(state)),
        )
        with self.assertRaises(PermissionDenied):
            proxy_login_callback(
                request,
                callback_view_name='fake_callback',
            )

    def test_rejects_request_with_whitelisted_prefix(self):
        state = {'host': 'https://tweedledee.creds4u.biz'}
        request = self.init_request(
            'fake_proxy',
            dict(process='login', state=json.dumps(state))
        )
        with self.assertRaises(PermissionDenied):
            proxy_login_callback(
                request,
                callback_view_name='fake_callback',
            )
