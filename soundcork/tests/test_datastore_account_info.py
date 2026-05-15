import json
from pathlib import Path

from soundcork.datastore import ACCOUNTS_FILE, DataStore


def test_get_account_info_initializes_missing_accounts_file(monkeypatch, tmp_path):
    monkeypatch.setattr("soundcork.datastore.settings.data_dir", str(tmp_path))
    datastore = DataStore()

    account_dir = tmp_path / "7679292"
    account_dir.mkdir()

    label = datastore.get_account_info("7679292")

    assert label == "Unnamed account 7679292"
    with open(Path(tmp_path) / ACCOUNTS_FILE) as handle:
        accounts = json.load(handle)
    assert accounts == {"7679292": {"label": "Unnamed account 7679292"}}
