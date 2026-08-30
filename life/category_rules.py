"""自动分类规则引擎。

规则：商户名/备注包含某关键字 → 归入指定分类。命中按 priority 降序，
第一条即返回，让用户能用优先级解决冲突（如「星巴克」>「咖啡」）。

设计取舍：
- 规则数量按用户维度很少（几十到几百），Python 层顺序扫描足够，且能直接用
  priority 控制「先到先得」语义；无需上正则，纯子串匹配对中文商户名最直观。
- match_category 只认「文本源」：商户名、备注、语音转写标题都先拼成一段文本再匹配，
  因此记账、语音、导入三处接入点共用同一函数。
"""

from .models import CategoryRule


def match_category(user, text, type_="expense"):
    """Return the best-matching active Category for ``text``, or ``None``.

    ``type_`` 限定规则适用范围（expense/income/both）。文本为空直接返回 None，
    避免无意义的 DB 查询。
    """
    if not text or not text.strip():
        return None
    text_l = text.lower()

    rules = CategoryRule.objects.filter(user=user, is_active=True).select_related(
        "category"
    )
    if type_ in ("expense", "income"):
        rules = rules.filter(type_filter__in=[type_, CategoryRule.TypeFilter.BOTH])
    rules = rules.order_by("-priority", "id")

    for rule in rules:
        pat = rule.pattern.strip().lower()
        if pat and pat in text_l:
            return rule.category
    return None


def build_search_text(merchant="", note="", title=""):
    """把可能的文本源拼成一个小写无关、空白归一化的匹配串。"""
    parts = [merchant or "", note or "", title or ""]
    return " ".join(p for p in parts if p).strip()
