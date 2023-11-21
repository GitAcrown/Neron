import discord
import re
import logging
from discord import Interaction, app_commands
from discord.ext import commands
from deep_translator import GoogleTranslator

from common.utils import pretty, fuzzy
from common import dataio

logger = logging.getLogger(f'Neron.{__name__.capitalize()}')

DEFAULT_LANG = 'french'

class Tools(commands.Cog):
    """Ensemble d'outils divers"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
        self.translate = app_commands.ContextMenu(
            name='Traduire',
            callback=self.ctx_translate_callback,
            extras={'description': "Traduit le message visé dans votre langue configurée"}
        )
        self.bot.tree.add_command(self.translate)
        
        self.__translators = {}
        self.__supported : list = GoogleTranslator().get_supported_languages() #type: ignore
        
    def __initialize_users(self):
        query = """CREATE TABLE IF NOT EXISTS users_config (
            user_id INTEGER PRIMARY KEY,
            ctxlang TEXT
            )"""
        self.data.get('global').execute(query)
        
    @commands.Cog.listener()
    async def on_ready(self):
        self.__initialize_users()
        
    # TRADUCTION ----------------------------------------------
    
    def get_user_lang(self, user: discord.User | discord.Member) -> str:
        """Récupère la langue configurée par l'utilisateur"""
        query = "SELECT ctxlang FROM users_config WHERE user_id = ?"
        lang = self.data.get('global').fetchone(query, (user.id,))
        if lang:
            return lang['ctxlang']
        return DEFAULT_LANG
    
    def set_user_lang(self, user: discord.User | discord.Member, lang: str):
        """Configure la langue de l'utilisateur"""
        query = "INSERT OR REPLACE INTO users_config VALUES (?, ?)"
        self.data.get('global').execute(query, (user.id, lang))
    
    def translate_text(self, text: str, target: str, *, source: str = 'auto') -> str:
        """Traduit un texte dans la langue donnée
        
        :param text: Texte à traduire
        :param target: Langue cible
        :param source: Langue source (auto par défaut)"""
        if source == target:
            return text
        if target not in self.__supported:
            raise ValueError(f'Langue cible `{target}` non supportée')
        if source not in self.__supported + ['auto']:
            raise ValueError(f'Langue source `{source}` non supportée')

        if f'{source}:{target}' not in self.__translators:
            self.__translators[f'{source}:{target}'] = GoogleTranslator(source=source, target=target)
        return self.__translators[f'{source}:{target}'].translate(text)
        
    async def ctx_translate_callback(self, interaction: Interaction, message: discord.Message):
        """Traduit le message visé dans la langue configurée par le serveur"""
        guild = interaction.guild
        if not guild:
            lang = DEFAULT_LANG
        else:
            lang = self.get_user_lang(interaction.user)
        
        translated = self.translate_text(message.content, lang)
        await interaction.response.send_message(translated, ephemeral=True)
        
    @app_commands.command(name='useravatar')
    @app_commands.rename(user='utilisateur')
    async def useravatar(self, interaction: Interaction, user: discord.Member | None = None):
        """Affiche les avatars d'un utilisateur
        
        :param user: Utilisateur dont afficher les avatars"""
        if not user:
            user = interaction.user #type: ignore
        if not isinstance(user, discord.Member):
            await interaction.response.send_message("**Erreur** · Cet utilisateur n'est pas sur un serveur.", ephemeral=True)
            return
        
        links = []
        text = f"Avatar(s) de {user.mention}:\n"
        if user.avatar:
            links.append(user.avatar.url)
            text += f"[Avatar]({user.avatar.url})"
        if user.guild_avatar:
            links.append(user.guild_avatar.url)
            text += f" | [Avatar de serveur]({user.guild_avatar.url})"
        if not links:
            await interaction.response.send_message("**Erreur** · Aucun avatar trouvé.", ephemeral=True)
            return
        await interaction.response.send_message(text, ephemeral=True)
            
    @app_commands.command(name='guildinfo')
    async def guildinfo(self, interaction: Interaction):
        """Affiche les informations du serveur"""
        guild = interaction.guild #type: ignore
        if not guild:
            await interaction.response.send_message("**Erreur** · Vous n'êtes pas sur un serveur.", ephemeral=True)
            return
        
        embed = discord.Embed(title=guild.name, description=guild.description, color=pretty.DEFAULT_EMBED_COLOR)
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        embed.add_field(name='Membres', value=f'{guild.member_count} membres')
        embed.add_field(name='Boosters', value=f'{guild.premium_subscription_count} boosters')
        embed.add_field(name='Propriétaire', value=guild.owner.mention if guild.owner else None)
        embed.add_field(name='Création', value=f"<t:{int(guild.created_at.timestamp())}:R>")
        if guild.banner:
            embed.set_image(url=guild.banner.url)
        await interaction.response.send_message(embed=embed)
        
    @app_commands.command(name='getemojis')
    @app_commands.rename(silent='silencieux')
    async def getemojis(self, interaction: Interaction, emojis: str, silent: bool = False):
        """Extrait l'image des emojis donnés
        
        :param emojis: Liste des emojis à extraire
        :param silent: Si true, n'affichera les liens que pour vous"""
        emojis = re.findall(r'<a?:\w+:\d+>', emojis) #type: ignore
        if not emojis:
            await interaction.response.send_message("**Erreur** · Aucun emoji valide trouvé.", ephemeral=True)
            return
        emojis = list(set(emojis)) # On retire les doublons #type: ignore
        if len(emojis) > 10:
            await interaction.response.send_message("**Erreur** · Vous ne pouvez pas extraire plus de 10 emojis à la fois.", ephemeral=True)
            return
        links = []
        for emoji in emojis:
            partial = discord.PartialEmoji.from_str(emoji)
            if partial.is_custom_emoji():
                links.append(partial.url)
        if not links:
            await interaction.response.send_message("**Erreur** · Aucun emoji valide trouvé.", ephemeral=True)
            return
        await interaction.response.send_message("\n".join(links), ephemeral=silent)
        
    @app_commands.command(name='translate')
    @app_commands.rename(text='texte', target='cible')
    async def cmd_translate(self, interaction: Interaction, text: str, target: str, source: str = 'auto'):
        """Traduit un texte dans la langue donnée
        
        :param text: Texte à traduire dans la langue cible
        :param target: Langue cible ('french' par défaut)
        :param source: Langue source (automatique par défaut)"""
        try:
            translated = self.translate_text(text, target, source=source)
        except ValueError as e:
            await interaction.response.send_message(f"**Erreur** · {e}", ephemeral=True)
            return
        await interaction.response.send_message(translated)
    
    # CONFIGURATION ----------------------------------------------
    
    config_group = app_commands.Group(name='toolsconfig', description="Configuration des outils")

    @config_group.command(name='translang')
    @app_commands.rename(lang='langue')
    async def config_translang(self, interaction: Interaction, lang: str):
        """Configure votre langue personnelle cible pour la commande contextuelle 'Traduire'
        
        :param lang: Langue à utiliser"""
        if lang not in self.__supported:
            await interaction.response.send_message(f"**Erreur** · Langue `{lang}` non supportée.", ephemeral=True)
            return
        self.set_user_lang(interaction.user, lang)
        await interaction.response.send_message(f"**Langue configurée** · Votre langue personnelle cible pour la commande contextuelle 'Traduire' est maintenant `{lang}`.", ephemeral=True)
        
    @cmd_translate.autocomplete('target')
    @config_translang.autocomplete('lang')
    async def translate_target_autocomplete(self, interaction: Interaction, current: str):
        """Autocomplétion de la langue cible"""
        all_langs = self.__supported
        r = fuzzy.finder(current, all_langs)
        return [app_commands.Choice(name=lang.capitalize(), value=lang) for lang in r][:10]
    
    @cmd_translate.autocomplete('source')
    async def translate_source_autocomplete(self, interaction: Interaction, current: str):
        """Autocomplétion de la langue source"""
        all_langs = self.__supported + ['auto']
        r = fuzzy.finder(current, all_langs)
        return [app_commands.Choice(name=lang.capitalize(), value=lang) for lang in r][:10]
                    
async def setup(bot):
    await bot.add_cog(Tools(bot))
