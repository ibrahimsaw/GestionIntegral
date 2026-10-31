"""
SimulationCampagneEcranView — v3
================================
Le calcul est délégué à `campaigns/simulation_engine.py` (Python pur, testé).
Cette vue ne fait que : valider le formulaire, résoudre écrans/dates, appeler le
moteur, puis préparer le contexte du template.

Deux modes :
  A) Paramètres libres  -> calcul direct (1 ou 2 blocs)
  B) Objectif de spots  -> scénarios (fréquence x tranche x jours x écrans) à ±5 %

Champ nb_jours co-existe avec date_debut/date_fin :
  - nb_jours seul          -> simulation pure (date de début = aujourd'hui)
  - date_debut + nb_jours  -> date_fin calculée automatiquement
  - date_debut + date_fin  -> nb_jours calculé automatiquement
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import FormView

from inventory.models import Support
from campaigns.models import DUREE_CHOICES, FREQUENCE_CHOICES, LigneCampagne
from campaigns import simulation_engine as eng
from campaigns.simulation_engine import Bloc, TrancheError

# Fréquences disponibles (secondes) — multiples de 60 uniquement
FREQUENCES_DISPONIBLES: list[int] = sorted({f for f, _ in FREQUENCE_CHOICES if f % 60 == 0})
TOLERANCE = eng.TOLERANCE


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
        label="Date de début", required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    date_fin = forms.DateField(
        label="Date de fin", required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    nb_jours = forms.IntegerField(
        label="Nombre de jours", required=False, min_value=1, max_value=eng.MAX_JOURS,
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
        label="Tranches horaires principales", required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex : 08:00-12:00,14:00-18:00"}),
        help_text="Tranche principale à utiliser. Laissez vide pour laisser la simulation proposer des tranches.",
    )

    tranches_horaires_alternatives = forms.CharField(
        label="Créneaux à tester (mode Propositions)", required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex : 08:00-10:00,16:00-20:00"}),
        help_text=(
            "Actif UNIQUEMENT si « Tranches horaires principales » est vide et qu'un objectif "
            "de spots est renseigné. Chaque plage séparée par une virgule est testée séparément."
        ),
    )

    jours_ajout = forms.IntegerField(
        label="Jours supplémentaires à ajouter", required=False, initial=0,
        min_value=0, max_value=eng.MAX_JOURS,
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
        label="Tranches horaires pour les jours ajoutés", required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Ex : 08:00-12:00"}),
        help_text="Tranche spécifique aux jours supplémentaires.",
    )
    ecrans_jours_ajout = forms.ModelMultipleChoiceField(
        label="Écrans pour les jours ajoutés",
        queryset=Support.objects.filter(type_support="ecran", actif=True).order_by("code"),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "list-unstyled"}),
        help_text="Sélectionnez les écrans à utiliser uniquement pour les jours ajoutés.",
    )

    heure_debut_diffusion = forms.TimeField(
        label="Heure de début de diffusion", required=False,
        widget=forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
        help_text="Ex : 08:00",
    )
    duree_diffusion_heures = forms.DecimalField(
        label="Durée de diffusion (heures)", required=False,
        min_value=0.25, max_digits=4, decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.25", "placeholder": "Ex : 4 ou 4.5"}),
        help_text="Nombre d'heures à partir de l'heure de début (ex : 4.5 = 4h30).",
    )

    nombre_visuels = forms.IntegerField(
        label="Nombre de visuels", required=False, initial=1, min_value=1, max_value=20,
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
        label="Nombre d’écrans", required=False, min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1, "placeholder": "Automatique"}),
        help_text="Laissez vide pour comparer plusieurs nombres d’écrans disponibles.",
    )

    nb_spots_cible = forms.IntegerField(
        label="Objectif de spots par écran", required=False, min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ex : 4000"}),
        help_text="À utiliser à la place de l’objectif total.",
    )
    nb_spots_cible_total = forms.IntegerField(
        label="Objectif total de spots", required=False, min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Ex : 4500"}),
        help_text="Nombre global pour tous les écrans sélectionnés.",
    )

    prix_spot = forms.DecimalField(
        label="Prix unitaire par spot (optionnel)", required=False,
        min_value=0, max_digits=12, decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Ex : 150"}),
        help_text="Sert uniquement à estimer le budget (spots x prix), comme Campagne.montant_total().",
    )

    # ── Validation ───────────────────────────────────────────────────────────
    def _normaliser_tranche(self, champ: str, valeur: str | None):
        """Valide + fusionne une tranche. Retourne la chaîne normalisée, ou None en cas d'erreur."""
        if not valeur or not valeur.strip():
            return None
        try:
            plages = eng.parser_tranches(valeur)
        except TrancheError as exc:
            self.add_error(champ, str(exc))
            return None
        if not plages:
            self.add_error(champ, "Format invalide — ex : 08:00-12:00,14:00-18:00")
            return None
        return eng.formater_tranches(plages)

    def clean(self):
        data = super().clean()

        if data.get("nb_spots_cible") is not None and data.get("nb_spots_cible_total") is not None:
            self.add_error("nb_spots_cible_total", "Renseignez l’objectif par écran ou l’objectif total, pas les deux.")
        if data.get("ecrans") and data.get("nb_ecrans_cible") is not None:
            self.add_error("nb_ecrans_cible", "Cochez des écrans ou saisissez un nombre, pas les deux.")

        d_debut, d_fin, nb_jours = data.get("date_debut"), data.get("date_fin"), data.get("nb_jours")
        has_target = data.get("nb_spots_cible") is not None or data.get("nb_spots_cible_total") is not None
        jours_principal = int(nb_jours or 0)

        # ── Période principale ──
        if d_debut and d_fin:
            if d_fin < d_debut:
                raise forms.ValidationError("La date de fin doit être après la date de début.")
            data["nb_jours"] = (d_fin - d_debut).days + 1
            if data["nb_jours"] > eng.MAX_JOURS:
                raise forms.ValidationError(f"La période ne peut pas dépasser {eng.MAX_JOURS} jours.")
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

        # ── Heure de début + durée -> tranche (en minutes entières) ──
        heure_debut = data.get("heure_debut_diffusion")
        duree_h = data.get("duree_diffusion_heures")
        tranches = (data.get("tranches_horaires") or "").strip()

        if heure_debut and duree_h and not tranches:
            debut_min = heure_debut.hour * 60 + heure_debut.minute
            fin_min = debut_min + int(round(float(duree_h) * 60))
            if debut_min < eng.OUVERTURE_MIN:
                self.add_error("heure_debut_diffusion",
                               f"La diffusion commence à {eng.minutes_vers_hhmm(eng.OUVERTURE_MIN)} au plus tôt.")
            elif fin_min > eng.FERMETURE_MIN:
                self.add_error(
                    "duree_diffusion_heures",
                    f"La diffusion dépasserait {eng.minutes_vers_hhmm(eng.FERMETURE_MIN)} "
                    f"(fin calculée : {eng.minutes_vers_hhmm(fin_min)}). Réduisez la durée ou avancez l'heure de début.",
                )
            else:
                tranches = f"{eng.minutes_vers_hhmm(debut_min)}-{eng.minutes_vers_hhmm(fin_min)}"
                data["tranches_horaires"] = tranches
        elif (heure_debut and not duree_h) or (duree_h and not heure_debut):
            self.add_error(
                "duree_diffusion_heures" if heure_debut else "heure_debut_diffusion",
                "Renseignez à la fois l'heure de début ET la durée pour générer une tranche.",
            )

        if data.get("frequence_jours_ajout") == "":
            data["frequence_jours_ajout"] = data.get("frequence")

        # ── Normalisation / fusion des tranches ──
        if tranches:
            norm = self._normaliser_tranche("tranches_horaires", tranches)
            if norm:
                data["tranches_horaires"] = norm
        if data.get("tranches_horaires_jours_ajout"):
            norm = self._normaliser_tranche("tranches_horaires_jours_ajout", data["tranches_horaires_jours_ajout"])
            if norm:
                data["tranches_horaires_jours_ajout"] = norm

        alternatives = data.get("tranches_horaires_alternatives") or ""
        liste_alt: list[str] = []
        if alternatives.strip():
            for morceau in alternatives.replace("\n", ",").replace(";", ",").split(","):
                if not morceau.strip():
                    continue
                norm = self._normaliser_tranche("tranches_horaires_alternatives", morceau)
                if norm is None:
                    liste_alt = []
                    break
                if norm not in liste_alt:
                    liste_alt.append(norm)
        data["tranches_horaires_alternatives_list"] = liste_alt

        # ── Contrainte physique de la boucle : visuels x durée <= fréquence ──
        duree = int(data["duree_passage"]) if data.get("duree_passage") else None
        visuels = int(data.get("nombre_visuels") or 1)
        jours_ajout = int(data.get("jours_ajout") or 0)
        if duree:
            for freq in (data.get("frequence"), data.get("frequence_jours_ajout") if jours_ajout else None):
                if freq:
                    boucle = eng.analyser_boucle(int(freq), duree, visuels)
                    if not boucle["compatible"]:
                        self.add_error("nombre_visuels", boucle["message"])
                        break
        return data


