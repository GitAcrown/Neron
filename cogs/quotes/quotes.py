import logging
from pathlib import Path
import numpy as np
import re
import os
import textwrap
from io import BytesIO
from typing import List, Optional, Tuple

import aiohttp
import moviepy.editor as mp
import cv2
import ffmpeg
import colorgram
import discord
from discord import Interaction, app_commands
from discord.components import SelectOption
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from common import dataio
from common.utils import pretty

logger = logging.getLogger(f'Neron.{__name__.capitalize()}')

class QuotifyMessageSelect(discord.ui.Select):
    """Menu déroulant pour sélectionner les messages à citer"""
    def __init__(self, view: 'QuotifyView', placeholder: str, options: List[discord.SelectOption]):
        super().__init__(placeholder=placeholder, 
                         min_values=1, 
                         max_values=min(len(options), 5), 
                         options=options)
        self.__view = view
        
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if sum([len(m.clean_content) for m in self.__view.selected_messages]) > 1000:
            return await interaction.followup.send("**Action impossible** · Le message est trop long", ephemeral=True)
        
        self.__view.selected_messages = [m for m in self.__view.potential_messages if m.id in [int(v) for v in self.values]]
        self.options = [SelectOption(label=f"{pretty.shorten_text(m.clean_content, 100)}", value=str(m.id), description=m.created_at.strftime('%H:%M %d/%m/%y'), default=str(m.id) in self.values) for m in self.__view.potential_messages]
        image = await self.__view._get_image()
        if not image:
            return await interaction.followup.send("**Erreur** · Impossible de créer l'image de la citation", ephemeral=True)
        await interaction.edit_original_response(view=self.__view, attachments=[image])


