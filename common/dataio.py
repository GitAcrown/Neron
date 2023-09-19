# Gestionnaire centralisé des données du bot (SQLite3)

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Type

from discord.ext import commands

COGS : dict[str, 'CogData'] = {} # Cache des modules chargés

class CogData:
    """Représente les données d'un module"""
    def __init__(self, cog_name: str):
        self.cog_name = cog_name
        self.cog_folder = Path(f'cogs/{cog_name}')
        
        self.__connections : dict[Any, 'ObjectData'] = {} # Cache des connexions aux bases de données des objets
        
    def __repr__(self) -> str:
        return f"<CogData '{self.cog_name}'>"
    
    # Bundled data ---------------------------------------------
    
    @property
    def bundled_data_path(self) -> Path:
        """Retourne le chemin du fichier de données du module ('cogs/<module>/assets')"""
        return self.cog_folder / 'assets'
    
    # Sqlite connection --------------------------------------------
        
    def __get_sqlite_connection(self, obj: Any) -> sqlite3.Connection:
        if _parse_object(obj) is None:
            raise ValueError(f"Invalid object '{obj}'")
        
        folder = self.cog_folder / "data"
        folder.mkdir(parents=True, exist_ok=True)
        db_name = _get_object_database_name(obj)
        conn = sqlite3.connect(folder / f"{db_name}.db")
        conn.row_factory = sqlite3.Row
        return conn
        
    # Data management ----------------------------------------------
    
    def get(self, obj: Any) -> 'ObjectData':
        """Retourne la base de données d'un objet"""
        if _parse_object(obj) is None:
            raise ValueError(f"Invalid object '{obj}'")
        
        if obj not in self.__connections:
            self.__connections[obj] = ObjectData(obj, self.__get_sqlite_connection(obj))
        return self.__connections[obj]
    
    def get_all(self) -> Iterable['ObjectData']:
        """Retourne toutes les bases de données de ce module"""
        return list(self.__connections.values())
    
    def close(self, obj: Any) -> None:
        """Ferme la connexion à la base de données d'un objet"""
        if _parse_object(obj) is None:
            raise ValueError(f"Invalid object '{obj}'")
        
        if obj in self.__connections:
            self.__connections[obj].connection.close()
            del self.__connections[obj]
    
    def close_all(self) -> None:
        """Ferme toutes les connexions liées à ce module"""
        for obj in self.__connections:
            self.__connections[obj].connection.close()
            
    def delete(self, obj: Any) -> None:
        """Supprime les bases de données d'un objet"""
        if _parse_object(obj) is None:
            raise ValueError(f"Invalid object '{obj}'")
        
        if obj in self.__connections:
            self.__connections[obj].connection.close()
            db_name = _get_object_database_name(obj)
            os.remove(self.cog_folder / f"data/{db_name}.db")
            del self.__connections[obj]
            
    def delete_all(self) -> None:
        """Supprime toutes les bases de données de ce module"""
        for obj in self.__connections:
            self.__connections[obj].connection.close()
            db_name = _get_object_database_name(obj)
            os.remove(self.cog_folder / f"data/{db_name}.db")
        self.__connections.clear()
        
    # Utils --------------------------------------------------------
    
    def bulk_initialize(self, objects: Iterable[Any], queries: Iterable[str]) -> None:
        """Execute une série de requêtes SQL sur plusieurs objets
        
        :param objects: Itérable d'objets concernés
        :param queries: Itérable de requêtes SQL à exécuter
        """
        for obj in objects:
            if _parse_object(obj) is None:
                raise ValueError(f"Invalid object '{obj}'")
            
            for query in queries:
                self.get(obj).execute(query, commit=False)
            self.get(obj).commit()
            
    # Config shortcuts ----------------------------------------------
    # Ce sont des formats de table couramment utilisés dans les modules pour stocker des paramètres souvent à l'échelle du serveur
                
    def build_settings_table(self, objects: Iterable[Any], default_settings: dict[str, Any]) -> None:
        """Crée une table au format clé-valeur pour chaque objet et initialise les paramètres par défaut

        :param objects: Itérable d'objets concernés
        :param default_settings: Paramètres par défaut
        """
        query = """CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"""
        for obj in objects:
            if _parse_object(obj) is None:
                raise ValueError(f"Invalid object '{obj}'")
            
            self.get(obj).execute(query, commit=False)
            self.get(obj).executemany("INSERT OR IGNORE INTO settings VALUES (?, ?)", [(key, str(value)) for key, value in default_settings.items()], commit=False)
            self.get(obj).commit()
            
    def get_all_settings(self, obj: Any) -> dict[str, Any]:
        """Retourne tous les paramètres d'un objet sous la forme d'un dictionnaire clé-valeur
        
        :param obj: Objet concerné"""
        if _parse_object(obj) is None:
            raise ValueError(f"Invalid object '{obj}'")

        r = self.get(obj).fetchall("SELECT * FROM settings")
        return {row['key']: row['value'] for row in r}
    
    def get_setting(self, obj: Any, key: str, *, cast_as: Type[Any] = str) -> Any:
        """Retourne la valeur d'un paramètre d'un objet
        
        :param obj: Objet concerné
        :param key: Clé du paramètre
        :param cast_as: Transforme la valeur en le type spécifié"""
        if _parse_object(obj) is None:
            raise ValueError(f"Invalid object '{obj}'")
        
        r = self.get(obj).fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return cast_as(r['value']) if r is not None else None
    
    def update_settings(self, obj: Any, settings: dict[str, Any]) -> None:
        """Met à jour les paramètres d'un objet

        :param obj: Objet concerné
        :param settings: Paramètres à mettre à jour
        """
        if _parse_object(obj) is None:
            raise ValueError(f"Invalid object '{obj}'")
        
        self.get(obj).executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)", [(key, str(value)) for key, value in settings.items()])
    
    
