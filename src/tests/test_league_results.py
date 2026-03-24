from headless.parsers.league_results import (
    build_fixtures_url,
    derive_context_from_results_url,
    normalize_results_url,
)


def test_normalize_results_url_from_results_page():
    assert (
        normalize_results_url(
            "https://www.flashscore.com/football/england/premier-league/results/"
        )
        == "https://www.flashscore.com/football/england/premier-league/results/"
    )


def test_normalize_results_url_from_fixtures_page():
    assert (
        normalize_results_url(
            "https://www.flashscore.com/football/england/premier-league/fixtures/"
        )
        == "https://www.flashscore.com/football/england/premier-league/results/"
    )


def test_build_fixtures_url():
    assert (
        build_fixtures_url(
            "https://www.flashscore.com/football/england/premier-league/results/"
        )
        == "https://www.flashscore.com/football/england/premier-league/fixtures/"
    )


def test_derive_context_from_results_url():
    country, competition = derive_context_from_results_url(
        "https://www.flashscore.com/football/england/premier-league/results/"
    )
    assert country == "England"
    assert competition == "Premier League"
