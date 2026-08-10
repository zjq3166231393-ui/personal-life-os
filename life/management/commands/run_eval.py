"""Run parser evaluation against test fixtures.

Usage:
    python manage.py run_eval
    python manage.py run_eval --provider fake
    python manage.py run_eval --fixture tests/fixtures/parser_cases.json
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from life.ai_provider import FakeProvider, get_provider, set_provider


class Command(BaseCommand):
    help = "Run parser evaluation against test fixtures."

    def add_arguments(self, parser):
        parser.add_argument("--fixture", default="tests/fixtures/parser_cases.json")
        parser.add_argument("--provider", default="fake", choices=["fake", "real"])

    def handle(self, fixture, provider, **options):
        path = Path(fixture)
        if not path.exists():
            self.stderr.write(f"Fixture not found: {path}")
            return

        data = json.loads(path.read_text(encoding="utf-8"))
        cases = data.get("cases", [])

        if provider == "fake":
            set_provider(FakeProvider())
        # else use real provider (needs DEEPSEEK_API_KEY)

        p = get_provider()
        passed = 0
        failed = 0
        skipped = 0

        for case in cases:
            cid = case["id"]
            text = case["text"]
            expected = case.get("expected", {})

            if not text:
                skipped += 1
                continue

            try:
                result = p.parse(text)
            except Exception as e:
                self.stderr.write(f"  {cid}: ERROR {e}")
                failed += 1
                continue

            # Evaluate
            actions = result.get("actions", [])
            ok = self._check(cid, actions, expected)
            if ok:
                passed += 1
            else:
                failed += 1

        total = passed + failed + skipped
        rate = passed / (passed + failed) * 100 if (passed + failed) > 0 else 0
        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Results: {passed}/{passed+failed} passed ({rate:.1f}%), {skipped} skipped")
        if failed > 0:
            self.stdout.write(f"FAILED: {failed} case(s) need attention.")

    def _check(self, cid, actions, expected):
        if "should_not_crash" in expected:
            return True  # Just surviving is enough
        if len(actions) == 0:
            self.stderr.write(f"  {cid}: no actions returned")
            return False

        # Multi-action check
        if "actions" in expected:
            for i, exp in enumerate(expected["actions"]):
                if i >= len(actions):
                    return False
                if not self._match(actions[i], exp):
                    return False
            return True

        # Min actions check
        if "min_actions" in expected:
            if len(actions) < expected["min_actions"]:
                self.stderr.write(f"  {cid}: expected >= {expected['min_actions']} actions, got {len(actions)}")
                return False
            return True

        # Intents check
        if "intents" in expected:
            got = [a["intent"] for a in actions]
            if set(expected["intents"]) != set(got):
                self.stderr.write(f"  {cid}: intents mismatch, expected {expected['intents']}, got {got}")
                return False
            return True

        # Single action check
        return self._match(actions[0], expected)

    def _match(self, action, expected):
        for key, val in expected.items():
            if key in ("date_relative", "amount_estimated", "has_amount", "has_due_at",
                       "recurring", "installment", "should_reject", "should_fallback", "sensitive",
                       "should_not_crash", "actions", "intents", "min_actions", "reminder_type"):
                continue
            if action.get(key) != val:
                self.stderr.write(f"    {key}: expected '{val}', got '{action.get(key)}'")
                return False
        return True
