"""
simulation_engine.py — moteur de calcul du simulateur DOOH (Python pur, sans Django)
===================================================================================

Principes
---------
* Toutes les durées sont manipulées en MINUTES ENTIÈRES (jamais en heures flottantes),
  ce qui supprime les erreurs d'arrondi du type 07:19:59.99 -> "07:19".
* Les tranches horaires sont parsées, validées, bornées à la plage d'exploitation
  (06:00-23:00 par défaut) et FUSIONNÉES : "08:00-12:00,10:00-14:00" = 6h, pas 8h.
* La formule de base reste celle du modèle Campagne / LigneCampagne :
      spots = (3600 / fréquence) x heures x jours x écrans x visuels
* Contrainte physique ajoutée : visuels x durée du spot <= fréquence
  (sinon la boucle ne tient pas dans l'intervalle).
* Part de voix = temps d'antenne / temps de diffusion disponible (plage active),
  pondérée correctement quand il y a deux blocs. La présence sur 24h est
  fournie à part.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTES
# ─────────────────────────────────────────────────────────────────────────────

OUVERTURE_MIN = 6 * 60            # 06:00
FERMETURE_MIN = 23 * 60           # 23:00
AMPLITUDE_MAX_MIN = FERMETURE_MIN - OUVERTURE_MIN   # 17h
PAS_MIN = 5                       # granularité des tranches proposées
DUREE_MIN_TRANCHE = 15            # on ne propose pas de tranche < 15 min
DUREE_MIN_RECHERCHE = 120         # recherche ouverte (jours libres) : tranches >= 2h
TOLERANCE = 0.05
MAX_JOURS = 366

FREQUENCES_DEFAUT: tuple[int, ...] = (60, 120, 180, 240, 300, 600, 900, 1200, 1800)

TRANCHES_PREDEFINIES: tuple[tuple[str, str], ...] = (
    ("Plein temps", "06:00-23:00"),
    ("Journée", "07:00-21:00"),
    ("Matin et soir", "07:00-09:30,17:00-20:00"),
    ("Bureaux", "08:00-18:00"),
    ("Midi et soir", "12:00-14:30,18:00-21:00"),
    ("Soirée", "18:00-22:30"),
    ("Demi-journée", "12:00-18:00"),
)


class TrancheError(ValueError):
    """Tranche horaire invalide (format, ordre ou hors plage d'exploitation)."""


# ─────────────────────────────────────────────────────────────────────────────
#  TRANCHES HORAIRES
# ─────────────────────────────────────────────────────────────────────────────

_RE_PLAGE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


def minutes_vers_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def hhmm_vers_minutes(valeur: str) -> int:
    h, m = valeur.strip().split(":")
    return int(h) * 60 + int(m)


def _vers_minutes(h: str, m: str, plage: str) -> int:
    h, m = int(h), int(m)
    if m > 59 or h > 24 or (h == 24 and m):
        raise TrancheError(f"Heure invalide dans « {plage} ».")
    return h * 60 + m


def fusionner_plages(plages: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Trie et fusionne les plages qui se chevauchent ou se touchent."""
    fusion: list[list[int]] = []
    for debut, fin in sorted(plages):
        if fusion and debut <= fusion[-1][1]:
            fusion[-1][1] = max(fusion[-1][1], fin)
        else:
            fusion.append([debut, fin])
    return [(d, f) for d, f in fusion]


def parser_tranches(
    texte: str | None,
    *,
    ouverture: int = OUVERTURE_MIN,
    fermeture: int = FERMETURE_MIN,
    fusionner: bool = True,
) -> list[tuple[int, int]]:
    """
    "08:00-12:00, 14:00-18:00" -> [(480, 720), (840, 1080)]
    Lève TrancheError si format invalide, fin <= début ou hors plage d'exploitation.
    """
    if not texte or not texte.strip():
        return []
    plages: list[tuple[int, int]] = []
    for morceau in (p.strip() for p in re.split(r"[,;\n]", texte)):
        if not morceau:
            continue
        m = _RE_PLAGE.match(morceau)
        if not m:
            raise TrancheError(f"Format invalide « {morceau} » — attendu HH:MM-HH:MM.")
        debut = _vers_minutes(m[1], m[2], morceau)
        fin = _vers_minutes(m[3], m[4], morceau)
        if fin <= debut:
            raise TrancheError(f"« {morceau} » : l'heure de fin doit être après l'heure de début.")
        if debut < ouverture or fin > fermeture:
            raise TrancheError(
                f"« {morceau} » sort de la plage de diffusion "
                f"{minutes_vers_hhmm(ouverture)}-{minutes_vers_hhmm(fermeture)}."
            )
        plages.append((debut, fin))
    return fusionner_plages(plages) if fusionner else plages


def formater_tranches(plages: Sequence[tuple[int, int]]) -> str:
    return ",".join(f"{minutes_vers_hhmm(d)}-{minutes_vers_hhmm(f)}" for d, f in plages)


def duree_minutes(plages: Sequence[tuple[int, int]]) -> int:
    return sum(f - d for d, f in plages)


def duree_heures_tranches(texte: str | None, **kwargs) -> float:
    return duree_minutes(parser_tranches(texte, **kwargs)) / 60


def debut_tranche(texte: str | None) -> int:
    """Première heure de début (en minutes) d'une tranche, 06:00 par défaut."""
    try:
        plages = parser_tranches(texte)
    except TrancheError:
        return OUVERTURE_MIN
    return plages[0][0] if plages else OUVERTURE_MIN


def tranche_depuis_minutes(minutes: int, depart: int = OUVERTURE_MIN) -> str:
    """
    Plage unique de `minutes` à partir de `depart`. Si elle dépasse 23:00, on
    DÉCALE le début (on garde la durée exacte) au lieu de tronquer.
    """
    minutes = max(PAS_MIN, min(int(minutes), AMPLITUDE_MAX_MIN))
    debut = max(OUVERTURE_MIN, min(depart, FERMETURE_MIN - minutes))
    return f"{minutes_vers_hhmm(debut)}-{minutes_vers_hhmm(debut + minutes)}"


def construire_timeline(plages: Sequence[tuple[int, int]]) -> list[dict]:
    """24 cases horaires ; une heure partiellement couverte est marquée partial-hour."""
    cases = []
    for h in range(24):
        debut_h, fin_h = h * 60, (h + 1) * 60
        couvert = sum(max(0, min(f, fin_h) - max(d, debut_h)) for d, f in plages)
        if couvert >= 60:
            cls, titre = "active-hour", f"{h}h00 — heure complète"
        elif couvert > 0:
            cls, titre = "active-hour partial-hour", f"{h}h00 — {couvert} min diffusées"
        else:
            cls, titre = "", f"{h}h00 — hors diffusion"
        cases.append({"h": h, "cls": cls, "couverture_min": couvert, "title": titre})
    return cases


# ─────────────────────────────────────────────────────────────────────────────
#  FORMATAGE
# ─────────────────────────────────────────────────────────────────────────────

def format_duree_minutes(minutes: int) -> str:
    h, m = divmod(int(round(minutes)), 60)
    if h and m:
        return f"{h}h{m:02d}"
    if h:
        return f"{h}h"
    return f"{m}min"


def label_frequence(frequence: int) -> str:
    if frequence >= 60 and frequence % 60 == 0:
        return f"toutes les {frequence // 60} min"
    return f"toutes les {frequence}s"


# ─────────────────────────────────────────────────────────────────────────────
#  FORMULES DE BASE
# ─────────────────────────────────────────────────────────────────────────────

def spots(frequence: int, heures: float, jours: int, nb_ecrans: int, visuels: int = 1) -> float:
    """spots = (3600 / fréquence) x heures x jours x écrans x visuels"""
    if frequence <= 0:
        return 0.0
    return (3600 / frequence) * heures * jours * nb_ecrans * max(visuels, 1)


def spots_par_minute_tranche(frequence: int, jours: int, nb_ecrans: int, visuels: int = 1) -> float:
    """Spots gagnés par minute de tranche quotidienne."""
    return (60 / frequence) * jours * nb_ecrans * max(visuels, 1) if frequence > 0 else 0.0


def analyser_boucle(frequence: int, duree: int, visuels: int = 1) -> dict:
    """
    Taux de remplissage de la boucle : visuels x durée / fréquence.
    > 100 % => physiquement impossible (les spots se chevauchent).
    """
    visuels = max(visuels, 1)
    if frequence <= 0:
        return {"taux_pct": 0.0, "niveau": "inconnu", "compatible": True, "message": ""}
    secondes = visuels * duree
    taux = secondes / frequence * 100
    if taux > 100:
        niveau, ok = "impossible", False
        msg = (f"{visuels} visuel(s) x {duree}s = {secondes}s : cela dépasse l'intervalle de "
               f"{frequence}s. Réduisez les visuels, la durée ou espacez la fréquence.")
    elif taux >= 75:
        niveau, ok = "dense", True
        msg = f"Boucle très dense ({taux:.0f} % du temps d'écran occupé par cette campagne)."
    else:
        niveau, ok, msg = "ok", True, ""
    return {"taux_pct": round(taux, 1), "niveau": niveau, "compatible": ok, "message": msg}


def frequences_compatibles(frequences: Iterable[int], duree: int, visuels: int = 1) -> list[int]:
    return [f for f in frequences if max(visuels, 1) * duree <= f]


def part_de_voix_active(frequence: int, duree: int, visuels: int = 1) -> float:
    """Part de voix sur la plage diffusée (identique pour tout écran/jour à fréquence donnée)."""
    return round(min(max(visuels, 1) * duree / frequence * 100, 100), 1) if frequence else 0.0


def impact_secondes_jour_ecran(frequence: int, minutes: int, duree: int, visuels: int = 1) -> float:
    """Secondes d'antenne ajoutées par jour sur UN écran (visuels inclus)."""
    return (3600 / frequence) * (minutes / 60) * duree * max(visuels, 1) if frequence else 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  BLOCS (principal + additionnel)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Bloc:
    jours: int
    frequence: int
    minutes: int          # minutes diffusées par jour et par écran
    nb_ecrans: int
    visuels: int = 1
    duree: int = 10

    @property
    def actif(self) -> bool:
        return self.jours > 0 and self.nb_ecrans > 0 and self.minutes > 0 and self.frequence > 0

    @property
    def spots(self) -> float:
        return spots(self.frequence, self.minutes / 60, self.jours, self.nb_ecrans, self.visuels) if self.actif else 0.0

    @property
    def secondes_antenne(self) -> float:
        return self.spots * self.duree

    @property
    def secondes_fenetre(self) -> float:
        return self.jours * self.minutes * 60 * self.nb_ecrans

    @property
    def secondes_24h(self) -> float:
        return self.jours * 86400 * self.nb_ecrans


def evaluer_blocs(blocs: Sequence[Bloc]) -> dict:
    """Agrège un ou plusieurs blocs avec une part de voix correctement pondérée."""
    actifs = [b for b in blocs if b.actif]
    antenne = sum(b.secondes_antenne for b in actifs)
    fenetre = sum(b.secondes_fenetre for b in actifs)
    h24 = sum(b.secondes_24h for b in actifs)
    return {
        "spots_blocs": [b.spots for b in blocs],
        "spots_total": sum(b.spots for b in actifs),
        "secondes_antenne": antenne,
        "heures_antenne": round(antenne / 3600, 2),
        "part_de_voix_pct": round(min(antenne / fenetre * 100, 100), 1) if fenetre else 0.0,
        "presence_24h_pct": round(antenne / h24 * 100, 2) if h24 else 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  RECHERCHE DE LA DURÉE QUI ATTEINT UN OBJECTIF
# ─────────────────────────────────────────────────────────────────────────────

def meilleure_duree(
    cible: float, frequence: int, jours: int, nb_ecrans: int, visuels: int = 1,
    minimum: int = DUREE_MIN_TRANCHE, maximum: int = AMPLITUDE_MAX_MIN,
) -> tuple[int, float] | None:
    """
    Durée quotidienne (minutes, multiple de 5) dont le total est le plus proche de `cible`.
    On teste le multiple de 5 inférieur ET supérieur (l'ancien code n'arrondissait
    qu'au plus proche en heures, ce qui pouvait manquer le meilleur voisin).
    Retourne (minutes, minutes_exactes) ou None si hors plage.
    """
    par_min = spots_par_minute_tranche(frequence, jours, nb_ecrans, visuels)
    if par_min <= 0 or cible <= 0:
        return None
    exact = cible / par_min
    bas = math.floor(exact / PAS_MIN) * PAS_MIN
    candidats = [m for m in {bas, bas + PAS_MIN} if minimum <= m <= maximum]
    if not candidats:
        return None
    meilleur = min(candidats, key=lambda m: (abs(par_min * m - cible), m))
    return meilleur, exact


# ─────────────────────────────────────────────────────────────────────────────
#  OBJECTIF : TRANCHE OPTIMALE (fréquence, jours, écrans fixés)
# ─────────────────────────────────────────────────────────────────────────────

def calculer_tranche_optimale_frequence_fixe(
    nb_spots_cible: int, frequence: int, nb_jours: int, nb_ecrans: int,
    duree_passage: int, nombre_visuels: int = 1,
) -> dict:
    par_min = spots_par_minute_tranche(frequence, nb_jours, nb_ecrans, nombre_visuels)
    if par_min <= 0:
        return {}
    exact = nb_spots_cible / par_min

    hors_plage = None
    if exact > AMPLITUDE_MAX_MIN:
        minutes, hors_plage = AMPLITUDE_MAX_MIN, "trop_grand"
    elif exact < DUREE_MIN_TRANCHE:
        minutes, hors_plage = DUREE_MIN_TRANCHE, "trop_petit"
    else:
        minutes = meilleure_duree(nb_spots_cible, frequence, nb_jours, nb_ecrans, nombre_visuels)[0]

    obtenus = par_min * minutes
    ecart = round(obtenus - nb_spots_cible)

    departs = {OUVERTURE_MIN, FERMETURE_MIN - minutes, 8 * 60, 9 * 60, 12 * 60, 14 * 60, 18 * 60}
    variantes = [
        {"depart": minutes_vers_hhmm(d), "tranche_val": tranche_depuis_minutes(minutes, d)}
        for d in sorted(departs) if OUVERTURE_MIN <= d <= FERMETURE_MIN - minutes
    ]
    boucle = analyser_boucle(frequence, duree_passage, nombre_visuels)
    return {
        "frequence": frequence,
        "frequence_label": label_frequence(frequence),
        "nb_jours": nb_jours,
        "nb_ecrans": nb_ecrans,
        "heures_necessaires_exactes": round(exact / 60, 2),
        "heures_arrondies": round(minutes / 60, 2),
        "minutes": minutes,
        "tranche_label": format_duree_minutes(minutes),
        "spots_obtenus": round(obtenus),
        "nb_spots_cible": nb_spots_cible,
        "ecart": ecart,
        "ecart_pct": round(ecart / nb_spots_cible * 100, 2) if nb_spots_cible else 0,
        "hors_plage": hors_plage,
        "variantes_horaires": variantes,
        "heures_antenne": round(obtenus * duree_passage / 3600, 2),
        "spots_par_jour": round(obtenus / nb_jours),
        "spots_par_jour_ecran": round(obtenus / nb_jours / nb_ecrans),
        "spots_par_jour_total": round(obtenus / nb_jours),
        "spots_par_visuel": round(obtenus / max(nombre_visuels, 1)),
        "rotation_visuel_str": f"{frequence // 60} min" if frequence >= 60 else f"{frequence}s",
        "temps_pub_par_heure_sec": round(impact_secondes_jour_ecran(frequence, 60, duree_passage, nombre_visuels), 1),
        "part_de_voix_pct": part_de_voix_active(frequence, duree_passage, nombre_visuels),
        "boucle": boucle,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  AJUSTEMENTS (même période, compléments, réductions)
# ─────────────────────────────────────────────────────────────────────────────

def generer_ajustements_meme_periode(
    objectif: int, nb_jours: int, nb_ecrans: int, nombre_visuels: int, duree_passage: int,
    frequence_actuelle: int | None = None, minutes_actuelles: int | None = None,
    depart: int = OUVERTURE_MIN, tolerance: float = TOLERANCE, max_resultats: int = 8,
    frequences: Sequence[int] = FREQUENCES_DEFAUT,
) -> list[dict]:
    """Change fréquence + durée de tranche, sans toucher aux jours ni aux écrans."""
    if objectif <= 0 or nb_jours <= 0 or nb_ecrans <= 0:
        return []
    resultats = []
    for freq in frequences_compatibles(frequences, duree_passage, nombre_visuels):
        trouve = meilleure_duree(objectif, freq, nb_jours, nb_ecrans, nombre_visuels)
        if not trouve:
            continue
        minutes, _ = trouve
        if freq == frequence_actuelle and minutes == minutes_actuelles:
            continue
        obtenus = round(spots_par_minute_tranche(freq, nb_jours, nb_ecrans, nombre_visuels) * minutes)
        ecart = obtenus - objectif
        if abs(ecart) / objectif > tolerance:
            continue
        resultats.append({
            "frequence": freq,
            "frequence_label": label_frequence(freq).replace("toutes les ", ""),
            "tranche_val": tranche_depuis_minutes(minutes, depart),
            "tranche_label": format_duree_minutes(minutes),
            "heures": round(minutes / 60, 2),
            "minutes": minutes,
            "spots_obtenus": obtenus,
            "ecart": ecart,
            "ecart_pct": round(ecart / objectif * 100, 1),
        })
    resultats.sort(key=lambda r: (
        abs(r["ecart"]),
        abs(r["frequence"] - (frequence_actuelle or r["frequence"])),
        abs(r["minutes"] - (minutes_actuelles or r["minutes"])),
    ))
    return resultats[:max_resultats]


def generer_combinaisons_complement(
    manque: int, nombre_visuels: int, duree_passage: int, nb_ecrans_max: int,
    frequence_reference: int | None = None, depart: int = OUVERTURE_MIN,
    tolerance: float = 0.08, max_jours_ajout: int = 4, max_resultats: int = 8,
    frequences: Sequence[int] = FREQUENCES_DEFAUT,
) -> list[dict]:
    """
    Comble `manque` spots avec des jours supplémentaires (bloc 2).
    `ecrans_ajout` = nombre d'écrans DU BLOC PRINCIPAL réutilisés sur ces jours
    (jamais plus que nb_ecrans_max : le bloc 2 est un sous-ensemble du bloc 1).
    Une seule proposition par couple (jours, écrans) : la plus précise.
    """
    if manque <= 0 or nb_ecrans_max <= 0:
        return []
    freqs = frequences_compatibles(frequences, duree_passage, nombre_visuels)
    meilleurs: dict[tuple[int, int], dict] = {}
    for jours in range(1, max_jours_ajout + 1):
        for ecrans in range(1, nb_ecrans_max + 1):
            for freq in freqs:
                trouve = meilleure_duree(manque, freq, jours, ecrans, nombre_visuels)
                if not trouve:
                    continue
                minutes, _ = trouve
                obtenus = round(spots_par_minute_tranche(freq, jours, ecrans, nombre_visuels) * minutes)
                ecart = obtenus - manque
                if abs(ecart) / manque > tolerance:
                    continue
                cand = {
                    "jours_ajout": jours,
                    "ecrans_ajout": ecrans,
                    "frequence": freq,
                    "frequence_label": label_frequence(freq).replace("toutes les ", ""),
                    "minutes": minutes,
                    "heures_necessaires": round(minutes / 60, 2),
                    "tranche_label": format_duree_minutes(minutes),
                    "tranche_val": tranche_depuis_minutes(minutes, depart),
                    "spots_obtenus": obtenus,
                    "ecart": ecart,
                    "ecart_pct": round(ecart / manque * 100, 1),
                    "complexite": jours + ecrans,
                    "description": (
                        f"Ajouter {jours} jour(s) sur {ecrans} écran(s), fréquence "
                        f"{label_frequence(freq).replace('toutes les ', '')}, tranche de {format_duree_minutes(minutes)}"
                    ),
                }
                cle = (jours, ecrans)
                ref = frequence_reference or freq
                score = (abs(ecart), abs(freq - ref))
                ancien = meilleurs.get(cle)
                if ancien is None or score < ancien["_score"]:
                    cand["_score"] = score
                    meilleurs[cle] = cand
    liste = sorted(meilleurs.values(), key=lambda c: (abs(c["ecart"]), c["complexite"]))[:max_resultats]
    for c in liste:
        c.pop("_score", None)
    return liste


def calculer_reduction_jours(
    frequence: int, minutes: int, nb_ecrans: int, nombre_visuels: int,
    nb_jours_principal: int, objectif: int,
) -> dict | None:
    """Dépassement : nombre maximal de jours qui reste <= objectif (mêmes paramètres)."""
    par_jour = spots(frequence, minutes / 60, 1, nb_ecrans, nombre_visuels)
    if par_jour <= 0:
        return None
    reduit = max(min(int(objectif // par_jour), nb_jours_principal), 0)
    cumul = round(par_jour * reduit)
    cas = None
    if reduit == 0:
        cas = (f"Même réduit à 1 seul jour, la diffusion produit déjà {round(par_jour)} spots, "
               f"ce qui dépasse l'objectif de {objectif} spots. Réduisez plutôt la tranche, "
               "le nombre d'écrans, ou ralentissez la fréquence.")
    return {
        "spots_par_jour_principal": round(par_jour),
        "nb_jours_original": nb_jours_principal,
        "nb_jours_reduit": reduit,
        "jours_retires": nb_jours_principal - reduit,
        "spots_avec_jours_reduits": cumul,
        "manque_apres_reduction": max(objectif - cumul, 0),
        "cas_particulier": cas,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  OBJECTIFS
# ─────────────────────────────────────────────────────────────────────────────

def calculer_objectif_total(nb_spots_par_ecran: int, nb_ecrans: int) -> int:
    return max(nb_spots_par_ecran, 0) * max(nb_ecrans, 0)


def normaliser_objectif_spots(
    nb_ecrans: int, objectif_par_ecran: int | None = None, objectif_total: int | None = None,
) -> int | None:
    if objectif_par_ecran is not None and objectif_total is not None:
        raise ValueError("Un seul type d'objectif peut être renseigné.")
    if objectif_total is not None:
        return objectif_total
    if objectif_par_ecran is not None:
        return calculer_objectif_total(objectif_par_ecran, nb_ecrans)
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  SCÉNARIOS
# ─────────────────────────────────────────────────────────────────────────────

def _scenario(freq, tranche_label, tranche_val, minutes, jours, nb_ecrans, visuels, duree, cible, optimise):
    total = round(spots_par_minute_tranche(freq, jours, nb_ecrans, visuels) * minutes)
    ecart_pct = ((total - cible) / cible * 100) if cible else 0
    ev = evaluer_blocs([Bloc(jours, freq, minutes, nb_ecrans, visuels, duree)])
    return {
        "frequence": freq,
        "frequence_label": label_frequence(freq),
        "tranches_label": f"{tranche_label} — {tranche_val}",
        "tranches_val": tranche_val,
        "heures_tranches": round(minutes / 60, 2),
        "minutes": minutes,
        "spots_total": total,
        "spots_total_par_ecran": round(total / nb_ecrans),
        "spots_par_jour": round(total / jours),
        "spots_par_jour_ecran": round(total / jours / nb_ecrans),
        "spots_par_jour_total": round(total / jours),
        "spots_par_visuel": round(total / max(visuels, 1)),
        "rotation_visuel_str": f"{freq // 60} min" if freq >= 60 else f"{freq}s",
        "temps_pub_par_heure_sec": round(impact_secondes_jour_ecran(freq, 60, duree, visuels), 1),
        "part_de_voix_pct": ev["part_de_voix_pct"],
        "presence_24h_pct": ev["presence_24h_pct"],
        "ecart_pct": round(ecart_pct, 2),
        "abs_ecart": abs(ecart_pct),
        "heures_antenne": round(total * duree / 3600, 2),
        "nb_ecrans_utilises": nb_ecrans,
        "nb_ecrans": nb_ecrans,
        "nb_jours": jours,
        "objectif_total": cible,
        "optimise": optimise,
        "dans_tolerance": not cible or abs(ecart_pct) <= TOLERANCE * 100,
    }


def generer_scenarios(
    *, duree_passage: int, nombre_visuels: int, nb_jours: int | None,
    nb_ecrans_candidates: Sequence[int],
    objectif_total: int | None = None, objectif_par_ecran: int | None = None,
    frequences: Sequence[int] = FREQUENCES_DEFAUT, frequence_fixe: int | None = None,
    tranches_fixes: str | None = None, tranches_alternatives: Sequence[str] | None = None,
    frequence_preferee: int | None = None, tranche_reference: str | None = None,
    max_resultats: int = 50, max_par_nb_ecrans: int = 6,
) -> list[dict]:
    """
    Explore uniquement les paramètres NON fixés. Nouveautés :
      * fréquences incompatibles avec visuels x durée écartées ;
      * en mode objectif, ajout d'un scénario « durée optimisée » (minutes exactes,
        pas de 5 min) -> trouve une solution à ±5 % même si aucun preset ne tombe juste ;
      * tranches fusionnées / validées.
    """
    a_objectif = objectif_total is not None or objectif_par_ecran is not None
    if not nb_jours and not a_objectif:
        return []

    freqs = [frequence_fixe] if frequence_fixe else list(frequences)
    freqs = frequences_compatibles(freqs, duree_passage, nombre_visuels)

    brutes: list[tuple[str, str]]
    if tranches_fixes:
        brutes = [("Personnalisée", tranches_fixes)]
    elif tranches_alternatives:
        brutes = [("Créneau autorisé", t) for t in tranches_alternatives]
    else:
        brutes = list(TRANCHES_PREDEFINIES)
        if tranche_reference and all(v != tranche_reference for _, v in brutes):
            brutes.append(("Tranche actuelle", tranche_reference))
    tranches: list[tuple[str, str, int]] = []
    for label, val in brutes:
        try:
            plages = parser_tranches(val)
        except TrancheError:
            continue
        minutes = duree_minutes(plages)
        if minutes > 0:
            tranches.append((label, formater_tranches(plages), minutes))

    optimiser = a_objectif and not tranches_fixes and not tranches_alternatives
    depart = debut_tranche(tranche_reference)
    minutes_ref = duree_minutes(parser_tranches(tranche_reference)) if tranche_reference else None
    try:
        minutes_ref = minutes_ref
    except Exception:  # pragma: no cover
        minutes_ref = None

    vus: set = set()
    resultats: list[dict] = []

    def ajouter(freq, label, val, minutes, jours, ecrans, cible, optimise):
        cle = (ecrans, jours, freq, val)
        if cle in vus or minutes <= 0 or jours <= 0:
            return
        vus.add(cle)
        resultats.append(_scenario(freq, label, val, minutes, jours, ecrans,
                                   nombre_visuels, duree_passage, cible, optimise))

    for nb_ecrans in nb_ecrans_candidates:
        if nb_ecrans <= 0:
            continue
        cible = normaliser_objectif_spots(nb_ecrans, objectif_par_ecran, objectif_total)
        for freq in freqs:
            for label, val, minutes in tranches:
                if nb_jours:
                    jours_liste = [nb_jours]
                elif cible:
                    exact = cible / (spots_par_minute_tranche(freq, 1, nb_ecrans, nombre_visuels) * minutes)
                    jours_liste = sorted({max(1, math.floor(exact)), max(1, math.ceil(exact))})
                    jours_liste = [j for j in jours_liste if j <= MAX_JOURS]
                else:
                    continue
                for jours in jours_liste:
                    ajouter(freq, label, val, minutes, jours, nb_ecrans, cible, False)

            if optimiser and cible:
                if nb_jours:
                    jours_liste = [nb_jours]
                    mini = DUREE_MIN_TRANCHE
                else:
                    par_jour_max = spots_par_minute_tranche(freq, 1, nb_ecrans, nombre_visuels) * AMPLITUDE_MAX_MIN
                    par_jour_min = spots_par_minute_tranche(freq, 1, nb_ecrans, nombre_visuels) * DUREE_MIN_RECHERCHE
                    j_min = max(1, math.ceil(cible / par_jour_max))
                    j_max = min(MAX_JOURS, math.floor(cible / par_jour_min)) if par_jour_min else j_min
                    jours_liste = list(range(j_min, min(j_max, j_min + 7) + 1))
                    mini = DUREE_MIN_RECHERCHE
                for jours in jours_liste:
                    trouve = meilleure_duree(cible, freq, jours, nb_ecrans, nombre_visuels, minimum=mini)
                    if trouve:
                        m = trouve[0]
                        ajouter(freq, "Durée optimisée", tranche_depuis_minutes(m, depart),
                                m, jours, nb_ecrans, cible, True)

    if a_objectif:
        heures_ref = (minutes_ref / 60) if minutes_ref else None
        resultats.sort(key=lambda r: (
            round(r["abs_ecart"]),               # écarts < 0,5 % considérés équivalents
            r["nb_ecrans"],
            r["nb_jours"],
            abs(r["frequence"] - frequence_preferee) if frequence_preferee else 0,
            abs(r["heures_tranches"] - heures_ref) if heures_ref is not None else 0,
            r["optimise"],
            -r["frequence"],
            r["heures_tranches"],
        ))
        dans_tol = [r for r in resultats if r["dans_tolerance"]]
        return dans_tol[:max_resultats] if dans_tol else resultats[:min(max_resultats, 8)]

    resultats.sort(key=lambda r: (r["nb_ecrans"], -r["frequence"], r["heures_tranches"]))
    groupes: dict[int, list[dict]] = {}
    for r in resultats:
        g = groupes.setdefault(r["nb_ecrans"], [])
        if len(g) < max_par_nb_ecrans:
            g.append(r)
    return [r for g in groupes.values() for r in g][:max_resultats]