# ─────────────────────────────────────────────────────────────────────────────
#  OUTILS D'ASSEMBLAGE (sans logique de calcul : tout vient du moteur)
# ─────────────────────────────────────────────────────────────────────────────

def _supports_ecran_disponibles(date_debut, date_fin) -> list[Support]:
    supports = Support.objects.filter(
        type_support="ecran", actif=True, etat__in=Support.ETATS_OPERATIONNELS,
    ).order_by("code")
    if date_debut and date_fin:
        engages = LigneCampagne.objects.filter(
            support__type_support="ecran",
            campagne__statut__in=["en_cours", "a_venir"],
            campagne__date_debut__lte=date_fin,
            campagne__date_fin__gte=date_debut,
        ).values_list("support_id", flat=True)
        supports = supports.exclude(pk__in=engages)
    return list(supports)


def _construire_details_ecrans(
    ecrans_qs, frequence: int, minutes: int, nb_jours: int, nb_ecrans_bloc: int,
    duree_passage: int, date_debut, nombre_visuels: int, bloc_label: str = "principal",
) -> list[dict]:
    """Détail + saturation par écran pour un bloc (principal ou ajout)."""
    if nb_jours <= 0 or nb_ecrans_bloc <= 0:
        return []

    spots_total = eng.spots(frequence, minutes / 60, nb_jours, nb_ecrans_bloc, nombre_visuels)
    par_jour_ecran = round(spots_total / nb_jours / nb_ecrans_bloc)
    # Impact quotidien d'UN écran, visuels inclus (l'ancien calcul les ignorait).
    impact_sec = eng.impact_secondes_jour_ecran(frequence, minutes, duree_passage, nombre_visuels)
    # N visuels de d secondes par intervalle = occupation d'un spot de N x d secondes.
    duree_boucle = duree_passage * max(nombre_visuels, 1)

    details = []
    for support in ecrans_qs.select_related("ecran_info"):
        ecran_info = getattr(support, "ecran_info", None)
        taux_actuel = taux_apres = peut = msg = None

        if ecran_info and date_debut:
            taux_actuel = ecran_info.taux_occupation_pourcentage(date_debut)
            sec_dispo = ecran_info.secondes_disponibles_jour(date_debut)
            occupe = ecran_info.calculer_occupation_reelle(date_debut)
            if sec_dispo:
                taux_apres = round(min(((occupe + impact_sec) / sec_dispo) * 100, 100), 2)
            peut, msg = ecran_info.peut_accueillir_spot(
                duree_sec=duree_boucle,
                frequence_min=frequence / 60,
                date_debut=date_debut,
                date_fin=date_debut + timedelta(days=nb_jours - 1),
            )

        details.append({
            "support": support,
            "taux_actuel": taux_actuel,
            "taux_apres": taux_apres,
            "peut_accueillir": peut,
            "message_dispo": msg,
            "spots_par_jour": par_jour_ecran,
            "spots_total": round(spots_total / nb_ecrans_bloc),
            "bloc": bloc_label,
        })
    return details


