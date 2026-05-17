def test_oauth_registry_has_two_providers(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gs")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "h")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "hs")
    import importlib

    import api.oauth

    importlib.reload(api.oauth)
    assert api.oauth.oauth.create_client("google") is not None
    assert api.oauth.oauth.create_client("github") is not None
