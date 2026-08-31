"""第三方账单导入测试（P0-2）。

中文用户的流水九成在支付宝和微信里，导入能不能吃下这两家的原生 CSV，
直接决定这个应用「敢不敢开始用」。

样本按两家的真实导出格式构造：GBK 编码、十几行前置说明、真正的表头在文件中间、
交易状态用中文表达、金额带 ￥ 前缀、还有大量「不计收支」的内部划转。
"""

from datetime import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from life.importers import (
    SOURCE_ALIPAY,
    SOURCE_WECHAT,
    detect_source,
    parse_statement,
)

from .models import Category, Expense

# ── 支付宝账单样本（新版格式，GBK 导出）──────────────────────────────
ALIPAY_CSV = """支付宝交易明细查询
账号:13900000000
起始日期:2026-08-01 终止日期:2026-08-30
---------------------------------
交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注
2026-08-30 12:34:56,餐饮美食,沙县小吃,,拌面加蛋,支出,18.50,余额宝,交易成功,202608301234001,,
2026-08-29 09:10:11,交通出行,滴滴出行,,快车,支出,32.00,花呗,交易成功,202608290910002,,
2026-08-28 20:00:00,转账红包,张三,,转账,支出,100.00,余额,交易关闭,202608282000003,,
2026-08-27 15:00:00,投资理财,余额宝,,转入,不计收支,1000.00,余额宝,交易成功,202608271500004,,
2026-08-26 08:00:00,工资,某某公司,,八月工资,收入,12000.00,银行卡,交易成功,202608260800005,,
2026-08-25 10:00:00,购物,某电商,,衣服,支出,299.00,花呗,已全额退款,202608251000006,,
2026-08-24 19:00:00,日用百货,便利店,,日用品,支出,￥26.80,余额,交易成功,202608241900007,,
---------------------------------
本文件由支付宝提供
"""

# ── 微信支付账单样本（GBK 导出）──────────────────────────────────────
WECHAT_CSV = """微信支付账单明细
微信昵称：[测试用户]
起始时间：2026-08-01 00:00:00 终止时间：2026-08-30 23:59:59
导出类型：全部
导出时间：2026-08-30 12:00:00
共 5 笔记录
----------------------微信支付账单明细列表--------------------
交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注
2026-08-30 12:00:00,商户消费,星巴克,拿铁,支出,￥35.00,零钱,支付成功,420001,,
2026-08-29 18:00:00,商户消费,美团外卖,晚餐,支出,￥48.50,招商银行(1234),支付成功,420002,,
2026-08-28 10:00:00,转账,李四,,收入,￥200.00,零钱,已存入零钱,420003,,
2026-08-27 09:00:00,商户消费,便利店,零食,支出,￥12.00,零钱,已全额退款,420004,,
2026-08-26 08:00:00,零钱提现,/,/,不计收支,￥500.00,零钱,提现成功,420005,,
"""


def _mkuser(name):
    return User.objects.create_user(name, password="TestPass123!")


def _upload(text, encoding="gbk"):
    """按真实导出场景编码成 bytes（两家都是 GBK）。"""
    return SimpleUploadedFile("bill.csv", text.encode(encoding), content_type="text/csv")


class DetectSourceTests(TestCase):
    """账单来源识别。"""

    def test_detect_alipay(self):
        self.assertEqual(detect_source(ALIPAY_CSV), SOURCE_ALIPAY)

    def test_detect_wechat(self):
        self.assertEqual(detect_source(WECHAT_CSV), SOURCE_WECHAT)

    def test_detect_unknown(self):
        self.assertIsNone(detect_source("a,b,c\n1,2,3"))

    def test_detect_by_header_when_intro_stripped(self):
        """用户手动删掉前置说明后，仍要能靠表头认出来。"""
        body = "\n".join(WECHAT_CSV.splitlines()[7:])
        self.assertEqual(detect_source(body), SOURCE_WECHAT)
        body = "\n".join(ALIPAY_CSV.splitlines()[4:])
        self.assertEqual(detect_source(body), SOURCE_ALIPAY)


