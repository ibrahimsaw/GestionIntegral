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
        help_text="Sélectionnez uniquement les écrans à utiliser. Laisser vide = tous les écrans actifs.",
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
        max_ecrans_ajout = max(ecrans_disponibles, 5)

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
        "nombre_visuels":        max(nombre_visuels, 1),
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
        nombre_visuels = int(data.get("nombre_visuels") or 1)
        tranches_alternatives = data.get("tranches_horaires_alternatives_list") or []
        jours_ajout = int(data.get("jours_ajout") or 0)
        ecrans_qs = data.get("ecrans") or Support.objects.filter(type_support="ecran", actif=True)
        nb_ecrans = ecrans_qs.count()
        frequence_ajout = int(data.get("frequence_jours_ajout") or data.get("frequence") or 0)
        tranches_ajout = data.get("tranches_horaires_jours_ajout") or tranches
        ecrans_ajout_qs = data.get("ecrans_jours_ajout") or ecrans_qs
        nb_ecrans_ajout = ecrans_ajout_qs.count() if hasattr(ecrans_ajout_qs, "count") else 1
        print(f"DEBUG: nb_ecrans={nb_ecrans}, nb_ecrans_ajout={nb_ecrans_ajout}, frequence_ajout={frequence_ajout}, tranches_ajout={tranches_ajout}")

        if nb_ecrans == 0:
            context["erreur"] = "Aucun écran actif trouvé."
            return context

        # ── Mode A : calcul direct ────────────────────────────────────────────
        if frequence and tranches:
            resultats = _calcul_direct(
                frequence, tranches, nb_jours, nb_ecrans,
                duree_passage, ecrans_qs, date_debut,
                nombre_visuels=nombre_visuels,
            )
            if jours_ajout > 0 and frequence_ajout and tranches_ajout:
                heures_ajout = calculer_duree_tranches(tranches_ajout)
                if heures_ajout > 0:
                    ajout = _spots(
                        frequence_ajout,
                        heures_ajout,
                        jours_ajout,
                        nb_ecrans_ajout,
                        nombre_visuels=nombre_visuels,
                    )
                    resultats["spots_total_tous"] = round(resultats["spots_total_tous"] + ajout)
                    resultats["spots_total_ecran"] = round(resultats["spots_total_tous"] / max(nb_ecrans + nb_ecrans_ajout, 1))
                    resultats["jours_ajout"] = jours_ajout
                    resultats["frequence_ajout"] = frequence_ajout
                    resultats["tranches_ajout"] = tranches_ajout
                    resultats["ecrans_ajout"] = nb_ecrans_ajout
                    resultats["spots_ajout"] = round(ajout)
                    resultats["spots_principal"] = round(resultats["spots_total_tous"] - ajout)
                    resultats["nb_ecrans_principal"] = nb_ecrans
                    resultats["nb_ecrans_ajout"] = nb_ecrans_ajout
                    resultats["nb_ecrans_total"] = nb_ecrans + nb_ecrans_ajout

                    details_ajout = _construire_details_ecrans(
                        ecrans_ajout_qs, frequence_ajout, heures_ajout, jours_ajout,
                        nb_ecrans_ajout, duree_passage, date_debut, nombre_visuels,
                        bloc_label="ajout",
                    )
                    resultats["details_ecrans"] = resultats["details_ecrans"] + details_ajout
            if nb_spots_cible is not None:
                total_actuel = int(resultats["spots_total_tous"])
                cible = int(nb_spots_cible)
                ecart_objectif = cible - total_actuel  # positif = manque, négatif = dépassement

                resultats["nb_spots_cible"] = cible
                resultats["ecart_objectif"] = ecart_objectif

                if ecart_objectif > 0:
                    # ── Cas 1 : il manque des spots -> combinaisons à AJOUTER ──
                    manque = ecart_objectif
                    resultats["manque"] = manque
                    resultats["suggestions"] = calculer_suggestions_objectif(
                        objectif=cible, total_obtenu=total_actuel,
                        nb_jours=nb_jours, nb_ecrans=nb_ecrans,
                    )
                    resultats["combinaisons_complement"] = generer_combinaisons_complement(
                        manque=manque,
                        nombre_visuels=nombre_visuels,
                        ecrans_disponibles=nb_ecrans,
                    )
                    if not resultats["combinaisons_complement"]:
                        resultats["combinaisons_complement_erreur"] = (
                            f"Aucune combinaison simple (≤4 jours ajoutés, ≤{max(nb_ecrans, 5)} écrans ajoutés) "
                            f"ne permet d'approcher les {manque} spots manquants à ±8 % près. "
                            "Essayez une fréquence plus rapide ou davantage d'écrans."
                        )

                elif ecart_objectif < 0:
                    # ── Cas 2 : dépassement -> réduire les jours principaux, puis ajuster ──
                    depassement = -ecart_objectif
                    resultats["depassement"] = depassement

                    heures_tranche_actuelle = calculer_duree_tranches(tranches)

                    reduction = calculer_reduction_jours(
                        frequence=frequence,
                        heures_tranche=heures_tranche_actuelle,
                        nb_ecrans=nb_ecrans,
                        nombre_visuels=nombre_visuels,
                        nb_jours_principal=nb_jours,
                        objectif=cible,
                    )
                    if reduction:
                        if date_debut and reduction["nb_jours_reduit"] > 0:
                            reduction["date_debut"] = date_debut
                            reduction["date_fin_reduite"] = date_debut + timedelta(
                                days=reduction["nb_jours_reduit"] - 1
                            )
                        resultats["reduction_jours"] = reduction

                        manque_ajustement = reduction["manque_apres_reduction"]
                        if manque_ajustement > 0:
                            combos_ajust = generer_combinaisons_complement(
                                manque=manque_ajustement,
                                nombre_visuels=nombre_visuels,
                                ecrans_disponibles=nb_ecrans,
                            )
                            for combo in combos_ajust:
                                combo["total_final"] = reduction["spots_avec_jours_reduits"] + combo["spots_obtenus"]
                                combo["ecart_final"] = combo["total_final"] - cible
                            resultats["combinaisons_ajustement"] = combos_ajust
                            if not combos_ajust:
                                resultats["combinaisons_ajustement_erreur"] = (
                                    f"Aucun petit ajustement simple ne permet d'approcher précisément "
                                    f"les {manque_ajustement} spots restants après réduction des jours."
                                )
                        else:
                            resultats["combinaisons_ajustement"] = []
                else:
                    resultats["manque"] = 0
            context["resultats"] = resultats

        # ── Mode B : propositions vers cible ─────────────────────────────────
        elif nb_spots_cible:
            if frequence and not tranches and not tranches_alternatives:
                # ── Mode B précis : fréquence + jours + écrans fixés -> 1 seule inconnue (la tranche) ──
                tranche_optimale = calculer_tranche_optimale_frequence_fixe(
                    nb_spots_cible = nb_spots_cible,
                    frequence      = frequence,
                    nb_jours       = nb_jours,
                    nb_ecrans      = nb_ecrans,
                    duree_passage  = duree_passage,
                    nombre_visuels = nombre_visuels,
                )
                if not tranche_optimale:
                    context["erreur"] = "Impossible de calculer une tranche horaire avec ces paramètres."
                else:
                    context.update({
                        "tranche_optimale": tranche_optimale,
                        "nb_spots_cible":   nb_spots_cible,
                        "nb_jours":         nb_jours,
                        "nb_ecrans":        nb_ecrans,
                        "duree_passage":    duree_passage,
                    })
            else:
                # ── Mode B ouvert : fréquence ET tranches libres -> scan large ──
                props = _generer_propositions(
                    nb_spots_cible = nb_spots_cible,
                    nb_jours       = nb_jours,
                    nb_ecrans      = nb_ecrans,
                    duree_passage  = duree_passage,
                    frequence_fixe = frequence,
                    tranches_fixe  = tranches,
                    nombre_visuels = nombre_visuels,
                    tranches_alternatives = tranches_alternatives,
                )
                if not props:
                    context["erreur"] = (
                        f"Aucune combinaison trouvée à ±5 % de {nb_spots_cible:,} spots "
                        f"pour {nb_jours} jours / {nb_ecrans} écran(s). "
                        "Essayez de modifier la durée du spot, le nombre d'écrans, la période ou le nombre de visuels."
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