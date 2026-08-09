from django.test import TestCase
from django.urls import reverse

class SmokeTests(TestCase):
    def test_capture_index_returns_ok(self):
        response = self.client.get(reverse("capture_index"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["app"], "capture")
