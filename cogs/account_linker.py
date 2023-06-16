from typing import Optional
import nextcord
from nextcord.ext import commands, tasks
from nextcord.interactions import Interaction

from internal_tools.configuration import CONFIG, JsonDictSaver
from internal_tools.discord import *


class AccountLinkModal(nextcord.ui.Modal):
    def __init__(self, cog: "AccountLinker", platform: str, region: str):
        self.cog = cog
        self.platform = platform
        self.region = region

        super().__init__(
            "Enter your Account name: ",
            timeout=None,
            custom_id="AccountLinkModal",
        )

        self.account_name_input = nextcord.ui.TextInput(
            "Account Name",
            custom_id="AccountLinkModal:account_name",
            min_length=6,
            required=True,
            placeholder="ToasterUwU#8527",
        )
        self.add_item(self.account_name_input)

    async def callback(self, interaction: Interaction):
        if interaction.user:
            self.cog.add_account(
                interaction.user.id,
                platform=self.platform,
                region=self.region,
                account_name=self.account_name_input.value,  # type: ignore
            )

            await interaction.send(
                f"You are now entered as '{self.account_name_input.value}'",
                ephemeral=True,
            )
        else:
            await interaction.send(
                "Something went wrong, please try again.", ephemeral=True
            )

        self.stop()


class AccountLinkMenu(nextcord.ui.View):
    def __init__(self, cog: "AccountLinker"):
        self.cog = cog

        super().__init__(timeout=None)

        self.platform_select = nextcord.ui.StringSelect(
            placeholder="Platform",
            custom_id="AccountLinkModal:platform",
            row=0,
            min_values=1,
            max_values=1,
            options=[
                nextcord.SelectOption(label=key, value=val)
                for key, val in {
                    "PC": "pc",
                    "Playstation": "psn",
                    "XBox": "xbl",
                    "Nintendo Switch": "nintendo-switch",
                }.items()
            ],
        )
        self.add_item(self.platform_select)

        self.region_select = nextcord.ui.StringSelect(
            placeholder="Region",
            custom_id="AccountLinkModal:region",
            row=1,
            min_values=1,
            max_values=1,
            options=[
                nextcord.SelectOption(label=key, value=val)
                for key, val in {
                    "Europe": "eu",
                    "USA": "us",
                    "Asia": "asia",
                }.items()
            ],
        )
        self.add_item(self.region_select)

    @nextcord.ui.button(
        label="Link Account Now",
        custom_id="AccountLinkMenu:button",
        style=nextcord.ButtonStyle.primary,
        row=2,
    )  # type: ignore
    async def open_modal_button(
        self, button: nextcord.Button, interaction: nextcord.Interaction
    ):
        await interaction.response.send_modal(
            AccountLinkModal(
                self.cog,
                platform=self.platform_select.values[0],
                region=self.region_select.values[0],
            )
        )


class AccountLinker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.accounts = JsonDictSaver("linked_accounts")

    async def cog_application_command_check(self, interaction: nextcord.Interaction):
        """
        Everyone can use this.
        """
        return True

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(AccountLinkMenu(self))

        channel = await GetOrFetch.channel(
            self.bot, CONFIG["ACCOUNT_LINKER"]["MENU_CHANNEL_ID"]
        )
        if isinstance(channel, nextcord.TextChannel):
            async for msg in channel.history(limit=None):
                await msg.delete()

            with open("assets/ACCOUNT_LINKER/link_account.md", "r") as f:
                content = f.read()

            await channel.send(
                content,
                file=nextcord.File(
                    "assets/ACCOUNT_LINKER/social_settings_screenshot.png"
                ),
                view=AccountLinkMenu(self),
            )

    def add_account(self, user_id: int, platform: str, region: str, account_name: str):
        self.accounts[user_id] = {
            "platform": platform,
            "region": region,
            "account_name": account_name,
        }

        self.accounts.save()

    @tasks.loop(hours=12)
    async def update_overwatch_roles(self):
        ...


async def setup(bot):
    bot.add_cog(AccountLinker(bot))
