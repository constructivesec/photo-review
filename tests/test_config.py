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
