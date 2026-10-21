# Configuration des piges Google Drive

Les transferts utilisent le Drive personnel du compte Google connecté par OAuth
2.0. Le code n'embarque aucun identifiant ; les trois valeurs sont lues depuis
les variables d'environnement.

## Préparer Google Cloud et le compte OAuth

1. Dans Google Cloud Console, créez ou sélectionnez un projet puis activez
   **Google Drive API**.
2. Configurez l'écran de consentement OAuth, renseignez le nom de l'application,
   les coordonnées et le domaine. Pour un compte Google externe à l'organisation,
   publiez l'application en **Production** et terminez la vérification demandée
   par Google pour l'écran de consentement et les scopes utilisés. Un client
   externe laissé en mode test peut voir ses refresh tokens expirer après sept
   jours.
3. Créez un identifiant OAuth de type **Application Web** et ajoutez
   `https://developers.google.com/oauthplayground` comme URI de redirection
   autorisé si vous utilisez le Playground.
4. Dans [OAuth 2.0 Playground](https://developers.google.com/oauthplayground),
   ouvrez les paramètres (roue dentée), cochez **Use your own OAuth credentials**
   et renseignez le client ID et le secret. Indiquez le scope
   `https://www.googleapis.com/auth/drive.file`, autorisez l'accès avec le
   compte Google qui doit posséder les dossiers, puis échangez le code
   d'autorisation contre les jetons en mode d'accès hors ligne (`offline`) et
   avec le consentement forcé si l'option est proposée. Copiez le
   `refresh_token` retourné.
5. Copiez [.env.example](../.env.example) en `.env` (non suivi par Git) et
   renseignez `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` et
   `GOOGLE_REFRESH_TOKEN`. Ne partagez pas et ne commitez pas ce fichier.
6. Installez les dépendances et appliquez la migration :

   ```powershell
   pip install -r requirements.txt
   python manage.py migrate
   ```

Le premier envoi crée `Piges`, le dossier du client et celui de la réservation.
Les envois suivants réutilisent ces dossiers. Seul le dossier de réservation
reçoit une permission « toute personne disposant du lien » en lecture seule.
Avec le scope `drive.file`, l'application peut réutiliser les dossiers qu'elle
a créés ou auxquels l'utilisateur lui a explicitement donné accès ; les
dossiers préexistants créés hors de l'application peuvent nécessiter cet accès
préalable.

## Traitement des envois

L'envoi est synchrone : chaque requête valide au plus 20 images, puis les
redimensionne et les envoie une à une. Cette première version évite d'introduire
un worker et Redis dans le déploiement actuel. Si les transferts approchent la
limite de temps du serveur web, il faudra déplacer la boucle dans une tâche
Celery et déployer Redis ainsi qu'un worker.
