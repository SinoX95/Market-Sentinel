# Market Sentinel

Plateforme de veille marchés : collecte automatique, scoring, opportunités sourcées, suivi des grands investisseurs, graphiques.

## Utilisation (sans Docker, le plus simple)

Double-clique sur `start.bat` (Linux/Mac : `./start.sh`). Le navigateur s'ouvre sur http://localhost:8000.

- Au premier lancement, le script installe Python (via winget) s'il manque, crée son environnement et installe les dépendances (quelques minutes).
- Laisse la fenêtre noire ouverte : c'est elle qui fait tourner le programme.
- Les données sont dans `data/sentinel.db` et conservées entre les lancements.
- Première collecte : les cours arrivent en quelques secondes, les actualités et les positions des investisseurs (13F) en 1 à 2 minutes.

## Avec Docker (PostgreSQL)

`start-docker.bat` (arrêt : `stop-docker.bat`). Même programme, avec PostgreSQL.

## Connexion

À l'ouverture, une page demande de se connecter : identifiant **test**, mot de passe **test** par défaut. Pour les changer, modifie `login_user` et `login_password` dans `config/settings.json` (`"auth_enabled": false` désactive la page). C'est une protection d'accès locale : le programme n'écoute que sur ta machine avec `start.bat` ; ne l'expose pas sur Internet.

## Déploiement sur Internet (Firebase)

Voir `deploy/README-DEPLOY.md` : Firebase Hosting + Cloud Run + base Postgres + collecte horaire. Le script `deploy\deploy.bat` fait tout.

## Style

Interface épurée façon application de courtage : noir et blanc (thème clair ou sombre selon ton système), vert et rouge réservés aux variations et aux niveaux, mini-courbes, gros chiffres.

## Ce que montre le dashboard

