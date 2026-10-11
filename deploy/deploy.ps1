# Déploiement de Market Sentinel : Cloud Run (backend) + Firebase Hosting (site) + Cloud Scheduler (mise à jour progressive toutes les 10 minutes).
# À lancer depuis le dossier du projet :  powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1
# Prérequis (voir deploy\README-DEPLOY.md) : gcloud, firebase-tools, un projet Firebase en plan Blaze. Les données sont stockées dans Firestore (aucune autre base à créer).
param(
  [string]$Project    = "marketsentinel-9f9e4",
  [string]$Region     = "europe-west1",
  [string]$Service    = "market-sentinel",
  [string]$user       = "Sino",
  [string]$email      = "yass75blm@gmail.com",
  [string]$SmtpPassword = "",               # mot de passe d'application Gmail : demandé une seule fois s'il n'est pas déjà enregistré
  [switch]$ChangePassword,                  # deploy\deploy.bat -ChangePassword : changer le mot de passe administrateur
  [string]$PrettySite = "market-sentinel"   # adresse propre (sans l'id du projet) : https://<PrettySite>.web.app
)
# Aucun mot de passe ni aucune clé n'est écrit dans ce fichier : tout est rangé dans Google Secret Manager, saisi une
# seule fois au clavier (masqué) ou lu dans config\settings.json, qui reste sur ce PC et n'est jamais envoyé chez Google.
$ErrorActionPreference = "Continue"
# gcloud ne pose jamais de question cachée : avant, une question posée par une commande dont l'affichage était masqué
# faisait attendre le script indéfiniment, sans rien montrer à l'écran.
$env:CLOUDSDK_CORE_DISABLE_PROMPTS = "1"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Need($cmd) { if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd est introuvable. Installe-le (voir deploy\README-DEPLOY.md)." } }
function Run($what, [scriptblock]$block) { Write-Host "  > $what..."; & $block; if ($LASTEXITCODE -ne 0) { throw "Échec : $what" } }
function Test-Secret($name) { & gcloud secrets describe $name --project $Project *> $null; return ($LASTEXITCODE -eq 0) }
function Set-Secret($name, $value) {
  $exists = Test-Secret $name
  if ($exists -and ((& gcloud secrets versions access latest --secret $name --project $Project 2>$null) -join "`n") -ceq $value) { return }  # inchangé : pas de nouvelle version
  $tmp = New-TemporaryFile
  try {
    [IO.File]::WriteAllText($tmp.FullName, $value)   # sans retour à la ligne final
    if ($exists) { Run "mise à jour du secret $name" { gcloud secrets versions add $name "--data-file=$($tmp.FullName)" --project $Project *> $null } }
    else { Run "création du secret $name" { gcloud secrets create $name "--data-file=$($tmp.FullName)" --project $Project *> $null } }
  } finally { Remove-Item $tmp.FullName -Force -ErrorAction SilentlyContinue }   # le fichier temporaire ne reste jamais sur le disque
}
# Anciennes valeurs d'un secret : détruites (seule la dernière reste), pour qu'un ancien mot de passe ne traîne nulle part.
function Clear-OldVersions($name) {
  if (-not (Test-Secret $name)) { return }
  $vers = & gcloud secrets versions list $name --project $Project --filter="state!=DESTROYED" --sort-by="~createTime" --format="value(name.basename())" 2>$null
  $vers | Select-Object -Skip 1 | ForEach-Object { & gcloud secrets versions destroy $_ --secret $name --project $Project --quiet *> $null }
}
# Saisie masquée à l'écran ; la valeur n'est jamais affichée ni enregistrée dans un fichier.
function Read-Secret($prompt) {
  $sec = Read-Host -AsSecureString $prompt
  $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

Need gcloud; Need firebase
if (-not $Project) { $Project = Read-Host "ID de ton projet Firebase / Google Cloud" }

Run "sélection du projet" { gcloud config set project $Project }
Write-Host "`n--- Activation des services Google Cloud ---"
Run "activation des API" { gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com firestore.googleapis.com firebaserules.googleapis.com iam.googleapis.com --project $Project }

Write-Host "`n--- Base de données Firestore ---"
& gcloud firestore databases describe --database="(default)" --project $Project *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Aucune base Firestore trouvée : création (région eur3)..."
  Run "création de la base Firestore" { gcloud firestore databases create --database="(default)" --location=eur3 --type=firestore-native --project $Project }
} else { Write-Host "Base Firestore existante : utilisée telle quelle." }

Write-Host "`n--- Secrets (Google Secret Manager) ---"
# Mot de passe administrateur : jamais écrit dans un fichier. Demandé au premier lancement, une fois pour remplacer l'ancien
# (qui était écrit en clair dans ce script et dans les envois précédents chez Google), puis seulement avec -ChangePassword.
$rotated = (& gcloud secrets describe ms-login-password --project $Project --format="value(labels.rotated)" 2>$null)
if (-not (Test-Secret "ms-login-password") -or $ChangePassword -or $rotated -ne "v2") {
  Write-Host "Choisis le mot de passe administrateur (12 caractères minimum ; il ne s'affiche pas pendant la saisie)."
  do {
    $p1 = Read-Secret "Nouveau mot de passe"
    $p2 = Read-Secret "Confirme-le"
    $ok = ($p1 -ceq $p2) -and $p1.Length -ge 12
    if (-not $ok) { Write-Host "Les deux saisies diffèrent ou le mot de passe fait moins de 12 caractères : recommence." -ForegroundColor Yellow }
  } while (-not $ok)
  Set-Secret "ms-login-password" $p1
  $p1 = $p2 = $null
  & gcloud secrets update ms-login-password --project $Project --update-labels=rotated=v2 *> $null
}
# Secret de la mise à jour automatique : créé une seule fois puis réutilisé. (Avant, il changeait à chaque lancement : un
# déploiement interrompu laissait Cloud Scheduler avec l'ancien secret, refusé par le serveur, et plus rien ne se mettait à jour.)
$cron = ""
if (Test-Secret "ms-cron-secret") { $cron = ((& gcloud secrets versions access latest --secret ms-cron-secret --project $Project 2>$null) -join "").Trim() }
if ($cron.Length -lt 32) {
  $bytes = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)   # générateur cryptographique
  $cron = -join ($bytes | ForEach-Object { $_.ToString("x2") })
  Set-Secret "ms-cron-secret" $cron
}
# Envoi des codes par email (double authentification de l'administrateur, codes d'invitation) : mot de passe
# d'application Gmail, demandé une seule fois puis conservé dans Secret Manager.
$smtpReady = Test-Secret "ms-smtp-password"
if ($SmtpPassword) { Set-Secret "ms-smtp-password" ($SmtpPassword -replace '\s', ''); $smtpReady = $true }
elseif (-not $smtpReady) {
  Write-Host "`nLes codes de connexion et d'invitation sont envoyés par email depuis $email."
  Write-Host "Colle ici le mot de passe d'application Gmail (16 lettres, à créer sur https://myaccount.google.com/apppasswords),"
  Write-Host "ou appuie sur Entrée pour passer (les codes seront alors écrits dans les journaux Cloud Run) :"
  $plain = Read-Secret "Mot de passe d'application"
  if ($plain) { Set-Secret "ms-smtp-password" ($plain -replace '\s', ''); $smtpReady = $true }
  else { Write-Host "ATTENTION : envoi d'email non configuré, les codes iront dans les journaux Cloud Run." -ForegroundColor Yellow }
  $plain = $null
}
$secrets = "LOGIN_PASSWORD=ms-login-password:latest,CRON_SECRET=ms-cron-secret:latest"
if ($smtpReady) { $secrets += ",SMTP_PASSWORD=ms-smtp-password:latest" }
# Clés d'API : lues dans config\settings.json (qui reste sur ce PC) et rangées dans Secret Manager. Le serveur les reçoit
# au même niveau que l'ancien fichier : une clé modifiée ensuite depuis l'interface du site reste prioritaire.
$keyEnv = [ordered]@{ finnhub_api_key = "FINNHUB_API_KEY"; twelvedata_api_key = "TWELVEDATA_API_KEY"; alphavantage_api_key = "ALPHAVANTAGE_API_KEY";
                      polygon_api_key = "POLYGON_API_KEY"; tiingo_api_key = "TIINGO_API_KEY";
                      # canaux d'alertes (voir alerts/notify.py) : sans ça, ces réglages resteraient sur ce PC
                      # et les alertes ne partiraient jamais depuis Cloud Run.
                      telegram_bot_token = "TELEGRAM_BOT_TOKEN"; telegram_chat_id = "TELEGRAM_CHAT_ID";
                      discord_webhook_url = "DISCORD_WEBHOOK_URL" }
