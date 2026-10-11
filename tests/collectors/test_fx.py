"""Tests de collectors/fx.py : analyse du flux XML de la BCE, dont la robustesse face à un flux malveillant
(correction appliquée lors de cet audit — voir MAX_ECB_BYTES et le filtre DOCTYPE/ENTITY)."""
import pytest

from collectors.fx import _parse_ecb

VALID_ECB_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <Cube>
    <Cube time="2026-01-02">
      <Cube currency="USD" rate="1.0876"/>
      <Cube currency="GBP" rate="0.8604"/>
    </Cube>
  </Cube>
</gesmes:Envelope>
"""

XML_ENTITY_BOMB = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<lolz>&lol2;</lolz>
"""


class TestParseEcb:
    def test_returns_eur_rate_of_one_for_valid_feed(self):
        """
        Given un flux BCE valide
        When il est analysé
        Then le taux EUR de référence est 1.0 — la BCE exprime tout en base EUR.
        """
        result = _parse_ecb(VALID_ECB_XML)

        assert result["rates"]["EUR"] == pytest.approx(1.0)

    def test_returns_rates_for_each_currency_in_feed(self):
        result = _parse_ecb(VALID_ECB_XML)

        assert result["rates"]["USD"] == pytest.approx(1.0876)
        assert result["rates"]["GBP"] == pytest.approx(0.8604)

    def test_returns_publication_date_from_feed(self):
        result = _parse_ecb(VALID_ECB_XML)

        assert result["date"] == "2026-01-02"

    def test_raises_value_error_if_feed_declares_xml_entities(self):
        """
        Given un flux contenant une déclaration DOCTYPE/ENTITY (attaque par expansion d'entités, « billion laughs »)
        When il est analysé
        Then une ValueError est levée avant tout appel à l'analyseur XML — empêche l'épuisement mémoire si la
        source BCE était compromise ou usurpée.
        """
        with pytest.raises(ValueError, match="entités"):
            _parse_ecb(XML_ENTITY_BOMB)

    def test_raises_value_error_if_feed_exceeds_max_size(self):
        oversized = VALID_ECB_XML + b"<!-- " + b"x" * (3 * 1024 * 1024) + b" -->"

        with pytest.raises(ValueError, match="grosse"):
            _parse_ecb(oversized)
