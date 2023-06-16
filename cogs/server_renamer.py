import nextcord
from nextcord.ext import commands, tasks

from internal_tools.configuration import CONFIG
from internal_tools.discord import *


class ServerRenamer(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.home_guild: nextcord.Guild

    async def cog_application_command_check(self, interaction: nextcord.Interaction):
        """
        Everyone can use this.
        """
        return True

    @commands.Cog.listener()
    async def on_ready(self):
        home_guild = await GetOrFetch.guild(
            self.bot, CONFIG["SERVER_RENAMER"]["HOME_SERVER_ID"]
        )

        if home_guild:
            self.home_guild = home_guild
            self.rename_server.start()

    @tasks.loop(minutes=10)
    async def rename_server(self):
        member_count = self.home_guild.member_count
        if member_count:
            amount = member_count - len(self.home_guild.bots)

            new_name = "OW " + CONFIG["SERVER_RENAMER"]["GROUP_NAMES"][amount]
            if self.home_guild.name != new_name:
                await self.home_guild.edit(name=new_name)


async def setup(bot):
    bot.add_cog(ServerRenamer(bot))
