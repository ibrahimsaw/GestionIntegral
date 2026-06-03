from datetime import date, datetime, time, timedelta
import json

from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.utils import timezone

from campaigns.models import Campagne, LigneCampagne
from inventory.models import Support, EcranNumerique


# ══════════════════════════════════════════
# UTILITAIRES (inchangés)
# ══════════════════════════════════════════

def get_month_boundaries(year, month):
    """Retourne le premier et le dernier jour d'un mois donné."""
    first_day = date(year, month, 1)
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    return first_day, last_day


def parse_iso_date(value, default=None):
    """Valeur ISO 8601 en date, fallback propre en cas d'erreur."""
    if default is None:
        default = timezone.localdate()
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return default


def parse_iso_time(value, default=None):
    """Valeur ISO 8601 en time, fallback propre en cas d'erreur."""
    if default is None:
        default = time(0, 0)
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError):
        return default


def iter_dates(start_date, end_date):
    """Itérateur inclusif de dates entre start_date et end_date."""
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _generer_timeline(ecran, date_cible):
    """Génère la timeline de diffusion pour un écran numérique sur une journée."""
    spots = LigneCampagne.objects.filter(
        support=ecran.support,
        campagne__date_debut__lte=date_cible,
        campagne__date_fin__gte=date_cible,
        campagne__statut__in=['en_cours', 'a_venir'],
    ).select_related('campagne__client').order_by('ordre_dans_boucle')

    if not spots:
        return []

    boucle = []
    cursor = 0
    for spot in spots:
        duree = spot.campagne.duree_passage or 20
        boucle.append({
            'client':           spot.campagne.client.nom,
            'campagne':         spot.campagne.nom,
            'duree':            duree,
            'debut_dans_boucle': cursor,
        })
        cursor += duree

    boucle_duration = max(cursor, 1)
    start_dt  = datetime.combine(date_cible, ecran.heure_allumage)
    end_dt    = datetime.combine(date_cible, ecran.heure_extinction)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    timeline       = []
    midnight       = datetime.combine(date_cible, time(0, 0))
    current_start  = start_dt

    while current_start < end_dt:
        for item in boucle:
            event_dt = current_start + timedelta(seconds=item['debut_dans_boucle'])
            if event_dt >= end_dt:
                break
            abs_sec = int((event_dt - midnight).total_seconds())
            timeline.append({
                'time':     event_dt.time().strftime('%H:%M:%S'),
                'abs_sec':  abs_sec,
                'client':   item['client'],
                'campagne': item['campagne'],
                'duree':    item['duree'],
            })
        current_start += timedelta(seconds=boucle_duration)

    return timeline[:2000]


# ══════════════════════════════════════════
# VUES
# ══════════════════════════════════════════

# planning/views.py

import json
from datetime import timedelta
import calendar

from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.views import View

from campaigns.models import Campagne, LigneCampagne
from inventory.models import Support


MOIS_NOMS = [
    '', 'Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin',
    'Juillet', 'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre'
]

COLORS = [
    '#6366f1', '#f43f5e', '#10b981', '#f59e0b',
    '#3b82f6', '#ec4899', '#14b8a6', '#8b5cf6',
    '#ef4444', '#22c55e', '#0ea5e9', '#a855f7',
]


def get_month_boundaries(annee, mois):
    _, dernier = calendar.monthrange(annee, mois)
    from datetime import date
    return date(annee, mois, 1), date(annee, mois, dernier)


