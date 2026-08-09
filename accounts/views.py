from django import forms
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True, label="邮箱")

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


def register(request):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("home")
    else:
        form = RegisterForm()
    return render(request, "accounts/register.html", {"form": form})


class AccountLoginView(LoginView):
    template_name = "accounts/login.html"
    next_page = reverse_lazy("home")

    def form_valid(self, form):
        return super().form_valid(form)


class AccountLogoutView(LogoutView):
    next_page = reverse_lazy("login")
    http_method_names = ["get", "post", "options"]