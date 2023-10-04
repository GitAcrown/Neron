import logging
from os import name
import random
import re
from datetime import datetime, timedelta
from typing import Any, Iterable, List

import discord
from io import BytesIO
import re
from discord import Interaction, app_commands
from discord.ext import commands
from numpy import isin
from tabulate import tabulate

from common.utils import pretty
        
class Tools(commands.GroupCog, group_name='tools', description="Ensemble d'outils divers"):
    """Ensemble d'outils divers"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
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
        embed.add_field(name='Propriétaire', value=guild.owner.mention if guild.owner else None)
        embed.add_field(name='Création', value=f"<t:{int(guild.created_at.timestamp())}:R>")
        if guild.banner:
            embed.set_image(url=guild.banner.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    @app_commands.command(name='getemojis')
    async def getemojis(self, interaction: Interaction, emojis: str, silent: bool = False):
        """Extrait l'image des emojis donnés
        
        :param emojis: Liste des emojis à extraire
        :param silent: Si true, n'affichera les liens que pour vous"""
        emojis = re.findall(r'<a?:\w+:\d+>', emojis) #type: ignore
        if not emojis:
            await interaction.response.send_message("**Erreur** · Aucun emoji valide trouvé.", ephemeral=True)
            return
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
        await interaction.response.send_message("\n".join(links), ephemeral=not silent)
                    
async def setup(bot):
    await bot.add_cog(Tools(bot))
