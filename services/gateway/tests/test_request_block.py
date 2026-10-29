import os
import unittest

from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "postgresql://unused")
os.environ.setdefault("GATEWAY_API_KEY", "test-password")
os.environ.setdefault("PROVIDER_API_KEY", "test-provider-key")
os.environ.setdefault("PROVIDER_API_BASE", "http://provider.example/v1")
os.environ.setdefault("DEFAULT_MODEL", "test-model")

from app import main


class FakeDb:
    def __init__(self, *, global_blocked=False, user_blocked=False):
        self.global_blocked = global_blocked
        self.user_blocked = user_blocked
        self.quota_charges = 0

    def acquire(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def fetchrow(self, query, user_id):
        return {
            "user_limit": None,
            "global_limit": None,
            "user_blocked": self.user_blocked,
            "global_blocked": self.global_blocked,
        }

    async def fetchval(self, query, *args):
        self.quota_charges += 1
        return 1


class RequestBlockTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_block_rejects_before_quota_charge(self):
        db = FakeDb(global_blocked=True)
        main.app.state.db = db
        with self.assertRaises(HTTPException) as raised:
            await main.charge(1)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(db.quota_charges, 0)

    async def test_user_block_rejects_before_quota_charge(self):
        db = FakeDb(user_blocked=True)
        main.app.state.db = db
        with self.assertRaises(HTTPException) as raised:
            await main.charge(1)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(db.quota_charges, 0)

    async def test_unblocked_user_can_charge(self):
        db = FakeDb()
        main.app.state.db = db
        self.assertEqual(await main.charge(1), 1)
        self.assertEqual(db.quota_charges, 1)


if __name__ == "__main__":
    unittest.main()
