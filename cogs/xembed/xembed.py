import re
from typing import Iterable
from io import BytesIO

import discord
import logging
import aiohttp
from discord import Interaction, app_commands
from discord.ext import commands

from common import dataio

logger = logging.getLogger(f'Neron.{__name__.capitalize()}')

API_ENDPOINT = 'https://api.vxtwitter.com/Twitter/status'

class CancelButtonView(discord.ui.View):
    """Ajoute un bouton permettant d'annuler la preview et restaurer celle du message original"""
    def __init__(self, xeet_message: discord.Message, view_message: discord.Message, *, timeout: float | None = 10):
        super().__init__(timeout=timeout)
        self.xeel_message = xeet_message
        self.view_message = view_message
        self.cancelled = False

    @discord.ui.button(label='Annuler la prévisualisation', style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: Interaction, button: discord.ui.Button):
        self.cancelled = True
        # On efface ce message
        await self.view_message.delete()
        # On restaure le message original
        await self.xeel_message.edit(suppress=False)

    async def interaction_check(self, interaction: Interaction):
        if interaction.user != self.xeel_message.author:
            await interaction.response.send_message('Seul l\'auteur du message peut annuler la prévisualisation.', ephemeral=True)
            return False
        return True
    
    async def on_timeout(self):
        if not self.cancelled:
            # On efface que le bouton
            await self.view_message.edit(view=None)

class XEmbed(commands.Cog):
    """Prise en charge des liens X (a.k.a. Twitter)"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
    def __initialize_guilds(self, guilds: Iterable[discord.Guild]):
        default_settings = {
            'Enabled': 0
        }
        self.data.build_settings_table(guilds, default_settings)
        
    @commands.Cog.listener()
    async def on_ready(self):
        self.__initialize_guilds(self.bot.guilds)
        
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self.__initialize_guilds([guild])
        
    # API ---------------------------------
    
    async def get_xeet_data(self, xeet_id: str | int):
        """Récupère les données d'un tweet via l'API VXTwitter"""
        async with aiohttp.ClientSession() as session:
            async with session.get(f'{API_ENDPOINT}/{xeet_id}') as r:
                if r.status == 200:
                    return await r.json()
                else:
                    return None
        
    def extract_xeet(self, message: discord.Message) -> int:
        """Renvoie l'ID du tweet si le message contient un lien X, sinon None"""
        xeet = re.findall(r'(?:https?://(?:www\.)?(?:twitter|x)\.com/)(?:\w+/status/)?(\d+)', message.content)
        if xeet:
            return int(xeet[0])
        return 0
    
    # Affichage ---------------------------
    
    async def embed_xeet(self, message: discord.Message, xeet_id: str | int):
        """Affiche les données d'un tweet sous la forme d'un message classique"""
        text = '>>> '
        medias = []
        medias_too_big = []
        data = await self.get_xeet_data(xeet_id)
        if not data:
            return

        channel = message.channel
        
        async with channel.typing():
            # On récupère les médias en local
            for media in data['media_extended']:
                alt_text = media.get('altText', '')
                file_type = media['type']
                async with aiohttp.ClientSession() as session:
                    async with session.get(media['url']) as r:
                        if r.status == 200:
                            # Taille max 20 MB
                            s = int(r.headers['Content-Length'])
                            if s > 20 * 1024 * 1024:
                                logger.warning(f'Le média {media["url"]} est trop lourd ({s} bytes) et ne sera pas uploadé')
                                medias_too_big.append({'url': media['url'], 'type': file_type})
                                continue
                            m = BytesIO(await r.read())
                            m.seek(0)
                            # On cherche l'extension en sachant qu'il peut y avoir des tags à la fin
                            ext = re.findall(r'\.(\w+)(?:\?.*)?$', media['url'])[0]
                            medias.append(discord.File(m, description=alt_text, filename=f'{file_type}.{ext}'))
                        else:
                            logger.warning(f'Erreur lors de la récupération du média {media["url"]}')
                            
            text += f'**{data["user_name"]}** (@{data["user_screen_name"]}) - <t:{data["date_epoch"]}:R>\n'
            
            if data['text']:
                text += f'{data["text"]}\n'
                
            if medias_too_big:
                text += '\n'.join([f"{'📷' if m['type'] == 'image' else '📼'} [{m['type'].capitalize()}]({m['url']})" for m in medias_too_big])
                
            likes, replies, retweets = data['likes'], data['replies'], data['retweets']
            text += f'\n❤️ `{likes}` | 💬 `{replies}` | 🔁 `{retweets}`'
            
            view_message = await message.reply(text, files=medias, mention_author=False)
            
            view = CancelButtonView(message, view_message)
            await view_message.edit(view=view)
            
            
            
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return
        if message.author.bot:
            return
        if not self.data.get_setting(message.guild, 'Enabled', cast_as=int):
            return
        
        xeet_id = self.extract_xeet(message)
        if xeet_id:
            await self.embed_xeet(message, xeet_id)
            # On efface la preview du message du membre
            await message.edit(suppress=True)
            
    # COMMANDES ===========================
    
    config_group = app_commands.Group(name='xembed', description="Paramètres d'intégration des liens X / Twitter")

    @config_group.command(name='enable')
    @app_commands.rename(enabled='activer')
    async def enable(self, interaction: Interaction, enabled: bool):
        """Active l'intégration des liens X / Twitter
        
        :param enabled: Si true, active l'intégration"""
        self.data.update_settings(interaction.guild, {'Enabled': int(enabled)})
        await interaction.response.send_message(f"{'Activation' if enabled else 'Désactivation'} effectuée avec succès.", ephemeral=True)
                            
async def setup(bot):
    await bot.add_cog(XEmbed(bot))
