"""Tests de alerts/importance.py (Module E) : chaque règle de score est déterministe et vient toujours
avec une raison en texte — aucune dépendance externe à mocker ici (le scoring des actualités délègue à
alerts/relevance.py, testé séparément dans test_relevance.py)."""
from alerts import importance


class TestScoreFor:
    def test_scores_an_earnings_filing_as_critique(self):
        score, reason = importance.score_for("earnings_filing")

        assert importance.tier_for(score) == importance.CRITIQUE
        assert reason  # une raison explicite est toujours fournie

    def test_scores_an_official_feed_item_as_critique(self):
        score, _ = importance.score_for("official_feed:fed")

        assert importance.tier_for(score) == importance.CRITIQUE

    def test_scores_a_price_move_more_than_double_the_threshold_as_critique(self):
        score, _ = importance.score_for("intraday_move", pct=16.0, threshold=7.0)

        assert importance.tier_for(score) == importance.CRITIQUE

    def test_scores_a_price_move_just_past_the_threshold_as_important(self):
        score, _ = importance.score_for("intraday_move", pct=8.0, threshold=7.0)

        assert importance.tier_for(score) == importance.IMPORTANT

    def test_scores_a_score_change_reaching_buy_as_important(self):
        score, _ = importance.score_for("score_change", label="BUY")

        assert importance.tier_for(score) == importance.IMPORTANT

    def test_scores_a_score_change_reaching_avoid_as_important(self):
        score, _ = importance.score_for("score_change", label="AVOID")

        assert importance.tier_for(score) == importance.IMPORTANT

    def test_scores_a_score_change_to_a_neutral_label_as_a_surveiller(self):
        score, _ = importance.score_for("score_change", label="WATCH")

        assert importance.tier_for(score) == importance.A_SURVEILLER

    def test_scores_an_executive_mention_with_no_business_keyword_as_low_relevance(self):
        """Sans titre (ctx vide), relevance.score_news n'a aucun mot-clé d'affaires à trouver : c'est
        exactement le cas qui recevait un score fixe de 30/100 auparavant, quel que soit le contenu réel."""
        score, _ = importance.score_for("executive")

        assert importance.tier_for(score) == importance.FAIBLE

    def test_scores_a_company_news_item_with_a_merger_keyword_higher_than_a_bare_mention(self):
        score, _ = importance.score_for("company_news", title="NVIDIA acquires a leading AI startup",
                                        ticker="NVDA", company_name="NVIDIA")

        assert score > 70

    def test_does_not_raise_on_an_unknown_kind(self):
        score, reason = importance.score_for("quelque_chose_qui_n_existe_pas")

        assert 0 <= score <= 100
        assert reason

    def test_does_not_raise_when_kind_is_none(self):
        score, reason = importance.score_for(None)

        assert 0 <= score <= 100
        assert reason


class TestTierFor:
    def test_returns_critique_at_the_boundary(self):
        assert importance.tier_for(importance.SEUIL_CRITIQUE) == importance.CRITIQUE

    def test_returns_important_at_the_boundary(self):
        assert importance.tier_for(importance.SEUIL_IMPORTANT) == importance.IMPORTANT

    def test_returns_a_surveiller_at_the_boundary(self):
        assert importance.tier_for(importance.SEUIL_SURVEILLER) == importance.A_SURVEILLER

    def test_returns_faible_below_the_surveiller_boundary(self):
        assert importance.tier_for(importance.SEUIL_SURVEILLER - 1) == importance.FAIBLE


class TestClassify:
    def test_classifies_a_price_rise_as_market_alert_with_positive_bias(self):
        category, bias = importance.classify("daily_move", {"pct": 8.0})

        assert category == importance.MARKET_ALERT
        assert bias == 1

    def test_classifies_a_price_drop_as_market_alert_with_negative_bias(self):
        category, bias = importance.classify("daily_move", {"pct": -8.0})

        assert category == importance.MARKET_ALERT
        assert bias == -1

    def test_classifies_a_buy_score_change_as_catalyst_positive(self):
        category, bias = importance.classify("score_change", {"label": "BUY"})

        assert category == importance.CATALYST_POS
        assert bias == 1

    def test_classifies_an_earnings_filing_as_upcoming_event(self):
        category, _ = importance.classify("earnings_filing", {})

        assert category == importance.UPCOMING

    def test_classifies_a_merger_headline_as_innovation(self):
        category, _ = importance.classify("company_news", {"title": "NVIDIA acquires a leading AI startup"})

        assert category == importance.INNOVATION

    def test_classifies_a_lawsuit_headline_as_risk(self):
        category, _ = importance.classify("company_news", {"title": "AMD sued over patent infringement"})

        assert category == importance.RISK

    def test_classifies_a_headline_with_no_sentiment_as_info(self):
        category, bias = importance.classify("executive", {"title": "Jensen Huang speaks at a conference"})

        assert category == importance.INFO
        assert bias == 0
