import io
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

import httplib2
import requests
from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from PIL import Image, ImageOps


DRIVE_FILE_SCOPE = 'https://www.googleapis.com/auth/drive.file'
DRIVE_FOLDER_MIME_TYPE = 'application/vnd.google-apps.folder'
GOOGLE_TOKEN_URI = 'https://oauth2.googleapis.com/token'
MAX_IMAGE_DIMENSION = 2000
JPEG_QUALITY = 85


class DrivePigesError(Exception):
    """Erreur d'authentification ou de transfert Google Drive destinée au personnel."""


@dataclass(frozen=True)
class DriveFolder:
    folder_id: str
    web_view_link: str


def get_drive_service():
    credentials_values = {
        'GOOGLE_CLIENT_ID': os.environ.get('GOOGLE_CLIENT_ID'),
        'GOOGLE_CLIENT_SECRET': os.environ.get('GOOGLE_CLIENT_SECRET'),
        'GOOGLE_REFRESH_TOKEN': os.environ.get('GOOGLE_REFRESH_TOKEN'),
    }
    missing = [key for key, value in credentials_values.items() if not value]
    if missing:
        raise DrivePigesError(
            "Configuration Google Drive incomplète. Renseignez dans le fichier .env : "
            + ', '.join(missing)
            + "."
        )

    credentials = Credentials(
        token=None,
        refresh_token=credentials_values['GOOGLE_REFRESH_TOKEN'],
        token_uri=GOOGLE_TOKEN_URI,
        client_id=credentials_values['GOOGLE_CLIENT_ID'],
        client_secret=credentials_values['GOOGLE_CLIENT_SECRET'],
        scopes=[DRIVE_FILE_SCOPE],
    )
    try:
        credentials.refresh(Request())
        return build('drive', 'v3', credentials=credentials, cache_discovery=False)
    except HttpError as exc:
        raise _http_error(exc) from exc
    except RefreshError as exc:
        error_text = str(exc).lower()
        if 'invalid_grant' in error_text or 'expired or revoked' in error_text:
            raise DrivePigesError(
                "Le refresh token Google Drive a expiré ou a été révoqué. "
                "Générez un nouveau refresh token OAuth, remplacez GOOGLE_REFRESH_TOKEN dans .env, "
                "puis redémarrez Django."
            ) from exc
        raise DrivePigesError(
            "L'authentification Google Drive a échoué. Vérifiez le refresh token et les identifiants OAuth."
        ) from exc
    except (
        GoogleAuthError,
        httplib2.HttpLib2Error,
        requests.exceptions.RequestException,
        OSError,
    ) as exc:
        raise DrivePigesError(
            "L'authentification Google Drive a échoué. Vérifiez le refresh token et les identifiants OAuth."
        ) from exc


def escape_drive_query_literal(value):
    return str(value).replace('\\', '\\\\').replace("'", "\\'")


def _execute(request):
    try:
        return request.execute()
    except HttpError as exc:
        raise _http_error(exc) from exc
    except (GoogleAuthError, httplib2.HttpLib2Error, requests.exceptions.RequestException, OSError) as exc:
        raise DrivePigesError(
            "Google Drive est momentanément inaccessible. Vérifiez la connexion réseau puis réessayez."
        ) from exc


def _http_error(error):
    try:
        payload = json.loads(error.content.decode('utf-8'))
    except (AttributeError, UnicodeDecodeError, ValueError):
        payload = {}

    reasons = {
        item.get('reason', '')
        for item in payload.get('error', {}).get('errors', [])
        if isinstance(item, dict)
    }
    status = getattr(error.resp, 'status', None)

    if 'storageQuotaExceeded' in reasons:
        message = "L'espace de stockage Google Drive du compte est dépassé."
    elif status == 401:
        message = "Le jeton Google Drive n'est plus valide. Vérifiez ou régénérez le refresh token."
    elif status == 429 or 'rateLimitExceeded' in reasons or 'userRateLimitExceeded' in reasons:
        message = "La limite temporaire de requêtes Google Drive est atteinte. Réessayez dans quelques minutes."
    elif status == 403:
        message = (
            "Google Drive a refusé l'opération. Vérifiez les autorisations du compte OAuth, "
            "le partage externe et l'espace de stockage."
        )
    else:
        message = "Google Drive n'a pas pu terminer l'opération. Réessayez ou contactez l'administrateur."
    return DrivePigesError(message)


def _list_files(service, query):
    page_token = None
    while True:
        response = _execute(service.files().list(
            q=query,
            spaces='drive',
            pageSize=100,
            fields='nextPageToken,files(id,name,mimeType,webViewLink)',
            pageToken=page_token,
        ))
        yield from response.get('files', [])
        page_token = response.get('nextPageToken')
        if not page_token:
            break


def get_or_create_folder(service, name, parent_id):
    escaped_name = escape_drive_query_literal(name)
    escaped_parent = escape_drive_query_literal(parent_id)
    query = (
        f"'{escaped_parent}' in parents and name = '{escaped_name}' "
        f"and mimeType = '{DRIVE_FOLDER_MIME_TYPE}' and trashed = false"
    )
    existing = next(_list_files(service, query), None)
    if existing:
        return existing['id']

    folder = _execute(service.files().create(
        body={
            'name': name,
            'mimeType': DRIVE_FOLDER_MIME_TYPE,
            'parents': [parent_id],
        },
        fields='id',
    ))
    return folder['id']


