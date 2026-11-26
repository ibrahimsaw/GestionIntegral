import json


def cle_tarif_ville(ville, code):
    ville = (ville or '').strip()
    return 'ville:' + json.dumps([ville, code], ensure_ascii=False, separators=(',', ':')) if ville else code


def tarif_par_ville(tarifs, ville, code, defaut=None):
    return tarifs.get(cle_tarif_ville(ville, code), tarifs.get(code, defaut))
