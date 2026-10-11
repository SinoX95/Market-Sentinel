"""Assistant de premier lancement : demande l'email de contact exigé par la SEC (facultatif, modifiable ensuite)."""
import json
import re
import sys
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "config" / "settings.json"


def main():
    cfg = json.loads(P.read_text(encoding="utf-8")) if P.exists() else {}
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cfg.get("contact_email", "")) or not sys.stdin.isatty():
        return
    print("\nLa SEC (source des positions des grands investisseurs) exige d'identifier les programmes qui l'interrogent.")
    print("Ton email sera uniquement placé dans l'en-tete des requetes vers sec.gov. Laisse vide pour ignorer (les donnees SEC seront desactivees).")
    try:
        email = input("Email : ").strip()
    except EOFError:
        return
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        cfg["contact_email"] = email
        P.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        print("Enregistre dans config/settings.json\n")
    elif email:
        print("Adresse invalide, ignoree. Tu pourras la renseigner dans l'onglet Sources.\n")


main()
