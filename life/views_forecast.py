"""现金流预测页面（洞察增强）。

复用 ``forecast.cashflow_forecast`` 服务，把未来 30 天的余额走势与即将扣款
以图表 + 列表呈现。对标 MoneyWiz 的现金流预测能力。
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .forecast import cashflow_forecast

FORECAST_DAYS = 30


@login_required
def forecast(request):
    cf = cashflow_forecast(request.user, days=FORECAST_DAYS)
    return render(
        request,
        "life/forecast.html",
        {"cf": cf, "days": FORECAST_DAYS},
    )
