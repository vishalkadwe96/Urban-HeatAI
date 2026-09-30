import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.api.admin import require_admin


class AdminAccessTests(unittest.TestCase):
    def test_missing_token_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            require_admin(None)

        self.assertEqual(error.exception.status_code, 401)

    @patch("backend.api.admin._user_from_token", return_value={"role": "user"})
    def test_regular_user_is_forbidden(self, _user_from_token):
        with self.assertRaises(HTTPException) as error:
            require_admin("Bearer user-token")

        self.assertEqual(error.exception.status_code, 403)

    @patch("backend.api.admin._user_from_token", return_value={"id": 1, "role": "admin"})
    def test_admin_user_is_allowed(self, _user_from_token):
        user = require_admin("Bearer admin-token")

        self.assertEqual(user["role"], "admin")


if __name__ == "__main__":
    unittest.main()