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
import math
import re
from datetime import timedelta
from urllib.parse import urlencode

from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import FormView

from inventory.models import Support
from campaigns.models import (
    DUREE_CHOICES,
    FREQUENCE_CHOICES,
    LigneCampagne,
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
MAX_SCENARIOS_PAR_NB_ECRANS = 6

TRANCHES_PREDEFINIES = [
    ("Plein temps", "06:00-23:00"),
    ("Journée", "07:00-21:00"),
    ("Matin et soir", "07:00-09:30,17:00-20:00"),
    ("Bureaux", "08:00-18:00"),
    ("Midi et soir", "12:00-14:30,18:00-21:00"),
    ("Soirée", "18:00-22:30"),
    ("Demi-journée", "12:00-18:00"),
]


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
        label="Tranches horaires principales",
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ex : 08:00-12:00,14:00-18:00",
        }),
        help_text="Tranche principale à utiliser. Laissez vide pour laisser la simulation proposer des tranches.",
    )

    tranches_horaires_alternatives = forms.CharField(
        label="Créneaux à tester (mode Propositions)",
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ex : 08:00-10:00,16:00-20:00",
        }),
        help_text=(
            "Actif UNIQUEMENT si « Tranches horaires principales » est vide et qu'un "
            "« Nombre de spots souhaités » est renseigné. Restreint les créneaux testés "
            "pour approcher l'objectif (sinon, tous les créneaux de 06:00 à 23:00 sont testés)."
        ),
    )

    jours_ajout = forms.IntegerField(
        label="Jours supplémentaires à ajouter",
        required=False,
        initial=0,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 0}),
        help_text="Ajoute des jours à la période pour augmenter le volume total de spots.",
    )

    frequence_jours_ajout = forms.ChoiceField(
        label="Fréquence pour les jours ajoutés",
        choices=[("", "— idem principale —")] + FREQUENCE_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Fréquence à utiliser sur les jours supplémentaires.",
    )

    tranches_horaires_jours_ajout = forms.CharField(
        label="Tranches horaires pour les jours ajoutés",
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Ex : 08:00-12:00",
        }),
        help_text="Tranche spécifique aux jours supplémentaires.",
    )

    ecrans_jours_ajout = forms.ModelMultipleChoiceField(
        label="Écrans pour les jours ajoutés",
        queryset=Support.objects.filter(type_support="ecran", actif=True).order_by("code"),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "list-unstyled"}),
        help_text="Sélectionnez les écrans à utiliser uniquement pour les jours ajoutés.",
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

    nombre_visuels = forms.IntegerField(
        label="Nombre de visuels",
        required=False,
        initial=1,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1}),
        help_text="Par défaut : 1. Le calcul des spots l’intègre automatiquement.",
    )

    ecrans = forms.ModelMultipleChoiceField(
        label="Écrans ciblés",
        queryset=Support.objects.filter(type_support="ecran", actif=True).order_by("code"),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "list-unstyled"}),
        help_text="Sélectionnez les écrans concernés par cette simulation.",
    )

    nb_ecrans_cible = forms.IntegerField(
        label="Nombre d’écrans",
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1, "placeholder": "Automatique"}),
        help_text="Laissez vide pour comparer plusieurs nombres d’écrans disponibles.",
    )

    nb_spots_cible = forms.IntegerField(
        label="Objectif de spots par écran",
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ex : 4000"}),
        help_text="À utiliser à la place de l’objectif total.",
    )

    nb_spots_cible_total = forms.IntegerField(
        label="Objectif total de spots",
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ex : 4500"}),
        help_text="Nombre global pour tous les écrans sélectionnés.",
    )

    def clean(self):
        data     = super().clean()
        if data.get("nb_spots_cible") is not None and data.get("nb_spots_cible_total") is not None:
            self.add_error("nb_spots_cible_total", "Renseignez l’objectif par écran ou l’objectif total, pas les deux.")

        if data.get("ecrans") and data.get("nb_ecrans_cible") is not None:
            self.add_error("nb_ecrans_cible", "Cochez des écrans ou saisissez un nombre, pas les deux.")

        d_debut  = data.get("date_debut")
        d_fin    = data.get("date_fin")
        nb_jours = data.get("nb_jours")
        has_target = (
            data.get("nb_spots_cible") is not None
            or data.get("nb_spots_cible_total") is not None
        )
        jours_ajout = int(data.get("jours_ajout") or 0)
        jours_principal = int(nb_jours or 0)

        # Résolution période principale uniquement
        if d_debut and d_fin:
            if d_fin < d_debut:
                raise forms.ValidationError("La date de fin doit être après la date de début.")
            data["nb_jours"] = (d_fin - d_debut).days + 1
            data["date_fin"] = d_fin
        elif d_debut and nb_jours:
            data["date_fin"] = d_debut + timedelta(days=jours_principal - 1)
        elif d_fin and nb_jours:
            data["date_debut"] = d_fin - timedelta(days=jours_principal - 1)
        elif nb_jours:
            data["nb_jours"] = jours_principal
        elif not has_target:
            raise forms.ValidationError(
                "Renseignez au moins une période ou un objectif de spots pour lancer la recherche."
            )
        else:
            data["nb_jours"] = None

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

        if data.get("frequence_jours_ajout") == "":
            data["frequence_jours_ajout"] = data.get("frequence")

        if data.get("tranches_horaires_jours_ajout"):
            try:
                h = calculer_duree_tranches(data["tranches_horaires_jours_ajout"])
                if h <= 0:
                    raise ValueError
            except Exception:
                self.add_error("tranches_horaires_jours_ajout", "Format invalide — ex : 08:00-12:00")

        # Validation tranches alternatives si saisie
        alternatives = data.get("tranches_horaires_alternatives")
        tranches_alt_list = []
        if alternatives:
            raw_parts = [part.strip() for part in alternatives.replace("\n", ",").replace(";", ",").split(",") if part.strip()]
            for part in raw_parts:
                try:
                    h = calculer_duree_tranches(part)
                    if h <= 0:
                        raise ValueError
                    tranches_alt_list.append(part)
                except Exception:
                    self.add_error("tranches_horaires_alternatives", f"Format invalide — ex : 08:00-12:00,14:00-18:00 ({part})")
                    break
            data["tranches_horaires_alternatives_list"] = tranches_alt_list
        else:
            data["tranches_horaires_alternatives_list"] = []

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

