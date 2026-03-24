from datetime import datetime

from headless.parsers.all_odds import (
    build_all_odds_snapshot,
    infer_selected_date_iso_from_label,
    parse_odds_match_rows,
)


def test_infer_selected_date_iso_from_label():
    assert (
        infer_selected_date_iso_from_label(
            "18/03 We",
            now=datetime(2026, 3, 18),
        )
        == "2026-03-18"
    )


def test_parse_odds_match_rows_with_context():
    html = """
    <section class="event odds">
      <div class="leagues--live">
        <div class="sportName soccer">
          <div class="headerLeague__wrapper">
            <div class="headerLeague__title-text">Champions League</div>
            <div class="headerLeague__category-text">EUROPE</div>
          </div>
          <div class="event__match event__match--withRowLink" data-event-row="true">
            <a class="eventRowLink" href="/match/football/barcelona-SKbpVP5K/newcastle-utd-p6ahwuwJ/?mid=hx7cXCAd"></a>
            <div class="event__participant event__participant--home">Barcelona</div>
            <div class="event__participant event__participant--away">Newcastle</div>
            <div class="event__time">18:45</div>
            <div class="event__odds">
              <div class="odds__odd event__odd--odd1"><span>1.65</span></div>
              <div class="odds__odd event__odd--odd2"><span>4.78</span></div>
              <div class="odds__odd event__odd--odd3"><span>4.98</span></div>
            </div>
          </div>
        </div>
      </div>
      <button data-testid="wcl-dayPickerButton">18/03 We</button>
    </section>
    """

    rows = parse_odds_match_rows(html, page_url="https://www.flashscore.com/")

    assert len(rows) == 1
    assert rows[0]["match_id"] == "hx7cXCAd"
    assert rows[0]["home"] == "Barcelona"
    assert rows[0]["away"] == "Newcastle"
    assert rows[0]["competition"] == "Champions League"
    assert rows[0]["country"] == "EUROPE"
    assert rows[0]["odds"]["1"] == "1.65"
    assert rows[0]["odds"]["X"] == "4.78"
    assert rows[0]["odds"]["2"] == "4.98"

    snapshot = build_all_odds_snapshot(
        html,
        page_url="https://www.flashscore.com/",
        day_offset=0,
        now=datetime(2026, 3, 18),
    )
    assert snapshot["date"] == "2026-03-18"
    assert "hx7cXCAd" in snapshot["matches"]
