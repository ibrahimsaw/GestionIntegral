import re


def cle_code_naturelle(code):
    """Trie les parties numériques d'un code selon leur valeur."""
    return [
        (0, int(partie)) if partie.isdecimal() else (1, partie.casefold())
        for partie in re.split(r'(\d+)', str(code or '')) if partie
    ]


def cle_code_support(code):
    """Trie par numéro final, puis par code."""
    match = re.search(r'(\d+)\s*$', str(code or ''))
    numero = int(match.group(1)) if match else float('inf')
    return numero, cle_code_naturelle(code)
