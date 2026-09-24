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
        'id="card-inbox"',
        'id="card-senders"',
        'id="card-mailboxes"',
        'id="dashPrefs"',
    ):
        assert element in html, element
    assert client.get("/static/js/kpis.js").status_code == 200
    assert client.get("/static/js/dashboard-catalog.js").status_code == 200


def test_each_screen_size_gets_its_own_navigation(client):
    """Phone: bottom tab bar. Tablet: icon rail. Laptop and up: sidebar + top tabs (see css/adaptive.css)."""
    html = client.get("/").text
    assert 'id="bottomNav"' in html and 'id="rail"' in html
    # the same actions are reachable from both, and the timeframe chips exist for sizes without the sidebar select
    for control in (
        'data-nav="dashboard"',
        'data-nav="activity"',
        'data-action="sync"',
        'data-action="menu"',
        'id="timeChips"',
    ):
        assert html.count(control) >= 1, control
    assert html.count('data-nav="activity"') == 2  # bottom bar + rail
    assert "dataset.ui" in html  # the active layout class is recorded on <html> before first paint
    css = client.get("/static/css/adaptive.css")
    assert css.status_code == 200
    for layout in ("phone", "tablet", "laptop", "desktop"):
        assert layout in css.text


def test_inbox_explorer_replaces_the_charts(client):
    """The donut and line chart are gone: an interactive explorer (chips, timeline bars, search, list) took over."""
    html = client.get("/").text
    for element in (
        'id="priorityChips"',
        'id="timelineBars"',
        'id="inboxSearch"',
        'id="inboxList"',
        'data-sort="smart"',
    ):
        assert element in html, element
    for gone in ("pieChart", "lineChart", "plotly"):
        assert gone not in html, gone  # no chart library is loaded any more
    assert client.get("/static/js/explorer.js").status_code == 200


def test_trips_view_and_navigation_exist_on_every_screen_size(client):
    html = client.get("/").text
    for element in (
        'id="view-trips"',
        'id="tripsGrid"',
        'id="tripsScan"',
        'id="tripKinds"',
        'id="tab-trips"',
        'id="card-trips"',
    ):
        assert element in html, element
    assert html.count('data-nav="trips"') == 2  # phone bottom bar + tablet rail
    assert client.get("/static/js/trips.js").status_code == 200


def test_privacy_note_is_public_and_says_the_uncomfortable_things_plainly(client):
    r = client.get(
        "/privacy"
    )  # no login: it must be reachable from the sign-in screen and the OAuth consent screen
    assert r.status_code == 200
    text = r.text.lower()
    for statement in (
        "read-only",
        "stores the text of your emails",
        "not encrypted",
        "gemini",
        "disconnecting a mailbox",
        "attachments",
    ):
        assert statement in text, statement


def test_privacy_is_linked_from_sign_in_and_settings(client):
    html = client.get("/").text
    assert html.count('href="/privacy"') == 2  # sign-in screen + Settings
    assert 'id="privacyNote"' in html


def test_settings_is_tabbed_and_dashboard_options_are_toggle_switches(client):
    html = client.get("/").text
    for tab in ("appearance", "dashboard", "inbox", "privacy"):
        assert f'data-settings-tab="{tab}"' in html and f'data-settings-panel="{tab}"' in html, tab
    assert (
        'role="tablist"' in html and 'data-pref="timeframe"' in html
    )  # timeframe is a segmented control now
    assert 'id="prefTimeframe"' not in html
    # the option rows are built in JS: each one is a switch (a styled checkbox with role="switch"), not a bare checkbox
    js = client.get("/static/js/settings.js").text
    assert 'role="switch"' in js and "switch-track" in js and "pref-row" not in js
    css = client.get("/static/css/theme.css").text
    assert ".switch-thumb" in css and ".theme-tile" in css
