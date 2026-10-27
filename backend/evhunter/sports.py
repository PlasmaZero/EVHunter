"""Supported league conventions and research requirements, shared by feeds and grades."""


def profile(key, unit, spread_label, scale, minimum_games, scoring_hours, matchup_hours, injury_hours, matchup_hint, injury_hint, settlement, demo, checks=None):
    return {"api_key": key, "unit": unit, "spread_label": spread_label, "matchup_scale": scale,
            "minimum_games": minimum_games, "scoring_hours": scoring_hours, "matchup_hours": matchup_hours, "injury_hours": injury_hours,
            "matchup_hint": matchup_hint, "injury_hint": injury_hint, "settlement": settlement,
            "required_checks": checks or {}, "demo_scoring": demo,
            "rule_hint": "Confirm full-game extra innings, action/listed-pitcher, shortened-game, and void rules across reviewed venues." if unit == "runs" else "Confirm full-game overtime/shootout and shootout-goal treatment for totals and puck lines." if unit == "goals" else "Confirm full-game overtime and void rules. Whole-number lines remain excluded.",
            "injury_label": "Injuries / IL & availability" if unit == "runs" else "Injuries & goalie status" if unit == "goals" else "Injuries & minutes" if "basketball" in key else "Injury report / IR"}


SPORT_PROFILES = {
    "NBA": profile("basketball_nba", "points", "Spread", 10, 4, 72, 24, 6,
        "Analyze pace, offensive/defensive efficiency, positional defense, usage, rebounding, rest, and home/away splits.",
        "Review starters, rotations, minutes restrictions, usage redistribution, rest, and back-to-backs.",
        "nba-full-game-ot-no-push-v1", [116, 109, 112, 114]),
    "NFL": profile("americanfootball_nfl", "points", "Spread", 6, 4, 168, 24, 6,
        "Analyze WR/CB alignment, coverage, pass protection, run defense, pace, weather, and usage. Identify projected versus confirmed assignments.",
        "Review actives, IR, questionable players, offensive-line availability, and workload restrictions.",
        "nfl-full-game-ot-no-push-v1", [26, 20, 22, 25]),
    "MLB": profile("baseball_mlb", "runs", "Run line", 2, 10, 72, 12, 3,
        "Analyze confirmed starting pitchers, handedness, lineup splits, bullpen workload, park factors, weather, and recent offense. Team run averages alone are not a pitcher projection.",
        "Review IL status, scratches, confirmed starting pitchers and batting orders, pitch limits, and bullpen availability.",
        "mlb-full-game-extra-innings-no-push-v1", [4.9, 4.0, 4.2, 4.5],
        {"starting_pitchers": "Both starting pitchers confirmed", "starting_lineups": "Both batting orders confirmed", "bullpen": "Bullpen availability reviewed"}),
    "NHL": profile("icehockey_nhl", "goals", "Puck line", 1.5, 6, 72, 12, 3,
        "Analyze starting goalies, expected-goal generation, shot quality, special teams, line combinations, rest, and travel.",
        "Review starting-goalie confirmation, scratches, injured reserve, line combinations, and workload.",
        "nhl-full-game-ot-shootout-no-push-v1", [3.5, 2.7, 2.9, 3.1],
        {"starting_goalies": "Both starting goalies confirmed", "line_combinations": "Line combinations and scratches reviewed"}),
    "WNBA": profile("basketball_wnba", "points", "Spread", 8, 4, 72, 24, 6,
        "Analyze pace, offensive/defensive efficiency, positional defense, usage, rebounding, rotations, and rest.",
        "Review starters, absences, minutes restrictions, usage redistribution, rotations, and travel.",
        "wnba-full-game-ot-no-push-v1", [86, 78, 81, 83]),
    "NCAAB": profile("basketball_ncaab", "points", "Spread", 10, 4, 72, 24, 6,
        "Analyze tempo, opponent-adjusted efficiency, shooting profile, rebounding, turnovers, strength of schedule, and home-court context.",
        "Review available starters, suspensions, rotation changes, minutes expectations, and travel.",
        "ncaab-full-game-ot-no-push-v1", [78, 69, 73, 75]),
    "NCAAF": profile("americanfootball_ncaaf", "points", "Spread", 8, 4, 168, 24, 6,
        "Analyze quarterback play, WR/CB alignment, line play, explosiveness, pace, strength of schedule, weather, and home-field context.",
        "Review starters, quarterback status, suspensions, offensive-line availability, and workload restrictions.",
        "ncaaf-full-game-ot-no-push-v1", [32, 22, 27, 29]),
}
SPORTS = {p["api_key"]: name for name, p in SPORT_PROFILES.items()}
DEFAULT_LIVE_SPORTS = ["NBA", "NFL", "MLB", "NHL"]
LEGACY_RULE_SPORTS = {"NBA", "NFL"}


def public_profiles():
    return {name: {key: value for key, value in p.items() if key != "demo_scoring"} for name, p in SPORT_PROFILES.items()}