class AlipayParseTests(TestCase):
    """支付宝账单解析细节。"""

    def setUp(self):
        self.rows, self.skipped = parse_statement(ALIPAY_CSV, SOURCE_ALIPAY)

    def test_parses_valid_rows(self):
        # 3 笔支出 + 1 笔收入（含一笔带 ￥ 前缀的）
        self.assertEqual(len(self.rows), 4)

    def test_skips_closed_and_refunded_and_neutral(self):
        total = sum(self.skipped.values())
        self.assertEqual(total, 3)  # 交易关闭 / 不计收支 / 已全额退款
        joined = " ".join(self.skipped)
        self.assertIn("不计收支", joined)

    def test_amount_and_direction(self):
        by_note = {r["note"]: r for r in self.rows}
        self.assertEqual(by_note["拌面加蛋"]["amount"], "18.50")
        self.assertEqual(by_note["拌面加蛋"]["type"], "expense")
        self.assertEqual(by_note["八月工资"]["type"], "income")
        self.assertEqual(by_note["八月工资"]["amount"], "12000.00")

    def test_strips_currency_symbol(self):
        """带 ￥ 前缀的金额也要能解析。"""
        row = next(r for r in self.rows if r["note"] == "日用品")
        self.assertEqual(row["amount"], "26.80")

    def test_merchant_and_category(self):
        row = next(r for r in self.rows if r["note"] == "拌面加蛋")
        self.assertEqual(row["merchant"], "沙县小吃")
        self.assertEqual(row["category_name"], "餐饮美食")

    def test_order_no_preserved(self):
        row = next(r for r in self.rows if r["note"] == "拌面加蛋")
        self.assertEqual(row["order_no"], "202608301234001")

    def test_datetime_parsed(self):
        row = next(r for r in self.rows if r["note"] == "拌面加蛋")
        self.assertEqual(row["occurred_at"], datetime(2026, 8, 30, 12, 34, 56))

    def test_footer_not_counted_as_error(self):
        """文件尾注无声忽略，不污染跳过统计。"""
        self.assertNotIn("时间无法解析", self.skipped)


class WechatParseTests(TestCase):
    """微信支付账单解析细节。"""

    def setUp(self):
        self.rows, self.skipped = parse_statement(WECHAT_CSV, SOURCE_WECHAT)

    def test_parses_valid_rows(self):
        self.assertEqual(len(self.rows), 3)  # 2 支出 + 1 收入

    def test_skips_refund_and_withdraw(self):
        self.assertEqual(sum(self.skipped.values()), 2)
        self.assertIn("不计收支", " ".join(self.skipped))

    def test_amount_with_yen_prefix(self):
        by_note = {r["note"]: r for r in self.rows}
        self.assertEqual(by_note["拿铁"]["amount"], "35.00")
        self.assertEqual(by_note["晚餐"]["amount"], "48.50")

    def test_income_detected(self):
        row = next(r for r in self.rows if r["type"] == "income")
        self.assertEqual(row["amount"], "200.00")
        self.assertEqual(row["merchant"], "李四")

    def test_method_recorded(self):
        row = next(r for r in self.rows if r["note"] == "晚餐")
        self.assertEqual(row["method"], "招商银行(1234)")


