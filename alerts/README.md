Notifications (Telegram + Discord) déclenchées automatiquement par le cycle de collecte existant
(`backend/service.py::run_budget` → `_units()`, appelé toutes les 10 min par Cloud Scheduler) — pas de
tâche planifiée séparée. Cinq types de déclencheurs existent aujourd'hui :

- **Changement de niveau de recommandation** (BUY/HOLD/WATCH/AVOID) — `_record_recommendations`.
- **Mouvement de prix** au-delà d'un seuil (`DEFAULT_ALERT_PCT = 7 %`, personnalisable par titre via
  `alert_pct` dans `config/watchlist.json`) — à la fois sur la clôture (`_check_price_moves`, filet de
  sécurité) et **en intrajournalier**, toutes les ~10 min via les données 5 min de yfinance
  (`_check_intraday_moves`). Une alerte ne se répète pas pour le même sens tant que le mouvement ne
  s'aggrave pas d'un palier complet (`INTRADAY_ALERT_STEP_PCT = 5 %`) ou ne s'inverse pas — voir
  `_maybe_alert_price_move`. Quand une actualité récente (moins de 2 jours, déjà en cache grâce à la
  collecte habituelle — aucun appel réseau supplémentaire) va dans le même sens que le mouvement
  (`_movement_note`, la même logique que celle qui explique un mouvement sur le tableau de bord), son
  titre **en français** (traduit automatiquement si besoin, voir `collectors/translate.py`), sa source et
  son lien sont ajoutés au message — c'est le canal le plus concerné par ce résumé. **Limite à connaître** :
  c'est une corrélation par sentiment de titre, jamais une causalité confirmée — l'alerte le dit
  explicitement, et rien n'est ajouté si aucune actualité récente ne va dans le même sens (silence plutôt
  que bruit).
- **Nouveau dépôt officiel de résultats trimestriels** (8-K, item SEC 2.02) — `_check_earnings_filing`,
  branché sur la collecte SEC déjà existante, toutes les 3 h par titre maximum. **Limite à connaître** :
  c'est la publication elle-même qui déclenche l'alerte, pas une comparaison au consensus des analystes —
  la SEC ne fournit pas les attentes du marché, et cette donnée est habituellement payante (Visible Alpha,
  Zacks...). Pour savoir si le résultat publié est un « manqué » ou un « dépassement », il faut lire
  l'actualité qui suit généralement dans l'heure.
- **Annonces officielles des banques centrales** (Fed, BCE — flux RSS) — `_check_official_feeds`, toutes
  les 15 min.
- **Mentions de dirigeants suivis** (`config/executives.json`) — `_check_executives`, toutes les 2 h.
  **Limite à connaître** : ce sont des mentions dans l'actualité, pas des citations vérifiées — à prendre
  comme un signal à vérifier soi-même, pas une déclaration confirmée (voir le score d'importance ci-dessous,
  volontairement bas pour ce type).

## Score d'importance (0-100) et palier

Chaque alerte reçoit un score 0-100 et un palier — **Critique** (≥ 75), **Important** (≥ 45) ou
**Informatif** (< 45) — calculé par des règles explicites (`alerts/importance.py`), jamais par un modèle
opaque : chaque score vient avec sa raison en texte, ajoutée au message envoyé et enregistrée dans
l'historique (`backend/db.py::add_alert` / `recent_alerts`, colonnes `importance_score`/`importance_tier`).
Objectif : pouvoir filtrer/trier plus tard (tableau de bord à venir) sans devoir relire chaque message.

Repères actuels : résultats trimestriels et annonces de banque centrale sont toujours Critique (impact
direct et large) ; un mouvement de prix est Critique au-delà du double du seuil réglé, Important sinon ;
un changement de score atteignant BUY ou AVOID est Important, les autres paliers (HOLD/WATCH) Informatif ;
une mention de dirigeant reste Informatif par défaut (fiabilité non vérifiée, voir plus haut).

## Historique et audit des mouvements manqués

`GET /api/alerts` (filtrable par `ticker`, `limit`) expose l'historique (`backend/db.py::recent_alerts`),
importance comprise. `GET /api/missed-opportunities` (`backend/service.py::find_missed_price_moves`,
Module K) compare les clôtures des `days` derniers jours (90 par défaut) aux seuils de chaque titre et
signale celles qui auraient dû déclencher une alerte de prix mais n'ont aucune trace correspondante dans
l'historique — un contrôle après coup (cycle en panne, données manquantes, alerting pas encore activé à
l'époque...), pas une recommandation d'achat/vente. Un résultat vide est une bonne nouvelle, pas une
absence de données.

## Regroupement par cycle

`run_budget`/`run_cycle` encadrent chaque passage avec `start_batch()`/`flush_batch()`
(`alerts/notify.py`) : si plusieurs déclencheurs se produisent dans le même passage (ex. un changement de
score et un dépôt SEC sur le même titre), un seul message combiné part par canal au lieu d'un message par
déclencheur — trié du plus important au moins important. Chaque élément reste journalisé individuellement
dans l'historique avec son propre score.

## Canaux et configuration

Voir `alerts/notify.py` pour le détail. Deux canaux indépendants, chacun actif dès que ses réglages sont
présents (dans `config/settings.json`, ou les variables d'environnement en cloud — même mécanisme que les
clés d'API existantes). **L'email a été retiré des canaux de notification/actualité** (Discord est désormais
l'unique canal pour ça) — `backend/mailer.py` et les réglages SMTP restent en place uniquement pour les
emails d'authentification/invitation (`backend/auth.py`), sans rapport avec ce module :

- **Telegram** — `telegram_bot_token` + `telegram_chat_id` (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`).
  Envoyé en HTML (`parse_mode: "HTML"`) : le sujet est en gras, et chaque URL (souvent très longue — ex.
  un lien de redirection Google News pour une mention de dirigeant) est masquée derrière un lien court
  « 🔗 Source » au lieu d'être affichée en clair sur plusieurs lignes.
- **Discord** — `discord_webhook_url` (`DISCORD_WEBHOOK_URL`). Un webhook de salon suffit (Paramètres du
  serveur → Intégrations → Webhooks → Nouveau webhook → copier l'URL) : pas besoin d'un bot Discord
  complet avec une connexion permanente, qui aurait demandé un processus à part, contraire à
  l'architecture sans serveur dédié (Cloud Scheduler + Cloud Run) du reste du projet. Envoyé comme un
  embed (sujet en titre, corps en description, URL masquées de la même façon qu'en Telegram mais en
  Markdown Discord). Description tronquée avec avertissement au-delà de la limite Discord (~4000
  caractères).

Sans configuration, les alertes sont silencieusement ignorées sur les canaux (aucune erreur) —
**l'historique continue d'être enregistré dans tous les cas**, pour vérifier après coup ce qui aurait dû
partir.

À faire ensuite, pas encore construit :
- Une page de tableau de bord (visuelle) pour parcourir `/api/alerts` et `/api/missed-opportunities` —
  les deux existent côté API, mais rien ne les affiche encore dans `backend/static/`.
- Seuils personnalisés par titre sur le volume (Module H) — nécessite de collecter le volume, pas fait
  actuellement.
- `find_missed_price_moves` ne couvre que les clôtures ; un audit équivalent pour les mouvements
  intrajournaliers nécessiterait de conserver l'historique des cours 5 min plus longtemps qu'aujourd'hui.
