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
    # Reflects current Flashscore HTML structure:
    # - wcl-tableScore replaces wcl-matchRowScore
    # - data-live replaces data-state on score nodes
    # - event__match--scheduled / event__match--live classes drive status
    html = """
    <section class="event odds">
      <div class="leagues--live">
        <div class="sportName soccer">
          <div class="headerLeague__wrapper">
            <div class="headerLeague__title-text">Champions League</div>
            <div class="headerLeague__category-text">EUROPE</div>
          </div>
          <div class="event__match event__match--withRowLink event__match--twoLine" data-event-row="true">
            <a class="eventRowLink" href="/match/football/barcelona-SKbpVP5K/newcastle-utd-p6ahwuwJ/?mid=hx7cXCAd"></a>
            <div class="event__participant event__participant--home">Barcelona</div>
            <div class="event__participant event__participant--away">Newcastle</div>
            <span class="event__score event__score--home" data-testid="wcl-tableScore" data-live="false" data-side="1">2</span>
            <span class="event__score event__score--away" data-testid="wcl-tableScore" data-live="false" data-side="2">1</span>
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
    assert rows[0]["status"] == "finished"
    assert rows[0]["odds"]["1b"] == "1.65"
    assert rows[0]["odds"]["Xb"] == "4.78"
    assert rows[0]["odds"]["2b"] == "4.98"
    assert rows[0]["scores"]["ft_home"] == 2
    assert rows[0]["scores"]["ft_away"] == 1
    assert rows[0]["scores"]["state"] == "finished"

    snapshot = build_all_odds_snapshot(
        html,
        page_url="https://www.flashscore.com/",
        day_offset=0,
        now=datetime(2026, 3, 18),
    )
    assert snapshot["date"] == "2026-03-18"
    assert "hx7cXCAd" in snapshot["matches"]
    assert snapshot["matches"]["hx7cXCAd"]["scores"]["ft_home"] == 2


def test_parse_odds_match_rows_scheduled():
    html = """
    <section class="event odds">
      <div class="leagues--live">
        <div class="sportName soccer">
          <div class="headerLeague__wrapper">
            <div class="headerLeague__title-text">Premier League</div>
            <div class="headerLeague__category-text">England</div>
          </div>
          <div class="event__match event__match--scheduled event__match--withRowLink" data-event-row="true">
            <a class="eventRowLink" href="/match/football/arsenal-AbCdEfGh/chelsea-IjKlMnOp/?mid=xY7zAbCd"></a>
            <div class="event__participant event__participant--home">Arsenal</div>
            <div class="event__participant event__participant--away">Chelsea</div>
            <div class="event__time">20:00</div>
            <div class="event__odds">
              <div class="odds__odd event__odd--odd1"><span>2.10</span></div>
              <div class="odds__odd event__odd--odd2"><span>3.50</span></div>
              <div class="odds__odd event__odd--odd3"><span>3.20</span></div>
            </div>
          </div>
        </div>
      </div>
    </section>
    """

    rows = parse_odds_match_rows(html, page_url="https://www.flashscore.com/")
    assert len(rows) == 1
    assert rows[0]["time"] == "20:00"
    assert rows[0]["status"] == "scheduled"
    assert rows[0]["scores"] == {}
    assert rows[0]["odds"]["1b"] == "2.10"


def test_parse_odds_match_rows_live():
    html = """
    <section class="event odds">
      <div class="leagues--live">
        <div class="sportName soccer">
          <div class="headerLeague__wrapper">
            <div class="headerLeague__title-text">La Liga</div>
            <div class="headerLeague__category-text">Spain</div>
          </div>
          <div class="event__match event__match--live event__match--withRowLink" data-event-row="true">
            <a class="eventRowLink" href="/match/football/real-madrid-QrStUvWx/atletico-YzAbCdEf/?mid=Lm9nOpQr"></a>
            <div class="event__participant event__participant--home">Real Madrid</div>
            <div class="event__participant event__participant--away">Atletico</div>
            <div class="event__stage"><div class="event__stage--block">45</div></div>
            <span class="event__score event__score--home" data-testid="wcl-tableScore" data-live="true" data-side="1">1</span>
            <span class="event__score event__score--away" data-testid="wcl-tableScore" data-live="true" data-side="2">0</span>
            <div class="event__odds">
              <div class="odds__odd event__odd--odd1"><span>1.40</span></div>
              <div class="odds__odd event__odd--odd2"><span>5.00</span></div>
              <div class="odds__odd event__odd--odd3"><span>7.50</span></div>
            </div>
          </div>
        </div>
      </div>
    </section>
    """

    rows = parse_odds_match_rows(html, page_url="https://www.flashscore.com/")
    assert len(rows) == 1
    assert rows[0]["status"] == "live"
    assert rows[0]["time"] == "45"
    assert rows[0]["scores"]["ft_home"] == 1
    assert rows[0]["scores"]["ft_away"] == 0
    assert rows[0]["scores"]["state"] == "live"
