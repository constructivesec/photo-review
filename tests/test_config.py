from photo_review.config import Config


def test_round_trip(tmp_path):
    path = tmp_path / "sub" / "config.json"
    Config(last_folder="/p", keep_folder="/k", sort_key="taken_desc").save(path)
    cfg = Config.load(path)
    assert (cfg.last_folder, cfg.keep_folder, cfg.sort_key) == ("/p", "/k", "taken_desc")


def test_corrupt_or_missing_config_gives_defaults(tmp_path):
    assert Config.load(tmp_path / "missing.json") == Config()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert Config.load(bad) == Config()


def test_show_gps_defaults_on_and_persists(tmp_path):
    path = tmp_path / "config.json"
    assert Config().show_gps is True
    Config(show_gps=False).save(path)
    assert Config.load(path).show_gps is False


def test_wrong_types_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"show_gps": "no", "sort_key": 5, "last_folder": "/p"}')
    cfg = Config.load(path)
    assert cfg.show_gps is True and cfg.sort_key == Config().sort_key and cfg.last_folder == "/p"