class QuotifyView(discord.ui.View):
    """Menu de création de citation afin de sélectionner les messages à citer"""
    def __init__(self, cog: 'Quotes', initial_message: discord.Message, *, timeout: float | None = 180):
        super().__init__(timeout=timeout)
        self.__cog = cog
        self.initial_message = initial_message
        self.potential_messages = []
        self.selected_messages = [initial_message]
        
        self.interaction : Interaction | None = None
        
    async def interaction_check(self, interaction: discord.Interaction):
        if not self.interaction:
            return False
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("**Action impossible** · Seul l'auteur du message initial peut utiliser ce menu", ephemeral=True)
            return False
        return True
    
    async def on_timeout(self):
        new_view = discord.ui.View()
        message_url = self.selected_messages[0].jump_url
        new_view.add_item(discord.ui.Button(label="Source", url=message_url, style=discord.ButtonStyle.link))
        if self.interaction:
            await self.interaction.edit_original_response(view=new_view)
            
    async def start(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        potential_msgs = await self.__cog.fetch_following_messages(self.initial_message)
        self.potential_messages = sorted(potential_msgs, key=lambda m: m.created_at)
        if len(self.potential_messages) > 1:
            options = [SelectOption(label=f"{pretty.shorten_text(m.clean_content, 100)}", value=str(m.id), description=m.created_at.strftime('%H:%M %d/%m/%y'), default= m == self.initial_message) for m in self.potential_messages]
            self.add_item(QuotifyMessageSelect(self, "Sélectionnez les messages à citer", options))
        
        image = await self._get_image()
        if not image:
            return await interaction.followup.send("**Erreur** · Impossible de créer l'image de la citation", ephemeral=True)
        await interaction.followup.send(view=self, file=image)
        self.interaction = interaction

    async def _get_image(self) -> Optional[discord.File]:
        try:
            return await self.__cog.quote_messages(self.selected_messages)
        except Exception as e:
            logger.exception(e)
            if self.interaction:
                await self.interaction.edit_original_response(content=str(e), view=None)
            return None
        
    @discord.ui.button(label="Enregistrer", style=discord.ButtonStyle.green, row=1)
    async def save_quit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        new_view = discord.ui.View()
        message_url = self.selected_messages[0].jump_url
        new_view.add_item(discord.ui.Button(label="Source", url=message_url, style=discord.ButtonStyle.link))
        if self.interaction:
            await self.interaction.edit_original_response(view=new_view)
        self.stop()
        
    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.red, row=1)
    async def quit(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        if self.interaction:
            await self.interaction.delete_original_response()
            
# COG ========================================================================
            
class Quotes(commands.Cog):
    """Citations aléatoires et customisées"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = dataio.get_cog_data(self)
        
        self.quotify = app_commands.ContextMenu(
            name='Imager ce message',
            callback=self.message_menu_quotify,
            extras={'description': "Créer une image ou une vidéo avec le contenu du message"}
        )
        self.bot.tree.add_command(self.quotify)
        
        self.__busy = False
        self.__assets = {}
        self.__load_assets()
        
    def __load_assets(self):
        """Charge en amont les assets du cog"""
        self.__assets['icon_white'] = Image.open(str(self.data.bundled_data_path / "quotes_white_A.png")).convert("RGBA")
        self.__assets['icon_black'] = Image.open(str(self.data.bundled_data_path / "quotes_black_A.png")).convert("RGBA")
        self.__assets['musical_note_white'] = Image.open(str(self.data.bundled_data_path / "musical-note_white.png")).convert("RGBA")
        self.__assets['musical_note_black'] = Image.open(str(self.data.bundled_data_path / "musical-note.png")).convert("RGBA")
        
        # On crée un dossier temp pour stocker temporairment les fichiers audio créés
        temp_folder = self.data.cog_folder / "temp"
        if not temp_folder.exists():
            temp_folder.mkdir()
        self.__temp_folder = temp_folder
        
    def cog_unload(self):
        # On supprime les fichiers temporaires
        for file in self.__temp_folder.iterdir():
            os.remove(file)
    
    @app_commands.command(name='quote')
    @app_commands.checks.cooldown(1, 600)
    async def get_inspirobot_quote(self, interaction: Interaction):
        """Obtenir une citation aléatoire depuis Inspirobot.me"""
        await interaction.response.defer()
    
        async def fetch_inspirobot_quote():
            async with aiohttp.ClientSession() as session:
                async with session.get("http://inspirobot.me/api?generate=true") as page:
                    return await page.text()
                
        url = await fetch_inspirobot_quote()
        if not url:
            return await interaction.followup.send("Impossible d'obtenir une image depuis Inspirobot.me", ephemeral=True)
        
        image = aiohttp.ClientSession()
        async with image.get(url) as resp:
            if resp.status != 200:
                return await interaction.followup.send("Impossible d'obtenir une image depuis Inspirobot.me", ephemeral=True)
            data = BytesIO(await resp.read())
            await image.close()
        
        await interaction.followup.send(file=discord.File(data, 'quote.png'))
        
    async def message_menu_quotify(self, interaction: Interaction, message: discord.Message):
        """Menu de création de citation"""
        if message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith('audio/'):
                    if self.__busy:
                        return await interaction.response.send_message("**Action impossible** · Le bot est déjà occupé à créer une citation vidéo, veuillez réessayer plus tard", ephemeral=True)
                    self.__busy = True
                    await interaction.response.defer()
                    try:
                        file, data = await self.audio_quote_msg_attachment(message)
                        view = discord.ui.View()
                        view.add_item(discord.ui.Button(label="Source", url=message.jump_url, style=discord.ButtonStyle.link))
                        await interaction.followup.send(file=file, view=view)
                        # On supprime les fichiers temporaires
                        for path in data.values():
                            os.remove(path)
                        self.__busy = False
                        return 
                    except commands.CommandError as e:
                        await interaction.followup.send(f"**Erreur** · Impossible de créer la vidéo de la citation\n{e}", ephemeral=True)
                        self.__busy = False
                        return
                    except Exception as e:
                        logger.exception(e)
                        await interaction.followup.send(f"**Erreur** · Impossible de créer la vidéo de la citation\n{e}", ephemeral=True)
                        self.__busy = False
                        return
                    
        if not message.content or message.content.isspace():
            return await interaction.response.send_message("**Action impossible** · Le message sélectionné ne contient pas de texte", ephemeral=True)
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message("**Action impossible** · Le message sélectionné n'est pas dans un salon textuel", ephemeral=True)
        try:
            view = QuotifyView(self, message, timeout=20)
            await view.start(interaction)
        except Exception as e:
            logger.exception(e)
            return await interaction.edit_original_response(content=f"**Erreur** · Impossible de créer le menu de citation, veuillez réessayer plus tard\n{e}", view=None)
        
    # Quotify ---------------------------------------------------------------

    def _add_gradient(self, image: Image.Image, gradient_magnitude=1.0, color: Tuple[int, int, int]=(0, 0, 0)):
        im = image
        if im.mode != 'RGBA':
            im = im.convert('RGBA')
        width, height = im.size
        y, _ = np.indices((height, width))
        alpha = (gradient_magnitude * y / height * 255).astype(np.uint8)
        alpha = np.minimum(alpha, 255)
        black_im = Image.new('RGBA', (width, height), color=color)
        black_im.putalpha(Image.fromarray(alpha))
        gradient_im = Image.alpha_composite(im, black_im)
        return gradient_im

    def create_quote_image(self, avatar: str | BytesIO, text: str, author_name: str, date: str, *, size: tuple[int, int] = (512, 512)):
        """Crée une image de citation avec un avatar, un texte, un nom d'auteur et une date."""
        text = text.upper()

        w, h = size
        box_w, _ = int(w * 0.92), int(h * 0.72)
        image = Image.open(avatar).convert("RGBA").resize(size)

        font_path = str(self.data.bundled_data_path / "NotoBebasNeue.ttf")
        bg_color = colorgram.extract(image.resize((50, 50)), 1)[0].rgb 
        grad_magnitude = 0.85 + 0.05 * (len(text) // 100)
        image = self._add_gradient(image, grad_magnitude, bg_color)
        luminosity = (0.2126 * bg_color[0] + 0.7152 * bg_color[1] + 0.0722 * bg_color[2]) / 255

        text_size = int(h * 0.08)
        text_font = ImageFont.truetype(font_path, text_size, encoding='unic')
        draw = ImageDraw.Draw(image)
        text_color = (255, 255, 255) if luminosity < 0.5 else (0, 0, 0)

        # Texte principal --------
        max_lines = len(text) // 60 + 2 if len(text) > 200 else 4
        wrap_width = int(box_w / (text_font.getlength("A") * 0.85))
        lines = textwrap.fill(text, width=wrap_width, max_lines=max_lines, placeholder="§")
        while lines[-1] == "§":
            text_size -= 2
            text_font = ImageFont.truetype(font_path, text_size, encoding='unic')
            wrap_width = int(box_w / (text_font.getlength("A") * 0.85))
            lines = textwrap.fill(text, width=wrap_width, max_lines=max_lines, placeholder="§")
        draw.multiline_text((w / 2, h * 0.835), lines, font=text_font, spacing=0.25, align='center', fill=text_color, anchor='md')

        # Icone et lignes ---------
        icon = self.__assets['icon_white'] if luminosity < 0.5 else self.__assets['icon_black']
        icon_image = icon.resize((int(w * 0.06), int(w * 0.06)))
        icon_left = w / 2 - icon_image.width / 2
        image.paste(icon_image, (int(icon_left), int(h * 0.85 - icon_image.height / 2)), icon_image)

        author_font = ImageFont.truetype(font_path, int(h * 0.060), encoding='unic')
        draw.text((w / 2,  h * 0.95), author_name, font=author_font, fill=text_color, anchor='md', align='center')

        draw.line((icon_left - w * 0.25, h * 0.85, icon_left - w * 0.02, h * 0.85), fill=text_color, width=1) # Ligne de gauche
        draw.line((icon_left + icon_image.width + w * 0.02, h * 0.85, icon_left + icon_image.width + w * 0.25, h * 0.85), fill=text_color, width=1) # Ligne de droite

        # Date -------------------
        date_font = ImageFont.truetype(font_path, int(h * 0.040), encoding='unic')
        draw.text((w / 2,  h * 0.985), date, font=date_font, fill=text_color, anchor='md', align='center')

        return image
    
    # Audio Quotify ---------------------------------------------------------
    
    def create_video_quote(self, audio_path: str, avatar: str | BytesIO, author_name: str, date: str, *, size: tuple[int, int] = (512, 512)):
        """Crée une image de citation avec un avatar, un texte, un nom d'auteur et une date."""
        w, h = size
        image = Image.open(avatar).convert("RGBA").resize(size)
        font_path = str(self.data.bundled_data_path / "NotoBebasNeue.ttf")
        bg_color = colorgram.extract(image.resize((50, 50)), 1)[0].rgb 
        grad_magnitude = 0.7
        image = self._add_gradient(image, grad_magnitude, bg_color)
        luminosity = (0.2126 * bg_color[0] + 0.7152 * bg_color[1] + 0.0722 * bg_color[2]) / 255
        draw = ImageDraw.Draw(image)
        
        # On veut créer une couleur légèrement plus foncée que la couleur de fond
        darker_bg_color = tuple([int(c * 0.8) for c in bg_color])
        
        # Nom de l'auteur
        text_color = (255, 255, 255) if luminosity < 0.5 else (0, 0, 0)
        author_font = ImageFont.truetype(font_path, int(h * 0.055), encoding='unic')
        draw.text((w / 2,  h * 0.95), author_name, font=author_font, fill=text_color, anchor='md', align='center')
        
        # Lignes
        # On fait une seule ligne fine au dessus du nom de l'auteur
        draw.line((w * 0.25, h * 0.85, w * 0.75, h * 0.85), fill=text_color, width=1)
            
        # Date
        date_font = ImageFont.truetype(font_path, int(h * 0.035), encoding='unic')
        draw.text((w / 2,  h * 0.98), date, font=date_font, fill=text_color, anchor='md', align='center')
        
        # Icone
        icon_note : Image.Image = self.__assets['musical_note_white'] if luminosity < 0.5 else self.__assets['musical_note_black']
        icon_note = icon_note.resize((int(w * 0.2), int(w * 0.2)))
        # On met l'icone au milieu de l'image un peu en bas
        icon_left = w / 2 - icon_note.width / 2
        image.paste(icon_note, (int(icon_left), int(h * 0.65 - icon_note.height / 2)), icon_note)
        
        # Images avec barre de progression
        bg = cv2.cvtColor(np.array(image), cv2.COLOR_BGR2RGB)[..., ::-1]
        duration = float(ffmpeg.probe(audio_path)['format']['duration'])
        fps = 1
        images = []
        nb = round(duration * fps)
        # On veut que la barre se trouve à peu près au milieu de l'image
        mw, mh = int(w * 0.2), int(h * 0.2)
        bar_width = int(h * 0.01)
        bar_length = w - 2 * mw
        # On crée les images
        for i in range(nb):
            im = bg.copy()
            if i == nb - 1:
                cv2.rectangle(im, (mw, h - mh), (mw + bar_length, h - mh - bar_width), text_color, -1)
                images.append(im)
                break
            # On dessine la barre (centre bas de l'image) (on veut qu'elle soit 50% transparente)
            cv2.rectangle(im, (mw, h - mh), (mw + bar_length, h - mh - bar_width), darker_bg_color, -1)
            # On dessine la progression de la barre (% de progression de la musique par rapport à la durée totale)
            cv2.rectangle(im, (mw, h - mh), (mw + int(bar_length * (i / nb)), h - mh - bar_width), text_color, -1)
            images.append(im)
        clip = mp.ImageSequenceClip(images, fps=fps)
        clip = clip.set_audio(mp.AudioFileClip(audio_path))
        
        file_name = f"{date.replace('/','-')}.mp4"
        file_path = str(self.__temp_folder / file_name)
        clip.write_videofile(file_path, fps=fps)
        return file_path
        
    async def fetch_following_messages(self, starting_message: discord.Message, messages_limit: int = 5, lenght_limit: int = 1000) -> list[discord.Message]:
        """Ajoute au message initial les messages suivants jusqu'à atteindre la limite de caractères ou de messages"""
        messages = [starting_message]
        total_length = len(starting_message.content)
        async for message in starting_message.channel.history(limit=25, after=starting_message):
            if not message.content or message.content.isspace():
                continue
            if message.author != starting_message.author:
                continue
            total_length += len(message.content)
            if total_length > lenght_limit:
                break
            messages.append(message)
            if len(messages) >= messages_limit:
                break
        return messages
    
    def normalize_text(self, text: str) -> str:
        """Effectue des remplacements de texte pour éviter les problèmes d'affichage"""
        # On remplace les codes d'emojis par leurs noms
        text = re.sub(r'<a?:(\w+):\d+>', r':\1:', text)
        # On retire les balises de formatage markdown
        text = re.sub(r'(\*|_|`|~|\\)', r'', text)
        
        return text
    
    async def quote_messages(self, messages: list[discord.Message]) -> discord.File:
        messages = sorted(messages, key=lambda m: m.created_at)
        avatar = BytesIO(await messages[0].author.display_avatar.read())
        message_date = messages[0].created_at.strftime("%d/%m/%y")
        full_content = ' '.join([self.normalize_text(m.clean_content) for m in messages])
        try:
            author_name = f"@{messages[0].author.name}" if messages[0].author.name.lower() == messages[0].author.display_name.lower() else f"{messages[0].author.display_name} (@{messages[0].author.name})"
            image = self.create_quote_image(avatar, full_content, author_name, message_date, size=(700, 700))
        except Exception as e:
            logger.exception(e)
            raise commands.CommandError(f"Impossible de créer l'image de la citation : {e}")
        with BytesIO() as buffer:
            image.save(buffer, format='PNG')
            buffer.seek(0)
            alt_text = f"\"{full_content}\" - @{messages[0].author.name} ({message_date})"
            return discord.File(buffer, filename='quote.png', description=alt_text)
        
    async def audio_quote_msg_attachment(self, message: discord.Message) -> Tuple[discord.File, dict[str, str | Path]]:
        """Crée une vidéo de citation à partir d'un message contenant un fichier audio"""
        if not message.attachments:
            raise commands.CommandError("Le message ne contient pas de fichier audio")
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith('audio/'):
                # On vérifie que le fichier audio n'est pas trop gros (< 8Mo)
                if attachment.size > 8 * 1024 * 1024:
                    raise commands.CommandError("Le fichier audio est trop gros (8Mo max)")
                audio_path = self.__temp_folder / attachment.filename
                await attachment.save(audio_path)
                author_name = f"@{message.author.name}" if message.author.name.lower() == message.author.display_name.lower() else f"{message.author.display_name} (@{message.author.name})"
                message_date = message.created_at.strftime("%d/%m/%y")
                try:
                    video_path = self.create_video_quote(str(audio_path), BytesIO(await message.author.display_avatar.read()), author_name, message_date)
                except Exception as e:
                    logger.exception(e)
                    raise commands.CommandError(f"Impossible de créer la vidéo de la citation : {e}")
                # On renvoie le fichier ainsi que les paths des fichiers temporaires pour les supprimer
                filename = f"{message_date.replace('/','-')}.mp4"
                return discord.File(video_path, filename=filename, description=f"\"{message.content}\" - @{message.author.name} ({message_date})"), {'audio_path': audio_path, 'video_path': video_path}
        raise commands.CommandError("Le message ne contient pas de fichier audio")
    
        
async def setup(bot):
    await bot.add_cog(Quotes(bot))