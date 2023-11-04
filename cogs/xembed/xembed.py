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

    @discord.ui.button(label='Annuler la preview', style=discord.ButtonStyle.danger)
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
            await self.view_message.edit(view=None, suppress=True)

class XEmbed(commands.Cog):
    """Prise en charge des liens X (a.k.a. Twitter)"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
    def __initialize_guilds(self, guilds: Iterable[discord.Guild]):
        default_settings = {
            'Enabled': 0,
            'DeleteDelay': 10,
            'Mode': 'full'
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
        text = ''
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
                
            if data.get('conversationID'):
                if data['conversationID'] != str(xeet_id):
                    original_tweet = await self.get_xeet_data(data['conversationID'])
                    if original_tweet:
                        text += f'> *{original_tweet["user_name"]}* (@{original_tweet["user_screen_name"]}) - <t:{original_tweet["date_epoch"]}:R>\n'
                        for l in original_tweet['text'].split('\n'):
                            text += f'> {l}\n'
                        text += '**... Réponse ...**\n>>> '
                        if not medias and not medias_too_big:
                            for media in original_tweet['media_extended']:
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
            if data.get('qrtURL'):
                if data['qrtURL'] != data['tweetURL']:
                    qrt_id = re.findall(r'(?:https?://(?:www\.)?(?:twitter|x)\.com/)(?:\w+/status/)?(\d+)', data['qrtURL'])
                    if qrt_id:
                        qrt_id = int(qrt_id[0])
                        qrt_tweet = await self.get_xeet_data(qrt_id)
                        if qrt_tweet:
                            text += f'> *{qrt_tweet["user_name"]}* (@{qrt_tweet["user_screen_name"]}) - <t:{qrt_tweet["date_epoch"]}:R>\n'
                            for l in qrt_tweet['text'].split('\n'):
                                text += f'> {l}\n'
                            text += '**... QRT ...**\n>>> '
                            if not medias and not medias_too_big:
                                for media in qrt_tweet['media_extended']:
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
                    
                            
            text += f'**{data["user_name"]}** (@{data["user_screen_name"]}) · <t:{data["date_epoch"]}:R>\n'
            
            if data['text']:
                text += f'{data["text"]}\n'
                
            if medias_too_big:
                text += '\n'.join([f"{'📷' if m['type'] == 'image' else '📼'} [{m['type'].capitalize()}]({m['url']})" for m in medias_too_big])
                
            likes, replies, retweets = data['likes'], data['replies'], data['retweets']
            text += f'\n❤️ `{likes}` | 💬 `{replies}` | 🔁 `{retweets}`'
            
            view_message = await message.reply(text, files=medias, mention_author=False, suppress_embeds=True)
            
            view = CancelButtonView(message, view_message, timeout=self.data.get_setting(message.guild, 'DeleteDelay', cast_as=int))
            await view_message.edit(view=view, suppress=True)
            
    def sub_xeet_link(self, message: discord.Message) -> list[str]:
        """Remplace les liens X par des liens VXTwitter et renvoie la liste des liens remplacés"""
        links = []
        new_links = []
        for word in message.content.split(' '):
            if word.startswith('http'):
                links.append(word)
        if not links:
            return []
        
        # On remplace les liens
        for link in links:
            if link.startswith('https://twitter.com/'):
                new_links.append(link.replace('https://twitter.com/', 'https://vxtwitter.com/'))
            elif link.startswith('https://x.com/'):
                new_links.append(link.replace('https://x.com/', 'https://vxtwitter.com/'))
        
        return new_links
            
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return
        if message.author.bot:
            return
        if not self.data.get_setting(message.guild, 'Enabled', cast_as=int):
            return
        
        mode = self.data.get_setting(message.guild, 'Mode')
        
        xeet_id = self.extract_xeet(message)
        if xeet_id:
            if mode == 'full':
                await self.embed_xeet(message, xeet_id)
                # On efface la preview du message du membre
                await message.edit(suppress=True)
                
            elif mode == 'embed':
                links = self.sub_xeet_link(message)
                if links:
                    view_message = await message.reply('\n'.join(links), mention_author=False)
                    view = CancelButtonView(message, view_message, timeout=self.data.get_setting(message.guild, 'DeleteDelay', cast_as=int))
                    await view_message.edit(view=view)
                    
                
    # COMMANDES ===========================
    
    config_group = app_commands.Group(name='xembed', description="Paramètres d'intégration des liens X / Twitter")

    @config_group.command(name='enable')
    @app_commands.rename(enabled='activer')
    async def xembed_enable(self, interaction: Interaction, enabled: bool):
        """Active l'intégration des liens X / Twitter
        
        :param enabled: Si true, active l'intégration"""
        self.data.update_settings(interaction.guild, {'Enabled': int(enabled)})
        await interaction.response.send_message(f"**Preview des liens X** · {'Activée' if enabled else 'Désactivée'}.", ephemeral=True)
        
    @config_group.command(name='delay')
    @app_commands.rename(delay='delai')
    async def xembed_delay(self, interaction: Interaction, delay: int):
        """Définit le délai pendant lequel la preview peut encore être annulée
        
        :param delay: Délai en secondes"""
        self.data.update_settings(interaction.guild, {'DeleteDelay': delay})
        await interaction.response.send_message(f"**Délai défini** · Vous aurez désormais {delay} secondes pour annuler l'action.", ephemeral=True)
        
    @config_group.command(name='mode')
    async def xembed_mode(self, interaction: Interaction, mode: str):
        """Définit le mode d'intégration des liens X / Twitter
        
        :param mode: Mode d'intégration (full, embed)"""
        self.data.update_settings(interaction.guild, {'Mode': mode})
        await interaction.response.send_message(f"**Mode défini** · Le mode d'intégration est désormais en `{mode}`.", ephemeral=True)
        
    @app_commands.command(name='fetchx')
    async def get_x_medias(self, interaction: Interaction, url: str):
        """Récupère les médias d'un lien X / Twitter

        :param url: Lien X / Twitter"""
        xeet_id = re.findall(r'(?:https?://(?:www\.)?(?:twitter|x)\.com/)(?:\w+/status/)?(\d+)', url)
        if not xeet_id:
            await interaction.response.send_message("**Erreur** · Votre URL n'est pas valide.", ephemeral=True)
            return
        
        xeet_id = int(xeet_id[0])
        data = await self.get_xeet_data(xeet_id)
        if not data:
            await interaction.response.send_message("**Erreur** · Impossible de récupérer les données du tweet.", ephemeral=True)
            return
        
        medias = []
        medias_too_big = []
        await interaction.response.defer()
        
        for media in data['media_extended']:
            alt_text = media.get('altText', '')
            file_type = media['type']
            async with aiohttp.ClientSession() as session:
                async with session.get(media['url']) as r:
                    if r.status == 200:
                        # Taille max 20 MB
                        s = int(r.headers['Content-Length'])
                        if s > 20 * 1024 * 1024:
                            logger.warning(f'Le média {media["url"]} est trop lourd ({s} bytes) et ne sera pas récupéré')
                            medias_too_big.append({'url': media['url'], 'type': file_type})
                            continue
                        m = BytesIO(await r.read())
                        m.seek(0)
                        # On cherche l'extension en sachant qu'il peut y avoir des tags à la fin
                        ext = re.findall(r'\.(\w+)(?:\?.*)?$', media['url'])[0]
                        medias.append(discord.File(m, description=alt_text, filename=f'{file_type}.{ext}'))
                    else:
                        logger.warning(f'Erreur lors de la récupération du média {media["url"]}')
        
        if not medias and not medias_too_big:
            return await interaction.followup.send("**Vide** · Aucun média n'a été récupéré de ce *Xeet*", ephemeral=True)
        
        text = f'**{data["user_name"]}** (@{data["user_screen_name"]})'
        if medias_too_big:
            text += "\nMédias trop lourds :"
            text += '\n'.join([f"{'📷' if m['type'] == 'image' else '📼'} [{m['type'].capitalize()}]({m['url']})" for m in medias_too_big])
        
        await interaction.followup.send(text, files=medias)
        
    @xembed_mode.autocomplete('mode')
    async def autocomplete_command(self, interaction: discord.Interaction, current: str):
        modes = [app_commands.Choice(name='Remplacement complet', value='full'), app_commands.Choice(name='Intégration vxTwitter', value='embed')]
        return modes
                            
async def setup(bot):
    await bot.add_cog(XEmbed(bot))