- **Vue d'ensemble** : uniquement tes entreprises, en liste épurée (nom, mini-courbe, niveau, prix et variation), cliquables sur toute la ligne, rangées par niveau (À acheter, À garder, À surveiller, À éviter), sans rien à déplier.
- **Ouverture d'une entreprise** : l'essentiel est visible d'emblée (niveau BUY/HOLD… avec le score, pourquoi, ce qu'il manque pour passer au niveau supérieur, chiffres clés, points forts, points faibles, grands investisseurs). Boutons **Suivre** et **Comparer** (compare l'action avec les meilleures actions du moment : graphique en base 100 et chiffres côte à côte). Trois sections dépliables seulement : le graphique, les actualités et analystes, le détail complet.
- **Recherche** (barre en haut) : tape un nom ou un symbole (NVIDIA, ASML, AMZN…) pour ouvrir la même fiche, et « Suivre » pour l'ajouter à ta watchlist.
- **Secteurs** : les 5 meilleurs secteurs (via leurs ETF), chacun avec ses 3 meilleures entreprises. Un clic sur un secteur (par ex. Cybersécurité) ouvre le classement de toutes ses entreprises (jusqu'à 24), avec niveau, performance et bouton Suivre ; ça marche pour les 25 secteurs. Les autres secteurs et le graphique comparatif sont repliés.
- **Opportunités** : la liste des entreprises (nom, symbole, force) ; clique sur l'une pour voir les raisons d'acheter ou d'attendre, datées et sourcées.
- **Grands investisseurs** : dépôts 13F de 15 fonds : ce que plusieurs fonds font en même temps, ce qu'ils font de tes actions, et le détail de chaque fonds (replié).
- **Sources** : état de chaque source de données, dernière erreur (code HTTP) et conseil.

## Sources de données et fiabilité

Aucune source gratuite n'est garantie exacte à 100 %, ni en temps réel absolu (les flux officiels des bourses sont payants). Le programme recoupe donc plusieurs sources et affiche un **indice de fiabilité** sur chaque fiche.

- **Cours :** Yahoo Finance (principal), Stooq (sans clé, seconde source indépendante), et, avec une clé gratuite à coller dans l'onglet Sources : Finnhub et Tiingo (temps réel, actions US), Twelve Data, Polygon.io, Alpha Vantage. Si Yahoo est bloqué, une autre source prend le relais. La dernière clôture est comparée entre sources ; un écart de plus de 0,3 % déclenche une alerte.
- **Vue d'ensemble en direct :** les prix se rafraîchissent toutes les 45 secondes avec la première source temps réel disponible (Finnhub ou Tiingo ; sinon Yahoo, léger différé possible).
- **Comptes officiels (SEC EDGAR) :** croissance, marges, dette et liquidité calculées depuis le dernier rapport annuel 10-K, comparées à ce que dit Yahoo. Sociétés américaines uniquement.
- **Documents officiels :** derniers dépôts 8-K, 10-Q, 10-K et achats/ventes des dirigeants (formulaire 4), source primaire des annonces.
- **Actualités :** Yahoo, Google, Bing et Finnhub, dédoublonnées.
- **Grands investisseurs :** dépôts 13F de la SEC.

L'onglet **Sources** montre l'état de chaque fournisseur, le quota du jour et permet d'ajouter les clés. Des clés gratuites : finnhub.io, tiingo.com, twelvedata.com, polygon.io, alphavantage.co.

## Erreurs 403 / 429 : causes et solution

- **SEC** : elle exige un contact dans l'en-tête de chaque requête (règle officielle). Sans email, elle répond 403. `start.bat` te le demande au premier lancement (ou onglet Sources) ; sans email, aucune requête n'est envoyée à la SEC.
- **Yahoo Finance** : yfinance n'est pas une API officielle ; Yahoo bloque quand il reçoit trop de requêtes ou depuis un VPN / une IP d'entreprise. Le programme télécharge les cours en groupe, met tout en cache, espace les appels et se met en pause 15 min après un 403/429 (les données déjà collectées restent affichées). Secours optionnel : clé gratuite Finnhub dans `config/settings.json` (`finnhub_api_key`).
- **Google Actualités** : Bing Actualités prend le relais en cas de blocage.

## Limites à connaître

- Un 13F est publié jusqu'à 45 jours après la fin du trimestre et ne donne pas les motifs : les explications sont des lectures des mouvements.
- Pas de tweets : l'API de X est payante.
- Les fondamentaux viennent de Yahoo (non officiel) et peuvent être faux ou manquants ; les seuils du verdict sont génériques (à comparer au secteur). Sans fondamentaux, l'analyse est marquée « partielle » et ne conclut pas.
- Le sentiment des articles est détecté par mots-clés dans le titre : indicatif seulement.
- Ce n'est pas un conseil en investissement.

## Réglages

`config/watchlist.json` : entreprises de base (celles ajoutées avec « Suivre » sont gardées à part). `config/sectors.json` : secteurs et univers d'entreprises. `config/investors.json` : fonds suivis. `config/settings.json` : email de contact SEC, clé Finnhub facultative. `COLLECT_INTERVAL_MIN` : fréquence de collecte (60 par défaut). Changements pris en compte au cycle suivant.

## Structure

`backend/` API + dashboard (`static/`) + boucle automatique · `collectors/` prix, actualités, analystes, fondamentaux, secteurs, recherche, 13F, état des sources · `analysis/` scoring, opportunités, lecture des 13F · `alerts/` à venir · `infrastructure/sql` schéma PostgreSQL · `config/` · `docs/`

## Roadmap

1. [x] Collecte de prix, scoring technique, suivi des recommandations
2. [x] Actualités, analystes, calendrier, 13F, opportunités, graphiques
3. [x] Secteurs en hausse, recherche d'entreprise, analyse complète, diagnostic des sources
4. [ ] Threat intel (CVE / CISA KEV) dans le score
5. [x] Alertes (Discord / Telegram)


## Profil, langue et devises

- Onglet **Profil** (et pastille en haut à droite) : identifiant, nom affiché, email (aussi utilisé pour identifier le programme auprès de la SEC), langue, devise, statistiques de ton suivi (avec « Ne plus suivre »), clés d'API optionnelles, changement de mot de passe et déconnexion.
- **Langue** français / anglais : toute l'interface et les analyses générées par le serveur (scores, fondamentaux, opportunités, 13F, secteurs) suivent la langue choisie.
- **Devises** (EUR, USD, GBP, CHF, CAD, JPY, AUD) : cours et montants convertis avec les taux de référence de la Banque centrale européenne (Frankfurter en secours) ; la courbe d'une entreprise utilise les taux historiques jour par jour. Les variations en % restent celles de la devise de cotation.
- L'onglet **Sources** n'affiche plus que les sources et leur état (GDELT retiré).

## Mise à jour automatique et progressive

Le programme n'a plus de « gros cycle » horaire : un planificateur traite **une seule unité de travail à la fois** (un lot de cours, l'actualité d'une entreprise, un fonds 13F, les secteurs, les taux de change), toujours celle qui est la plus en retard par rapport à sa durée de validité (cours 1 h, actualités 1 h, vérification et catalyseurs plusieurs heures, secteurs 4 h, 13F 24 h). Après chaque unité il attend au moins 20 s, et deux fois la durée du travail effectué si celui-ci a été long : la charge reste faible même avec une grande liste de suivi. Au premier démarrage, il rattrape plus vite. Une source bloquée (403/429) est mise en pause et ignorée le temps qu'il faut. Réglages optionnels : `PRICE_REFRESH_MIN` (défaut 60), `REFRESH_TICK_S` (défaut 20). Sur Cloud Run, Cloud Scheduler appelle `/internal/cycle` toutes les 5 à 10 minutes ; chaque appel traite les unités en retard pendant 45 s au plus.

## Interface

En-tête (nom, actualiser, profil) et onglets toujours visibles au défilement, bouton « retour en haut » après un long défilement, recherche discrète sous l'en-tête à droite, mise en page adaptée téléphone, tablette et grand écran.

Une unité qui n'a rien produit n'est pas retentée avant 2 minutes, et une source en pause (403/429) n'empêche plus les autres fournisseurs de mettre les cours à jour. La barre d'état indique « Mis à jour à HH:MM · il y a X min » et un point clignotant pendant une mise à jour ; l'infobulle donne le nombre de tâches en attente.

## Animations

Onglets avec trait glissant (léger effet néon en mode sombre), apparition en cascade des blocs à chaque changement de page, squelettes de chargement, fenêtres qui s'ouvrent et se ferment en fondu, écran de chargement animé (courbe qui se dessine, étapes) pour les fiches société, secteurs et comparaisons. Tout est désactivé si le système demande de réduire les animations.

## Fiches d'actions : instantanées

Le programme précharge les fiches (5 ans d'historique, fondamentaux, actualités, analystes, SEC) en arrière-plan toutes les heures, et le navigateur les garde en mémoire dès l'ouverture du site : un clic affiche la fiche tout de suite. La fiche montre d'abord l'indice (BUY/HOLD…, score, prix, trois raisons), puis, sans menu caché : l'évolution du cours avec période au choix (1M à 5A) et des chiffres qui s'adaptent (évolution, moyenne par an, plus haut/bas, volatilité), la progression année par année, les chiffres clés en langage simple, les actualités, les analystes et grands investisseurs. Un seul menu « Plus de détails » regroupe le reste.

## Comptes utilisateurs

- Le compte **administrateur** est celui de `config/settings.json` (`login_user` / `login_password`), ou des variables `LOGIN_USER` / `LOGIN_PASSWORD` en déploiement.
- **Chacun crée son compte lui-même** depuis la page de connexion (onglet « Créer un compte » : identifiant + mot de passe, sans email) et est connecté aussitôt. Limites : 5 créations de compte par heure et par adresse IP, 300 comptes au maximum. L'administrateur peut fermer les inscriptions (case dans Profil, ou variable `SIGNUP_ENABLED=0`).
- L'administrateur peut aussi créer un compte à la main dans **Profil > Comptes utilisateurs** (identifiant + mot de passe provisoire, rôle utilisateur ou administrateur), peut réinitialiser un mot de passe ou supprimer un compte. Chacun peut changer son mot de passe dans son profil.
- Les **clés d'API et l'email SEC sont partagés** : ils sont enregistrés une seule fois, côté serveur (en base, donc conservés après un redémarrage ou dans le cloud), utilisés pour tout le monde et jamais affichés aux autres comptes. Seuls les administrateurs peuvent les modifier. Les clés déjà présentes dans `config/settings.json` continuent de fonctionner.
- Chaque utilisateur a **sa propre liste d'entreprises suivies** : le compte principal démarre avec la liste de base du projet (`config/watchlist.json`), les nouveaux comptes démarrent avec une liste vide, sa langue, sa devise et son nom affiché. Les données de marché sont collectées une seule fois pour l'ensemble des listes.
- Les mots de passe des comptes créés sont enregistrés hachés (PBKDF2 + sel). Les connexions échouées sont ralenties et limitées.

## Mobile et accessibilité

Le site s'adapte du petit téléphone (280 px) au grand écran, en portrait comme en paysage. Sur mobile : champs en 16 px (pas de zoom automatique sur iOS), zones tactiles agrandies, hauteur d'écran dynamique (`dvh`), respect des zones sûres (encoche), couleur de la barre du navigateur, et le bouton « retour » du téléphone ferme la fenêtre ouverte au lieu de quitter le site. Les listes et fiches se mettent à jour à la fermeture d'une fenêtre après un « Suivre » / « Ne plus suivre ». Les animations respectent le réglage système « réduire les animations ».

## Stockage : Firestore avec cache mémoire (déploiement Firebase)

En ligne, les données sont enregistrées dans **Firestore** (base du projet Google Cloud, créée par le script de déploiement). Le programme garde tout en mémoire : au démarrage il charge les cours, recommandations et réglages en une fois, chaque lecture suivante est servie sans appel à la base, et chaque écriture met le cache à jour tout de suite. Firestore sert à conserver les données d'un démarrage à l'autre. En local, c'est toujours SQLite ; `DATABASE_URL` (Postgres) reste possible, et `DB_BACKEND=firestore` force Firestore hors Cloud Run.
