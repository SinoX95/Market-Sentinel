"""Tests de alerts/notify.py : jamais de vrai réseau (urllib.request.urlopen et backend.mailer.send sont les
deux frontières externes mockées), et la garantie centrale du module — aucune fonction ne lève jamais,
puisqu'un canal d'alerte en échec ne doit jamais faire échouer le cycle de collecte."""
import io
import json
import urllib.error

import pytest

from alerts import notify

HTTP_OK = 200
HTTP_NO_CONTENT = 204
HTTP_BAD_REQUEST = 400
TOKEN, CHAT_ID = "123:ABC-fake-token", "-10042"
WEBHOOK_URL = "https://discord.com/api/webhooks/123/fake-token"


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestTelegramConfigured:
    def test_returns_false_when_token_and_chat_id_are_absent(self, no_settings_file):
        assert notify.telegram_configured() is False

    def test_returns_true_when_token_and_chat_id_are_both_set(self, settings_file):
        settings_file({"telegram_bot_token": TOKEN, "telegram_chat_id": CHAT_ID})

        assert notify.telegram_configured() is True


class TestSendTelegram:
    def test_returns_false_without_calling_network_when_not_configured(self, no_settings_file, monkeypatch):
        called = []
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: called.append(1))

        result = notify.send_telegram("peu importe")

        assert result is False
        assert called == []

    def test_returns_true_when_telegram_responds_with_http_200(self, settings_file, monkeypatch):
        settings_file({"telegram_bot_token": TOKEN, "telegram_chat_id": CHAT_ID})
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResponse(HTTP_OK))

        assert notify.send_telegram("NVDA : HOLD -> BUY") is True

    def test_returns_false_when_telegram_rejects_the_request(self, settings_file, monkeypatch):
        settings_file({"telegram_bot_token": TOKEN, "telegram_chat_id": CHAT_ID})

        def raise_http_error(*a, **k):
            raise urllib.error.HTTPError("url", HTTP_BAD_REQUEST, "bad request", {}, io.BytesIO(b"chat not found"))

        monkeypatch.setattr("urllib.request.urlopen", raise_http_error)

        assert notify.send_telegram("peu importe") is False

    def test_never_raises_on_an_unexpected_network_error(self, settings_file, monkeypatch):
        """
        Given une erreur réseau quelconque (DNS, coupure, délai dépassé...), pas seulement HTTPError
        When send_telegram est appelé
        Then il renvoie False au lieu de propager l'exception — un canal d'alerte en échec ne doit jamais
        faire tomber le cycle de collecte qui l'appelle.
        """
        settings_file({"telegram_bot_token": TOKEN, "telegram_chat_id": CHAT_ID})
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))

        assert notify.send_telegram("peu importe") is False

    def test_sends_html_parse_mode_with_the_subject_line_bolded(self, settings_file, monkeypatch):
        settings_file({"telegram_bot_token": TOKEN, "telegram_chat_id": CHAT_ID})
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode("utf-8")))
            return _FakeResponse(HTTP_OK)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        notify.send_telegram("NVDA : HOLD\n\nScore 82/100, prix 182.3.")

        assert captured[0]["parse_mode"] == "HTML"
        assert "<b>NVDA : HOLD</b>" in captured[0]["text"]

    def test_masks_a_url_behind_a_short_link(self, settings_file, monkeypatch):
        """
        Given un corps de message contenant une URL très longue (ex. redirection Google News)
        When send_telegram est appelé
        Then l'URL est masquée derrière un lien HTML court plutôt qu'affichée en clair — c'est ce qui
        rendait le message illisible sur Telegram.
        """
        settings_file({"telegram_bot_token": TOKEN, "telegram_chat_id": CHAT_ID})
        captured = []
        long_url = "https://news.google.com/rss/articles/" + ("A" * 150)

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode("utf-8"))["text"])
            return _FakeResponse(HTTP_OK)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        notify.send_telegram(f"Sujet\n\nUn article intéressant\n{long_url}")

        assert f'<a href="{long_url}">🔗 Source</a>' in captured[0]  # l'URL est dans le lien, pas affichée en clair
        assert f">{long_url}<" not in captured[0]  # jamais affichée comme texte visible

    def test_escapes_html_special_characters_outside_of_links(self, settings_file, monkeypatch):
        settings_file({"telegram_bot_token": TOKEN, "telegram_chat_id": CHAT_ID})
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode("utf-8"))["text"])
            return _FakeResponse(HTTP_OK)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        notify.send_telegram("Sujet\n\nR&D <important> & croissance")

        assert "R&amp;D &lt;important&gt; &amp; croissance" in captured[0]


