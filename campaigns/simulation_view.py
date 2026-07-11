"""
SimulationCampagneEcranView — v2
================================
Deux modes :
  A) Paramètres libres  -> calcul direct du volume de spots
  B) Spots cible        -> génère jusqu'à 5 propositions (fréquence x tranches)
     qui approchent le nombre voulu à ±5 %

Champ nb_jours co-existe avec date_debut/date_fin :
  - nb_jours seul          -> simulation pure (pas de dates)
  - date_debut + nb_jours  -> date_fin calculée automatiquement
  - date_debut + date_fin  -> nb_jours calculé automatiquement
"""

from __future__ import annotations

import itertools
from datetime import timedelta

from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.views.generic import FormView

from inventory.models import Support
from campaigns.models import (
    DUREE_CHOICES,
    FREQUENCE_CHOICES,
    calculer_duree_tranches,
)

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTES
# ─────────────────────────────────────────────────────────────────────────────

# Fréquences disponibles (secondes) — multiples de 60 uniquement
FREQUENCES_DISPONIBLES: list[int] = sorted(
    {f for f, _ in FREQUENCE_CHOICES if f % 60 == 0}
)

# Tranches typiques : (label_affichage, valeur_str, heures_float)
# une fonction pour me remplire automatiquement tranches_typiques de 06:00 a 23:00 par pas de 5min donc 5min 10min 15min 20min 25min 30min ....
from datetime import datetime, timedelta

