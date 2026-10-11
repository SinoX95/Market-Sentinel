"""Tests de alerts/relevance.py : scoring multi-critères d'une actualité, remplace le score fixe de
30/100 qu'une simple mention de dirigeant recevait auparavant."""
from alerts import relevance


class TestCategorize:
    def test_detects_a_merger_keyword(self):
        cat, base, hits = relevance.categorize("NVIDIA acquires a leading AI startup")

        assert cat == relevance.MERGER
        assert "acquires" in hits
        assert base > 0

    def test_detects_a_french_earnings_keyword(self):
        cat, _, _ = relevance.categorize("AMD publie ses résultats trimestriels")

        assert cat == relevance.EARNINGS

    def test_falls_back_to_other_when_no_keyword_matches(self):
        cat, base, hits = relevance.categorize("Jensen Huang visits a university campus")

        assert cat == relevance.OTHER
        assert hits == []
        assert base == 20


class TestScoreNews:
    def test_scores_a_merger_headline_mentioning_the_company_higher_than_a_bare_mention(self):
        with_merger, _, _ = relevance.score_news("NVIDIA acquires a leading AI startup", ticker="NVDA",
                                                  company_name="NVIDIA")
        bare_mention, _, _ = relevance.score_news("Jensen Huang visits a university campus", ticker="NVDA",
                                                   company_name="NVIDIA")

        assert with_merger > bare_mention

    def test_penalizes_a_headline_that_does_not_name_the_company(self):
        named, _, _ = relevance.score_news("NVIDIA signs a strategic partnership", ticker="NVDA",
                                           company_name="NVIDIA")
        unnamed, _, reason = relevance.score_news("A strategic partnership was signed", ticker="NVDA",
                                                   company_name="NVIDIA")

        assert unnamed < named
        assert "n'est pas explicitement nommée" in reason

    def test_heavily_penalizes_recognized_anecdotal_content(self):
        score, cat, reason = relevance.score_news("Jensen Huang, the tech sector's biggest philanthropy donor",
                                                   ticker="NVDA", company_name="NVIDIA")

        assert score < 40
        assert cat == relevance.OTHER
        assert "anecdotique" in reason

    def test_boosts_score_for_a_reliable_source(self):
        reuters, _, _ = relevance.score_news("NVIDIA announces new chip", ticker="NVDA", company_name="NVIDIA",
                                             source="Reuters")
        blog, _, _ = relevance.score_news("NVIDIA announces new chip", ticker="NVDA", company_name="NVIDIA",
                                          source="Bing Actualités")

        assert reuters > blog

    def test_score_stays_within_bounds(self):
        score, _, _ = relevance.score_news("", ticker=None, company_name=None, source=None)

        assert 0 <= score <= 100

    def test_does_not_raise_when_title_is_none(self):
        score, cat, reason = relevance.score_news(None)

        assert 0 <= score <= 100
        assert cat == relevance.OTHER
        assert reason
