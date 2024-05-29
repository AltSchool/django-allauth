#!/usr/bin/env python
import io
from setuptools import find_packages, setup

test_requirements = []

IS_PY2 = sys.version_info[0] < 3

if IS_PY2:
    openid_package = 'python-openid >= 2.2.5'
    test_requirements.append('mock >= 1.0.1')
else:
    openid_package = 'python3-openid >= 3.0.8'

long_description = io.open('README.rst', encoding='utf-8').read()

# Dynamically calculate the version based on allauth.VERSION.
version = __import__('allauth').__version__

METADATA = dict(
    name='django-allauth',
    version='0.20.1',
    author='Raymond Penners',
    author_email='raymond.penners@intenct.nl',
    description='Integrated set of Django applications addressing'
    ' authentication, registration, account management as well as'
    ' 3rd party (social) account authentication.',
    long_description=long_description,
    url='http://github.com/pennersr/django-allauth',
    keywords='django auth account social openid twitter facebook oauth'
    ' registration',
    tests_require=test_requirements,
    install_requires=['Django >= 1.11',
                      openid_package,
                      'requests-oauthlib >= 0.3.0',
                      "requests"],
    include_package_data=True,
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Environment :: Web Environment',
        'Topic :: Internet',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Framework :: Django',
        'Framework :: Django :: 1.11',
        'Framework :: Django :: 2.0',
        'Framework :: Django :: 2.1',
        'Framework :: Django :: 2.2',
    ],
    packages=find_packages(exclude=['example']),
)

if __name__ == '__main__':
    setup(**METADATA)
