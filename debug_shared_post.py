import os, json, datetime, uuid
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'geoad.settings')
import django
django.setup()
from django.test import Client as DjangoClient
from django.urls import reverse
from django.contrib.auth import get_user_model
from campaigns.models import DemandeReservation, Client as CampaignClient
from inventory.models import Support, FacePanneau, FormatSupport

prefix = f'DBG-{uuid.uuid4().hex[:8]}'
standard = FormatSupport.objects.create(code=f'{prefix}-STANDARD', categorie='Standard', dimensions='4m x 3m')
panel = Support.objects.create(code=f'{prefix}-PANEL', nom='Panneau principal', type_support=Support.TYPE_PANNEAU, format=standard.code, latitude=12.34, longitude=-1.51, ville='Ouagadougou')
face = FacePanneau.objects.create(support=panel, label=FacePanneau.LABEL_A)
client_obj = CampaignClient.objects.create(nom='Annonceur existant', contact_nom='Awa Contact', telephone='70000000', email='awa@example.com')
user = get_user_model().objects.create_user(username=f'debug_shared_user_{uuid.uuid4().hex[:8]}', password='pass', role='staff')
start = datetime.date.today() + datetime.timedelta(days=10)
end = start + datetime.timedelta(days=5)
demande = DemandeReservation.objects.create(
    type_client=DemandeReservation.TYPE_CLIENT_EXISTANT,
    client=client_obj,
    reference_client_saisie=client_obj.reference,
    nom_contact='Awa Contact',
    societe='Annonceur existant',
    email='awa@example.com',
    telephone='70000000',
    accepte_contact=True,
    date_debut_souhaitee=start,
    date_fin_souhaitee=end,
    statut=DemandeReservation.STATUT_BROUILLON,
    token_partage=uuid.uuid4(),
)
demande.faces_souhaitees.add(face)
other_panel = Support.objects.create(code=f'{prefix}-EDIT-PANEL', nom='Panneau modifié par le client', type_support=Support.TYPE_PANNEAU, format=standard.code, latitude=12.35, longitude=-1.53, ville='Ouagadougou')
other_face = FacePanneau.objects.create(support=other_panel, label=FacePanneau.LABEL_A)

client = DjangoClient()
client.force_login(user)
updated_start = start + datetime.timedelta(days=2)
updated_end = updated_start + datetime.timedelta(days=7)
resp1 = client.post(
    reverse('portail:demande_partage', kwargs={'token': demande.token_partage}),
    {
        'faces_selectionnees': json.dumps([other_face.pk]),
        'supports_selectionnees': '[]',
        'date_debut': updated_start.isoformat(),
        'date_fin': updated_end.isoformat(),
    },
)
print('RESP1', resp1.status_code, resp1.get('Location'))
print('STEP2 GET', client.get(reverse('portail:demande_partage_etape2', kwargs={'token': demande.token_partage})).status_code)
resp3 = client.post(reverse('portail:demande_partage_etape2', kwargs={'token': demande.token_partage}), {})
print('STEP2 POST', resp3.status_code, resp3.get('Location'))
print('DEMANDE STATUS', DemandeReservation.objects.get(pk=demande.pk).statut)

second_start = updated_start + datetime.timedelta(days=1)
second_end = second_start + datetime.timedelta(days=3)
resp4 = client.post(
    reverse('portail:demande_partage', kwargs={'token': demande.token_partage}),
    {
        'faces_selectionnees': json.dumps([other_face.pk]),
        'supports_selectionnees': '[]',
        'date_debut': second_start.isoformat(),
        'date_fin': second_end.isoformat(),
    },
)
print('RESP4', resp4.status_code, resp4.get('Location'))
if resp4.context and resp4.context.get('form'):
    print('FORM ERRORS', resp4.context['form'].errors)
print(resp4.content.decode()[:1500])
