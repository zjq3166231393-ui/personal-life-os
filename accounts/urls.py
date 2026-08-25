from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.AccountLoginView.as_view(), name="login"),
    path("logout/", views.AccountLogoutView.as_view(), name="logout"),
    path("profile/", views.profile, name="profile"),
    path("avatar/upload/", views.avatar_upload, name="avatar_upload"),
    path("avatar/history/select/", views.avatar_history_select, name="avatar_history_select"),
    path("change-identity/", views.change_identity, name="change_identity"),  # 用户名/邮箱/手机号（POST，限次）
    path("password-change/", auth_views.PasswordChangeView.as_view(template_name="accounts/password_change.html", success_url="/accounts/password-change/done/"), name="password_change"),
    path("password-change/done/", auth_views.PasswordChangeDoneView.as_view(template_name="accounts/password_change_done.html"), name="password_change_done"),
    path("password-reset/", auth_views.PasswordResetView.as_view(template_name="accounts/password_reset.html"), name="password_reset"),
    path("password-reset/done/", auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"), name="password_reset_done"),
    path("password-reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(template_name="accounts/password_reset_confirm.html"), name="password_reset_confirm"),
    path("password-reset/complete/", auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"), name="password_reset_complete"),
    path("export/", views.export_data, name="export_data"),
    path("delete-account/", views.delete_account, name="delete_account"),
]