import logging
import random
import re
from datetime import datetime, timedelta
from typing import Any, Iterable, List

import discord
from discord import Interaction, app_commands
from discord.ext import commands
from tabulate import tabulate

from common.utils import pretty
from common import dataio

logger = logging.getLogger(f'Neron.{__name__.capitalize()}')

WEBHOOK_DEFAULT = {
    'name': 'Sorties',
    'avatar': 'https://i.imgur.com/d11TTS8.png'
}
        
class Exit(commands.Cog):
    """Suivi des départs des membres du serveur"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
    def __initialize_guilds(self, guilds: Iterable[discord.Guild]):
        default_settings = {
            'Enabled': 0,
            'WebhookURL': '',
            'WebhookName': WEBHOOK_DEFAULT['name'],
            'WebhookAvatar': WEBHOOK_DEFAULT['avatar']
        }
        self.data.build_settings_table(guilds, default_settings)
        
    @commands.Cog.listener()
    async def on_ready(self):
        self.__initialize_guilds(self.bot.guilds)
        
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self.__initialize_guilds([guild])
    
    # Webhook -----------------------------------------------------------------
    
    def get_guild_webhook(self, guild: discord.Guild) -> discord.Webhook | None:
        webhook_url = self.data.get_setting(guild, 'WebhookURL')
        if webhook_url:
            return discord.Webhook.from_url(webhook_url, client=self.bot)
        return None
    
    async def send_webhook(self, guild: discord.Guild, message: str):
        webhook = self.get_guild_webhook(guild)
        name = self.data.get_setting(guild, 'WebhookName')
        avatar = self.data.get_setting(guild, 'WebhookAvatar')
        if webhook:
            await webhook.send(message, username=name, avatar_url=avatar)
        
    # Départs -----------------------------------------------------------------
    
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        if self.data.get_setting(guild, 'Enabled'):
            name = f'@**{member.name}**' if not member.nick else f'@**{member.name}** ({member.nick})'
            message = random.choice([
                f"{name} a quitté le serveur.",
                f"{name} est reparti.e dans la fosse aux randoms.",
                f"{name} a préféré refaire sa vie ailleurs.",
                f"{name} a pris la poudre d'escampette.",
                f"{name} a décidé de nous quitter.",
                f"{name} a quitté le navire.",
                f"Drama dans la villa. {name} a quitté l'aventure.",
                f"Au revoir {name}...",
                f"Bye bye {name} !",
                f"Adieu {name} !",
                f"À bientôt {name} !",
                f"À la prochaine {name} !",
                f"À la revoyure {name} !"
            ])
            await self.send_webhook(guild, message)
        
    # Commandes ---------------------------------------------------------------
    
    settings_group = app_commands.Group(name='exit', description='Paramètres du suivi des départs', default_permissions=discord.Permissions(manage_guild=True), guild_only=True)
    
    @settings_group.command(name='enable')
    @app_commands.rename(enabled='activer')
    async def enable(self, interaction: Interaction, enabled: bool):
        """Active ou désactive le suivi des départs

        :param enabled: Active ou désactive le suivi des départs
        """
        guild = interaction.guild
        self.data.update_settings(guild, {'Enabled': int(enabled)})
        await interaction.response.send_message(f"Le suivi des départs est maintenant **{'activé' if enabled else 'désactivé'}**.", ephemeral=True)
    
    @settings_group.command(name='webhook')
    async def webhook(self, interaction: Interaction, url: str):
        """Définit l'URL du webhook à utiliser pour les départs

        :param url: URL du webhook
        """
        guild = interaction.guild
        self.data.update_settings(guild, {'WebhookURL': url})
        await interaction.response.send_message(f"L'URL du webhook a été mise à jour.", ephemeral=True)
        
    @settings_group.command(name='name')
    async def webhook_name(self, interaction: Interaction, name: str):
        """Définit le nom qui doit être utilisé par le webhook pour les départs

        :param name: Nom du webhook
        """
        guild = interaction.guild
        self.data.update_settings(guild, {'WebhookName': name})
        await interaction.response.send_message(f"Le nom du webhook a été mis à jour pour **{name}**.", ephemeral=True)
        
    @settings_group.command(name='avatar')
    async def webhook_avatar(self, interaction: Interaction, avatar: str):
        """Définit l'avatar qui doit être utilisé par le webhook pour les départs

        :param avatar: URL de l'avatar
        """
        guild = interaction.guild
        self.data.update_settings(guild, {'WebhookAvatar': avatar})
        await interaction.response.send_message(f"L'avatar du webhook a été mis à jour pour **<{avatar}>**.", ephemeral=True)
        
    @settings_group.command(name='help')
    async def webhook_help(self, interaction: Interaction):
        """Affiche de l'aide pour créer un webhook et configurer le suivi des départs"""
        txt = f"""
        Pour créer un webhook, rendez-vous dans les paramètres du salon dans lequel vous souhaitez recevoir les notifications de départs puis allez dans le volet "Intégrations".
        Cliquez ensuite sur "Webhooks" puis "Nouveau webhook". Le nom et l'avatar n'a pas d'importance : cliquez sur "Copier l'URL du webhook" et fermez la fenêtre.
        Collez ensuite cet url dans la commande `/exit webhook` pour l'enregistrer.
        """
        em = discord.Embed(title="Créer un webhook", description=txt, color=pretty.DEFAULT_EMBED_COLOR)
        em.set_thumbnail(url=WEBHOOK_DEFAULT['avatar'])
        await interaction.response.send_message(embed=em, ephemeral=True)
            
async def setup(bot):
    await bot.add_cog(Exit(bot))
