import os
from .settings import *  # noqa
from .settings import BASE_DIR

# Configure the domain name using the environment variable
# that Azure automatically creates for us.
# Fetch custom domains from environment variables, separated by commas
CUSTOM_DOMAINS = os.environ.get('CUSTOM_DOMAINS', '').split(',')

# Configure ALLOWED_HOSTS
ALLOWED_HOSTS = []
if 'WEBSITE_HOSTNAME' in os.environ:
    ALLOWED_HOSTS.append(os.environ['WEBSITE_HOSTNAME'])
ALLOWED_HOSTS += [domain.strip() for domain in CUSTOM_DOMAINS if domain.strip()]

# Configure CSRF_TRUSTED_ORIGINS
CSRF_TRUSTED_ORIGINS = []
if 'WEBSITE_HOSTNAME' in os.environ:
    CSRF_TRUSTED_ORIGINS.append('https://' + os.environ['WEBSITE_HOSTNAME'])
CSRF_TRUSTED_ORIGINS += [f'https://{domain.strip()}' for domain in CUSTOM_DOMAINS if domain.strip()]

DEBUG = True

# WhiteNoise configuration and Middleware list
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'azureproject.redirect_www_middleware.RedirectWWWToRootDomainMiddleware',
    'azureproject.middleware.DomainURLRoutingMiddleware',  # <-- Added multi-domain routing middleware
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

# Set default URL configuration; this will be overridden by our middleware as needed.
ROOT_URLCONF = 'azureproject.urls_default'

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "static"),
]


SITE_ID = 2

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Configure Postgres database based on connection string of the libpq Keyword/Value form
conn_str = os.environ['AZURE_POSTGRESQL_CONNECTIONSTRING']
conn_str_params = {pair.split('=')[0]: pair.split('=')[1] for pair in conn_str.split(' ')}
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': conn_str_params['dbname'],
        'HOST': conn_str_params['host'],
        'USER': conn_str_params['user'],
        'PASSWORD': conn_str_params['password'],
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

# Uncomment and configure the following if you wish to use cache-backed sessions:
# SESSION_ENGINE = "django.contrib.sessions.backends.cache"
# CACHES = {
#    "default": {
#        "BACKEND": "django_redis.cache.RedisCache",
#        "LOCATION": os.environ.get('AZURE_REDIS_CONNECTIONSTRING'),
#        "OPTIONS": {
#            "CLIENT_CLASS": "django_redis.client.DefaultClient",
#            "COMPRESSOR": "django_redis.compressors.zlib.ZlibCompressor",
#        },
#    }
# }
