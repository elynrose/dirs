def test_app_import():
    from director_api.main import app

    assert app.title == "Directely API"
