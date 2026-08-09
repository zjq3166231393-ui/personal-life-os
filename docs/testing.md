# Personal Life OS — 测试约定

## 运行测试

```powershell
python manage.py test                # 全部
python manage.py test life           # 指定 App
python manage.py test --verbosity 2  # 详细输出
```

## 测试目录结构

```
life/tests.py        ← V0.1 parser 规则解析
accounts/tests.py    ← V0.2 注册/登录/数据隔离
finance/tests.py     ← V0.3 预留
planning/tests.py    ← V0.4 预留
notes/tests.py       ← V0.4 预留
capture/tests.py     ← V0.5 预留
common/tests.py      ← 通用工具
```

## 编写约定

- 测试类按功能分组：`class <Feature>Tests(TestCase)`
- 测试方法描述场景：`def test_<scenario>(self)`
- 每功能覆盖：正常流程 / 异常流程 / 边界条件 / 权限隔离

## 测试类型

| 类型 | Django 类 | 用途 |
|------|-----------|------|
| 单元测试 | SimpleTestCase | 纯函数，不涉及数据库 |
| 模型测试 | TestCase | 模型创建、字段验证 |
| 视图测试 | TestCase | URL 访问、模板渲染、重定向 |
| 集成测试 | TestCase | 完整用户流程 |

## 数据隔离测试模板

```python
class DataIsolationTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user("a", password="pass")
        self.user_b = User.objects.create_user("b", password="pass")
        self.client.login(username="a", password="pass")

    def test_other_user_data_not_visible(self):
        # 用户 A 看不到用户 B 的数据
        ...
```

## 什么不测

- Django 框架内置行为
- 第三方库内部逻辑
- 纯 HTML/CSS 样式
- 浏览器语音识别 API