class ObjectData:
    """Représente les données d'un objet spécifique (serveur, salon, custom, etc.)"""
    def __init__(self, obj: Any, conn: sqlite3.Connection):
        self.obj = obj
        self.connection = conn
    
    def __repr__(self) -> str:
        return f"<ObjectData '{self.obj}'>"
    
    # Data management ----------------------------------------------
    
    def execute(self, query: str, *args, commit: bool = True) -> None:
        """Exécute une requête SQL"""
        with closing(self.connection.cursor()) as cursor:
            cursor.execute(query, *args)
        if commit:
            self.connection.commit()
        
    def executemany(self, query: str, *args, commit: bool = True) -> None:
        """Exécute une requête SQL avec plusieurs jeux de données"""
        with closing(self.connection.cursor()) as cursor:
            cursor.executemany(query, *args)
        if commit:
            self.connection.commit()
    
    def fetchone(self, query: str, *args) -> dict[str, Any] | None:
        """Exécute une requête SQL et retourne la première ligne"""
        with closing(self.connection.cursor()) as cursor:
            cursor.execute(query, *args)
            r = cursor.fetchone()
        return dict(r) if r is not None else None
    
    def fetchall(self, query: str, *args) -> list[dict[str, Any]]:
        """Exécute une requête SQL et retourne toutes les lignes"""
        with closing(self.connection.cursor()) as cursor:
            cursor.execute(query, *args)
            r = cursor.fetchall()
        return [dict(row) for row in r]
    
    def commit(self) -> None:
        """Enregistre manuellement les modifications dans la base de données"""
        self.connection.commit()
        
    def rollback(self) -> None:
        """Annule manuellement les modifications dans la base de données"""
        self.connection.rollback()
    
    # Utils --------------------------------------------------------
    
    @property
    def size(self) -> int:
        """Retourne la taille de la base de données en octets"""
        r = self.fetchone("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")
        return r['page_count * page_size'] if r is not None else 0
    
    @property
    def tables(self) -> list[str]:
        """Retourne les noms des tables de la base de données"""
        r = self.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
        return [row['name'] for row in r]
    
    
# Accès aux données =======================================================
    
def get_cog_data(cog: commands.Cog | str) -> CogData:
    """Renvoie les données d'un module sous la forme d'un objet CogData"""
    cog_name = cog if isinstance(cog, str) else cog.qualified_name
    if cog_name not in COGS:
        COGS[cog_name] = CogData(cog_name)
    return COGS[cog_name]

def get_total_data_size(cog: commands.Cog | str) -> int:
    """Renvoie la taille estimée totale des données d'un module en octets"""
    cog_data = get_cog_data(cog)
    return sum([data.size for data in cog_data.get_all()])

# Utils -------------------------------------------------------------------

def _parse_object(obj: Any) -> str | None:
    """Renvoie une chaîne de caractères représentant un objet ou None si l'objet n'est pas valide"""
    if isinstance(obj, (int, str)):
        return str(obj)
    elif hasattr(obj, 'id'):
        return f"{obj.__class__.__name__}_{obj.id}"
    return None
    
def _get_object_database_name(obj: Any) -> str:
    """Renvoie le nom normalisé de la base de données d'un objet"""
    name = _parse_object(obj)
    if name is None:
        raise ValueError(f"Invalid object '{obj}'")
    return name.lower()
