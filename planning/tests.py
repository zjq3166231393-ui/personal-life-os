import json
from django.test import RequestFactory, SimpleTestCase
from .views import index

class SmokeTests(SimpleTestCase):
    def test_planning_index_returns_ok(self):
        response = index(RequestFactory().get("/"))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data["ok"])
