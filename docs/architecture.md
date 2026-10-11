# Architecture

collectors (prix, actualités, analystes, fondamentaux, secteurs, recherche, 13F SEC ; toutes les requêtes passent par http.py / yahoo.py : identification, cadence, pause sur 403/429, journal d'état) -> base (SQLite ou PostgreSQL)
-> analysis (scoring, opportunités, lecture des 13F) -> backend FastAPI -> dashboard web

Scoring technique : BUY >= 80, HOLD 60-79, WATCH 40-59, AVOID/REASSESS < 40.
Opportunités : somme de signaux pondérés (technique, analystes, actualités, calendrier, investisseurs), chacun sourcé.
Chaque changement de niveau est stocké dans `recommendations` pour mesurer la performance réelle.
Les données non tabulaires (actualités, 13F) sont stockées en JSON dans la table `kv`.

Secteurs : score de momentum par ETF sectoriel/thématique, puis classement technique des entreprises des secteurs en hausse.
Analyse d'entreprise : sections notées (technique, valorisation, croissance, rentabilité, solidité, analystes, actualités, grands investisseurs) + timing.