def iter_dates(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


class PlanningCalendrierView(LoginRequiredMixin, View):
    template_name = 'planning/calendrier.html'

    def get(self, request):
        today  = timezone.localdate()
        mois   = int(request.GET.get('mois',  today.month))
        annee  = int(request.GET.get('annee', today.year))

        # Clamp valeurs
        mois  = max(1, min(12, mois))
        annee = max(2000, min(2100, annee))

        premier_jour, dernier_jour = get_month_boundaries(annee, mois)

        # ── Campagnes actives ce mois ─────────────────────────────────────────
        campagnes = Campagne.objects.filter(
            date_debut__lte=dernier_jour,
            date_fin__gte=premier_jour,
        ).select_related('client').prefetch_related('lignes__support').order_by('date_debut')
        
        campagnes_actives = Campagne.objects.filter(
            date_debut__lte=dernier_jour,
            date_fin__gte=premier_jour,
            statut__in=['en_cours', 'a_venir'],
        ).select_related('client').prefetch_related('lignes__support').order_by('date_debut')

        # Attribution de couleur stable par client
        client_colors = {}
        color_idx = 0
        events = []
        for c in campagnes:
            if c.client_id not in client_colors:
                client_colors[c.client_id] = COLORS[color_idx % len(COLORS)]
                color_idx += 1
            color = client_colors[c.client_id]

            # Badges statut
            statut_labels = {
                'en_cours':  'En cours',
                'a_venir':   'À venir',
                'brouillon': 'Brouillon',
                'terminee':  'Terminée',
                'annulee':   'Annulée',
            }

            events.append({
                'id':          c.pk,
                'title':       c.nom,
                'start':       c.date_debut.isoformat(),
                'end':         (c.date_fin + timedelta(days=1)).isoformat(),
                'color':       color,
                'url':         f'/campaigns/campaigns/{c.pk}/',
                'extendedProps': {
                    'client':      c.client.nom,
                    'nb_supports': c.nb_supports(),
                    'statut':      statut_labels.get(c.statut, c.statut),
                    'reference':   c.reference or '',
                    'type':        c.type_support,
                },
            })

        # ── Stats supports — panneaux ET écrans séparés ───────────────────────
        jours_total    = (dernier_jour - premier_jour).days + 1
        supports_stats = []

        for s in Support.objects.filter(actif=True).order_by('type_support', 'code')[:30]:
            lignes = LigneCampagne.objects.filter(
                support=s,
                campagne__date_debut__lte=dernier_jour,
                campagne__date_fin__gte=premier_jour,
                campagne__statut__in=['en_cours', 'a_venir'],
            ).select_related('campagne')

            jours_occupe = set()
            campagnes_noms = []
            for ligne in lignes:
                start = max(ligne.campagne.date_debut, premier_jour)
                end   = min(ligne.campagne.date_fin,   dernier_jour)
                jours_occupe.update(iter_dates(start, end))
                campagnes_noms.append(ligne.campagne.nom)

            taux = round(len(jours_occupe) / jours_total * 100, 1) if jours_total else 0.0
            # s est de type ecran
            if s.type_support == 'ecran':
                taux = s.taux_occupation_pourcentage()
            if s.type_support == 'panneau':
                taux = s.taux_occupation_pourcentage()
            supports_stats.append({
                'support':   s,
                'taux':      taux,
                'jours':     len(jours_occupe),
                'campagnes': campagnes_noms[:3],  # max 3 pour le tooltip
            })

        supports_stats.sort(key=lambda x: -x['taux'])

        # ── Stats globales du mois ────────────────────────────────────────────
        nb_panneaux_occupe = sum(
            1 for s in supports_stats
            if s['support'].type_support == 'panneau' and s['taux'] > 0
        )
        nb_ecrans_occupe = sum(
            1 for s in supports_stats
            if s['support'].type_support == 'ecran' and s['taux'] > 0
        )
        taux_moyen = (
            round(sum(s['taux'] for s in supports_stats) / len(supports_stats), 1)
            if supports_stats else 0
        )

        # ── Navigation ────────────────────────────────────────────────────────
        prev_m, prev_y = (12, annee - 1) if mois == 1  else (mois - 1, annee)
        next_m, next_y = (1,  annee + 1) if mois == 12 else (mois + 1, annee)

        # ── Jours du mois pour mini-heatmap ──────────────────────────────────
        # { "2026-05-03": nb_campagnes_actives }
        heatmap = {}
        for ev in events:
            from datetime import date as date_cls
            d = date_cls.fromisoformat(ev['start'])
            fin_ev = date_cls.fromisoformat(ev['end']) - timedelta(days=1)
            cur = d
            while cur <= fin_ev and cur <= dernier_jour:
                if cur >= premier_jour:
                    k = cur.isoformat()
                    heatmap[k] = heatmap.get(k, 0) + 1
                cur += timedelta(days=1)

        return self._render(request, {
            'events_json':       json.dumps(events, ensure_ascii=False),
            'heatmap_json':      json.dumps(heatmap),
            'supports_stats':    supports_stats,
            # campagnes_count = nombre de campagnes actives ce mois (filtrées plus haut)
            'campagnes_count':   campagnes_actives.count(),
            'nb_panneaux_occupe': nb_panneaux_occupe,
            'nb_ecrans_occupe':  nb_ecrans_occupe,
            'taux_moyen':        taux_moyen,
            'mois':              mois,
            'annee':             annee,
            'mois_nom':          MOIS_NOMS[mois],
            'premier_jour':      premier_jour,
            'dernier_jour':      dernier_jour,
            'prev':              {'mois': prev_m, 'annee': prev_y},
            'next':              {'mois': next_m, 'annee': next_y},
            'today':             today,
            'client_colors':     client_colors,
        })

    def _render(self, request, context):
        from django.shortcuts import render
        return render(request, self.template_name, context)


class MainCouranteView(LoginRequiredMixin, View):
    """Timeline de diffusion pour un écran et une date."""
    template_name = 'planning/main_courante.html'

    def get(self, request):
        ecrans    = EcranNumerique.objects.select_related('support').order_by('support__code')
        ecran_pk  = request.GET.get('ecran')
        date_str  = request.GET.get('date', timezone.localdate().isoformat())
        date_cible = parse_iso_date(date_str, timezone.localdate())

        ecran_sel = None
        timeline  = []
        stats     = None
        rotation  = 20

        heure_debut_str = request.GET.get('heure_debut', '')
        heure_fin_str   = request.GET.get('heure_fin', '')

        if ecran_pk:
            ecran_sel = get_object_or_404(EcranNumerique, pk=ecran_pk)

            if date_cible == timezone.localdate():
                current = timezone.localtime().time()
                heure_debut = current
                heure_fin_dt = datetime.combine(date_cible, heure_debut) + timedelta(hours=1)
                ecran_extinction_dt = datetime.combine(date_cible, ecran_sel.heure_extinction)
                if heure_fin_dt > ecran_extinction_dt:
                    heure_fin_dt = ecran_extinction_dt
                heure_fin = heure_fin_dt.time()
                heure_debut_str = heure_debut.strftime('%H:%M')
                heure_fin_str = heure_fin.strftime('%H:%M')
            else:
                heure_debut = parse_iso_time(heure_debut_str, ecran_sel.heure_allumage)
                heure_fin   = parse_iso_time(heure_fin_str,   ecran_sel.heure_extinction)

            if heure_fin <= heure_debut:
                heure_fin = ecran_sel.heure_extinction

            full_timeline = _generer_timeline(ecran_sel, date_cible)
            rotation = full_timeline[0]['duree'] if full_timeline else 20
            if full_timeline:
                midnight = datetime.combine(date_cible, time(0, 0))
                start_sec = int((datetime.combine(date_cible, heure_debut) - midnight).total_seconds())
                end_sec = int((datetime.combine(date_cible, heure_fin) - midnight).total_seconds())
                timeline = [entry for entry in full_timeline if start_sec <= entry['abs_sec'] < end_sec]
            else:
                timeline = []

            taux      = ecran_sel.taux_occupation_pourcentage(date_cible) or 0.0
            plage     = f"{heure_debut.strftime('%H:%M')}–{heure_fin.strftime('%H:%M')}"
            stats = {
                'passages':        len(timeline),
                'plage_diffusion': plage,
                'taux_occupation': round(taux, 1),
                'type_ecran':      ecran_sel.get_type_ecran_display(),
            }

        return self.render_to_response(request, {
            'ecrans':        ecrans,
            'ecran_sel':     ecran_sel,
            'timeline':      timeline,
            'date_cible':    date_cible,
            'date_str':      date_str,
            'stats':         stats,
            'heure_debut':   heure_debut_str,
            'heure_fin':     heure_fin_str,
            'rotation':      rotation,
        })

    def render_to_response(self, request, context):
        from django.shortcuts import render
        return render(request, self.template_name, context)


class ApiTauxOccupationView(LoginRequiredMixin, View):
    """API JSON : taux d'occupation de tous les supports sur une période."""

    def get(self, request):
        d1 = parse_iso_date(request.GET.get('date_debut'), timezone.localdate())
        d2 = parse_iso_date(request.GET.get('date_fin'),   timezone.localdate())

        result = []
        for s in Support.objects.filter(etat='bon').prefetch_related('faces'):
            taux = 0.0

            if s.type_support == s.TYPE_ECRAN:
                taux_values = [
                    v for v in (
                        s.taux_occupation_pourcentage(d)
                        for d in iter_dates(d1, d2)
                    )
                    if v is not None
                ]
                taux = round(sum(taux_values) / len(taux_values), 1) if taux_values else 0.0
            else:
                dispo = all(face.is_disponible(d1, d2) for face in s.faces.all())
                taux  = 0 if dispo else 100

            result.append({'id': s.pk, 'code': s.code, 'taux': taux})

        return JsonResponse({'data': result})