def _spots(freq: int, heures: float, nb_jours: int, nb_ecrans: int, nombre_visuels: int = 1) -> float:
    """spots = (3600 / freq) x heures x jours x écrans x nombre_visuels"""
    return (3600 / freq) * heures * nb_jours * nb_ecrans * max(nombre_visuels, 1)


def calculer_repartition_blocs(
    nb_jours_principal: int,
    frequence_principale: int,
    heures_principales: float,
    nb_ecrans_principaux: int,
    nombre_visuels: int,
    jours_ajout: int,
    frequence_ajout: int,
    heures_ajout: float,
    nb_ecrans_ajout: int,
) -> dict:
    principal = _spots(
        frequence_principale,
        heures_principales,
        nb_jours_principal,
        nb_ecrans_principaux,
        nombre_visuels=nombre_visuels,
    )
    ajout = _spots(
        frequence_ajout,
        heures_ajout,
        jours_ajout,
        nb_ecrans_ajout,
        nombre_visuels=nombre_visuels,
    )
    return {
        "principal": round(principal),
        "ajout": round(ajout),
        "gain": round(ajout),
        "total": round(principal + ajout),
        "nb_ecrans_principal": nb_ecrans_principaux,
        "nb_ecrans_ajout": nb_ecrans_ajout,
        "nb_ecrans_total": nb_ecrans_principaux + nb_ecrans_ajout,
    }


def calculer_total_spots_avec_jours_ajoutes(
    nb_jours_principal: int,
    frequence_principale: int,
    heures_principales: float,
    nb_ecrans_principaux: int,
    nombre_visuels: int,
    jours_ajout: int,
    frequence_ajout: int,
    heures_ajout: float,
    nb_ecrans_ajout: int,
) -> float:
    return calculer_repartition_blocs(
        nb_jours_principal=nb_jours_principal,
        frequence_principale=frequence_principale,
        heures_principales=heures_principales,
        nb_ecrans_principaux=nb_ecrans_principaux,
        nombre_visuels=nombre_visuels,
        jours_ajout=jours_ajout,
        frequence_ajout=frequence_ajout,
        heures_ajout=heures_ajout,
        nb_ecrans_ajout=nb_ecrans_ajout,
    )["total"]


def _format_duree_heures(heures: float) -> str:
    """Formate une durée en heures décimales en label lisible : 5h, 4h30, 45min…"""
    total_minutes = round(heures * 60)
    h, m = divmod(total_minutes, 60)
    if h and m:
        return f"{h}h{m:02d}"
    if h:
        return f"{h}h"
    return f"{m}min"


def _construire_timeline_heures(tranches_horaires: str) -> list[dict]:
    """Construit les 24 créneaux horaires attendus par la timeline du template."""
    actives = set()
    for tranche in (tranches_horaires or "").split(","):
        correspondance = re.fullmatch(r"\s*(\d{1,2}):\d{2}\s*-\s*(\d{1,2}):\d{2}\s*", tranche)
        if not correspondance:
            continue
        debut, fin = (int(valeur) for valeur in correspondance.groups())
        if not (0 <= debut <= 23 and 0 <= fin <= 24) or debut == fin:
            continue
        if fin > debut:
            actives.update(range(debut, fin))
        else:
            actives.update(range(debut, 24))
            actives.update(range(0, fin))

    return [
        {"h": heure, "cls": "active-hour" if heure in actives else ""}
        for heure in range(24)
    ]


def _tranche_val_depuis_duree(heures: float, heure_debut: str = "06:00") -> str:
    """Construit une plage 'HH:MM-HH:MM' concrète (utilisable par le formulaire) à partir d'une durée."""
    debut_dt = datetime.strptime(heure_debut, "%H:%M")
    fin_dt = debut_dt + timedelta(hours=heures)
    heure_fin_max = datetime.strptime("23:00", "%H:%M")
    if fin_dt > heure_fin_max:
        fin_dt = heure_fin_max
    return f"{debut_dt.strftime('%H:%M')}-{fin_dt.strftime('%H:%M')}"


def generer_combinaisons_complement(
    manque: int,
    nombre_visuels: int,
    ecrans_disponibles: int,
    tolerance: float = 0.08,
    max_jours_ajout: int = 4,
    max_ecrans_ajout: int | None = None,
    max_resultats: int = 8,
) -> list[dict]:
    """
    Cherche des combinaisons (jours ajoutés x écrans ajoutés x fréquence)
    dont la tranche horaire nécessaire (déduite par calcul) permet
    de combler `manque` spots à ±tolerance près.

    Ex : il manque 600 spots -> "ajouter 1 jour, 2 écrans, fréquence 4 min,
    sur une tranche de 5h" est une des combinaisons proposées.
    """
    if manque <= 0:
        return []

    if max_ecrans_ajout is None:
        max_ecrans_ajout = ecrans_disponibles

    borne_min, borne_max = manque * (1 - tolerance), manque * (1 + tolerance)
    HEURE_MIN, HEURE_MAX = 0.25, 17.0  # 17h = amplitude max 06:00-23:00

    combinaisons = []
    for jours_ajout in range(1, max_jours_ajout + 1):
        for ecrans_ajout in range(1, max_ecrans_ajout + 1):
            for freq in FREQUENCES_DISPONIBLES:
                diviseur = (3600 / freq) * jours_ajout * ecrans_ajout * max(nombre_visuels, 1)
                if diviseur <= 0:
                    continue

                heures_necessaires = manque / diviseur
                if not (HEURE_MIN <= heures_necessaires <= HEURE_MAX):
                    continue

                # Arrondi au pas de 5 minutes (1/12 h), comme TRANCHES_TYPIQUES
                heures_arrondies = round(heures_necessaires * 12) / 12
                if heures_arrondies <= 0:
                    continue

                spots_obtenus = _spots(freq, heures_arrondies, jours_ajout, ecrans_ajout, nombre_visuels)
                if not (borne_min <= spots_obtenus <= borne_max):
                    continue

                ecart = round(spots_obtenus - manque)
                combinaisons.append({
                    "jours_ajout":       jours_ajout,
                    "ecrans_ajout":      ecrans_ajout,
                    "frequence":         freq,
                    "frequence_label":   f"{freq // 60} min" if freq >= 60 else f"{freq}s",
                    "heures_necessaires": round(heures_arrondies, 2),
                    "tranche_label":     _format_duree_heures(heures_arrondies),
                    "tranche_val":       _tranche_val_depuis_duree(heures_arrondies),
                    "spots_obtenus":     round(spots_obtenus),
                    "ecart":             ecart,
                    "ecart_pct":         round((ecart / manque) * 100, 1),
                    "complexite":        jours_ajout + ecrans_ajout,
                    "description": (
                        f"Ajouter {jours_ajout} jour(s), {ecrans_ajout} écran(s) supplémentaire(s), "
                        f"fréquence de {freq // 60} min, sur une tranche horaire de "
                        f"{_format_duree_heures(heures_arrondies)}"
                    ),
                })

    # Priorité : combinaisons les plus proches du manque exact, puis les plus simples
    combinaisons.sort(key=lambda c: (abs(c["ecart"]), c["complexite"]))

    # On varie les propositions (une seule par couple jours/écrans ajoutés)
    vus, resultats = set(), []
    for c in combinaisons:
        cle = (c["jours_ajout"], c["ecrans_ajout"])
        if cle in vus:
            continue
        vus.add(cle)
        resultats.append(c)
        if len(resultats) >= max_resultats:
            break

    return resultats