def _moyenne(valeurs) -> float | None:
    valeurs = [v for v in valeurs if v is not None]
    return round(sum(valeurs) / len(valeurs), 1) if valeurs else None


def construire_resultats_mode_a(
    *, frequence: int, tranches: str, nb_jours: int, ecrans_qs, nb_ecrans: int,
    duree_passage: int, date_debut, nombre_visuels: int,
    jours_ajout: int = 0, frequence_ajout: int = 0, tranches_ajout: str | None = None,
    ecrans_ajout_qs=None, nb_ecrans_ajout: int = 0, prix_spot: Decimal | None = None,
) -> dict:
    """Calcul direct : 1 bloc principal + 1 bloc additionnel optionnel."""
    visuels = max(nombre_visuels, 1)
    plages = eng.parser_tranches(tranches)
    minutes = eng.duree_minutes(plages)

    bloc1 = Bloc(nb_jours, frequence, minutes, nb_ecrans, visuels, duree_passage)
    blocs = [bloc1]
    bloc2 = None
    minutes_ajout = 0
    if jours_ajout > 0 and frequence_ajout and tranches_ajout:
        minutes_ajout = eng.duree_minutes(eng.parser_tranches(tranches_ajout))
        if minutes_ajout > 0:
            bloc2 = Bloc(jours_ajout, frequence_ajout, minutes_ajout, nb_ecrans_ajout, visuels, duree_passage)
            blocs.append(bloc2)

    ev = eng.evaluer_blocs(blocs)
    spots_principal = round(bloc1.spots)
    spots_ajout = round(bloc2.spots) if bloc2 else 0
    total = spots_principal + spots_ajout          # somme des arrondis : pas d'écart d'affichage
    jours_total = nb_jours + (jours_ajout if bloc2 else 0)

    details = _construire_details_ecrans(
        ecrans_qs, frequence, minutes, nb_jours, nb_ecrans, duree_passage, date_debut, visuels, "principal",
    )
    nb_ecrans_total = nb_ecrans
    if bloc2:
        ids = set(ecrans_qs.values_list("pk", flat=True)) | set(ecrans_ajout_qs.values_list("pk", flat=True))
        nb_ecrans_total = len(ids) or nb_ecrans
        details += _construire_details_ecrans(
            ecrans_ajout_qs, frequence_ajout, minutes_ajout, jours_ajout, nb_ecrans_ajout,
            duree_passage, date_debut, visuels, "ajout",
        )

    boucle = eng.analyser_boucle(frequence, duree_passage, visuels)
    resultats = {
        "mode": "direct",
        "frequence": frequence,
        "frequence_label": eng.label_frequence(frequence),
        "tranches_val": tranches,
        "heures_tranches": round(minutes / 60, 2),
        "minutes_tranches": minutes,
        "nb_jours": nb_jours,
        "nb_jours_total": jours_total,
        "nb_ecrans": nb_ecrans,
        "duree_passage": duree_passage,
        "nombre_visuels": visuels,
        "spots_par_heure": round((3600 / frequence) * visuels, 2),
        "spots_par_jour_ecran": round(bloc1.spots / nb_jours / nb_ecrans),
        "spots_par_jour_total": round(total / max(jours_total, 1)),
        "spots_total_ecran": round(total / max(nb_ecrans_total, 1)),
        "spots_total_tous": total,
        "spots_par_visuel": round(total / visuels),
        "heures_antenne": ev["heures_antenne"],
        "part_de_voix_pct": ev["part_de_voix_pct"],
        "presence_24h_pct": ev["presence_24h_pct"],
        "temps_pub_par_heure_sec": round(eng.impact_secondes_jour_ecran(frequence, 60, duree_passage, visuels), 1),
        "rotation_visuel_str": f"{frequence // 60} min" if frequence >= 60 else f"{frequence}s",
        "boucle": boucle,
        "timeline_hours": eng.construire_timeline(plages),
        "details_ecrans": details,
        "taux_moyen_actuel": _moyenne(d["taux_actuel"] for d in details if d["bloc"] == "principal"),
        "taux_moyen_apres": _moyenne(d["taux_apres"] for d in details if d["bloc"] == "principal"),
        "pct_realisation_cible": 100 if total else 0,
        "gain_spots": spots_ajout,
        "gain_pct": round(spots_ajout / spots_principal * 100, 1) if spots_ajout and spots_principal else 0,
    }
    if bloc2:
        resultats.update({
            "jours_ajout": jours_ajout,
            "frequence_ajout": frequence_ajout,
            "tranches_ajout": tranches_ajout,
            "ecrans_ajout": nb_ecrans_ajout,
            "spots_ajout": spots_ajout,
            "spots_principal": spots_principal,
            "nb_ecrans_principal": nb_ecrans,
            "nb_ecrans_ajout": nb_ecrans_ajout,
            "nb_ecrans_total": nb_ecrans_total,
        })
    if prix_spot is not None:
        resultats["prix_spot"] = prix_spot
        resultats["montant_estime"] = int((prix_spot * Decimal(total)).quantize(Decimal("1")))
    return resultats


