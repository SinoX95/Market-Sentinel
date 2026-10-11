"""Logo d'entreprise pour les embeds Discord (Module 2) : mapping statique ticker -> domaine, via le
service gratuit Clearbit Logo (pas de clé API, pas de scraping d'article). Un ticker absent de la table
renvoie None — pas d'image plutôt qu'une image cassée ou sans rapport (voir alerts/README.md).

Couvre les tickers actuellement présents dans config/executives.json et les valeurs les plus courantes de
config/watchlist.json ; à étendre à la main au fil des titres suivis — aucune API de résolution automatique
fiable et gratuite n'existe pour ça."""

TICKER_DOMAIN = {
    "NVDA": "nvidia.com", "AMD": "amd.com", "AVGO": "broadcom.com", "TSM": "tsmc.com",
    "PANW": "paloaltonetworks.com", "CRWD": "crowdstrike.com", "NET": "cloudflare.com",
    "MSFT": "microsoft.com", "AAPL": "apple.com", "GOOGL": "abc.xyz", "GOOG": "abc.xyz",
    "AMZN": "amazon.com", "META": "meta.com", "TSLA": "tesla.com", "INTC": "intel.com",
    "QCOM": "qualcomm.com", "MU": "micron.com", "ORCL": "oracle.com", "CRM": "salesforce.com",
    "ADBE": "adobe.com", "IBM": "ibm.com", "CSCO": "cisco.com", "TXN": "ti.com", "ARM": "arm.com",
}


def logo_url(ticker):
    domain = TICKER_DOMAIN.get((ticker or "").upper())
    return f"https://logo.clearbit.com/{domain}?size=128" if domain else None