def calculer_reduction_jours(
    frequence: int,
    heures_tranche: float,
    nb_ecrans: int,
    nombre_visuels: int,
    nb_jours_principal: int,
    objectif: int,
) -> dict | None:
    """
    Cas où les paramètres actuels DÉPASSENT l'objectif :
    calcule le nombre de jours principaux à conserver (au maximum, sans
    dépasser l'objectif) en gardant la même fréquence/tranche/écrans,
    et l'écart restant à combler par un petit ajustement.
    """
    spots_par_jour = _spots(frequence, heures_tranche, 1, nb_ecrans, nombre_visuels)
    if spots_par_jour <= 0:
        return None

    nb_jours_reduit = min(int(objectif // spots_par_jour), nb_jours_principal)
    nb_jours_reduit = max(nb_jours_reduit, 0)
    spots_avec_jours_reduits = round(spots_par_jour * nb_jours_reduit)
    manque_apres_reduction = max(objectif - spots_avec_jours_reduits, 0)

    cas_particulier = None
    if nb_jours_reduit == 0:
        cas_particulier = (
            f"Même réduit à 1 seul jour, la diffusion produit déjà {round(spots_par_jour)} spots, "
            f"ce qui dépasse l'objectif de {objectif} spots. "
            "Réduisez plutôt la tranche horaire, le nombre d'écrans, ou ralentissez la fréquence."
        )

    return {
        "spots_par_jour_principal":    round(spots_par_jour),
        "nb_jours_original":           nb_jours_principal,
        "nb_jours_reduit":             nb_jours_reduit,
        "jours_retires":               nb_jours_principal - nb_jours_reduit,
        "spots_avec_jours_reduits":    spots_avec_jours_reduits,
        "manque_apres_reduction":      manque_apres_reduction,
        "cas_particulier":             cas_particulier,
    }


def calculer_suggestions_objectif(
    objectif: int,
    total_obtenu: int,
    nb_jours: int,
    nb_ecrans: int,
) -> dict:
    """Retourne des suggestions simples pour atteindre un objectif de spots."""
    manque = max(objectif - total_obtenu, 0)
    if manque == 0:
        return {
            "manque": 0,
            "jours_ajout": 0,
            "ecrans_ajout": 0,
            "message": "Objectif atteint.",
        }

    spots_par_jour = max(total_obtenu // max(nb_jours, 1), 1)
    spots_par_ecran = max(total_obtenu // max(nb_ecrans, 1), 1)
    jours_ajout = max((manque + spots_par_jour - 1) // spots_par_jour, 1)
    ecrans_ajout = max((manque + spots_par_ecran - 1) // spots_par_ecran, 1)

    return {
        "manque": manque,
        "jours_ajout": jours_ajout,
        "ecrans_ajout": ecrans_ajout,
        "message": (
            f"Il manque {manque} spots. "
            f"Vous pouvez ajouter {jours_ajout} jour(s) de diffusion ou {ecrans_ajout} écran(s) supplémentaire(s)."
        ),
    }


def calculer_objectif_total(nb_spots_par_ecran: int, nb_ecrans: int) -> int:
    """Convertit un objectif par écran en objectif global pour les écrans choisis."""
    return max(nb_spots_par_ecran, 0) * max(nb_ecrans, 0)


def normaliser_objectif_spots(
    nb_ecrans: int,
    objectif_par_ecran: int | None = None,
    objectif_total: int | None = None,
) -> int | None:
    """Retourne l’objectif global sans multiplier une cible déjà totale."""
    if objectif_par_ecran is not None and objectif_total is not None:
        raise ValueError("Un seul type d’objectif peut être renseigné.")
    if objectif_total is not None:
        return objectif_total
    if objectif_par_ecran is not None:
        return calculer_objectif_total(objectif_par_ecran, nb_ecrans)
    return None


def generer_ajustements_meme_periode(
    objectif: int,
    nb_jours: int,
    nb_ecrans: int,
    nombre_visuels: int,
    frequence_actuelle: int | None = None,
    heures_actuelles: float | None = None,
    heure_debut: str = "06:00",
    tolerance: float = TOLERANCE,
    max_resultats: int = 8,
) -> list[dict]:
    """Propose une fréquence et une tranche adaptées sans changer jours ou écrans."""
    if objectif <= 0 or nb_jours <= 0 or nb_ecrans <= 0:
        return []

    heure_fin_max = datetime.strptime("23:00", "%H:%M")
    heure_debut_dt = datetime.strptime(heure_debut, "%H:%M")
    amplitude_max = (heure_fin_max - heure_debut_dt).total_seconds() / 3600

    combinaisons = []
    for frequence in FREQUENCES_DISPONIBLES:
        diviseur = (3600 / frequence) * nb_jours * nb_ecrans * max(nombre_visuels, 1)
        if diviseur <= 0:
            continue

        heures_exactes = objectif / diviseur
        if not 0.25 <= heures_exactes <= min(17.0, amplitude_max):
            continue

        heures = round(heures_exactes * 12) / 12
        spots = round(_spots(frequence, heures, nb_jours, nb_ecrans, nombre_visuels))
        ecart = spots - objectif
        ecart_pct = abs(ecart / objectif)
        if ecart_pct > tolerance:
            continue
        if frequence == frequence_actuelle and heures == heures_actuelles:
            continue

        combinaisons.append({
            "frequence": frequence,
            "frequence_label": f"{frequence // 60} min" if frequence >= 60 else f"{frequence}s",
            "tranche_val": _tranche_val_depuis_duree(heures, heure_debut),
            "tranche_label": _format_duree_heures(heures),
            "heures": heures,
            "spots_obtenus": spots,
            "ecart": ecart,
            "ecart_pct": round((ecart / objectif) * 100, 1),
        })

    combinaisons.sort(key=lambda item: (
        abs(item["ecart"]),
        abs(item["frequence"] - (frequence_actuelle or item["frequence"])),
        abs(item["heures"] - (heures_actuelles or item["heures"])),
    ))
    return combinaisons[:max_resultats]


def calculer_tranche_optimale_frequence_fixe(
    nb_spots_cible: int,
    frequence: int,
    nb_jours: int,
    nb_ecrans: int,
    duree_passage: int,
    nombre_visuels: int = 1,
) -> dict:
    """
    Cas où fréquence, nombre de jours et nombre d'écrans sont FIXÉS et seule
    la tranche horaire reste à déterminer pour atteindre nb_spots_cible.
    Un seul degré de liberté -> calcul direct d'une réponse précise,
    plutôt qu'un balayage de centaines de tranches approximatives.
    """
    diviseur = (3600 / frequence) * nb_jours * nb_ecrans * max(nombre_visuels, 1)
    if diviseur <= 0:
        return {}

    heures_exactes = nb_spots_cible / diviseur
    HEURE_MIN, HEURE_MAX = 0.25, 17.0  # amplitude max possible : 06:00 -> 23:00

    hors_plage = None
    heures_cible = heures_exactes
    if heures_exactes > HEURE_MAX:
        heures_cible = HEURE_MAX
        hors_plage = "trop_grand"
    elif heures_exactes < HEURE_MIN:
        heures_cible = HEURE_MIN
        hors_plage = "trop_petit"

    heures_arrondies = round(heures_cible * 12) / 12  # pas de 5 minutes
    spots_obtenus = _spots(frequence, heures_arrondies, nb_jours, nb_ecrans, nombre_visuels)
    ecart = round(spots_obtenus - nb_spots_cible)
    ecart_pct = round((ecart / nb_spots_cible) * 100, 2) if nb_spots_cible else 0

    # Quelques heures de départ plausibles pour la même durée (flexibilité de créneau)
    heure_fin_max = datetime.strptime("23:00", "%H:%M")
    variantes = []
    for depart in ["06:00", "08:00", "09:00", "12:00", "14:00", "18:00"]:
        depart_dt = datetime.strptime(depart, "%H:%M")
        fin_dt = depart_dt + timedelta(hours=heures_arrondies)
        if fin_dt > heure_fin_max:
            continue  # ce départ ferait déborder après 23:00 -> on l'exclut
        variantes.append({
            "depart": depart,
            "tranche_val": f"{depart_dt.strftime('%H:%M')}-{fin_dt.strftime('%H:%M')}",
        })

    total = round(spots_obtenus)
    total_heur = max(nb_jours, 1)
    part_de_voix = round((total * max(duree_passage, 1)) / (total_heur * 24 * 3600 * max(nb_ecrans, 1)) * 100, 1)

    return {
        "frequence":              frequence,
        "frequence_label":        f"toutes les {frequence}s ({frequence // 60} min)",
        "nb_jours":                nb_jours,
        "nb_ecrans":               nb_ecrans,
        "heures_necessaires_exactes": round(heures_exactes, 2),
        "heures_arrondies":        round(heures_arrondies, 2),
        "tranche_label":           _format_duree_heures(heures_arrondies),
        "spots_obtenus":           round(spots_obtenus),
        "nb_spots_cible":          nb_spots_cible,
        "ecart":                   ecart,
        "ecart_pct":               ecart_pct,
        "hors_plage":              hors_plage,
        "variantes_horaires":      variantes,
        "heures_antenne":          round((spots_obtenus * duree_passage) / 3600, 2),
        "spots_par_jour":          round(spots_obtenus / nb_jours),
        "spots_par_jour_ecran":    round(spots_obtenus / nb_jours / nb_ecrans),
        "spots_par_jour_total":    round(spots_obtenus / nb_jours),
        "spots_par_visuel":        round(spots_obtenus / max(nombre_visuels, 1)),
        "rotation_visuel_str":     f"{frequence // 60} min" if frequence >= 60 else f"{frequence}s",
        "temps_pub_par_heure_sec": round((3600 / max(frequence, 1)) * max(duree_passage, 1), 1) if frequence else 0,
        "part_de_voix_pct":       part_de_voix,
    }


def _generer_propositions(
    nb_spots_cible: int,
    nb_jours: int,
    nb_ecrans: int,
    duree_passage: int,
    frequence_fixe: int | None,
    tranches_fixe: str | None,
    nombre_visuels: int = 1,
    tranches_alternatives: list[str] | None = None,
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
    tranches_candidates = []
    if tranches_fixe:
        tranches_candidates.append(("", tranches_fixe, calculer_duree_tranches(tranches_fixe)))
    if tranches_alternatives:
        for part in tranches_alternatives:
            tranches_candidates.append(("", part, calculer_duree_tranches(part)))
    if not tranches_candidates:
        tranches_candidates = TRANCHES_TYPIQUES

    borne_min = nb_spots_cible * (1 - TOLERANCE)
    borne_max = nb_spots_cible * (1 + TOLERANCE)

    resultats = []
    for freq, (t_label, t_val, t_heures) in itertools.product(
        freq_candidates, tranches_candidates
    ):
        if t_heures <= 0:
            continue
        spots    = _spots(freq, t_heures, nb_jours, nb_ecrans, nombre_visuels=nombre_visuels)
        ecart    = ((spots - nb_spots_cible) / nb_spots_cible) * 100

        if borne_min <= spots <= borne_max:
            part_de_voix = round((spots * max(duree_passage, 1)) / (max(nb_jours, 1) * 24 * 3600 * max(nb_ecrans, 1)) * 100, 1)
            resultats.append({
                "frequence":            freq,
                "frequence_label":      f"toutes les {freq}s ({freq//60} min)",
                "tranches_label":       t_label or t_val,
                "tranches_val":         t_val,
                "heures_tranches":      t_heures,
                "spots_total":          round(spots),
                "spots_par_jour":       round(spots / nb_jours),
                "spots_par_jour_ecran": round(spots / nb_jours / nb_ecrans),
                "spots_par_jour_total": round(spots / nb_jours),
                "spots_par_visuel":     round(spots / max(nombre_visuels, 1)),
                "rotation_visuel_str":  f"{freq // 60} min" if freq >= 60 else f"{freq}s",
                "temps_pub_par_heure_sec": round((3600 / max(freq, 1)) * max(duree_passage, 1), 1) if freq else 0,
                "part_de_voix_pct":     part_de_voix,
                "ecart_pct":            round(ecart, 2),
                "abs_ecart":            abs(ecart),
                "heures_antenne":       round((spots * duree_passage) / 3600, 2),
                "nb_ecrans_utilises":   nb_ecrans,
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


def generer_scenarios_simulation(
    duree_passage: int,
    nombre_visuels: int,
    nb_jours: int | None,
    nb_ecrans_candidates: list[int],
    objectif_total: int | None = None,
    objectif_par_ecran: int | None = None,
    frequence_fixe: int | None = None,
    tranches_fixes: str | None = None,
    frequence_preferee: int | None = None,
    tranche_reference: str | None = None,
    tranches_alternatives: list[str] | None = None,
    max_resultats: int = MAX_PROPS,
) -> list[dict]:
    """Explore des choix finis et ne fait varier que les paramètres non fixés."""
    if not nb_jours and objectif_total is None and objectif_par_ecran is None:
        return []

    frequences = [frequence_fixe] if frequence_fixe else FREQUENCES_DISPONIBLES
    if tranches_fixes:
        tranches = [("Personnalisée", tranches_fixes)]
    elif tranches_alternatives:
        tranches = [("Créneau autorisé", tranche) for tranche in tranches_alternatives]
    else:
        tranches = list(TRANCHES_PREDEFINIES)
        if tranche_reference and all(valeur != tranche_reference for _, valeur in tranches):
            tranches.append(("Tranche actuelle", tranche_reference))

    heures_reference = calculer_duree_tranches(tranche_reference) if tranche_reference else None

    resultats = []
    for nb_ecrans in nb_ecrans_candidates:
        if nb_ecrans <= 0:
            continue
        cible = normaliser_objectif_spots(
            nb_ecrans=nb_ecrans,
            objectif_par_ecran=objectif_par_ecran,
            objectif_total=objectif_total,
        )
        for frequence in frequences:
            for tranche_label, tranche_val in tranches:
                try:
                    heures = calculer_duree_tranches(tranche_val)
                except (TypeError, ValueError):
                    continue
                if heures <= 0:
                    continue
                if nb_jours:
                    jours_candidates = [nb_jours]
                elif cible:
                    spots_par_jour = _spots(frequence, heures, 1, nb_ecrans, nombre_visuels)
                    jours_exact = cible / spots_par_jour
                    jours_candidates = sorted({max(1, math.floor(jours_exact)), max(1, math.ceil(jours_exact))})
                else:
                    continue

                for jours in jours_candidates:
                    spots = round(_spots(frequence, heures, jours, nb_ecrans, nombre_visuels))
                    ecart_pct = ((spots - cible) / cible) * 100 if cible else 0

                    resultats.append({
                        "frequence": frequence,
                        "frequence_label": f"toutes les {frequence // 60} min" if frequence >= 60 else f"toutes les {frequence}s",
                        "tranches_label": f"{tranche_label} — {tranche_val}",
                        "tranches_val": tranche_val,
                        "heures_tranches": heures,
                        "spots_total": spots,
                        "spots_total_par_ecran": round(spots / nb_ecrans),
                        "spots_par_jour": round(spots / jours),
                        "spots_par_jour_ecran": round(spots / jours / nb_ecrans),
                        "spots_par_jour_total": round(spots / jours),
                        "spots_par_visuel": round(spots / max(nombre_visuels, 1)),
                        "rotation_visuel_str": f"{frequence // 60} min" if frequence >= 60 else f"{frequence}s",
                        "temps_pub_par_heure_sec": round((3600 / max(frequence, 1)) * max(duree_passage, 1), 1),
                        "part_de_voix_pct": round((spots * duree_passage) / (jours * 24 * 3600 * nb_ecrans) * 100, 1),
                        "ecart_pct": round(ecart_pct, 2),
                        "abs_ecart": abs(ecart_pct),
                        "heures_antenne": round((spots * duree_passage) / 3600, 2),
                        "nb_ecrans_utilises": nb_ecrans,
                        "nb_ecrans": nb_ecrans,
                        "nb_jours": jours,
                        "objectif_total": cible,
                        "dans_tolerance": not cible or abs(ecart_pct) <= TOLERANCE * 100,
                    })

    if objectif_total is not None or objectif_par_ecran is not None:
        resultats.sort(key=lambda item: (
            item["abs_ecart"],
            item["nb_ecrans"],
            item["nb_jours"],
            abs(item["frequence"] - frequence_preferee) if frequence_preferee else 0,
            abs(item["heures_tranches"] - heures_reference) if heures_reference is not None else 0,
            -item["frequence"],
            item["heures_tranches"],
        ))
        dans_tolerance = [item for item in resultats if item["dans_tolerance"]]
        if dans_tolerance:
            return dans_tolerance[:max_resultats]
        return resultats[:min(max_resultats, 8)]

    resultats.sort(key=lambda item: (
        item["nb_ecrans"],
        -item["frequence"],
        item["heures_tranches"],
    ))
    par_nombre_ecrans = {}
    for scenario in resultats:
        groupe = par_nombre_ecrans.setdefault(scenario["nb_ecrans"], [])
        if len(groupe) < MAX_SCENARIOS_PAR_NB_ECRANS:
            groupe.append(scenario)
    return [scenario for groupe in par_nombre_ecrans.values() for scenario in groupe][:max_resultats]


def _construire_details_ecrans(
    ecrans_qs,
    frequence: int,
    heures: float,
    nb_jours: int,
    nb_ecrans_bloc: int,
    duree_passage: int,
    date_debut,
    nombre_visuels: int,
    bloc_label: str = "principal",
) -> list[dict]:
    """Construit les lignes de détail par écran pour un bloc (principal ou ajout)."""
    if nb_jours <= 0 or nb_ecrans_bloc <= 0:
        return []

    spots_total = _spots(frequence, heures, nb_jours, nb_ecrans_bloc, nombre_visuels=nombre_visuels)
    spots_par_jour_ecran = round(spots_total / nb_jours / max(nb_ecrans_bloc, 1))

    details = []
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

        details.append({
            "support":          support,
            "taux_actuel":      taux_actuel,
            "taux_apres":       taux_apres,
            "peut_accueillir":  peut,
            "message_dispo":    msg,
            "spots_par_jour":   spots_par_jour_ecran,
            "spots_total":      round(spots_total / max(nb_ecrans_bloc, 1)),
            "bloc":             bloc_label,
        })
    return details


def _enrich_resultat_metriques(
    resultats: dict,
    nb_jours: int,
    nb_ecrans: int,
    nombre_visuels: int,
    duree_passage: int,
    frequence: int,
    nb_spots_cible: int | None = None,
) -> dict:
    """Ajoute les métriques attendues par le template du simulateur."""
    total = int(resultats.get("spots_total_tous", 0) or 0)
    if total == 0 and nb_ecrans > 0:
        total = int(resultats.get("spots_total_ecran", 0) or 0) * max(nb_ecrans, 1)

    resultats["spots_par_jour_total"] = round(total / max(nb_jours, 1))
    resultats["spots_par_visuel"] = round(total / max(nombre_visuels, 1))
    resultats["rotation_visuel_str"] = f"{frequence // 60} min" if frequence >= 60 else f"{frequence}s"
    resultats["temps_pub_par_heure_sec"] = round((3600 / max(frequence, 1)) * max(duree_passage, 1), 1) if frequence else 0

    if nb_jours and nb_ecrans:
        window_seconds = max(nb_jours * 24 * 3600 * max(nb_ecrans, 1), 1)
        resultats["part_de_voix_pct"] = round((total * max(duree_passage, 1)) / window_seconds * 100, 1)
    else:
        resultats["part_de_voix_pct"] = 0

    if nb_spots_cible:
        resultats["pct_realisation_cible"] = round(min(100.0, (total / max(nb_spots_cible, 1)) * 100), 1)
    else:
        resultats["pct_realisation_cible"] = 100 if total else 0

    resultats["gain_spots"] = int(resultats.get("spots_ajout", 0) or 0)
    if resultats["gain_spots"] and total:
        resultats["gain_pct"] = round((resultats["gain_spots"] / max(total - resultats["gain_spots"], 1)) * 100, 1)
    else:
        resultats["gain_pct"] = 0

    resultats["taux_moyen_actuel"] = resultats.get("taux_moyen_actuel")
    resultats["taux_moyen_apres"] = resultats.get("taux_moyen_apres")
    return resultats


def _calcul_direct(
    frequence: int,
    tranches_horaires: str,
    nb_jours: int,
    nb_ecrans: int,
    duree_passage: int,
    ecrans_qs,
    date_debut=None,
    nombre_visuels: int = 1,
) -> dict:
    """Calcul direct quand fréquence ET tranches sont fournis."""
    heures       = calculer_duree_tranches(tranches_horaires)
    spots_total  = _spots(frequence, heures, nb_jours, nb_ecrans, nombre_visuels=nombre_visuels)
    spots_par_jour_ecran = round(spots_total / nb_jours / max(nb_ecrans, 1))

    details_ecrans = _construire_details_ecrans(
        ecrans_qs, frequence, heures, nb_jours, nb_ecrans,
        duree_passage, date_debut, nombre_visuels, bloc_label="principal",
    )

    resultats = {
        "mode":                  "direct",
        "frequence":             frequence,
        "frequence_label":       f"toutes les {frequence}s ({frequence//60} min)",
        "tranches_val":          tranches_horaires,
        "heures_tranches":       round(heures, 2),
        "nb_jours":              nb_jours,
        "nb_ecrans":             nb_ecrans,
        "duree_passage":         duree_passage,
        "spots_par_heure":       round(3600 / frequence, 2),
        "spots_par_jour_ecran":  spots_par_jour_ecran,
        "spots_total_ecran":     round(spots_total / max(nb_ecrans, 1)),
        "spots_total_tous":      round(spots_total),
        "heures_antenne":        round((spots_total * duree_passage) / 3600, 2),
        "timeline_hours":        _construire_timeline_heures(tranches_horaires),
        "details_ecrans":        details_ecrans,
        "nombre_visuels":        max(nombre_visuels, 1),
    }
    return _enrich_resultat_metriques(
        resultats,
        nb_jours=nb_jours,
        nb_ecrans=nb_ecrans,
        nombre_visuels=max(nombre_visuels, 1),
        duree_passage=duree_passage,
        frequence=frequence,
    )


def _supports_ecran_disponibles(date_debut, date_fin) -> list[Support]:
    supports = Support.objects.filter(
        type_support="ecran",
        actif=True,
        etat=Support.ETAT_BON,
    ).order_by("code")
    if date_debut and date_fin:
        supports_engages = LigneCampagne.objects.filter(
            support__type_support="ecran",
            campagne__statut__in=["en_cours", "a_venir"],
            campagne__date_debut__lte=date_fin,
            campagne__date_fin__gte=date_debut,
        ).values_list("support_id", flat=True)
        supports = supports.exclude(pk__in=supports_engages)
    return list(supports)


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
        date_debut_saisie = data.get("date_debut")
        date_fin_saisie = data.get("date_fin")
        date_debut     = date_debut_saisie or timezone.localdate()
        date_fin       = data.get("date_fin") or (
            date_debut + timedelta(days=nb_jours - 1) if nb_jours else None
        )
        frequence      = int(data["frequence"]) if data.get("frequence") else None
        # tranches en h min secondes
        tranches       = data.get("tranches_horaires") or None
        
        nb_spots_cible_par_ecran = data.get("nb_spots_cible")
        nb_spots_cible_total = data.get("nb_spots_cible_total")
        scenario_applique = self.request.GET.get("scenario_applique") == "1"
        objectif_et_periode_fixes = bool(
            nb_jours
            and (nb_spots_cible_par_ecran is not None or nb_spots_cible_total is not None)
        ) and not scenario_applique
        nombre_visuels = int(data.get("nombre_visuels") or 1)
        tranches_alternatives = data.get("tranches_horaires_alternatives_list") or []
        jours_ajout = int(data.get("jours_ajout") or 0)
        ecrans_selectionnes = list(data.get("ecrans") or [])
        nb_ecrans_cible = data.get("nb_ecrans_cible")
        ecrans_fixes = bool(ecrans_selectionnes or nb_ecrans_cible)
        ecrans_disponibles = _supports_ecran_disponibles(date_debut, date_fin or date_debut)
        if ecrans_selectionnes:
            nb_ecrans = len(ecrans_selectionnes)
            ecrans_qs = Support.objects.filter(pk__in=[support.pk for support in ecrans_selectionnes]).order_by("code")
            nb_ecrans_candidates = [nb_ecrans]
        elif nb_ecrans_cible:
            nb_ecrans = nb_ecrans_cible
            nb_ecrans_candidates = [nb_ecrans]
            ecrans_qs = Support.objects.filter(
                pk__in=[support.pk for support in ecrans_disponibles[:nb_ecrans]]
            ).order_by("code")
        else:
            nb_ecrans = 0
            nb_ecrans_candidates = list(range(1, len(ecrans_disponibles) + 1))
            ecrans_qs = Support.objects.none()
        nb_spots_cible = normaliser_objectif_spots(
            nb_ecrans=nb_ecrans,
            objectif_par_ecran=nb_spots_cible_par_ecran,
            objectif_total=nb_spots_cible_total,
        ) if ecrans_fixes else nb_spots_cible_total
        target_query = urlencode(
            {"nb_spots_cible": nb_spots_cible_par_ecran}
            if nb_spots_cible_par_ecran is not None
            else {"nb_spots_cible_total": nb_spots_cible_total}
            if nb_spots_cible_total is not None
            else {}
        )
        screen_query = urlencode([("ecrans", support_id) for support_id in ecrans_qs.values_list("pk", flat=True)])
        frequence_ajout = int(data.get("frequence_jours_ajout") or data.get("frequence") or 0)
        tranches_ajout = data.get("tranches_horaires_jours_ajout") or tranches
        ecrans_ajout_qs = data.get("ecrans_jours_ajout") or ecrans_qs
        nb_ecrans_ajout = ecrans_ajout_qs.count() if hasattr(ecrans_ajout_qs, "count") else 1

        if not ecrans_disponibles and not ecrans_selectionnes:
            context["erreur"] = "Aucun écran actif et disponible ne peut être utilisé pour cette recherche."
            return context
        if nb_ecrans_cible and nb_ecrans_cible > len(ecrans_disponibles) and not ecrans_selectionnes:
            context["erreur"] = f"Seulement {len(ecrans_disponibles)} écran(s) sont disponibles pour cette période."
            return context

        # ── Mode A : calcul direct ────────────────────────────────────────────
        if nb_jours and ecrans_fixes and frequence and tranches and not objectif_et_periode_fixes:
            resultats = _calcul_direct(
                frequence, tranches, nb_jours, nb_ecrans,
                duree_passage, ecrans_qs, date_debut,
                nombre_visuels=nombre_visuels,
            )
            if jours_ajout > 0 and frequence_ajout and tranches_ajout:
                heures_ajout = calculer_duree_tranches(tranches_ajout)
                if heures_ajout > 0:
                    ecrans_total = set(ecrans_qs.values_list("pk", flat=True))
                    ecrans_total.update(ecrans_ajout_qs.values_list("pk", flat=True))
                    nb_ecrans_total = len(ecrans_total)
                    ajout = _spots(
                        frequence_ajout,
                        heures_ajout,
                        jours_ajout,
                        nb_ecrans_ajout,
                        nombre_visuels=nombre_visuels,
                    )
                    resultats["spots_total_tous"] = round(resultats["spots_total_tous"] + ajout)
                    resultats["spots_total_ecran"] = round(resultats["spots_total_tous"] / max(nb_ecrans_total, 1))
                    resultats["jours_ajout"] = jours_ajout
                    resultats["frequence_ajout"] = frequence_ajout
                    resultats["tranches_ajout"] = tranches_ajout
                    resultats["ecrans_ajout"] = nb_ecrans_ajout
                    resultats["spots_ajout"] = round(ajout)
                    resultats["spots_principal"] = round(resultats["spots_total_tous"] - ajout)
                    resultats["nb_ecrans_principal"] = nb_ecrans
                    resultats["nb_ecrans_ajout"] = nb_ecrans_ajout
                    resultats["nb_ecrans_total"] = nb_ecrans_total

                    details_ajout = _construire_details_ecrans(
                        ecrans_ajout_qs, frequence_ajout, heures_ajout, jours_ajout,
                        nb_ecrans_ajout, duree_passage, date_debut, nombre_visuels,
                        bloc_label="ajout",
                    )
                    resultats["details_ecrans"] = resultats["details_ecrans"] + details_ajout
            if nb_spots_cible is not None:
                resultats = _enrich_resultat_metriques(
                    resultats,
                    nb_jours=nb_jours,
                    nb_ecrans=nb_ecrans,
                    nombre_visuels=nombre_visuels,
                    duree_passage=duree_passage,
                    frequence=frequence,
                    nb_spots_cible=int(nb_spots_cible),
                )
                total_actuel = int(resultats["spots_total_tous"])
                cible = int(nb_spots_cible)
                ecart_objectif = cible - total_actuel  # positif = manque, négatif = dépassement

                resultats["nb_spots_cible"] = cible
                resultats["nb_spots_cible_par_ecran"] = nb_spots_cible_par_ecran
                resultats["nb_spots_cible_total_saisi"] = nb_spots_cible_total
                resultats["ecart_objectif"] = ecart_objectif

                if ecart_objectif > 0:
                    resultats["manque"] = ecart_objectif
                elif ecart_objectif < 0:
                    resultats["depassement"] = -ecart_objectif
                else:
                    resultats["manque"] = 0

            creation_params = {
                "nom": "Campagne écran simulée",
                "type_support": "ecran",
                "duree_passage": duree_passage,
                "frequence": frequence,
                "nombre_visuels": nombre_visuels,
                "tranches_horaires": tranches,
                "supports": ",".join(
                    str(support_id) for support_id in ecrans_qs.values_list("pk", flat=True)
                ),
            }
            if jours_ajout > 0 and frequence_ajout and tranches_ajout:
                creation_params.update({
                    "jours_ajout": jours_ajout,
                    "frequence_ajout": frequence_ajout,
                    "tranches_ajout": tranches_ajout,
                    "supports_bloc2": ",".join(
                        str(support_id) for support_id in ecrans_ajout_qs.values_list("pk", flat=True)
                    ),
                })
            if date_debut:
                creation_params["date_debut"] = date_debut.isoformat()
            if data.get("date_fin"):
                creation_params["date_fin"] = data["date_fin"].isoformat()
                if jours_ajout > 0:
                    creation_params["date_fin_principal"] = data["date_fin"].isoformat()
                    creation_params["date_fin"] = (
                        data["date_fin"] + timedelta(days=jours_ajout)
                    ).isoformat()
            resultats["url_creer_campagne"] = (
                f"{reverse('campagne_create')}?{urlencode(creation_params)}"
            )
            context["resultats"] = resultats

        elif nb_jours or nb_spots_cible_par_ecran is not None or nb_spots_cible_total is not None:
            propositions = generer_scenarios_simulation(
                duree_passage=duree_passage,
                nombre_visuels=nombre_visuels,
                nb_jours=nb_jours,
                nb_ecrans_candidates=nb_ecrans_candidates,
                objectif_total=nb_spots_cible_total,
                objectif_par_ecran=nb_spots_cible_par_ecran,
                frequence_fixe=None if objectif_et_periode_fixes else frequence,
                tranches_fixes=None if objectif_et_periode_fixes else tranches,
                frequence_preferee=frequence if objectif_et_periode_fixes else None,
                tranche_reference=tranches if objectif_et_periode_fixes else None,
                tranches_alternatives=tranches_alternatives,
                max_resultats=50,
            )

            disponibles_par_date = {}
            scenarios_valides = []
            for proposition in propositions:
                if date_debut_saisie:
                    debut_scenario = date_debut_saisie
                    fin_scenario = debut_scenario + timedelta(days=proposition["nb_jours"] - 1)
                elif date_fin_saisie:
                    fin_scenario = date_fin_saisie
                    debut_scenario = fin_scenario - timedelta(days=proposition["nb_jours"] - 1)
                else:
                    debut_scenario = date_debut
                    fin_scenario = debut_scenario + timedelta(days=proposition["nb_jours"] - 1)
                if ecrans_selectionnes:
                    supports_scenario = ecrans_selectionnes
                else:
                    if fin_scenario not in disponibles_par_date:
                        disponibles_par_date[fin_scenario] = _supports_ecran_disponibles(
                            debut_scenario, fin_scenario
                        )
                    supports_scenario = disponibles_par_date[fin_scenario]
                if len(supports_scenario) < proposition["nb_ecrans"]:
                    continue

                supports_retenus = supports_scenario[:proposition["nb_ecrans"]]
                support_query = urlencode([("ecrans", support.pk) for support in supports_retenus])
                proposition["support_query"] = support_query
                proposition["supports_label"] = ", ".join(support.code for support in supports_retenus)
                proposition["date_debut"] = debut_scenario.isoformat()
                proposition["date_fin"] = fin_scenario.isoformat()
                proposition["query"] = urlencode({
                    "duree_passage": duree_passage,
                    "frequence": proposition["frequence"],
                    "tranches_horaires": proposition["tranches_val"],
                    "nb_jours": proposition["nb_jours"],
                    "date_debut": proposition["date_debut"],
                })
                if target_query:
                    proposition["query"] += f"&{target_query}"
                if support_query:
                    proposition["query"] += f"&{support_query}"
                proposition["query"] += "&scenario_applique=1"
                scenarios_valides.append(proposition)

            if not scenarios_valides:
                context["erreur"] = (
                    "Aucune combinaison compatible avec les paramètres et les écrans disponibles n’a été trouvée. "
                    "La cible peut être impossible à atteindre avec les fréquences et tranches horaires autorisées."
                )
            elif nb_spots_cible_par_ecran is not None or nb_spots_cible_total is not None:
                configuration_actuelle = None
                if objectif_et_periode_fixes and ecrans_fixes and frequence and tranches:
                    total_actuel = round(_spots(
                        frequence,
                        calculer_duree_tranches(tranches),
                        nb_jours,
                        nb_ecrans,
                        nombre_visuels,
                    ))
                    configuration_dans_propositions = any(
                        proposition["frequence"] == frequence
                        and proposition["tranches_val"] == tranches
                        and proposition["nb_jours"] == nb_jours
                        and proposition["nb_ecrans"] == nb_ecrans
                        for proposition in scenarios_valides
                    )
                    query_actuelle = urlencode({
                        "duree_passage": duree_passage,
                        "frequence": frequence,
                        "tranches_horaires": tranches,
                        "nb_jours": nb_jours,
                        "date_debut": date_debut.isoformat(),
                        "scenario_applique": "1",
                    })
                    if target_query:
                        query_actuelle += f"&{target_query}"
                    if screen_query:
                        query_actuelle += f"&{screen_query}"
                    if not configuration_dans_propositions:
                        configuration_actuelle = {
                            "frequence_label": f"toutes les {frequence // 60} min" if frequence >= 60 else f"toutes les {frequence}s",
                            "tranches": tranches,
                            "nb_jours": nb_jours,
                            "nb_ecrans": nb_ecrans,
                            "spots_total": total_actuel,
                            "ecart_pct": round((total_actuel - nb_spots_cible) / nb_spots_cible * 100, 2),
                            "supports_label": ", ".join(ecrans_qs.values_list("code", flat=True)),
                            "query": query_actuelle,
                        }
                context.update({
                    "propositions": scenarios_valides,
                    "configuration_actuelle": configuration_actuelle,
                    "nb_spots_cible": nb_spots_cible,
                    "nb_spots_cible_par_ecran": nb_spots_cible_par_ecran,
                    "nb_spots_cible_total_saisi": nb_spots_cible_total,
                    "nb_jours": nb_jours,
                    "nb_ecrans": nb_ecrans,
                    "duree_passage": duree_passage,
                    "target_query": target_query,
                })
            else:
                context["exploration_scenarios"] = scenarios_valides
                context["nb_jours"] = nb_jours

        else:
            context["erreur"] = (
                "Renseignez une période ou un objectif de spots pour définir la recherche."
            )

        return context

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self.get_context_data())