def generer_tranches_typiques():
    tranches = []
    
    # On définit l'heure de début globale et l'heure de fin maximale
    heure_depart_globale = datetime.strptime("06:00", "%H:%M")
    heure_fin_maximale = datetime.strptime("23:00", "%H:%M")
    
    # Premier curseur : l'heure de début de la tranche (avance de 5 min en 5 min)
    curr_debut = heure_depart_globale
    while curr_debut < heure_fin_maximale:
        
        # Second curseur : l'heure de fin de la tranche (commence à début + 5 min)
        curr_fin = curr_debut + timedelta(minutes=5)
        while curr_fin <= heure_fin_maximale:
            
            # 1. Formatage des chaînes de caractères "HH:MM"
            debut_str = curr_debut.strftime("%H:%M")
            fin_str = curr_fin.strftime("%H:%M")
            valeur_str = f"{debut_str}-{fin_str}"
            
            # 2. Calcul précis de la durée en heures (float)
            duree_minutes = (curr_fin - curr_debut).total_seconds() / 60
            heures_float = round(duree_minutes / 60, 4)
            
            # 3. Formatage du label d'affichage (ex: "15min — 06:00-06:15" ou "2h30 — 06:00-08:30")
            h_entieres = int(duree_minutes // 60)
            m_restantes = int(duree_minutes % 60)
            
            if h_entieres > 0:
                label_duree = f"{h_entieres}h{m_restantes:02d}" if m_restantes > 0 else f"{h_entieres}h"
            else:
                label_duree = f"{m_restantes}min"
                
            label_affichage = f"{label_duree.ljust(6)} — {valeur_str}"
            
            # 4. Ajout au tableau
            tranches.append((label_affichage, valeur_str, heures_float))
            
            # On passe à la fin suivante (+5 min)
            curr_fin += timedelta(minutes=5)
            
        # On décale le début de la tranche (+5 min)
        curr_debut += timedelta(minutes=5)
        
    return tranches

# Remplacement dynamique de ta constante
TRANCHES_TYPIQUES = generer_tranches_typiques()

TOLERANCE  = 0.05
MAX_PROPS  = 500


# ─────────────────────────────────────────────────────────────────────────────
#  FORMULAIRE
# ─────────────────────────────────────────────────────────────────────────────

class SimulationCampagneEcranForm(forms.Form):

    duree_passage = forms.ChoiceField(
        label="Durée du spot (s)",
        choices=DUREE_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    date_debut = forms.DateField(
        label="Date de début",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    date_fin = forms.DateField(
        label="Date de fin",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    nb_jours = forms.IntegerField(
        label="Nombre de jours",
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ex : 30"}),
        help_text="Seul, avec date_debut, ou avec date_fin. L'autre champ est calculé.",
    )

    frequence = forms.ChoiceField(
        label="Fréquence",
        choices=[("", "— Libre (propositions) —")] + FREQUENCE_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    tranches_horaires = forms.CharField(
        label="Tranches horaires",
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ex : 08:00-12:00,14:00-18:00",
        }),
        help_text="Vide = la simulation propose des tranches. Ou utilisez les 2 champs ci-dessous.",
    )

    # ── Nouveaux champs : saisie simplifiée par heure de début + durée ──────
    heure_debut_diffusion = forms.TimeField(
        label="Heure de début de diffusion",
        required=False,
        widget=forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
        help_text="Ex : 08:00",
    )
    duree_diffusion_heures = forms.DecimalField(
        label="Durée de diffusion (heures)",
        required=False,
        min_value=0.25,
        max_digits=4,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            "class": "form-control",
            "step": "0.25",
            "placeholder": "Ex : 4 ou 4.5",
        }),
        help_text="Nombre d'heures à partir de l'heure de début (ex : 4.5 = 4h30).",
    )

    ecrans = forms.ModelMultipleChoiceField(
        label="Écrans ciblés",
        queryset=Support.objects.filter(type_support="ecran", actif=True).order_by("code"),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "list-unstyled"}),
        help_text="Vide = tous les écrans actifs.",
    )

    nb_spots_cible = forms.IntegerField(
        label="Nombre de spots souhaités",
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ex : 4000"}),
        help_text="Laissez vide pour un calcul direct.",
    )

    def clean(self):
        data     = super().clean()
        d_debut  = data.get("date_debut")
        d_fin    = data.get("date_fin")
        nb_jours = data.get("nb_jours")

        # Résolution période
        if d_debut and d_fin:
            if d_fin < d_debut:
                raise forms.ValidationError("La date de fin doit être après la date de début.")
            data["nb_jours"] = (d_fin - d_debut).days + 1
        elif d_debut and nb_jours:
            data["date_fin"] = d_debut + timedelta(days=nb_jours - 1)
        elif d_fin and nb_jours:
            data["date_debut"] = d_fin - timedelta(days=nb_jours - 1)
        elif nb_jours:
            pass  # simulation pure sans dates
        else:
            raise forms.ValidationError(
                "Renseignez au moins : nb_jours, ou date_debut + date_fin, "
                "ou date_debut + nb_jours."
            )

        # ── Calcul automatique de la tranche horaire depuis heure_debut + durée ──
        heure_debut = data.get("heure_debut_diffusion")
        duree_h     = data.get("duree_diffusion_heures")
        tranches    = data.get("tranches_horaires")

        if heure_debut and duree_h and not tranches:
            debut_dt = datetime.combine(datetime.today(), heure_debut)
            fin_dt   = debut_dt + timedelta(hours=float(duree_h))

            # Cap à 23:00 comme le reste du système (heure_fin_maximale)
            heure_fin_max = datetime.combine(datetime.today(), datetime.strptime("23:00", "%H:%M").time())
            if fin_dt > heure_fin_max:
                self.add_error(
                    "duree_diffusion_heures",
                    f"La diffusion dépasserait 23:00 (fin calculée : {fin_dt.strftime('%H:%M')}). "
                    "Réduisez la durée ou avancez l'heure de début."
                )
            else:
                tranche_str = f"{heure_debut.strftime('%H:%M')}-{fin_dt.strftime('%H:%M')}"
                data["tranches_horaires"] = tranche_str
                tranches = tranche_str

        elif (heure_debut and not duree_h) or (duree_h and not heure_debut):
            self.add_error(
                "duree_diffusion_heures" if heure_debut else "heure_debut_diffusion",
                "Renseignez à la fois l'heure de début ET la durée pour générer une tranche."
            )

        # Validation tranches si saisie (manuelle ou générée)
        if tranches:
            try:
                h = calculer_duree_tranches(tranches)
                if h <= 0:
                    raise ValueError
            except Exception:
                self.add_error("tranches_horaires", "Format invalide — ex : 08:00-12:00,14:00-18:00")

        return data