$cfg = $null
if (Test-Path config\settings.json) { try { $cfg = Get-Content config\settings.json -Raw -Encoding UTF8 | ConvertFrom-Json } catch { } }
$keySecrets = @()
foreach ($k in $keyEnv.Keys) {
  $name = "ms-" + ($k -replace '_', '-')
  $v = if ($cfg -and $cfg.$k) { [string]$cfg.$k } else { "" }
  if ($v) { Set-Secret $name $v }
  if (Test-Secret $name) { $secrets += ",$($keyEnv[$k])_DEFAULT=${name}:latest"; $keySecrets += $name }
}
# Compte de service dédié au site, avec seulement les deux droits dont il a besoin (lire ses secrets, utiliser Firestore),
# au lieu du compte par défaut de Google, qui a en général des droits sur tout le projet.
Write-Host "`n--- Compte de service du site ---"
$sa = "market-sentinel-run@$Project.iam.gserviceaccount.com"
& gcloud iam service-accounts describe $sa --project $Project *> $null
if ($LASTEXITCODE -ne 0) {
  Run "création du compte de service" { gcloud iam service-accounts create market-sentinel-run --display-name "Market Sentinel (Cloud Run)" --project $Project *> $null }
  Start-Sleep -Seconds 10   # le temps que Google le propage
}
Run "droits d'accès aux secrets" { gcloud projects add-iam-policy-binding $Project --member="serviceAccount:$sa" --role="roles/secretmanager.secretAccessor" --condition=None *> $null }
Run "droits d'accès à Firestore" { gcloud projects add-iam-policy-binding $Project --member="serviceAccount:$sa" --role="roles/datastore.user" --condition=None *> $null }

