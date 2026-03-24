from headless.routes import build_match_routes


def test_build_match_routes_from_base_match_url():
    routes = build_match_routes(
        "https://www.flashscore.com/match/football/"
        "independiente-UVatI5Y2/instituto-8G45bXRA/?mid=2eDEHMBO"
    )

    assert routes.match_url.endswith("/?mid=2eDEHMBO")
    assert routes.h2h_overall_url.endswith("/h2h/overall/?mid=2eDEHMBO")
    assert routes.standings_overall_url.endswith(
        "/standings/standings/overall/?mid=2eDEHMBO"
    )
    assert routes.standings_home_url.endswith(
        "/standings/standings/home/?mid=2eDEHMBO"
    )
    assert routes.standings_away_url.endswith(
        "/standings/standings/away/?mid=2eDEHMBO"
    )


def test_build_match_routes_from_subpage_url():
    routes = build_match_routes(
        "https://www.flashscore.com/match/football/"
        "independiente-UVatI5Y2/instituto-8G45bXRA/"
        "standings/standings/away/?mid=2eDEHMBO"
    )

    assert routes.match_url == (
        "https://www.flashscore.com/match/football/"
        "independiente-UVatI5Y2/instituto-8G45bXRA/?mid=2eDEHMBO"
    )