class ImportFlowTests(TestCase):
    """端到端：上传 → 预览 → 确认写库。"""

    def setUp(self):
        self.u = _mkuser("imp_u1")
        self.client.force_login(self.u)

    def _post_bill(self, text, encoding="gbk"):
        return self.client.post(reverse("import_expense"), {"file": _upload(text, encoding)})

    def test_alipay_upload_preview(self):
        resp = self._post_bill(ALIPAY_CSV)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["source_label"], "支付宝")
        self.assertEqual(resp.context["ok_count"], 4)
        self.assertEqual(resp.context["skipped_total"], 3)
        # 还没确认，绝不能写库
        self.assertEqual(Expense.objects.filter(user=self.u).count(), 0)

    def test_wechat_upload_preview(self):
        resp = self._post_bill(WECHAT_CSV)
        self.assertEqual(resp.context["source_label"], "微信支付")
        self.assertEqual(resp.context["ok_count"], 3)

    def test_confirm_writes_expenses(self):
        self._post_bill(ALIPAY_CSV)
        resp = self.client.post(reverse("import_expense_confirm"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Expense.objects.filter(user=self.u, is_deleted=False).count(), 4)
        # 收入那条要标成 income
        self.assertTrue(
            Expense.objects.filter(user=self.u, type="income", amount=Decimal("12000.00")).exists()
        )

    def test_categories_auto_created_for_user(self):
        self._post_bill(ALIPAY_CSV)
        self.client.post(reverse("import_expense_confirm"))
        self.assertTrue(Category.objects.filter(user=self.u, name="餐饮美食").exists())

    def test_order_no_stored_for_dedup(self):
        self._post_bill(ALIPAY_CSV)
        self.client.post(reverse("import_expense_confirm"))
        self.assertTrue(
            Expense.objects.filter(user=self.u, raw_text="订单号 202608301234001").exists()
        )

    def test_reimport_same_bill_is_idempotent(self):
        """同一份账单导两次，第二次应全部判重跳过。"""
        for _ in range(2):
            self._post_bill(ALIPAY_CSV)
            self.client.post(reverse("import_expense_confirm"))
        self.assertEqual(Expense.objects.filter(user=self.u, is_deleted=False).count(), 4)

    def test_utf8_bill_also_works(self):
        """用户用 Excel 另存成 UTF-8 也要能读。"""
        resp = self._post_bill(ALIPAY_CSV, encoding="utf-8-sig")
        self.assertEqual(resp.context["ok_count"], 4)

    def test_unknown_file_rejected_gracefully(self):
        resp = self._post_bill("a,b,c\n1,2,3\n")
        self.assertEqual(resp.status_code, 302)  # 重定向回导入页并报错
        self.assertEqual(Expense.objects.filter(user=self.u).count(), 0)

    def test_no_file_shows_error(self):
        resp = self.client.post(reverse("import_expense"), {})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Expense.objects.filter(user=self.u).count(), 0)

    def test_confirm_without_session_is_rejected(self):
        resp = self.client.post(reverse("import_expense_confirm"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Expense.objects.filter(user=self.u).count(), 0)

    def test_other_users_data_not_touched(self):
        other = _mkuser("imp_u2")
        Expense.objects.create(
            user=other, type="expense", amount=Decimal("1.00"),
            occurred_at=timezone.now(), note="别人的账",
        )
        self._post_bill(ALIPAY_CSV)
        self.client.post(reverse("import_expense_confirm"))
        self.assertEqual(Expense.objects.filter(user=other).count(), 1)


class LifeosCsvRegressionTests(TestCase):
    """自家导出格式不能被第三方解析抢走（回归保护）。"""

    def setUp(self):
        self.u = _mkuser("imp_u3")
        self.client.force_login(self.u)

    def test_own_format_still_imports(self):
        csv_text = (
            "日期,类型,金额,分类,商家,备注,状态,来源\n"
            "2026-08-29 12:00,支出,35.50,餐饮,沙县小吃,午饭,,\n"
            "2026-08-28 09:00,收入,5000.00,工资,公司,月薪,,\n"
        )
        resp = self.client.post(
            reverse("import_expense"),
            {"file": SimpleUploadedFile("own.csv", csv_text.encode("utf-8"), content_type="text/csv")},
        )
        self.assertEqual(resp.context["ok_count"], 2)
        self.assertEqual(resp.context["source_label"], "LifeOS 导出")
        self.client.post(reverse("import_expense_confirm"))
        self.assertEqual(Expense.objects.filter(user=self.u, is_deleted=False).count(), 2)

    def test_import_index_mentions_alipay_and_wechat(self):
        html = self.client.get(reverse("import_index")).content.decode()
        self.assertIn("支付宝", html)
        self.assertIn("微信", html)