def _appliquer_objectif_et_suggestions(
    resultats: dict, *, cible: int, par_ecran, total_saisi, frequence: int, minutes: int,
    nb_jours: int, nb_ecrans: int, nombre_visuels: int, duree_passage: int,
    tranches: str, date_debut_saisie,
) -> None:
    """Écart à l'objectif + toutes les suggestions que le template sait afficher."""
    total = int(resultats["spots_total_tous"])
    ecart = cible - total                       # > 0 : manque ; < 0 : dépassement
    resultats.update({
        "nb_spots_cible": cible,
        "nb_spots_cible_par_ecran": par_ecran,
        "nb_spots_cible_total_saisi": total_saisi,
        "ecart_objectif": ecart,
        "pct_realisation_cible": round(min(100.0, total / max(cible, 1) * 100), 1),
    })
    if ecart > 0:
        resultats["manque"] = ecart
    elif ecart < 0:
        resultats["depassement"] = -ecart
    else:
        resultats["manque"] = 0
        return

    visuels = max(nombre_visuels, 1)
    depart = eng.debut_tranche(tranches)
    spots_principal = int(resultats.get("spots_principal", total))
    spots_ajout = int(resultats.get("spots_ajout", 0))

    # Même période, mêmes écrans : changer fréquence + durée de tranche
    resultats["ajustements_meme_periode"] = eng.generer_ajustements_meme_periode(
        objectif=cible, nb_jours=nb_jours, nb_ecrans=nb_ecrans, nombre_visuels=visuels,
        duree_passage=duree_passage, frequence_actuelle=frequence, minutes_actuelles=minutes,
        depart=depart, frequences=FREQUENCES_DISPONIBLES,
    )

    if ecart > 0:
        # Le bloc 2 proposé REMPLACE l'éventuel bloc 2 actuel -> on vise le manque vs le bloc 1 seul.
        manque_principal = max(cible - spots_principal, 0)
        resultats["combinaisons_complement"] = eng.generer_combinaisons_complement(
            manque_principal, visuels, duree_passage, nb_ecrans_max=nb_ecrans,
            frequence_reference=frequence, depart=depart, frequences=FREQUENCES_DISPONIBLES,
        )
        return

    reduction = eng.calculer_reduction_jours(
        frequence, minutes, nb_ecrans, visuels, nb_jours, max(cible - spots_ajout, 0),
    )
    if not reduction:
        return
    if date_debut_saisie and reduction["nb_jours_reduit"] > 0:
        reduction["date_fin_reduite"] = (
            date_debut_saisie + timedelta(days=reduction["nb_jours_reduit"] - 1)
        ).isoformat()
    resultats["reduction_jours"] = reduction

    if not reduction["cas_particulier"] and reduction["manque_apres_reduction"] > 0:
        combos = eng.generer_combinaisons_complement(
            reduction["manque_apres_reduction"], visuels, duree_passage, nb_ecrans_max=nb_ecrans,
            frequence_reference=frequence, depart=depart, frequences=FREQUENCES_DISPONIBLES,
        )
        resultats["combinaisons_ajustement"] = [
            {**c,
             "total_final": reduction["spots_avec_jours_reduits"] + c["spots_obtenus"],
             "ecart_final": reduction["spots_avec_jours_reduits"] + c["spots_obtenus"] - cible}
            for c in combos
        ]


