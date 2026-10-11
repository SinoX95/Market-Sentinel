# Déployer Market Sentinel avec Firebase

## Comment ça marche

Firebase Hosting ne sert que des fichiers statiques. Le programme (Python) tourne donc sur **Cloud Run** (Google Cloud, même projet que Firebase), et Firebase Hosting le place derrière une adresse propre `https://market-sentinel.web.app` (un second « site » Hosting créé automatiquement dans le même projet, pour ne pas avoir l'id du projet — ex. `marketsentinel-9f9e4` — dans l'adresse).

- **Site (HTML, CSS, JS)** : servi par Firebase Hosting.
- **Backend (API, connexion, analyse)** : Cloud Run, qui s'éteint quand personne ne l'utilise.
- **Données** : **Firestore** (dans ton projet Google Cloud, aucun autre compte). Le programme garde tout en cache mémoire, donc les pages restent rapides ; Firestore ne sert qu'à conserver les données entre deux démarrages. Le script crée la base si elle n'existe pas.
- **Mise à jour des données** : Cloud Scheduler appelle le backend toutes les 10 minutes ; à chaque appel, le programme traite pendant 45 secondes au plus les données les plus en retard (cours toutes les heures, actualités, secteurs, grands fonds…), une source à la fois (Cloud Run ne peut pas faire tourner de boucle en arrière-plan).

## Ce qu'il te faut

1. Un compte Google et un **projet Firebase** (console.firebase.google.com).
2. Le plan **Blaze** (paiement à l'usage, carte bancaire exigée). Avec ce mode d'utilisation (quelques heures de calcul par mois), tu restes dans les quotas gratuits ; mets quand même une **alerte de budget** à 5 € dans Google Cloud, par sécurité.
3. Installer sur ton PC :
   - Google Cloud CLI (`gcloud`) : cloud.google.com/sdk/docs/install
   - Node.js puis `npm install -g firebase-tools`
   - puis dans un terminal : `gcloud auth login` et `firebase login`

## Déployer

Dans le dossier du projet, double-clique sur **`deploy\deploy.bat`** (ou lance `powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1`) : tout est déjà réglé dans le script (projet Firebase, identifiant/mot de passe, email SEC, adresse du site), donc rien à saisir. Pour changer une de ces valeurs, modifie les valeurs par défaut en haut de `deploy\deploy.ps1` (bloc `param(...)`), ou passe-les en paramètres, ex. `powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -PrettySite mon-adresse`.

Il active les services Google Cloud, active Firestore, enregistre les secrets, déploie sur Cloud Run, programme la mise à jour automatique (toutes les 10 minutes), déploie Firebase Hosting (avec une adresse propre, sans l'id du projet) et lance une première collecte. Compte 5 à 10 minutes, puis quelques minutes de plus avant que les données apparaissent.

Ensuite ouvre `https://market-sentinel.web.app` et connecte-toi.

Pour mettre à jour le site après une modification, relance le même script.

## Points d'attention

- **Yahoo Finance bloque souvent les adresses des datacenters** (Google Cloud compris). Si les cours ne s'affichent pas, l'onglet **Sources** montrera une erreur 429 ou 403 sur Yahoo. C'est le principal risque de ce déploiement : hébergé en local ou sur un petit serveur personnel, ça marche mieux. Une clé Finnhub gratuite (variable `FINNHUB_API_KEY`) aide pour les actualités et certains chiffres, pas pour l'historique des cours.
- **Le mot de passe protège l'accès**, mais l'app reste sur Internet : choisis un mot de passe long et unique. Tant que les identifiants ne sont pas réglés, la connexion est désactivée.
- Les pages passant par Firebase Hosting ont **60 secondes maximum** ; l'analyse d'une entreprise (5 à 20 secondes) passe, la collecte complète est lancée directement par Cloud Scheduler (15 minutes maximum).
- Le bouton « Actualiser » fait une mise à jour légère (cours et actualités) ; le reste est mis à jour en continu par le planificateur.
- Les réglages passent par des variables d'environnement Cloud Run (`LOGIN_USER`, `CONTACT_EMAIL`, `FINNHUB_API_KEY`) et des secrets (`LOGIN_PASSWORD`, `CRON_SECRET`). Pour changer le mot de passe : relance le script.

## Comptes et clés d'API

- L'identifiant et le mot de passe demandés par le script sont ceux de l'**administrateur**. Les autres personnes créent leur compte elles-mêmes depuis la page de connexion (onglet « Créer un compte ») ; l'administrateur peut fermer les inscriptions dans Profil (ou avec la variable `SIGNUP_ENABLED=0`). Les comptes sont enregistrés dans Firestore.
- Les **clés d'API** présentes dans `config/settings.json` au moment du déploiement sont embarquées dans l'image Cloud Run (privée à ton projet) et servent à tous les utilisateurs. L'administrateur peut aussi les modifier en ligne dans **Profil > Clés d'API partagées** : elles sont alors enregistrées dans la base et gardées après chaque mise à jour. Les autres comptes ne les voient jamais.
- Attention : le site étant public, chaque inscrit consomme le quota gratuit de tes clés. Les créations de compte sont limitées (5 par heure et par adresse IP, 300 comptes au total) ; ferme les inscriptions une fois tes amis inscrits si besoin.

## Tout supprimer

```
gcloud run services delete market-sentinel --region europe-west1
gcloud scheduler jobs delete market-sentinel-cycle --location europe-west1
firebase hosting:disable
```
et supprime les données dans la console Firestore si tu ne veux plus rien garder.

## Ce qui n'a pas pu être testé

Ces scripts n'ont pas été exécutés contre un vrai compte Google Cloud. Le code (mode serverless, connexion, collecte déclenchée à distance) a été testé en local avec PostgreSQL ; les commandes `gcloud` et `firebase` du script, elles, sont à vérifier au premier lancement : en cas d'erreur, envoie le message exact.
