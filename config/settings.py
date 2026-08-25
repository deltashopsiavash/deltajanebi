import os
from pathlib import Path
import dj_database_url
BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.getenv("DEBUG", "0") == "1"
ALLOWED_HOSTS = [x.strip() for x in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if x.strip()]
DOMAIN = os.getenv("DOMAIN", "localhost")
CSRF_TRUSTED_ORIGINS = [f"https://{DOMAIN}"] if DOMAIN not in ("localhost", "127.0.0.1") else []
INSTALLED_APPS = ["django.contrib.admin","django.contrib.auth","django.contrib.contenttypes","django.contrib.sessions","django.contrib.messages","django.contrib.staticfiles","shop"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware","whitenoise.middleware.WhiteNoiseMiddleware","django.contrib.sessions.middleware.SessionMiddleware","django.middleware.common.CommonMiddleware","django.middleware.csrf.CsrfViewMiddleware","django.contrib.auth.middleware.AuthenticationMiddleware","django.contrib.messages.middleware.MessageMiddleware","django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND":"django.template.backends.django.DjangoTemplates","DIRS":[BASE_DIR / "templates"],"APP_DIRS":True,"OPTIONS":{"context_processors":["django.template.context_processors.request","django.contrib.auth.context_processors.auth","django.contrib.messages.context_processors.messages","shop.context_processors.store_context"]}}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": dj_database_url.parse(os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR/'db.sqlite3'}"), conn_max_age=60)}
AUTH_PASSWORD_VALIDATORS = [{"NAME":"django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},{"NAME":"django.contrib.auth.password_validation.MinimumLengthValidator"},{"NAME":"django.contrib.auth.password_validation.CommonPasswordValidator"},{"NAME":"django.contrib.auth.password_validation.NumericPasswordValidator"}]
LANGUAGE_CODE="fa-ir"; TIME_ZONE="Asia/Tehran"; USE_I18N=True; USE_TZ=True
STATIC_URL="/static/"; STATIC_ROOT=BASE_DIR/"staticfiles"; STATICFILES_DIRS=[BASE_DIR/"static"]
MEDIA_URL="/media/"; MEDIA_ROOT=BASE_DIR/"media"
DEFAULT_AUTO_FIELD="django.db.models.BigAutoField"; AUTH_USER_MODEL="shop.User"
LOGIN_URL="login"; LOGIN_REDIRECT_URL="account_orders"; LOGOUT_REDIRECT_URL="home"
EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend" if os.getenv("EMAIL_HOST_USER") else "django.core.mail.backends.console.EmailBackend"
EMAIL_HOST=os.getenv("EMAIL_HOST","smtp.gmail.com"); EMAIL_PORT=int(os.getenv("EMAIL_PORT","587")); EMAIL_HOST_USER=os.getenv("EMAIL_HOST_USER",""); EMAIL_HOST_PASSWORD=os.getenv("EMAIL_HOST_PASSWORD",""); EMAIL_USE_TLS=os.getenv("EMAIL_USE_TLS","1")=="1"; DEFAULT_FROM_EMAIL=os.getenv("DEFAULT_FROM_EMAIL") or EMAIL_HOST_USER or "noreply@localhost"
SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO","https"); SESSION_COOKIE_SECURE=DOMAIN not in ("localhost","127.0.0.1"); CSRF_COOKIE_SECURE=SESSION_COOKIE_SECURE; SECURE_CONTENT_TYPE_NOSNIFF=True; X_FRAME_OPTIONS="DENY"