class TestDiscordConfigured:
    def test_returns_false_when_webhook_url_is_absent(self, no_settings_file):
        assert notify.discord_configured() is False

    def test_returns_true_when_webhook_url_is_set(self, settings_file):
        settings_file({"discord_webhook_url": WEBHOOK_URL})

        assert notify.discord_configured() is True


SAMPLE_EMBED = {"title": "NVDA : HOLD -> BUY", "description": "Score 82/100.", "color": 0x5865F2, "fields": []}


class TestSendDiscord:
    """send_discord envoie l'embed qu'on lui donne tel quel (voir TestBuildEmbed pour la construction de
    l'embed à partir du sujet/texte/score — une responsabilité séparée depuis le passage aux embeds)."""

    def test_returns_false_without_calling_network_when_not_configured(self, no_settings_file, monkeypatch):
        called = []
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: called.append(1))

        result = notify.send_discord(SAMPLE_EMBED)

        assert result is False
        assert called == []

    def test_returns_true_when_discord_responds_with_http_204(self, settings_file, monkeypatch):
        settings_file({"discord_webhook_url": WEBHOOK_URL})
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResponse(HTTP_NO_CONTENT))

        assert notify.send_discord(SAMPLE_EMBED) is True

    def test_returns_false_when_discord_rejects_the_request(self, settings_file, monkeypatch):
        settings_file({"discord_webhook_url": WEBHOOK_URL})

        def raise_http_error(*a, **k):
            raise urllib.error.HTTPError("url", HTTP_BAD_REQUEST, "bad request", {}, io.BytesIO(b"invalid webhook"))

        monkeypatch.setattr("urllib.request.urlopen", raise_http_error)

        assert notify.send_discord(SAMPLE_EMBED) is False

    def test_never_raises_on_an_unexpected_network_error(self, settings_file, monkeypatch):
        settings_file({"discord_webhook_url": WEBHOOK_URL})
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))

        assert notify.send_discord(SAMPLE_EMBED) is False

    def test_sends_the_given_embed_unchanged_inside_the_embeds_array(self, settings_file, monkeypatch):
        settings_file({"discord_webhook_url": WEBHOOK_URL})
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode("utf-8"))["embeds"][0])
            return _FakeResponse(HTTP_NO_CONTENT)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        notify.send_discord(SAMPLE_EMBED)

        assert captured[0] == SAMPLE_EMBED


class TestBuildEmbed:
    """_build_embed construit l'embed Discord à partir du sujet/texte/score (Module 2) — une
    responsabilité séparée de l'envoi réseau (voir TestSendDiscord) depuis le passage aux embeds."""

    def _embed(self, subject="Sujet", body="", **kw):
        kw.setdefault("score", 70)
        kw.setdefault("tier", "Important")
        kw.setdefault("reason", "")
        kw.setdefault("category", "info")
        kw.setdefault("bias", 0)
        return notify._build_embed(subject, body, **kw)

    def test_prefixes_the_subject_with_a_category_emoji_as_the_title(self):
        embed = self._embed(subject="NVDA : HOLD -> BUY", category="catalyst_positive", bias=1)

        assert embed["title"] == "📈 NVDA : HOLD -> BUY"

    def test_puts_the_body_in_the_description(self):
        embed = self._embed(body="Score 82/100, prix 182.3.")

        assert "Score 82/100" in embed["description"]

    def test_masks_a_url_behind_a_short_link_in_the_description(self):
        """
        Given un corps de message contenant une URL très longue (ex. redirection Google News)
        When l'embed est construit
        Then l'URL est masquée derrière un lien Markdown court plutôt qu'affichée en clair — c'était
        illisible dans le salon Discord sinon.
        """
        long_url = "https://news.google.com/rss/articles/" + ("A" * 150)

        embed = self._embed(body=f"Un article intéressant\n{long_url}")

        assert embed["description"] == f"Un article intéressant\n[🔗 Source]({long_url})"

    def test_truncates_a_description_longer_than_the_discord_limit(self):
        embed = self._embed(body="x" * 5000)

        assert len(embed["description"]) <= notify.DISCORD_DESCRIPTION_MAX + len("\n…(tronqué)")
        assert "tronqué" in embed["description"]

    def test_includes_the_score_and_tier_as_a_field(self):
        embed = self._embed(score=82, tier="Critique")

        assert any(f["name"] == "Pertinence" and "82/100" in f["value"] and "Critique" in f["value"]
                  for f in embed["fields"])

    def test_includes_the_reason_as_a_field_when_given(self):
        embed = self._embed(reason="catégorie « merger »")

        assert any(f["name"] == "Pourquoi ce score ?" and "merger" in f["value"] for f in embed["fields"])

    def test_omits_the_reason_field_when_not_given(self):
        embed = self._embed(reason="")

        assert all(f["name"] != "Pourquoi ce score ?" for f in embed["fields"])

    def test_includes_company_and_ticker_as_a_field_when_given(self):
        embed = self._embed(company_name="NVIDIA", ticker="NVDA")

        assert any(f["name"] == "Entreprise" and f["value"] == "NVIDIA · NVDA" for f in embed["fields"])

    def test_makes_the_title_clickable_via_the_embed_url_when_a_link_is_given(self):
        embed = self._embed(url="https://example.org/article")

        assert embed["url"] == "https://example.org/article"

    def test_omits_the_url_field_when_no_link_is_given(self):
        embed = self._embed()

        assert "url" not in embed

    def test_includes_a_thumbnail_when_an_image_url_is_given(self):
        embed = self._embed(image_url="https://logo.clearbit.com/nvidia.com")

        assert embed["thumbnail"]["url"] == "https://logo.clearbit.com/nvidia.com"

    def test_omits_the_thumbnail_when_no_image_is_given(self):
        embed = self._embed()

        assert "thumbnail" not in embed

    def test_uses_a_green_tone_for_a_positive_market_move(self):
        embed = self._embed(category="market_alert", bias=1)

        assert embed["color"] == 0x57F287

    def test_uses_a_red_tone_for_a_negative_market_move(self):
        embed = self._embed(category="market_alert", bias=-1)

        assert embed["color"] == 0xED4245

    def test_uses_a_purple_tone_for_an_innovation_category(self):
        embed = self._embed(category="innovation")

        assert embed["color"] == 0x9B59B6