# ─────────────────────────────────────────────────────────────────────────────
#  MOTEUR DE CALCUL
# ─────────────────────────────────────────────────────────────────────────────

def _spots(freq: int, heures: float, nb_jours: int, nb_ecrans: int) -> float:
    """spots = (3600 / freq) x heures x jours x ecrans"""
    return (3600 / freq) * heures * nb_jours * nb_ecrans


def _generer_propositions(
    nb_spots_cible: int,
    nb_jours: int,
    nb_ecrans: int,
    duree_passage: int,
    frequence_fixe: int | None,
    tranches_fixe: str | None,
) -> list[dict]:
    """
    Parcourt toutes les combinaisons (fréquence x tranches) disponibles
    et retourne les MAX_PROPS plus proches du cible à ±TOLERANCE.
    """
    freq_candidates = (
        [frequence_fixe]
        if frequence_fixe
        else FREQUENCES_DISPONIBLES
    )
    tranches_candidates = (
        [("", tranches_fixe, calculer_duree_tranches(tranches_fixe))]
        if tranches_fixe
        else TRANCHES_TYPIQUES
    )

    borne_min = nb_spots_cible * (1 - TOLERANCE)
    borne_max = nb_spots_cible * (1 + TOLERANCE)

    resultats = []
    for freq, (t_label, t_val, t_heures) in itertools.product(
        freq_candidates, tranches_candidates
    ):
        if t_heures <= 0:
            continue
        spots    = _spots(freq, t_heures, nb_jours, nb_ecrans)
        ecart    = ((spots - nb_spots_cible) / nb_spots_cible) * 100

        if borne_min <= spots <= borne_max:
            resultats.append({
                "frequence":            freq,
                "frequence_label":      f"toutes les {freq}s ({freq//60} min)",
                "tranches_label":       t_label or t_val,
                "tranches_val":         t_val,
                "heures_tranches":      t_heures,
                "spots_total":          round(spots),
                "spots_par_jour":       round(spots / nb_jours),
                "spots_par_jour_ecran": round(spots / nb_jours / nb_ecrans),
                "ecart_pct":            round(ecart, 2),
                "abs_ecart":            abs(ecart),
                "heures_antenne":       round((spots * duree_passage) / 3600, 2),
            })

    # Tri par proximité, dédoublonnage par (freq, heures), limite MAX_PROPS
    resultats.sort(key=lambda x: x["abs_ecart"])
    seen, uniq = set(), []
    for r in resultats:
        key = (r["frequence"], r["heures_tranches"])
        if key not in seen:
            seen.add(key)
            uniq.append(r)
        if len(uniq) >= MAX_PROPS:
            break

    return uniq


