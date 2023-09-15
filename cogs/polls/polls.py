import logging
import re
from copy import copy
from datetime import datetime, timedelta
from typing import Any, List, Optional

import discord
from discord import Interaction, app_commands
from discord.ext import commands
from tabulate import tabulate

from common.utils import pretty

logger = logging.getLogger(f'Neron.{__name__.capitalize()}')

class FastPollSelect(discord.ui.Select):
    """Menu déroulant de sélection de choix pour un sondage rapide"""
    def __init__(self, cog: 'Polls', poll_session: dict):
        super().__init__(
            placeholder="Sélectionnez votre choix",
            min_values=1,
            max_values=1,
            row=0
        )
        self.__cog = cog
        self.session = poll_session
        self.__fill_options(poll_session['choices'])

    def __fill_options(self, choices: List[str]) -> None:
        for choice in choices:
            self.add_option(label=choice.capitalize(), value=choice)
    
    async def callback(self, interaction: discord.Interaction) -> Any:
        edited = False
        for v in self.session['votes']:
            if interaction.user.id in self.session['votes'][v]:
                self.session['votes'][v].remove(interaction.user.id)
                edited = True
                
        for v in self.values:
            if interaction.user.id not in self.session['votes'][v]:
                self.session['votes'][v].append(interaction.user.id)
        
        self.session['embed_message'] = await self.session['embed_message'].edit(embed=self.__cog.get_fastpoll_embed(self.session))
        if edited:
            return await interaction.response.send_message(f"**`{self.session['title']}` ·** __Vote modifié__, merci d'avoir participé !", ephemeral=True, delete_after=10)
        return await interaction.response.send_message(f"**`{self.session['title']}` ·** __Vote pris en compte__, merci d'avoir participé !", ephemeral=True, delete_after=10)

        
class Polls(commands.Cog):
    """Divers outils de sondage"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions = {}
        
    def get_fastpoll_embed(self, data: dict, *, results: bool = False):
        """Renvoie l'embed correspondant au sondage rapide"""
        winner = None
        if results:
            winner = max(data['votes'], key=lambda x: len(data['votes'][x]))
        
        table = []
        total_votes = sum(len(votes) for votes in data['votes'].values())
        for choice, votes in data['votes'].items():
            if results:
                choice = f'+ {choice.capitalize()}' if choice == winner else f'- {choice.capitalize()}'
                table.append([choice, len(votes), pretty.bargraph(len(votes), total_votes, lenght=8, display_percent=True)])
            else:
                table.append([choice.capitalize(), len(votes), pretty.bargraph(len(votes), total_votes, lenght=8)])
        
        embed = discord.Embed(title=f"***{data['title']}***", description=pretty.codeblock(tabulate(table, tablefmt='plain'), 'diff' if results else 'css'), color=0x2b2d31)
        embed.set_author(name=f"Sondage de {data['author'].display_name}", icon_url=data['author'].display_avatar.url)
        end_time = datetime.now() + timedelta(seconds=data['timeout'])
        embed.set_footer(text=f"Fermeture du sondage · {pretty.humanize_absolute_time(end_time, assume_today=True)}")
        
        return embed
    
    @app_commands.command(name="poll")
    @app_commands.guild_only()
    @app_commands.rename(title='titre', choices='choix', timeout='expiration', pin_message='épingler')
    async def create_fast_poll(self, interaction: Interaction, title: str, choices: str, timeout: app_commands.Range[int, 30, 300] = 90, pin_message: bool = False):
        """Créer un sondage rapide

        :param title: Titre du sondage
        :param choices: Choix possibles, séparés par des virgules ou points-virgules
        :param timeout: Temps d'expiration du sondage en secondes à partir de la dernière réponse, par défaut 90s
        :param pin_message: Si le message du sondage doit être épinglé automatiquement, par défaut False
        """
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return
        if channel.id in self.sessions:
            return await interaction.response.send_message("**Sondage déjà en cours** · Attendez que le sondage en cours sur ce salon se termine avant d'en lancer un nouveau !", ephemeral=True)
        
        options = re.split(r'[,;]', choices)
        options = [option.strip() for option in options if not option.isspace() and option != '']
        if len(options) < 2:
            return await interaction.response.send_message("**Sondage invalide** · Vous devez fournir au moins deux choix valides, séparés par des virgules ou des points-virgules !", ephemeral=True)
        
        self.sessions[channel.id] = {
            'title': title,
            'choices': options,
            'votes': {option: [] for option in options},
            'embed_message': None,
            'author': interaction.user,
            'timeout': timeout
        }
        embed = self.get_fastpoll_embed(self.sessions[channel.id])
        view = discord.ui.View()
        view.add_item(FastPollSelect(self, self.sessions[channel.id]))
        view.timeout = timeout
        msg : discord.Message = await channel.send(embed=embed, view=view)
        if pin_message:
            try:
                await msg.pin()
            except discord.HTTPException:
                pass
        self.sessions[channel.id]['embed_message'] = msg
        await interaction.response.send_message(f"**Sondage créé** · Le sondage a été créé dans {channel.mention} !", ephemeral=True, delete_after=15)
        await view.wait()
        await msg.edit(embed=self.get_fastpoll_embed(self.sessions[channel.id], results=True), content="**Sondage terminé** · Voici les résultats du sondage !", view=None)
        if pin_message:
            try:
                await msg.unpin()
            except discord.HTTPException:
                pass
        del self.sessions[channel.id]
    
async def setup(bot):
    await bot.add_cog(Polls(bot))