def _url_creation_campagne(
    *, duree_passage, frequence, nombre_visuels, tranches, ecrans_qs, date_debut, nb_jours,
    resultats: dict, ecrans_ajout_qs, prix_spot,
) -> str:
    """
    Dates toujours renseignées (l'ancien code n'en passait aucune quand l'utilisateur
    avait saisi seulement nb_jours) et période du bloc 2 incluse dans date_fin.
    """
    params = {
        "nom": "Campagne écran simulée",
        "type_support": "ecran",
        "duree_passage": duree_passage,
        "frequence": frequence,
        "nombre_visuels": nombre_visuels,
        "tranches_horaires": tranches,
        "supports": ",".join(str(pk) for pk in ecrans_qs.values_list("pk", flat=True)),
        "date_debut": date_debut.isoformat(),
    }
    fin_principal = date_debut + timedelta(days=nb_jours - 1)
    if resultats.get("jours_ajout"):
        params.update({
            "jours_ajout": resultats["jours_ajout"],
            "frequence_ajout": resultats["frequence_ajout"],
            "tranches_ajout": resultats["tranches_ajout"],
            "supports_bloc2": ",".join(str(pk) for pk in ecrans_ajout_qs.values_list("pk", flat=True)),
            "date_fin_principal": fin_principal.isoformat(),
            "date_fin": (fin_principal + timedelta(days=resultats["jours_ajout"])).isoformat(),
        })
    else:
        params["date_fin"] = fin_principal.isoformat()
    if prix_spot is not None:
        params["prix"] = str(prix_spot)
    return f"{reverse('campagne_create')}?{urlencode(params)}"