def _calcul_direct(
    frequence: int,
    tranches_horaires: str,
    nb_jours: int,
    nb_ecrans: int,
    duree_passage: int,
    ecrans_qs,
    date_debut=None,
) -> dict:
    """Calcul direct quand fréquence ET tranches sont fournis."""
    heures               = calculer_duree_tranches(tranches_horaires)
    spots_total          = _spots(frequence, heures, nb_jours, nb_ecrans)
    spots_par_jour_ecran = round(spots_total / nb_jours / max(nb_ecrans, 1))

    details_ecrans = []
    for support in ecrans_qs.select_related("ecran_info"):
        ecran_info  = getattr(support, "ecran_info", None)
        taux_actuel = taux_apres = peut = msg = None

        if ecran_info and date_debut:
            taux_actuel = ecran_info.taux_occupation_pourcentage(date_debut)
            impact_sec  = (3600 / frequence) * duree_passage * heures
            sec_dispo   = ecran_info.secondes_totales_disponibles_jour
            occupe      = ecran_info.calculer_occupation_reelle(date_debut)
            if sec_dispo:
                taux_apres = round(min(((occupe + impact_sec) / sec_dispo) * 100, 100), 2)
            peut, msg = ecran_info.peut_accueillir_spot(
                duree_sec    = duree_passage,
                frequence_min= frequence / 60,
                date_debut   = date_debut,
                date_fin     = date_debut + timedelta(days=nb_jours - 1),
            )

        details_ecrans.append({
            "support":          support,
            "taux_actuel":      taux_actuel,
            "taux_apres":       taux_apres,
            "peut_accueillir":  peut,
            "message_dispo":    msg,
            "spots_par_jour":   spots_par_jour_ecran,
            "spots_total":      round(spots_total / max(nb_ecrans, 1)),
        })

    return {
        "mode":                  "direct",
        "frequence":             frequence,
        "frequence_label":       f"toutes les {frequence}s ({frequence//60} min)",
        "tranches_val":          tranches_horaires,
        "heures_tranches":       round(heures, 2),
        "nb_jours":              nb_jours,
        "nb_ecrans":             nb_ecrans,
        "spots_par_heure":       round(3600 / frequence, 2),
        "spots_par_jour_ecran":  spots_par_jour_ecran,
        "spots_total_ecran":     round(spots_total / max(nb_ecrans, 1)),
        "spots_total_tous":      round(spots_total),
        "heures_antenne":        round((spots_total * duree_passage) / 3600, 2),
        "details_ecrans":        details_ecrans,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  VIEW
# ─────────────────────────────────────────────────────────────────────────────

class SimulationCampagneEcranView(LoginRequiredMixin, FormView):
    template_name = "campaigns/simulation_ecran.html"
    form_class    = SimulationCampagneEcranForm

    def get_form(self, form_class=None):
        if form_class is None:
            form_class = self.get_form_class()
        return form_class(self.request.GET) if self.request.GET else form_class()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"title": "Simulation de campagne écran", "resultats": None,
                         "propositions": None, "erreur": None})

        form = context["form"]
        if not self.request.GET or not form.is_valid():
            return context

        data           = form.cleaned_data
        duree_passage  = int(data["duree_passage"])
        nb_jours       = data["nb_jours"]
        date_debut     = data.get("date_debut")
        frequence      = int(data["frequence"]) if data.get("frequence") else None
        # tranches en h min secondes
        tranches       = data.get("tranches_horaires") or None
        
        nb_spots_cible = data.get("nb_spots_cible")

        ecrans_qs = data.get("ecrans") or Support.objects.filter(type_support="ecran", actif=True)
        nb_ecrans = ecrans_qs.count()

        if nb_ecrans == 0:
            context["erreur"] = "Aucun écran actif trouvé."
            return context

        # ── Mode A : calcul direct ────────────────────────────────────────────
        if frequence and tranches and not nb_spots_cible:
            context["resultats"] = _calcul_direct(
                frequence, tranches, nb_jours, nb_ecrans,
                duree_passage, ecrans_qs, date_debut,
            )

        # ── Mode B : propositions vers cible ─────────────────────────────────
        elif nb_spots_cible:
            props = _generer_propositions(
                nb_spots_cible = nb_spots_cible,
                nb_jours       = nb_jours,
                nb_ecrans      = nb_ecrans,
                duree_passage  = duree_passage,
                frequence_fixe = frequence,
                tranches_fixe  = tranches,
            )
            if not props:
                context["erreur"] = (
                    f"Aucune combinaison trouvée à ±5 % de {nb_spots_cible:,} spots "
                    f"pour {nb_jours} jours / {nb_ecrans} écran(s). "
                    "Essayez de modifier la durée du spot ou le nombre d'écrans."
                )
            else:
                context.update({
                    "propositions":   props,
                    "nb_spots_cible": nb_spots_cible,
                    "nb_jours":       nb_jours,
                    "nb_ecrans":      nb_ecrans,
                    "duree_passage":  duree_passage,
                })
        else:
            context["erreur"] = (
                "Renseignez soit (fréquence + tranches horaires) pour un calcul direct, "
                "soit un nombre de spots cible pour obtenir des propositions."
            )

        return context

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self.get_context_data())
