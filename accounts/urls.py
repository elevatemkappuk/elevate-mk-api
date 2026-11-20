from django.urls import path

from accounts.views import (
    CsrfView,
    LoginView,
    LogoutView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    TokenLoginView,
    TokenLogoutView,
    TokenRefreshEndpointView,
)


urlpatterns = [
    path("auth/csrf/", CsrfView.as_view(), name="auth-csrf"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/token/", TokenLoginView.as_view(), name="auth-token-login"),
    path("auth/token/refresh/", TokenRefreshEndpointView.as_view(), name="auth-token-refresh"),
    path("auth/token/logout/", TokenLogoutView.as_view(), name="auth-token-logout"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    path("auth/password-reset/", PasswordResetRequestView.as_view(), name="auth-password-reset"),
    path("auth/password-reset/confirm/", PasswordResetConfirmView.as_view(), name="auth-password-reset-confirm"),
]
