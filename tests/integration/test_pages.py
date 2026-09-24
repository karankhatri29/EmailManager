def test_app_shell_is_served_without_login(client):
    r = client.get("/")
    assert r.status_code == 200 and "Email Prioritizer" in r.text
    assert "/static/js/main.js" in r.text


def test_static_assets_are_served(client):
    assert client.get("/static/js/main.js").status_code == 200


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_api_docs_are_available(client):
    assert client.get("/openapi.json").status_code == 200


def test_theme_assets_are_served(client):
    for path in ("/static/css/theme.css", "/static/js/tailwind-config.js", "/static/js/settings.js"):
        assert client.get(path).status_code == 200, path


def test_shell_applies_saved_settings_before_first_paint_and_has_the_settings_dialog(client):
    html = client.get("/").text
    # the inline script runs before the stylesheet so the theme never flashes
    assert html.index("ep-prefs-v1") < html.index("css/theme.css")
    for element in (
        'id="settingsModal"',
        'id="themeToggle"',
        'data-pref="theme"',
        'data-pref="accent"',
        'data-pref="density"',
    ):
        assert element in html, element
    # calendar: three views, and no "Auto" option (automatic is the default behaviour)
    for view in ("day", "week", "month"):
        assert f'data-cal-view="{view}"' in html
    assert 'data-cal-view="auto"' not in html


def test_dashboard_has_its_kpi_widgets_and_a_settings_section_to_choose_them(client):
    html = client.get("/").text
    for element in (
        'id="kpiGrid"',
        'id="card-focus"',
        'id="card-senders"',
        'id="card-mailboxes"',
        'id="card-busyHours"',
        'id="dashPrefs"',
    ):
        assert element in html, element
    assert client.get("/static/js/kpis.js").status_code == 200
    assert client.get("/static/js/dashboard-catalog.js").status_code == 200