def _ensure_public_read_only(service, folder_id):
    response = _execute(service.permissions().list(
        fileId=folder_id,
        fields='permissions(id,type,role)',
    ))
    anyone_permission = next(
        (permission for permission in response.get('permissions', [])
         if permission.get('type') == 'anyone'),
        None,
    )
    if anyone_permission:
        if anyone_permission.get('role') != 'reader':
            _execute(service.permissions().update(
                fileId=folder_id,
                permissionId=anyone_permission['id'],
                body={'role': 'reader'},
                fields='id,role',
            ))
        return

    _execute(service.permissions().create(
        fileId=folder_id,
        body={'type': 'anyone', 'role': 'reader', 'allowFileDiscovery': False},
        fields='id',
    ))


def _folder_link(service, folder_id):
    result = _execute(service.files().get(fileId=folder_id, fields='webViewLink'))
    return result.get('webViewLink') or f'https://drive.google.com/drive/folders/{folder_id}'


def _folder_id_from_link(folder_link):
    parsed_url = urlparse(folder_link)
    if parsed_url.hostname not in {'drive.google.com', 'www.drive.google.com'}:
        return None

    match = re.search(r'/folders/([^/]+)', parsed_url.path)
    if match:
        return unquote(match.group(1))
    return parse_qs(parsed_url.query).get('id', [None])[0]


def get_or_create_campaign_folder(
    service,
    client_name,
    campaign_reference,
    existing_folder_link='',
):
    campaign_name = campaign_reference.strip()
    if existing_folder_link:
        campaign_id = _folder_id_from_link(existing_folder_link)
        if not campaign_id:
            raise DrivePigesError(
                "Le lien de piges enregistré n'est pas un lien de dossier Google Drive valide."
            )
        existing_folder = _execute(service.files().get(
            fileId=campaign_id,
            fields='id,name,mimeType',
        ))
        if (
            existing_folder.get('mimeType') != DRIVE_FOLDER_MIME_TYPE
            or existing_folder.get('name') != campaign_name
        ):
            raise DrivePigesError(
                "Le lien de piges enregistré ne correspond pas au dossier de cette réservation."
            )
        _ensure_public_read_only(service, campaign_id)
        return DriveFolder(campaign_id, existing_folder_link)

    root_id = get_or_create_folder(service, 'Piges', 'root')
    client_id = get_or_create_folder(service, client_name.strip(), root_id)
    campaign_id = get_or_create_folder(service, campaign_name, client_id)
    _ensure_public_read_only(service, campaign_id)
    return DriveFolder(campaign_id, _folder_link(service, campaign_id))


def _safe_filename_part(value, fallback):
    normalized = unicodedata.normalize('NFKD', value or '')
    ascii_value = normalized.encode('ascii', 'ignore').decode('ascii')
    safe_value = re.sub(r'[^A-Za-z0-9-]+', '_', ascii_value).strip('_-')
    return safe_value or fallback


def build_photo_prefix(city, support_name, photo_date):
    city_part = _safe_filename_part(city, 'Ville')
    support_part = _safe_filename_part(support_name, 'Support')
    return f'{city_part}_{support_part}_{photo_date:%Y-%m-%d}'


def build_photo_filename(prefix, sequence_number):
    return f'{prefix}_{sequence_number}.jpg'


def get_next_photo_number(service, folder_id, prefix):
    escaped_prefix = escape_drive_query_literal(prefix)
    escaped_folder = escape_drive_query_literal(folder_id)
    query = (
        f"'{escaped_folder}' in parents and name contains '{escaped_prefix}' "
        "and trashed = false"
    )
    pattern = re.compile(rf'^{re.escape(prefix)}_(\d+)\.jpg$', re.IGNORECASE)
    existing_numbers = (
        int(match.group(1))
        for file_data in _list_files(service, query)
        if (match := pattern.match(file_data.get('name', '')))
    )
    return max(existing_numbers, default=0) + 1


def prepare_photo(uploaded_file):
    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as opened_image:
            image = ImageOps.exif_transpose(opened_image)
            image.thumbnail(
                (MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION),
                Image.Resampling.LANCZOS,
            )
            if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
                image = image.convert('RGBA')
                background = Image.new('RGB', image.size, 'white')
                background.paste(image, mask=image.getchannel('A'))
                image = background
            else:
                image = image.convert('RGB')

            output = io.BytesIO()
            image.save(output, format='JPEG', quality=JPEG_QUALITY, optimize=True)
            output.seek(0)
            return output
    except (Image.DecompressionBombError, OSError, ValueError) as exc:
        raise DrivePigesError(
            f"Impossible de préparer la photo « {uploaded_file.name} ». Vérifiez le fichier."
        ) from exc
    finally:
        uploaded_file.seek(0)


def upload_photo(service, folder_id, uploaded_file, filename):
    photo_data = prepare_photo(uploaded_file)
    try:
        media = MediaIoBaseUpload(photo_data, mimetype='image/jpeg', resumable=True)
        result = _execute(service.files().create(
            body={'name': filename, 'parents': [folder_id]},
            media_body=media,
            fields='id,name',
        ))
    finally:
        photo_data.close()
    return result['id']


def download_photo(service, drive_file_id):
    output = io.BytesIO()
    downloader = MediaIoBaseDownload(
        output,
        service.files().get_media(fileId=drive_file_id),
    )
    done = False
    try:
        while not done:
            _, done = downloader.next_chunk()
    except HttpError as exc:
        raise _http_error(exc) from exc
    except (
        GoogleAuthError,
        httplib2.HttpLib2Error,
        requests.exceptions.RequestException,
        OSError,
    ) as exc:
        raise DrivePigesError(
            "Google Drive est momentanément inaccessible. Vérifiez la connexion réseau puis réessayez."
        ) from exc
    return output.getvalue()


def delete_photo(service, drive_file_id):
    """Supprime une photo Drive et transforme les erreurs API en message exploitable."""
    _execute(service.files().delete(fileId=drive_file_id))