Write-Host "`n--- Construction et déploiement sur Cloud Run (quelques minutes) ---"
function Show-Logs { Write-Host "`n--- Derniers messages du serveur (pour comprendre l'erreur) ---"; & gcloud run services logs read $Service --region $Region --project $Project --limit 40 2>&1 | Out-Host }
try {
Run "déploiement Cloud Run" { gcloud run deploy $Service --source . --region $Region --project $Project --allow-unauthenticated `
  --memory 1Gi --cpu 1 --timeout 900 --min-instances 0 --max-instances 2 `
  --set-env-vars "SERVERLESS=1,DB_BACKEND=firestore,GOOGLE_CLOUD_PROJECT=$Project,LOGIN_USER=$user,CONTACT_EMAIL=$email,SMTP_USER=$email" `
  --cpu-boost --service-account $sa --set-secrets $secrets }
} catch { Show-Logs; throw }
$url = (& gcloud run services describe $Service --region $Region --project $Project --format="value(status.url)").Trim()
Write-Host "Cloud Run : $url"
# La connexion à Firestore se fait en arrière-plan au démarrage du conteneur (quelques secondes) : on réessaie plutôt que de
# crier au loup sur le tout premier appel, qui tombe souvent pendant cette fenêtre de démarrage.
$dbReady = $false; $lastErr = ""
for ($i = 1; $i -le 6; $i++) {
  try {
    $h = Invoke-RestMethod "$url/health" -TimeoutSec 60
    if ($h.db) { $dbReady = $true; break }
    $lastErr = "base pas encore prête"
  } catch { $lastErr = $_ }
  if ($i -lt 6) { Start-Sleep -Seconds 5 }
}
if ($dbReady) {
  Write-Host "Base de données Firestore : connectée."
  # La nouvelle version tourne avec son propre compte : l'ancien compte par défaut n'a plus besoin de ces deux droits.
  # (Retirés seulement maintenant : les retirer avant la mise en ligne coupait l'accès à la version encore en service.)
  $num = (& gcloud projects describe $Project --format="value(projectNumber)").Trim()
  foreach ($role in "roles/secretmanager.secretAccessor", "roles/datastore.user") {
    & gcloud projects remove-iam-policy-binding $Project --member="serviceAccount:$num-compute@developer.gserviceaccount.com" --role=$role --condition=None *> $null
  }
}
else { Write-Host "ATTENTION : Firestore injoignable après 30s ($lastErr)." -ForegroundColor Yellow; Show-Logs }

Write-Host "`n--- Mise à jour automatique progressive, toutes les 10 minutes (Cloud Scheduler) ---"
$job = "market-sentinel-cycle"
& gcloud scheduler jobs describe $job --location $Region --project $Project *> $null
$verb = if ($LASTEXITCODE -eq 0) { "update" } else { "create" }
$headerFlag = if ($verb -eq "update") { "--update-headers" } else { "--headers" }   # 'update' n'accepte pas --headers, seulement --update-headers
Run "planification ($verb)" { gcloud scheduler jobs $verb http $job --location $Region --project $Project --schedule "*/10 * * * *" --time-zone "Europe/Paris" `
  --uri "$url/internal/cycle" --http-method POST $headerFlag "X-Cron-Secret=$cron" --attempt-deadline 900s }
# Vérification immédiate : on lance une mise à jour tout de suite et on regarde si le serveur l'a acceptée.
& gcloud scheduler jobs run $job --location $Region --project $Project *> $null
Write-Host "  > test de la mise à jour automatique (environ 1 minute 30)..."
Start-Sleep -Seconds 90
$code = (& gcloud scheduler jobs describe $job --location $Region --project $Project --format="value(status.code)" 2>$null)
if (-not $code -or $code -eq "0") { Write-Host "Mise à jour automatique : OK." }
else { Write-Host "ATTENTION : la mise à jour automatique a échoué (code $code). Relance deploy\deploy.bat." -ForegroundColor Yellow }

Write-Host "`n--- Firebase Hosting ---"
if (Test-Path hosting\static) { Remove-Item hosting\static -Recurse -Force }
New-Item -ItemType Directory -Force hosting | Out-Null
Copy-Item backend\static hosting\static -Recurse -Force
Remove-Item hosting\static\login.html, hosting\static\index.html -Force -ErrorAction SilentlyContinue   # servis par Cloud Run (protégés)
# firebase.json est réécrit en entier à chaque fois (avant : modifié par morceaux, il perdait la cible de l'adresse propre
# après un seul échec et gagnait une ligne vide à chaque lancement)
function Write-FirebaseJson($withTarget) {
  $t = if ($withTarget) { "`n    `"target`": `"pretty`"," } else { "" }
  $json = @"
{
  "hosting": {$t
    "public": "hosting",
    "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
    "headers": [
      { "source": "/static/**", "headers": [
        { "key": "Cache-Control", "value": "no-cache" },
        { "key": "X-Content-Type-Options", "value": "nosniff" }
      ] }
    ],
    "rewrites": [
      { "source": "**", "run": { "serviceId": "$Service", "region": "$Region" } }
    ]
  },
  "firestore": { "rules": "firestore.rules" }
}
"@
  [IO.File]::WriteAllText((Join-Path (Get-Location) "firebase.json"), $json + "`n")
}
Write-FirebaseJson $false
"{`n  `"projects`": { `"default`": `"$Project`" }`n}" | Set-Content .firebaserc -Encoding UTF8

Write-Host "Règles d'accès Firestore (tout accès direct depuis l'extérieur est refusé)..."
& firebase deploy --only firestore:rules --project $Project --non-interactive 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { Write-Host "ATTENTION : règles Firestore non déployées (voir ci-dessus)." -ForegroundColor Yellow }

# Adresse propre, sans l'id auto-généré du projet (ex. market-sentinel.web.app au lieu de marketsentinel-9f9e4.web.app) : un
# deuxième "site" Hosting dans le même projet Firebase. --non-interactive : la commande échoue proprement au lieu d'attendre
# une réponse. En cas d'échec, repli automatique sur l'adresse habituelle, et nouvel essai au prochain déploiement.
Write-Host "Adresse personnalisée : vérification du site '$PrettySite'..."
& firebase hosting:sites:create $PrettySite --project $Project --non-interactive 2>&1 | Out-Host   # échoue sans rien faire si déjà créé (normal après le 1er lancement)
$prettyOk = $true
& firebase target:apply hosting pretty $PrettySite --project $Project --non-interactive 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { $prettyOk = $false }
if ($prettyOk) {
  Write-FirebaseJson $true
  try { Run "déploiement Firebase Hosting ($PrettySite.web.app)" { firebase deploy --only hosting:pretty --project $Project --non-interactive } }
  catch { $prettyOk = $false }
}
if (-not $prettyOk) {
  Write-Host "ATTENTION : adresse '$PrettySite.web.app' indisponible, on garde l'adresse habituelle. Pour essayer un autre nom : deploy\deploy.bat -PrettySite mon-autre-nom" -ForegroundColor Yellow
  Write-FirebaseJson $false
  Run "déploiement Firebase Hosting" { firebase deploy --only hosting --project $Project --non-interactive }
}
Write-FirebaseJson $false   # le fichier sur ce PC reste sans cible : le prochain lancement refait l'essai proprement
$hostingUrl = if ($prettyOk) { "https://$PrettySite.web.app" } else { "https://$Project.web.app" }

Write-Host "`n--- Première collecte de données (1 à 3 minutes) ---"
& gcloud scheduler jobs run $job --location $Region --project $Project
Write-Host "`n--- Nettoyage des anciennes copies chez Google ---"
# Archives de code envoyées pour construire le serveur : inutiles une fois la construction finie (les anciennes contenaient
# config\settings.json et ce script, avec des secrets en clair).
Write-Host "  > suppression des archives de code (peut prendre une minute)..."
& gcloud storage rm --recursive "gs://run-sources-$Project-$Region/**" --quiet *> $null
# Anciennes images du serveur : on garde les 2 plus récentes, Google supprime automatiquement les autres (sous 24 h).
$pol = New-TemporaryFile
'[{"name":"garder-recentes","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":2}},{"name":"supprimer-anciennes","action":{"type":"Delete"},"condition":{"tagState":"any","olderThan":"1d"}}]' | Set-Content $pol.FullName -Encoding ASCII
& gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy --project $Project --location $Region --policy $pol.FullName --no-dry-run --quiet *> $null
Remove-Item $pol.FullName -Force -ErrorAction SilentlyContinue
# Anciennes valeurs des secrets (dont l'ancien mot de passe) : détruites, seule la valeur actuelle reste.
Write-Host "  > destruction des anciennes valeurs des secrets..."
foreach ($n in @("ms-login-password", "ms-cron-secret", "ms-smtp-password") + $keySecrets) { Clear-OldVersions $n }
Write-Host "Anciennes archives, images et valeurs de secrets nettoyées."

Write-Host "`nTerminé. Ouvre $hostingUrl puis connecte-toi (onglet Administrateur : ton email, ton mot de passe, puis le code reçu par email)."
Write-Host "Les premières données apparaissent après la première collecte (quelques minutes)."