@pytest.fixture
def spy_add_alert(monkeypatch):
    """Remplace backend.db.add_alert par un espion : aucun test de ce module ne doit toucher une vraie base."""
    calls = []

    def fake_add_alert(ticker, kind, subject, text, channels, importance_score=None, importance_tier=None,
                      category=None, url=None, image_url=None):
        calls.append({"ticker": ticker, "kind": kind, "subject": subject, "text": text, "channels": channels,
                      "importance_score": importance_score, "importance_tier": importance_tier,
                      "category": category, "url": url, "image_url": image_url})

    monkeypatch.setattr("backend.db.add_alert", fake_add_alert)
    return calls


class TestNotify:
    def test_returns_empty_dict_when_no_channel_is_configured(self, no_settings_file, spy_add_alert):
        assert notify.notify("sujet", "texte") == {}

    def test_sends_only_telegram_when_only_telegram_is_configured(self, settings_file, monkeypatch, spy_add_alert):
        settings_file({"telegram_bot_token": TOKEN, "telegram_chat_id": CHAT_ID})
        monkeypatch.setattr(notify, "send_telegram", lambda text: True)

        assert notify.notify("sujet", "texte") == {"telegram": True}

    def test_sends_only_discord_when_only_discord_is_configured(self, settings_file, monkeypatch, spy_add_alert):
        settings_file({"discord_webhook_url": WEBHOOK_URL})
        monkeypatch.setattr(notify, "send_discord", lambda text: True)

        assert notify.notify("sujet", "texte") == {"discord": True}

    def test_sends_both_telegram_and_discord_when_both_are_configured(self, settings_file, monkeypatch, spy_add_alert):
        settings_file({"telegram_bot_token": TOKEN, "telegram_chat_id": CHAT_ID, "discord_webhook_url": WEBHOOK_URL})
        monkeypatch.setattr(notify, "send_telegram", lambda text: True)
        monkeypatch.setattr(notify, "send_discord", lambda text: True)

        assert notify.notify("sujet", "texte") == {"telegram": True, "discord": True}

    def test_includes_subject_and_text_in_the_discord_embed(self, settings_file, monkeypatch, spy_add_alert):
        settings_file({"discord_webhook_url": WEBHOOK_URL})
        captured = []
        monkeypatch.setattr(notify, "send_discord", lambda embed: captured.append(embed) or True)

        notify.notify("NVDA : HOLD -> BUY", "Score 82/100, prix 182.3.")

        assert "NVDA : HOLD -> BUY" in captured[0]["title"]
        assert "Score 82/100" in captured[0]["description"]

    def test_does_not_send_email_even_when_smtp_is_configured(self, settings_file, monkeypatch, spy_add_alert):
        """
        Given smtp_user/smtp_password/contact_email sont réglés (utilisés par backend/auth.py pour les
        emails de connexion/invitation)
        When notify est appelé sans Telegram ni Discord réglés
        Then aucun email n'est envoyé — Discord est l'unique canal de notification/actualité, l'email n'en
        fait plus partie (voir le docstring du module).
        """
        settings_file({"contact_email": "sino@example.com", "smtp_user": "sino@example.com", "smtp_password": "x"})
        send_called = []
        monkeypatch.setattr("backend.mailer.send", lambda to, subject, body: send_called.append(to) or True)

        result = notify.notify("sujet", "texte")

        assert result == {}
        assert send_called == []

    def test_detected_news_triggers_discord_only_even_with_smtp_also_configured(
        self, settings_file, monkeypatch, spy_add_alert
    ):
        """
        Given SMTP est configuré (comme en production, pour les emails de connexion de backend/auth.py) ET
        Discord est configuré
        When une actualité est détectée (ex. mention d'un dirigeant suivi, kind="executive")
        Then seul Discord reçoit la notification — aucun appel à backend.mailer.send, quel que soit l'état
        de la config SMTP.
        """
        settings_file({
            "contact_email": "sino@example.com", "smtp_user": "sino@example.com", "smtp_password": "x",
            "discord_webhook_url": WEBHOOK_URL,
        })
        send_called = []
        monkeypatch.setattr("backend.mailer.send", lambda to, subject, body: send_called.append(to) or True)
        monkeypatch.setattr(notify, "send_discord", lambda embed: True)

        result = notify.notify("Market Sentinel : NVIDIA (NVDA)", "NVIDIA signe un partenariat stratégique (Reuters)",
                               ticker="NVDA", kind="executive", title="NVIDIA signe un partenariat stratégique",
                               company_name="NVIDIA", source="Reuters")

        assert result == {"discord": True}
        assert send_called == []

    def test_includes_subject_and_text_in_the_telegram_message(self, settings_file, monkeypatch, spy_add_alert):
        settings_file({"telegram_bot_token": TOKEN, "telegram_chat_id": CHAT_ID})
        captured = []
        monkeypatch.setattr(notify, "send_telegram", lambda text: captured.append(text) or True)

        notify.notify("NVDA : HOLD -> BUY", "Score 82/100, prix 182.3.")

        assert "NVDA : HOLD -> BUY" in captured[0]
        assert "Score 82/100" in captured[0]

    def test_logs_the_alert_even_when_no_channel_is_configured(self, no_settings_file, spy_add_alert):
        """
        Given aucun canal n'est réglé (ni Telegram ni Discord)
        When notify est appelé
        Then l'alerte est quand même journalisée — l'historique doit montrer ce qui AURAIT dû partir, pas
        seulement ce qui est effectivement arrivé sur un canal.
        """
        notify.notify("sujet", "texte", ticker="NVDA", kind="score_change")

        assert len(spy_add_alert) == 1
        assert spy_add_alert[0]["ticker"] == "NVDA"
        assert spy_add_alert[0]["kind"] == "score_change"

    def test_logs_which_channels_actually_sent_the_message(self, settings_file, monkeypatch, spy_add_alert):
        settings_file({"telegram_bot_token": TOKEN, "telegram_chat_id": CHAT_ID})
        monkeypatch.setattr(notify, "send_telegram", lambda text: True)

        notify.notify("sujet", "texte")

        assert spy_add_alert[0]["channels"] == {"telegram": True}

    def test_defaults_kind_to_other_when_not_given(self, no_settings_file, spy_add_alert):
        notify.notify("sujet", "texte")

        assert spy_add_alert[0]["kind"] == "other"

    def test_logs_the_importance_score_and_tier_computed_for_the_alert(self, no_settings_file, spy_add_alert):
        notify.notify("sujet", "texte", kind="earnings_filing")

        assert spy_add_alert[0]["importance_tier"] == "Critique"
        assert spy_add_alert[0]["importance_score"] == 90

    def test_forwards_importance_context_kwargs_to_the_scoring_rule(self, no_settings_file, spy_add_alert):
        notify.notify("sujet", "texte", kind="score_change", label="BUY")

        assert spy_add_alert[0]["importance_tier"] == "Important"

    def test_includes_the_importance_reason_in_the_message_sent(self, settings_file, monkeypatch):
        settings_file({"telegram_bot_token": TOKEN, "telegram_chat_id": CHAT_ID})
        captured = []
        monkeypatch.setattr(notify, "send_telegram", lambda text: captured.append(text) or True)

        notify.notify("sujet", "texte", kind="earnings_filing")

        assert "Critique" in captured[0]
        assert "90/100" in captured[0]


class TestNotifyLogging:
    def test_does_not_raise_when_logging_the_alert_fails(self, no_settings_file, monkeypatch):
        """
        Given l'écriture de l'historique échoue (ex. base indisponible)
        When notify est appelé
        Then il renvoie quand même le résultat de l'envoi au lieu de propager l'exception — un historique
        indisponible ne doit jamais faire échouer le cycle de collecte qui appelle notify.
        """
        monkeypatch.setattr("backend.db.add_alert", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("base indisponible")))

        assert notify.notify("sujet", "texte") == {}
