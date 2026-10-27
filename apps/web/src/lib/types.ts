export type Book = { name: string; short: string; color: string; kind: string };
export type Books = Record<string, Book>;
export type Leg = { book: string; selection: string; outcome: string; stake: number; odds: number; effective_odds: number; payout: number; fee: number; contracts: number | null; size_verified: boolean; source_at: string };
export type Comparison = { book: string; outcome: string; selection: string; odds: number; effective_odds: number; source_at: string; max_stake: number | null; contracts: number | null; fee_model: string };
export type Opportunity = {
  id: string; snapshot_id: string; market_id: string; type: "ev" | "arb"; sport: string;
  home: string; away: string; event: string; market: string; line: number | null; selection: string;
  books: string[]; edge: number; stake: number; profit: number; odds: number | null;
  effective_odds?: number; true_probability: number | null; fair_odds?: number; outcome?: string;
  method: string | null; reference: string | null; starts_at: string; source_at: string;
  observed_at: string; size_verified: boolean; ruleset: string; comparison: Comparison[]; legs: Leg[]; is_demo: boolean;
};
export type Summary = { bankroll: number; available: number; exposure: number; profit: number; roi: number; predicted_profit: number; bet_count: number; settled_count: number; deposited: number; withdrawn: number };
export type Preferences = {
  enabled_sports: string[]; league_rule_reviews: Record<string, string[]>;
  min_ev: number; min_arb: number; devig_method: string; reference_book: string; kelly_fraction: number;
  max_stake_pct: number; arb_budget: number; max_quote_age: number; max_quote_skew: number;
  jurisdiction: string; allowed_books: string[]; alert_ev: number; alert_arb: number;
  email: string; email_alerts: boolean; venue_balances: Record<string, number>; jurisdiction_reviewed_at: string | null;
};
export type SportProfile = { api_key: string; unit: string; spread_label: string; matchup_scale: number; minimum_games: number; scoring_hours: number; matchup_hours: number; injury_hours: number; matchup_hint: string; injury_hint: string; injury_label: string; settlement: string; rule_hint: string; required_checks: Record<string, string> };
export type Dashboard = { sports?: Record<string, SportProfile>; collection?: { poll_seconds: number; estimated_monthly_credits: number }; streaming?: boolean; best_bets?: BestBets; opportunities: Opportunity[]; mode: "demo" | "live"; books: Books; bankroll: Summary;
  preferences: Preferences; as_of: string; worker: { status: string; last_success?: string };
  counts: { quote_count: number; event_count: number; market_count: number; rejected: Record<string, number> };
};
export type Bet = { id: string; event: string; selection: string; market: string; book: string; stake: number; odds: number;
  profit: number; predicted_ev: number | null; placed_at: string; status: "open" | "won" | "lost" | "void";
  settled_at: string | null; notes: string; is_demo: boolean; entry_fee?: number; win_fee?: number };
export type BetData = { bets: Bet[]; summary: Summary; series: { at: string; realized: number; predicted: number }[] };
export type AlertItem = { id: string; event: string; selection: string; edge: number; type: string; created_at: string; delivery: string; read: boolean; opportunity_id: string };
export type SourceState = { status: string; message?: string; last_success?: string; last_attempt?: string; latency_ms?: number; credits_used?: string | number; credits_remaining?: string | number; budget_spent?: number };
export type Mapping = { id: string; market_id: string; source: string; external_id: string; yes_outcome: string; fee_model: string; fee_rate: number; reviewed_at: string };
export type Sources = { stream_sources: string[]; mode: string; states: Record<string, SourceState>; books: Books; quote_counts: Record<string, number>; mappings: Mapping[]; poll_seconds: number; prediction_poll_seconds: number; monthly_credit_budget: number; smtp_configured: boolean; reviewed_books: string[] };
export type Market = { id: string; event: string; market: string; line: number | null; outcomes: string[]; ruleset: string };

export type ScoringAverages = { home_for: number; home_against: number; away_for: number; away_against: number; home_games: number; away_games: number; source_url: string | null; as_of: string };
export type BetResearch = {
  market_id: string; outcome: string; origin?: "owner" | "synthetic"; updated_at?: string; scoring: ScoringAverages | null;
  matchup: "unknown" | "favorable" | "neutral" | "unfavorable"; matchup_notes: string; matchup_source_url: string | null; matchup_as_of: string | null;
  injury_status: "unknown" | "clear" | "manageable" | "material" | "unresolved"; injury_notes: string; injury_source_url: string | null; injury_as_of: string | null;
  availability_checks?: Record<string, boolean>;
};
export type BetFactor = { key: string; label: string; points: number; max_points: number; state: string; reasons: string[] };
export type BestBet = { opportunity: Opportunity; research_key: string; research: BetResearch | null; score: number; raw_score: number; letter: string;
  state: "candidate" | "provisional" | "watch"; coverage: number; factors: BetFactor[]; risks: string[];
  caps: { maximum: number; reason: string }[]; projection: { home: number; away: number; total: number; margin: number; selection_cushion: number; method: string; unit: string } | null; sport_profile: SportProfile;
  summary: string; graded_at: string; version: string; synthetic_research: boolean;
};
export type BestBets = { items: BestBet[]; methodology: { version: string; weights: Record<string, number>; description: string; freshness_by_sport: Record<string, { scoring_hours: number; matchup_hours: number; injury_hours: number }>; caps: Record<string, number> }; as_of: string; counts: { total: number; candidates: number; provisional: number; reviewed: number } };
