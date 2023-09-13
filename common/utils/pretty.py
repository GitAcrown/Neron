# Fonctions d'affichage transverses

def bargraph(value: int | float, total: int | float, *, lenght: int = 10, use_half_bar: bool = True, display_percent: bool = False) -> str:
    """Retourne un diagramme en barres

    :param value: Valeur à représenter
    :param total: Valeur maximale possible
    :param lenght: Longueur du diagramme, par défaut 10 caractères
    :param use_half_bar: S'il faut utiliser des demi-barres pour les valeurs intermédiaires, par défaut True
    :param display_percent: S'il faut afficher le pourcentage en fin de barre, par défaut False
    :return: str
    """
    if total == 0:
        return ' '
    percent = (value / total) * 100
    nb_bars = percent / (100 / lenght)
    bars = '█' * int(nb_bars)
    if (nb_bars % 1) >= 0.5 and use_half_bar:
        bars += '▌'
    if display_percent:
        bars += f' {round(percent)}%'
    return bars

def codeblock(text: str, lang: str = '') -> str:
    """Retourne le texte sous forme d'un bloc de code

    :param text: Texte à formatter
    :param lang: Langage à utiliser, par défaut "" (aucun)
    :return: str
    """
    return f"```{lang}\n{text}\n```"

def shorten_text(text: str, max_length: int, *, end: str = '...') -> str:
    """Retourne le texte raccourci

    :param text: Texte à raccourcir
    :param max_length: Longueur maximale du texte, par défaut 100 caractères
    :param end: Fin du texte, par défaut '...'
    :return: str
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(end)] + end
