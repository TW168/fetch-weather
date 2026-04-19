import importlib
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch


class DummyCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def close(self):
        return None


class DummyConnection:
    def __init__(self):
        self.autocommit = True
        self.cursor_obj = DummyCursor()
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        return None

    def close(self):
        return None


class FetchWeatherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fake_psycopg2 = types.ModuleType("psycopg2")

        class FakePsycopgError(Exception):
            pass

        fake_psycopg2.Error = FakePsycopgError
        fake_extras = types.ModuleType("psycopg2.extras")
        fake_extras.Json = lambda value: value
        fake_psycopg2.extras = fake_extras
        fake_psycopg2.connect = Mock()

        fake_dotenv = types.ModuleType("dotenv")
        fake_dotenv.load_dotenv = lambda: None

        cls.module_patches = patch.dict(
            sys.modules,
            {
                "psycopg2": fake_psycopg2,
                "psycopg2.extras": fake_extras,
                "dotenv": fake_dotenv,
            },
        )
        cls.module_patches.start()

        cls.env_patches = patch.dict(
            os.environ,
            {
                "WEATHER_API_KEY": "test-key",
                "PWS_STATION_ID": "TEST_STATION",
                "DATABASE_URL": "postgresql://user:pass@localhost/db",
            },
            clear=False,
        )
        cls.env_patches.start()

        cls.fetch_weather = importlib.import_module("fetch_weather")

    @classmethod
    def tearDownClass(cls):
        cls.env_patches.stop()
        cls.module_patches.stop()

    def test_uses_obs_time_utc_and_imperial_fields(self):
        conn = DummyConnection()
        payload = {
            "observations": [
                {
                    "obsTimeUtc": "2026-04-19T12:45:00Z",
                    "epoch": 100,
                    "humidity": 55,
                    "imperial": {
                        "temp": 72.1,
                        "windSpeed": 12,
                        "windGust": 18,
                        "pressure": 29.99,
                        "precipRate": 0.05,
                    },
                }
            ]
        }

        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = payload

        with patch.object(self.fetch_weather.requests, "get", return_value=response), patch.object(
            self.fetch_weather.psycopg2, "connect", return_value=conn
        ):
            self.fetch_weather.fetch_and_store()

        insert_params = conn.cursor_obj.calls[1][1]
        self.assertEqual(
            insert_params[1],
            datetime(2026, 4, 19, 12, 45, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(insert_params[3], 72.1)
        self.assertEqual(insert_params[4], 55)
        self.assertEqual(insert_params[5], 12)
        self.assertEqual(insert_params[6], 18)
        self.assertEqual(insert_params[7], 29.99)
        self.assertEqual(insert_params[8], 0.05)

    def test_falls_back_to_epoch_when_obs_time_utc_invalid(self):
        conn = DummyConnection()
        epoch = 1713528000
        payload = {
            "observations": [
                {
                    "obsTimeUtc": "not-a-time",
                    "epoch": epoch,
                    "humidity": 44,
                    "imperial": {},
                }
            ]
        }

        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = payload

        with patch.object(self.fetch_weather.requests, "get", return_value=response), patch.object(
            self.fetch_weather.psycopg2, "connect", return_value=conn
        ):
            self.fetch_weather.fetch_and_store()

        insert_params = conn.cursor_obj.calls[1][1]
        self.assertEqual(
            insert_params[1],
            datetime.fromtimestamp(epoch, tz=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