# ─────────────────────────────────────────────────────────────────────────────
#  VIEW
# ─────────────────────────────────────────────────────────────────────────────

class SimulationCampagneEcranView(LoginRequiredMixin, FormView):
    template_name = "campaigns/simulation_ecran.html"
    form_class = SimulationCampagneEcranForm

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

        data = form.cleaned_data
        duree_passage = int(data["duree_passage"])
        nb_jours = data["nb_jours"]
        date_debut_saisie = data.get("date_debut")
        date_fin_saisie = data.get("date_fin")
        date_debut = date_debut_saisie or timezone.localdate()
        jours_ajout = int(data.get("jours_ajout") or 0)
        # Période totale (bloc 1 + bloc 2) : sert à contrôler la disponibilité des écrans
        date_fin_principal = date_fin_saisie or (date_debut + timedelta(days=nb_jours - 1) if nb_jours else None)
        date_fin_totale = date_fin_principal + timedelta(days=jours_ajout) if date_fin_principal else None

        frequence = int(data["frequence"]) if data.get("frequence") else None
        tranches = data.get("tranches_horaires") or None
        prix_spot = data.get("prix_spot")

        nb_spots_cible_par_ecran = data.get("nb_spots_cible")
        nb_spots_cible_total = data.get("nb_spots_cible_total")
        scenario_applique = self.request.GET.get("scenario_applique") == "1"
        a_objectif = nb_spots_cible_par_ecran is not None or nb_spots_cible_total is not None
        objectif_et_periode_fixes = bool(nb_jours and a_objectif) and not scenario_applique
        nombre_visuels = int(data.get("nombre_visuels") or 1)
        tranches_alternatives = data.get("tranches_horaires_alternatives_list") or []

        ecrans_selectionnes = list(data.get("ecrans") or [])
        nb_ecrans_cible = data.get("nb_ecrans_cible")
        ecrans_fixes = bool(ecrans_selectionnes or nb_ecrans_cible)
        ecrans_disponibles = _supports_ecran_disponibles(date_debut, date_fin_totale or date_debut)

        if ecrans_selectionnes:
            nb_ecrans = len(ecrans_selectionnes)
            ecrans_qs = Support.objects.filter(pk__in=[s.pk for s in ecrans_selectionnes]).order_by("code")
            nb_ecrans_candidates = [nb_ecrans]
        elif nb_ecrans_cible:
            nb_ecrans = nb_ecrans_cible
            nb_ecrans_candidates = [nb_ecrans]
            ecrans_qs = Support.objects.filter(
                pk__in=[s.pk for s in ecrans_disponibles[:nb_ecrans]]
            ).order_by("code")
        else:
            nb_ecrans = 0
            nb_ecrans_candidates = list(range(1, len(ecrans_disponibles) + 1))
            ecrans_qs = Support.objects.none()

        nb_spots_cible = eng.normaliser_objectif_spots(
            nb_ecrans=nb_ecrans,
            objectif_par_ecran=nb_spots_cible_par_ecran,
            objectif_total=nb_spots_cible_total,
        ) if ecrans_fixes else nb_spots_cible_total

        target_query = urlencode(
            {"nb_spots_cible": nb_spots_cible_par_ecran} if nb_spots_cible_par_ecran is not None
            else {"nb_spots_cible_total": nb_spots_cible_total} if nb_spots_cible_total is not None
            else {}
        )
        screen_query = urlencode([("ecrans", pk) for pk in ecrans_qs.values_list("pk", flat=True)])
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
            resultats = construire_resultats_mode_a(
                frequence=frequence, tranches=tranches, nb_jours=nb_jours, ecrans_qs=ecrans_qs,
                nb_ecrans=nb_ecrans, duree_passage=duree_passage, date_debut=date_debut,
                nombre_visuels=nombre_visuels, jours_ajout=jours_ajout,
                frequence_ajout=frequence_ajout, tranches_ajout=tranches_ajout,
                ecrans_ajout_qs=ecrans_ajout_qs, nb_ecrans_ajout=nb_ecrans_ajout, prix_spot=prix_spot,
            )
            if nb_spots_cible is not None:
                _appliquer_objectif_et_suggestions(
                    resultats, cible=int(nb_spots_cible), par_ecran=nb_spots_cible_par_ecran,
                    total_saisi=nb_spots_cible_total, frequence=frequence,
                    minutes=resultats["minutes_tranches"], nb_jours=nb_jours, nb_ecrans=nb_ecrans,
                    nombre_visuels=nombre_visuels, duree_passage=duree_passage, tranches=tranches,
                    date_debut_saisie=date_debut_saisie,
                )
            resultats["url_creer_campagne"] = _url_creation_campagne(
                duree_passage=duree_passage, frequence=frequence, nombre_visuels=nombre_visuels,
                tranches=tranches, ecrans_qs=ecrans_qs, date_debut=date_debut, nb_jours=nb_jours,
                resultats=resultats, ecrans_ajout_qs=ecrans_ajout_qs, prix_spot=prix_spot,
            )
            context["resultats"] = resultats

        elif nb_jours or a_objectif:
            propositions = eng.generer_scenarios(
                duree_passage=duree_passage,
                nombre_visuels=nombre_visuels,
                nb_jours=nb_jours,
                nb_ecrans_candidates=nb_ecrans_candidates,
                objectif_total=nb_spots_cible_total,
                objectif_par_ecran=nb_spots_cible_par_ecran,
                frequences=FREQUENCES_DISPONIBLES,
                frequence_fixe=None if objectif_et_periode_fixes else frequence,
                tranches_fixes=None if objectif_et_periode_fixes else tranches,
                frequence_preferee=frequence if objectif_et_periode_fixes else None,
                tranche_reference=tranches if objectif_et_periode_fixes else None,
                tranches_alternatives=tranches_alternatives,
                max_resultats=50,
            )

            dispo_cache: dict = {}
            scenarios_valides = []
            for p in propositions:
                if date_debut_saisie:
                    debut_s = date_debut_saisie
                    fin_s = debut_s + timedelta(days=p["nb_jours"] - 1)
                elif date_fin_saisie:
                    fin_s = date_fin_saisie
                    debut_s = fin_s - timedelta(days=p["nb_jours"] - 1)
                else:
                    debut_s = date_debut
                    fin_s = debut_s + timedelta(days=p["nb_jours"] - 1)

                if ecrans_selectionnes:
                    supports_s = ecrans_selectionnes
                else:
                    cle = (debut_s, fin_s)     # l'ancien cache ignorait la date de début
                    if cle not in dispo_cache:
                        dispo_cache[cle] = _supports_ecran_disponibles(debut_s, fin_s)
                    supports_s = dispo_cache[cle]
                if len(supports_s) < p["nb_ecrans"]:
                    continue

                retenus = supports_s[:p["nb_ecrans"]]
                support_query = urlencode([("ecrans", s.pk) for s in retenus])
                p["support_query"] = support_query
                p["supports_label"] = ", ".join(s.code for s in retenus)
                p["date_debut"] = debut_s.isoformat()
                p["date_fin"] = fin_s.isoformat()
                p["query"] = urlencode({
                    "duree_passage": duree_passage,
                    "frequence": p["frequence"],
                    "tranches_horaires": p["tranches_val"],
                    "nb_jours": p["nb_jours"],
                    "date_debut": p["date_debut"],
                    "nombre_visuels": nombre_visuels,
                })
                if prix_spot is not None:
                    p["query"] += f"&prix_spot={prix_spot}"
                if target_query:
                    p["query"] += f"&{target_query}"
                if support_query:
                    p["query"] += f"&{support_query}"
                p["query"] += "&scenario_applique=1"
                if prix_spot is not None:
                    p["montant_estime"] = int(prix_spot * p["spots_total"])
                scenarios_valides.append(p)

            if not scenarios_valides:
                context["erreur"] = (
                    "Aucune combinaison compatible avec les paramètres et les écrans disponibles n’a été trouvée. "
                    "La cible peut être impossible à atteindre avec les fréquences et tranches horaires autorisées "
                    "(vérifiez aussi que visuels x durée du spot tient dans la fréquence)."
                )
            elif a_objectif:
                configuration_actuelle = None
                if objectif_et_periode_fixes and ecrans_fixes and frequence and tranches:
                    minutes_act = eng.duree_minutes(eng.parser_tranches(tranches))
                    total_actuel = round(eng.spots(frequence, minutes_act / 60, nb_jours, nb_ecrans, nombre_visuels))
                    deja = any(
                        p["frequence"] == frequence and p["tranches_val"] == tranches
                        and p["nb_jours"] == nb_jours and p["nb_ecrans"] == nb_ecrans
                        for p in scenarios_valides
                    )
                    if not deja:
                        query_actuelle = urlencode({
                            "duree_passage": duree_passage, "frequence": frequence,
                            "tranches_horaires": tranches, "nb_jours": nb_jours,
                            "date_debut": date_debut.isoformat(), "nombre_visuels": nombre_visuels,
                            "scenario_applique": "1",
                        })
                        if target_query:
                            query_actuelle += f"&{target_query}"
                        if screen_query:
                            query_actuelle += f"&{screen_query}"
                        configuration_actuelle = {
                            "frequence_label": eng.label_frequence(frequence),
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
            context["erreur"] = "Renseignez une période ou un objectif de spots pour définir la recherche."

        return context

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self.get_context_data())
