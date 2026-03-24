from datetime import datetime


class ExcelMatchFormatterV2:
    def __init__(self, match_data):
        self.match = match_data
        self.rows = []
        self.home_team = self.match.get("match_details", {}).get("home_team", "")
        self.away_team = self.match.get("match_details", {}).get("away_team", "")

    def build_rows(self):
        self.rows = []  # ✅ Clear previous state before building new rows

        # Build H2H, standings, odds

        h2h_list = self._build_h2h_rows()
        standings = [self._get_current_standing_row()] + self._get_h2h_standing_rows(
            h2h_list
        )
        odds_rows = [self._get_current_odds_row()] + self._get_h2h_odds_rows(h2h_list)

        # Add Meta Odds
        meta_odds_rows = self._get_meta_odds_rows()
        hloi_values, aloi_values = self._get_loi_block()

        # home away last match single opponents h2h
        home_h2h_last = self._get_last_week_h2h_vs_last_opponent(self.home_team, "HO")
        away_h2h_last = self._get_last_week_h2h_vs_last_opponent(self.away_team, "AO")

        # home away last weekly opponents h2h
        home_h2h_weekly = self._get_last_weekly_h2h_opponents(self.home_team, "HW")
        away_h2h_weekly = self._get_last_weekly_h2h_opponents(self.away_team, "AW")

        shared_row_data = {
            "HOME-CP": standings[0].get("H-Position", ""),
            "AWAY-CP": standings[0].get("A-Position", ""),
            "HomeLastStatus": (
                home_h2h_weekly[1].get("HW-Status", "")
                if len(home_h2h_weekly) > 1
                else ""
            ),
            "AwaylastStatus": (
                away_h2h_weekly[1].get("AW-Status", "")
                if len(away_h2h_weekly) > 1
                else ""
            ),
            "HomeLastArea": (
                home_h2h_weekly[1].get("HW-Area", "")
                if len(home_h2h_weekly) > 1
                else ""
            ),
            "AwayLastarea": (
                away_h2h_weekly[1].get("AW-Area", "")
                if len(away_h2h_weekly) > 1
                else ""
            ),
            "Hh2lastStatus": (
                self._normalize_status(h2h_list[1].get("HO-Status", ""))
                if len(h2h_list) > 1
                else ""
            ),
        }

        context = self._get_match_context()

        for i in range(6):
            row = {
                "Index": self.match.get("index", 1),
                "URL": self.match.get("url") if i == 0 else None,
                "Time": self._get_match_time_only(),
                "Details": self._get_detail_by_row(i),
                "Location": self._get_location_by_row(i),
                "...": "...",
                "Summary": self._get_summary_label(i),
                "Value": self._get_summary_value(i),
            }

            row["...."] = "..."

            # Add H2H data if available
            if i < len(h2h_list):
                h2h = h2h_list[i]
                row.update(
                    {
                        "Event": h2h.get("event"),
                        "Area": self._get_area_by_row(i, h2h_list),
                        "Status": h2h.get("status"),
                        "Date": h2h.get("date"),
                        "H-Goals": h2h.get("score_home"),
                        "A-Goals": h2h.get("score_away"),
                    }
                )

            row["....."] = "..."

            # Add Standing
            if i < len(standings):
                s = standings[i]
                row.update(
                    {
                        "H-POSITION": s.get("H-Position"),
                        "A-POSITION": s.get("A-Position"),
                        "H-ACTUALPOINT": s.get("H-ACTUALPOINT"),
                        "A-ACTUALPOINT": s.get("A-ACTUALPOINT"),
                        "H-TABLEPOINT": s.get("H-TABLEPOINT"),
                        "A-TABLEPOINT": s.get("A-TABLEPOINT"),
                        "T-Total": s.get("T-Total"),
                        "HTF": s.get("HTF"),
                        "ATF": s.get("ATF"),
                        "H-TITLE": s.get("H-TITLE"),
                        "A-TITLE": s.get("A-TITLE"),
                        "Promotion&Relegation": s.get("Promotion&Relegation"),
                    }
                )

            row["..-"] = "..."

            # Add Odds 1X2
            if i < len(odds_rows):
                o = odds_rows[i]
                row.update(
                    {
                        "H-ODDS": o.get("H-ODDS"),
                        "D-ODDS": o.get("D-ODDS"),
                        "A-ODDS": o.get("A-ODDS"),
                    }
                )

            row[".--"] = "..."
            # Add Urls
            row["Match URL"] = h2h_list[i].get("url", "") if i < len(h2h_list) else ""
            row["--."] = "..."

            # Add Meta Odds into this row
            if i < len(meta_odds_rows):
                row.update(meta_odds_rows[i])

            row["-.."] = "..."

            if i < len(home_h2h_weekly):
                row.update(home_h2h_weekly[i])

            row["--.."] = "..."

            if i < len(away_h2h_weekly):
                row.update(away_h2h_weekly[i])

            row["-..."] = "..."

            # Inject HLOI/ALOI values line by line
            if i < len(hloi_values):
                row.update({"HLOI": hloi_values[i], "ALOI": aloi_values[i]})

            row["-.-"] = "..."

            # Inject Home Last Week H2H Opponent Info
            if i < len(home_h2h_last):
                row.update(home_h2h_last[i])

            row["...-"] = "..."

            if i < len(away_h2h_last):
                row.update(away_h2h_last[i])

            row["-..-"] = "..."

            row.update(shared_row_data)

            row["-.--"] = "..."

            row.update(context)

            self.rows.append(row)

        return self.rows

    def _normalize_status(self, value):
        if value == self.home_team:
            return "H"
        elif value == self.away_team:
            return "A"
        elif value.lower() == "draw":
            return "D"
        return ""

    def _get_match_context(self):
        breadcrumb = self.match.get("breadcrumb", {})
        table = self.match.get("table", {})
        home_team = table.get("home_team", {})

        return {
            "COUNTRY": breadcrumb.get("country", ""),
            "COMPETITION": breadcrumb.get("competition", ""),
            "MATCH-TAGS": home_team.get("seasonal_stage", "CUP/FRIENDLY"),
        }

    def _get_match_time_only(self):
        time = self.match.get("match_details", {}).get("time", "")
        return time.strip()

    def _get_detail_by_row(self, i):
        if i == 0:
            return self.home_team
        elif i == 1:
            return self.away_team
        return ""

    def _get_match_value(self):
        return self.match.get("value", "H-boost: 15% A-boost: 100%")

    def _get_location_by_row(self, i):
        breadcrumb = self.match.get("breadcrumb", {})
        if i == 0:
            return self._get_goal_summary("home")
        elif i == 1:
            return self._get_goal_summary("away")
        elif i == 2:
            return breadcrumb.get("country", "")
        elif i == 3:
            return breadcrumb.get("competition", "")
        elif i == 4:
            return breadcrumb.get("stage", "")
        elif i == 5:
            return self.match.get("infobox", "")
        return ""

    def _get_summary_label(self, i):
        labels = [
            "H-boost:",
            "A-boost:",
            "Hlastgame:",
            "Alastgame:",
            "Cfd-Level:",
            "Analyzer:",
        ]
        return labels[i] if i < len(labels) else ""

    def _get_event_by_row(self, i):
        if i == 0:
            return self._get_event_code()
        elif i - 1 < len(self.match.get("h2h", [])):
            return self.match["h2h"][i - 1].get("event", "")
        return ""

    def _get_event_code(self):
        full = self.match.get("breadcrumb", {}).get("competition", "").upper()
        # If it's already short, use it as-is
        if len(full) <= 4:
            return full
        # Otherwise, generate abbreviation from capital letters
        abbrev = "".join([word[0] for word in full.split() if word[0].isalpha()])
        return abbrev[:3].upper()

    def _get_h2h_matches(self):
        h2h_section = next(
            (
                section
                for section in self.match.get("h2h", [])
                if "HEAD-TO-HEAD" in section.get("section_title", "").upper()
            ),
            {},
        )
        return h2h_section.get("matches", [])[:5]  # Limit to 5

    def _get_match_winner(self, home, away, score_home, score_away):
        try:
            h = int(score_home)
            a = int(score_away)
            if h > a:
                return home
            elif a > h:
                return away
            else:
                return "Draw"
        except:
            return ""

    def _build_h2h_rows(self):
        h2h_rows = []

        # Row 0: current match
        current = {
            # "event": self._get_event_code(),
            "event": "?",
            "area": "H",
            "status": "?",
            "date": self.match.get("match_details", {}).get("date", ""),
            "score_home": "?",
            "score_away": "?",
            "url": self.match.get("url", ""),
        }
        h2h_rows.append(current)

        # Rows 1–5: past H2H
        for match in self._get_h2h_matches():
            home = match.get("home", "")
            away = match.get("away", "")
            score_home = match.get("score_home", "")
            score_away = match.get("score_away", "")

            if self.home_team == home:
                h_goals = score_home
                a_goals = score_away
            elif self.home_team == away:
                h_goals = score_away
                a_goals = score_home
            else:
                h_goals = ""
                a_goals = ""

            h2h_rows.append(
                {
                    "event": match.get("event", ""),
                    "status": self._get_match_winner(
                        home, away, score_home, score_away
                    ),
                    "date": match.get("date", ""),
                    "score_home": h_goals,
                    "score_away": a_goals,
                    "home": home,
                    "away": away,
                    "url": match.get("url", ""),
                }
            )

        return h2h_rows

    def _get_area_by_row(self, i, h2h_list):
        if i == 0:
            return "H"
        elif i < len(h2h_list):
            match = h2h_list[i]
            if self.home_team == match.get("home"):
                return "H"
            elif self.home_team == match.get("away"):
                return "A"
        return ""

    def _get_current_standing_row(self):
        table = self.match.get("table", {})
        home = table.get("home_team", {})
        away = table.get("away_team", {})

        return {
            "H-Position": home.get("rank"),
            "A-Position": away.get("rank"),
            "H-ACTUALPOINT": home.get("actual_points"),
            "A-ACTUALPOINT": away.get("actual_points"),
            "H-TABLEPOINT": home.get("pts"),
            "A-TABLEPOINT": away.get("pts"),
            "T-Total": table.get("total_rows", ""),
            "HTF": self._format_form(home.get("form", [])),
            "ATF": self._format_form(away.get("form", [])),
            "H-TITLE": home.get("promotion_title", ""),
            "A-TITLE": away.get("promotion_title", ""),
            "Promotion&Relegation": self._format_promo_relegation(
                table.get("promotions"), table.get("relegations")
            ),
        }

    def _get_h2h_standing_rows(self, h2h_list):
        standings = self.match.get("h2h_standings", {})
        rows = []

        for i in range(1, len(h2h_list)):
            match_url = h2h_list[i].get("url", "")
            match_id = self._extract_match_id(match_url)
            if not match_id:
                continue

            data = standings.get(match_id, {})
            has_table = data.get("has_table", False)

            home = data.get("home_team", {}) or {}
            away = data.get("away_team", {}) or {}

            # Normalize team names
            home_name = self._extract_team_name(home)
            away_name = self._extract_team_name(away)

            # Determine alignment
            if self.home_team == home_name:
                h_team = home if isinstance(home, dict) else {}
                a_team = away if isinstance(away, dict) else {}
            elif self.home_team == away_name:
                h_team = away if isinstance(away, dict) else {}
                a_team = home if isinstance(home, dict) else {}
            else:
                h_team = {}
                a_team = {}

            row = {
                "MatchID": match_id,
                "H-Team": home_name if self.home_team == home_name else away_name,
                "A-Team": away_name if self.home_team == home_name else home_name,
                "H-Position": h_team.get("rank", ""),
                "A-Position": a_team.get("rank", ""),
                "H-ACTUALPOINT": h_team.get("actual_points", ""),
                "A-ACTUALPOINT": a_team.get("actual_points", ""),
                "H-TABLEPOINT": h_team.get("pts", ""),
                "A-TABLEPOINT": a_team.get("pts", ""),
                "T-Total": data.get("total_rows", ""),
                "HTF": self._format_form(h_team.get("form", [])) if has_table else "",
                "ATF": self._format_form(a_team.get("form", [])) if has_table else "",
                "H-TITLE": h_team.get("promotion_title", ""),
                "A-TITLE": a_team.get("promotion_title", ""),
                "Promotion&Relegation": (
                    self._format_promo_relegation(
                        data.get("promotions", 0), data.get("relegations", 0)
                    )
                    if has_table
                    else ""
                ),
            }

            rows.append(row)

        return rows

    def _extract_match_id(self, url):
        try:
            return url.split("mid=")[-1].strip()
        except:
            return ""

    def _format_promo_relegation(self, promotions, relegations):
        p = str(promotions) if promotions is not None else "0"
        r = str(relegations) if relegations is not None else "0"
        return f"{p} & {r}"

    def _format_form(self, form_list):
        if not form_list:
            return ""
        return "".join(form_list)

    def _get_current_odds_row(self):
        odds = self.match.get("odds", {}).get("1X2", {})
        return {
            "H-ODDS": odds.get("1", ""),
            "D-ODDS": odds.get("X", ""),
            "A-ODDS": odds.get("2", ""),
        }

    def _extract_team_name(self, team_obj):
        """
        Safely extracts the team name whether the input is a dict or a string.
        """
        if isinstance(team_obj, dict):
            return team_obj.get("team", "").strip()
        elif isinstance(team_obj, str):
            return team_obj.strip()
        return ""

    def _get_h2h_odds_rows(self, h2h_list):
        """
        Builds a list of odds rows for recent head-to-head matches using normalized team names.
        """
        standings = self.match.get("h2h_standings", {})
        rows = []

        for i in range(1, len(h2h_list)):
            match_url = h2h_list[i].get("url", "")
            match_id = self._extract_match_id(match_url)
            if not match_id:
                continue

            data = standings.get(match_id, {})
            odds = data.get("odds_data", {}).get("1X2", {})
            home = data.get("home_team", {})
            away = data.get("away_team", {})

            # Normalize team names from either dict or string
            home_team_name = self._extract_team_name(home)
            away_team_name = self._extract_team_name(away)

            # Align odds based on actual home/away roles
            if self.home_team == home_team_name:
                h_odds = odds.get("1", "")
                a_odds = odds.get("2", "")
            elif self.home_team == away_team_name:
                h_odds = odds.get("2", "")
                a_odds = odds.get("1", "")
            else:
                h_odds = ""
                a_odds = ""

            rows.append(
                {"H-ODDS": h_odds, "D-ODDS": odds.get("X", ""), "A-ODDS": a_odds}
            )

        return rows

    def _get_meta_odds_rows(self):
        odds = self.match.get("odds") or {}
        rows = []

        def get_odds_values(section) -> dict:
            """Return odds dict from either {'odds': {...}} or a plain dict; treat None as empty."""
            if not isinstance(section, dict):
                return {}
            nested = section.get("odds")
            return nested if isinstance(nested, dict) else section

        def get_ou(ou_list):
            return ou_list[0] if isinstance(ou_list, list) and ou_list else {}

        firsthalf_1x2 = get_odds_values(odds.get("firsthalf_1X2"))
        rows.append(
            {
                "ODDS": "FIRSTHALF-1X2",
                "1/YES": firsthalf_1x2.get("1", ""),
                "X/NO": firsthalf_1x2.get("X", ""),
                "2": firsthalf_1x2.get("2", ""),
            }
        )

        double_chance = get_odds_values(odds.get("double_chance"))
        rows.append(
            {
                "ODDS": "DOUBLE-CHANCE",
                "1/YES": double_chance.get("1", ""),
                "X/NO": double_chance.get("X", ""),
                "2": double_chance.get("2", ""),
            }
        )

        btts_first_half = get_odds_values(odds.get("btts_first_half"))
        rows.append(
            {
                "ODDS": "GG/NG",
                "1/YES": btts_first_half.get("yes", ""),
                "X/NO": btts_first_half.get("no", ""),
                "2": "",
            }
        )

        over_under = odds.get("over_under") or {}
        rows.append(
            {
                "ODDS": "FULLTIME-O/U-1.5",
                "1/YES": get_ou(over_under.get("full_time_1_5", [])).get("over", ""),
                "X/NO": get_ou(over_under.get("full_time_1_5", [])).get("under", ""),
                "2": "",
            }
        )

        rows.append(
            {
                "ODDS": "FIRSTHALF-O/U-1.5",
                "1/YES": get_ou(over_under.get("first_half_1_5", [])).get("over", ""),
                "X/NO": get_ou(over_under.get("first_half_1_5", [])).get("under", ""),
                "2": "",
            }
        )

        rows.append(
            {
                "ODDS": "SECONDHALF-O/U-1.5",
                "1/YES": get_ou(over_under.get("second_half_1_5", [])).get("over", ""),
                "X/NO": get_ou(over_under.get("second_half_1_5", [])).get("under", ""),
                "2": "",
            }
        )

        return rows

    def _get_loi_block(self):
        last_matches = self.match.get("last_matches", {})

        def extract_opponent(team_name):
            team_data = last_matches.get(team_name, {})
            match_url = team_data.get("match_url", "")
            table = team_data.get("table", {})
            h2h_sections = team_data.get("h2h", [])

            home = table.get("home_team", {}).get("team", "")
            away = table.get("away_team", {}).get("team", "")
            opponent = (
                table.get("away_team", {})
                if home == team_name
                else table.get("home_team", {})
            )

            event = ""
            for section in h2h_sections:
                matches = section.get("matches", [])
                if matches:
                    event = matches[0].get("event", "")
                    break

            return [
                str(opponent.get("rank", "")),
                "".join(opponent.get("form", [])),
                match_url,
                opponent.get("actual_points", ""),
                str(opponent.get("pts", "")),
                event,
            ]

        h_values = extract_opponent(self.home_team)
        a_values = extract_opponent(self.away_team)

        return h_values, a_values

    def _get_last_weekly_h2h_opponents(self, team_name, prefix="HW"):
        # last_data = self.match.get("last_matches", {}).get(team_name, {})
        h2h_sections = self.match.get("h2h", [])
        # Initialize with a fully structured empty row
        rows = [
            {
                f"{prefix}-Event": "",
                f"{prefix}-Area": "",
                f"{prefix}-Opponent": "",
                f"{prefix}-Status": "",
                f"{prefix}-Date": "",
                f"{prefix}-T-Goals": "",
                f"{prefix}-O-Goals": "",
            }
        ]

        # Find the section titled "LAST MATCHES: <TEAM>"
        matches = []
        target_title = f"LAST MATCHES: {team_name.upper()}"

        for section in h2h_sections:
            if section.get("section_title", "").upper() == target_title:
                matches = section.get("matches", [])
                break

        for match in matches[:5]:
            is_home = match.get("home") == team_name
            opponent = match.get("away") if is_home else match.get("home")
            area = "H" if is_home else "A"

            score_home = match.get("score_home", "")
            score_away = match.get("score_away", "")
            result = match.get("result", "").strip().upper()

            # Optional: fallback if result is missing
            try:
                if not result:
                    result = self._get_match_winner_O(
                        team_name, opponent, score_home, score_away, is_home
                    )
            except:
                result = ""
                continue

            team_goals = score_home if is_home else score_away
            opp_goals = score_away if is_home else score_home

            row = {
                f"{prefix}-Event": match.get("event", ""),
                f"{prefix}-Area": area,
                f"{prefix}-Opponent": opponent,
                f"{prefix}-Status": result,
                f"{prefix}-Date": match.get("date", ""),
                f"{prefix}-T-Goals": team_goals,
                f"{prefix}-O-Goals": opp_goals,
            }
            rows.append(row)

        return rows

    def _get_match_winner_O(
        self, team_name, opponent_name, score_home, score_away, is_home
    ):
        try:
            h = int(score_home)
            a = int(score_away)
        except (ValueError, TypeError):
            return ""

        if h == a:
            return "Draw"

        if is_home:
            return team_name if h > a else opponent_name
        else:
            return team_name if a > h else opponent_name

    def _get_last_week_h2h_vs_last_opponent(self, team_name, prefix):
        last_data = self.match.get("last_matches", {}).get(team_name, {})
        h2h_sections = last_data.get("h2h", [])

        # Initialize with a fully structured empty row
        rows = [
            {
                f"{prefix}-Event": "",
                f"{prefix}-Area": "",
                f"{prefix}-Status": "",
                f"{prefix}-Date": "",
                f"{prefix}-T-Goals": "",
                f"{prefix}-O-Goals": "",
            }
        ]

        opponent = None
        area = ""

        # First pass: find opponent and area from first HEAD-TO-HEAD match
        for section in h2h_sections:
            if "HEAD-TO-HEAD" in section.get("section_title", "").upper():
                matches = section.get("matches", [])
                if matches:
                    first_match = matches[0]
                    home = first_match.get("home", "")
                    away = first_match.get("away", "")
                    opponent = away if home == team_name else home
                    area = "H" if home == team_name else "A"
                break

        # Inject odds into first row if available
        odds = last_data.get("odds_data", {}).get("1X2", {})
        if odds:
            rows[0][f"{prefix}-Area"] = area
            if area == "H":
                rows[0][f"{prefix}-T-Goals"] = odds.get("1", "")
                rows[0][f"{prefix}-O-Goals"] = odds.get("2", "")
            else:
                rows[0][f"{prefix}-T-Goals"] = odds.get("2", "")
                rows[0][f"{prefix}-O-Goals"] = odds.get("1", "")

        if not opponent:
            return rows

        # Second pass: collect all matches between team and opponent
        for section in h2h_sections:
            if "HEAD-TO-HEAD" in section.get("section_title", "").upper():
                for match in section.get("matches", []):
                    home = match.get("home", "")
                    away = match.get("away", "")
                    if {home, away} != {team_name, opponent}:
                        continue

                    score_home = match.get("score_home", "")
                    score_away = match.get("score_away", "")
                    is_home = home == team_name
                    area = "H" if is_home else "A"
                    status = self._get_match_winner_O(
                        team_name, opponent, score_home, score_away, is_home
                    )

                    team_goals = score_home if is_home else score_away
                    opp_goals = score_away if is_home else score_home

                    row = {
                        f"{prefix}-Event": match.get("event", ""),
                        f"{prefix}-Area": area,
                        f"{prefix}-Status": status,
                        f"{prefix}-Date": match.get("date", ""),
                        f"{prefix}-T-Goals": team_goals,
                        f"{prefix}-O-Goals": opp_goals,
                    }
                    rows.append(row)

        return rows

    def _get_summary_value(self, i):
        if i == 0:
            return self._build_boost("home")
        elif i == 1:
            return self._build_boost("away")
        elif i == 2:
            return self._get_days_ago("home")
        elif i == 3:
            return self._get_days_ago("away")
        elif i == 4:
            return ""  # AI-Pred placeholder
        elif i == 5:
            return self._get_analyzer_code()
        return ""

    def _build_boost(self, side):
        team = self.home_team if side == "home" else self.away_team
        prefixO = "HO" if side == "home" else "AO"
        prefixW = "HW" if side == "home" else "AW"

        # 1. H2H Boost: last 2 results vs last opponent
        h2h_boost = self._get_h2h_boost(team, prefixO)

        # 2. Form Boost: from last matches section
        form_boost = self._get_form_boost(team, prefixW)

        # 3. Event Code: from last week match
        h2h = self._get_last_weekly_h2h_opponents(team, prefixW)

        # 4.
        event = h2h[1].get(f"{prefixW}-Event", "") if len(h2h) > 1 else ""
        event_code = event  # or self._map_event_code(event)

        return f"{h2h_boost}||{form_boost}||{event_code}"

    def _get_h2h_boost(self, team_name, prefix):
        h2h = self._get_last_week_h2h_vs_last_opponent(team_name, prefix)
        results = []

        # Only use actual matches (skip placeholder at index 0)
        real_matches = h2h[1:]

        for row in real_matches[:2]:  # Only take up to 2 matches
            team_goals = row.get(f"{prefix}-T-Goals", "")
            opp_goals = row.get(f"{prefix}-O-Goals", "")

            results.append(self.get_match_status_icon(team_goals, opp_goals))

        # Pad with "-" only if fewer than 2 results
        if len(results) == 1:
            results.append("-")
        elif len(results) == 0:
            results = ["-", "-"]

        return "".join(reversed(results))

    def _get_form_boost(self, team_name, prefix):
        form = self._get_last_weekly_h2h_opponents(team_name, prefix)
        results = []

        # Only use actual matches (skip placeholder at index 0)
        real_matches = form[1:]

        for row in real_matches[:2]:
            team_goals = row.get(f"{prefix}-T-Goals", "")
            opp_goals = row.get(f"{prefix}-O-Goals", "")
            results.append(self.get_match_status_icon(team_goals, opp_goals))

        # Pad with "-" only if fewer than 2 results
        if len(results) == 1:
            results.append("-")
        elif len(results) == 0:
            results = ["-", "-"]

        return "".join(reversed(results))

    def get_match_status_icon(self, team_goals, opp_goals):
        # Safely compare goals
        try:
            h = int(team_goals)
            a = int(opp_goals)

            if h > a:
                return "W"
            elif h < a:
                return "L"
            else:
                return "D"
        except:
            return "-"

    def _get_days_ago(self, side):
        def days_ago(current_date_str, past_date_str):
            try:
                # Parse current date (e.g. "19.12.2025")
                current = datetime.strptime(current_date_str, "%d.%m.%Y")

                # Parse past date (e.g. "14.12.25")
                past = datetime.strptime(past_date_str, "%d.%m.%y")

                delta = (current - past).days
                return f"{delta} days ago"
            except Exception as e:
                return ""

        team = self.home_team if side == "home" else self.away_team
        match_date = self.match.get("match_details", {}).get("date", "")
        h2h = self._get_last_weekly_h2h_opponents(
            team, "HW" if side == "home" else "AW"
        )
        if len(h2h) > 1:
            last_date = h2h[1].get(f"{'HW' if side == 'home' else 'AW'}-Date", "")
            return days_ago(match_date, last_date)
        return ""

    def _get_analyzer_code(self):
        stage = (
            self.match.get("table", {})
            .get("home_team", {})
            .get("seasonal_stage", "")
            .lower()
        )
        return {
            "firstleg": "FL",
            "secondleg": "SL",
            "thirdleg": "TL",
            "fourthleg": "FL",
            "cup": "CPF",
            "friendly": "CPF",
        }.get(stage, "")

    def _get_goal_summary(self, team_side):
        # Source blocks
        overall = self.match.get("table", {}).get(f"{team_side}_team", {})
        home = self.match.get("table_home_only", {}).get(f"{team_side}_team", {})
        away = self.match.get("table_away_only", {}).get(f"{team_side}_team", {})

        def extract(gf, ga):
            try:
                gf = int(gf)
                ga = int(ga)
                return f"{gf}:{ga} {gf - ga}"
            except:
                return ""

        overall_str = extract(overall.get("goals_for"), overall.get("goals_against"))
        home_str = extract(home.get("goals_for"), home.get("goals_against"))
        away_str = extract(away.get("goals_for"), away.get("goals_against"))

        return f"{overall_str} || {home_str} || {away_str}"
