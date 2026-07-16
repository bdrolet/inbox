import sys
from unittest.mock import MagicMock

# Mock psycopg and submodules before any imports that depend on it
psycopg_mock = MagicMock()
rows_mock = MagicMock()
rows_mock.dict_row = MagicMock()
types_mock = MagicMock()
json_mock = MagicMock()
json_mock.Jsonb = MagicMock()

psycopg_mock.rows = rows_mock
psycopg_mock.types = types_mock
types_mock.json = json_mock

# Make psycopg_mock a package-like object
psycopg_mock.__path__ = []

sys.modules["psycopg"] = psycopg_mock
sys.modules["psycopg.rows"] = rows_mock
sys.modules["psycopg.types"] = types_mock
sys.modules["psycopg.types.json"] = json_mock
