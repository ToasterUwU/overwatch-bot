import asyncio
import datetime
from typing import Dict

import aiohttp
import nextcord
from nextcord.ext import commands, tasks
from nextcord.interactions import Interaction

from internal_tools.configuration import CONFIG, JsonDictSaver
from internal_tools.discord import *
from internal_tools.general import error_webhook_send

PLATFORM_ROUTER = {
    "PC": "pc",
    "Playstation": "psn",
    "XBox": "xbl",
    "Nintendo Switch": "nintendo-switch",
}
PLATFORM_ROUTER_REVERSE = {v: k for k, v in PLATFORM_ROUTER.items()}

REGION_ROUTER = {
    "Europe": "eu",
    "USA": "us",
    "Asia": "asia",
}
REGION_ROUTER_REVERSE = {v: k for k, v in REGION_ROUTER.items()}


class HeroClassEnum:
    DPS = "DPS"
    SUPPORT = "SUPPORT"
    TANK = "TANK"


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
        if not interaction.user:
            await interaction.send(
                "Something went wrong, please try again.", ephemeral=True
            )
            return

        if self.account_name_input.value.count("#") != 1 or not self.account_name_input.value.split("#")[1].isnumeric():  # type: ignore
            await interaction.send(
                "You forgot to add the # + numbers part, or you put too many of them.\n"
                "Enter your full name, make sure the capitalization is right and that you include all numbers.\n\n"
                "Example:\n"
                "- ToasterUwU#8527 - Correct\n"
                "- ToasterUwU8527 - Wrong\n"
                "- ToasterUwU - Wrong\n"
                "- toasteruwu#8527 - Wrong\n",
                ephemeral=True,
            )
            return

        success = await self.cog.add_account(
            user_id=interaction.user.id,
            platform=self.platform,
            region=self.region,
            account_name=self.account_name_input.value,  # type: ignore
        )

        text = f"You are now entered as '{self.account_name_input.value}' ( Platform: {PLATFORM_ROUTER_REVERSE[self.platform]}, Region: {REGION_ROUTER_REVERSE[self.region]} ). "
        if success:
            text += "Adding your Roles was successful."
        else:
            text += "\nAdding your Roles was NOT successful, this might be a temporary issue, or you might have entered your name wrong or didnt make your profile public yet.\nRemember that the servers can take up to an hour to notice you setting your profile to public.\nYou DONT have to retry adding your name (if you selected the right one), since the Bot saves it and will automatically retry later."

        await interaction.send(
            text,
            ephemeral=True,
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
                for key, val in PLATFORM_ROUTER.items()
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
                for key, val in REGION_ROUTER.items()
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
        self.overwatch_roles = JsonDictSaver("overwatch_roles")
        self.notifications = JsonDictSaver(
            "notifications",
            default={"CAREER_PROFILE_PRIVATE": {}},
        )

    async def cog_application_command_check(self, interaction: nextcord.Interaction):
        """
        Everyone can use this.
        """
        return True

    @commands.Cog.listener()
    async def on_ready(self):
        channel = await GetOrFetch.channel(
            self.bot, CONFIG["ACCOUNT_LINKER"]["MENU_CHANNEL_ID"]
        )
        if isinstance(channel, nextcord.abc.GuildChannel):
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

            if len(self.overwatch_roles) == 0:
                guild = channel.guild

                # Main Roles
                self.overwatch_roles["MAIN_ROLE_IDS"] = {}
                for hero, vals in CONFIG["ACCOUNT_LINKER"]["HEROES"].items():
                    main_role = await guild.create_role(
                        name=f"{hero} Main",
                        color=nextcord.Color(int(vals["COLOR"].replace("#", ""), 16)),
                        hoist=True,
                        mentionable=True,
                    )

                    self.overwatch_roles["MAIN_ROLE_IDS"][hero] = main_role.id

                # Top 3 Seperator role
                top_3_seperator_role = await guild.create_role(
                    name=CONFIG["ACCOUNT_LINKER"]["SEPERATOR_ROLE_NAMES"][
                        "TOP_3_USED_HEROES"
                    ],
                    color=nextcord.Color(
                        int(
                            CONFIG["ACCOUNT_LINKER"]["SEPERATOR_ROLE_COLOR"].replace(
                                "#", ""
                            ),
                            16,
                        )
                    ),
                    hoist=True,
                    mentionable=True,
                )
                self.overwatch_roles[
                    "TOP_3_SEPERATOR_ROLE_ID"
                ] = top_3_seperator_role.id

                # Top 3 Hero roles
                self.overwatch_roles["HERO_ROLE_IDS"] = {}
                for hero, vals in CONFIG["ACCOUNT_LINKER"]["HEROES"].items():
                    hero_role = await guild.create_role(
                        name=f"{hero}",
                        color=nextcord.Color(int(vals["COLOR"].replace("#", ""), 16)),
                    )

                    self.overwatch_roles["HERO_ROLE_IDS"][hero] = hero_role.id

                # Other Seperator role
                other_seperator_role = await guild.create_role(
                    name=CONFIG["ACCOUNT_LINKER"]["SEPERATOR_ROLE_NAMES"][
                        "OTHER_INFOS"
                    ],
                    color=nextcord.Color(
                        int(
                            CONFIG["ACCOUNT_LINKER"]["SEPERATOR_ROLE_COLOR"].replace(
                                "#", ""
                            ),
                            16,
                        )
                    ),
                    hoist=True,
                    mentionable=True,
                )
                self.overwatch_roles[
                    "OTHER_SEPERATOR_ROLE_ID"
                ] = other_seperator_role.id

                # Main Class roles
                self.overwatch_roles["CLASS_ROLE_IDS"] = {}
                for hero_class, color in CONFIG["ACCOUNT_LINKER"][
                    "CLASS_ROLES"
                ].items():
                    class_role = await guild.create_role(
                        name=f"{hero_class}",
                        color=nextcord.Color(int(color.replace("#", ""), 16)),
                    )

                    self.overwatch_roles["CLASS_ROLE_IDS"][hero_class] = class_role.id

                self.overwatch_roles.save()

        self.update_overwatch_roles.start()

    async def assign_overwatch_roles(
        self, member: nextcord.Member, platform: str, region: str, account_name: str
    ):
        async with aiohttp.ClientSession() as session:
            try:
                resp = await session.get(
                    f"https://ow-api.com/v1/stats/{platform}/{region}/{account_name.replace('#', '-')}/complete"
                )
            except:
                return False

            if not resp.ok:
                return False

            data = await resp.json()
            if "private" not in data:
                return False

            if data["private"]:
                today = datetime.datetime.utcnow()
                if (
                    member.id in self.notifications["CAREER_PROFILE_PRIVATE"]
                    and today - self.notifications["CAREER_PROFILE_PRIVATE"][member.id]
                    > datetime.timedelta(days=3)
                ) or member.id not in self.notifications["CAREER_PROFILE_PRIVATE"]:
                    try:
                        await member.send(
                            "Hello, i tried to fetch your Career Profile to assign you the roles you should have,"
                            " but your Career Profile is private at the moment.\n"
                            "Please make it public again,"
                            " or ask Aki to remove your data from my database so that i wont try to do this again."
                        )
                        self.notifications["CAREER_PROFILE_PRIVATE"][
                            member.id
                        ] = today.isoformat()
                        self.notifications.save()
                    except:
                        pass

                return False

            played_amounts: Dict[str, datetime.timedelta] = {}
            class_amounts: Dict[str, datetime.timedelta] = {}
            for gamemode_stats in ["competitiveStats", "quickPlayStats"]:
                for api_hero, stats in data[gamemode_stats]["careerStats"].items():
                    if api_hero == "allHeroes":
                        continue

                    if api_hero not in [
                        x["API_NAME"]
                        for x in CONFIG["ACCOUNT_LINKER"]["HEROES"].values()
                    ]:
                        await error_webhook_send(f"Unknown Hero `{api_hero}` from API")
                        continue

                    hero_name = None
                    hero_class = None
                    for hero, vals in CONFIG["ACCOUNT_LINKER"]["HEROES"].items():
                        if api_hero == vals["API_NAME"]:
                            hero_name = hero
                            hero_class = vals["CLASS"]
                            break

                    if not hero_name or not hero_class:
                        return False

                    raw_time: str = stats["game"]["timePlayed"]
                    if raw_time.count(":") == 1:
                        hours = "0"
                        minutes, seconds = raw_time.split(":")
                    elif raw_time.count(":") == 2:
                        hours, minutes, seconds = raw_time.split(":")
                    else:
                        return False

                    time_amount = datetime.timedelta(
                        hours=int(hours), minutes=int(minutes), seconds=int(seconds)
                    )

                    if hero_name not in played_amounts:
                        played_amounts[hero_name] = datetime.timedelta()
                    played_amounts[hero_name] += time_amount

                    if hero_class not in class_amounts:
                        class_amounts[hero_class] = datetime.timedelta()
                    class_amounts[hero_class] += time_amount

            if len(played_amounts) == 0 or len(class_amounts) == 0:
                return False

            main_hero = max(played_amounts, key=played_amounts.get)  # type: ignore
            del played_amounts[main_hero]

            top_3_heroes = []
            for _ in range(3):
                if len(played_amounts) == 0:
                    break

                key = max(played_amounts, key=played_amounts.get)  # type: ignore
                top_3_heroes.append(key)

                del played_amounts[key]

            most_played_class = max(class_amounts, key=class_amounts.get)  # type: ignore

            roles_to_remove = []
            roles_to_add = []

            role = await GetOrFetch.role(
                member.guild, self.overwatch_roles["TOP_3_SEPERATOR_ROLE_ID"]
            )
            if role:
                if role not in member.roles:
                    roles_to_add.append(role)

            role = await GetOrFetch.role(
                member.guild, self.overwatch_roles["OTHER_SEPERATOR_ROLE_ID"]
            )
            if role:
                if role not in member.roles:
                    roles_to_add.append(role)

            for hero, role_id in self.overwatch_roles["MAIN_ROLE_IDS"].items():
                role = await GetOrFetch.role(member.guild, role_id)
                if role:
                    if main_hero == hero:
                        if role not in member.roles:
                            roles_to_add.append(role)
                    else:
                        if role in member.roles:
                            roles_to_remove.append(role)

            for hero, role_id in self.overwatch_roles["HERO_ROLE_IDS"].items():
                role = await GetOrFetch.role(member.guild, role_id)
                if role:
                    if hero in top_3_heroes:
                        if role not in member.roles:
                            roles_to_add.append(role)
                    else:
                        if role in member.roles:
                            roles_to_remove.append(role)

            for hero_class, role_id in self.overwatch_roles["CLASS_ROLE_IDS"].items():
                role = await GetOrFetch.role(member.guild, role_id)
                if role:
                    if hero_class == most_played_class:
                        if role not in member.roles:
                            roles_to_add.append(role)
                    else:
                        if role in member.roles:
                            roles_to_remove.append(role)

            if len(roles_to_remove) != 0:
                await member.remove_roles(*roles_to_remove)
            if len(roles_to_add) != 0:
                await member.add_roles(*roles_to_add)

            return True

    async def add_account(
        self, user_id: int, platform: str, region: str, account_name: str
    ):
        self.accounts[user_id] = {
            "platform": platform,
            "region": region,
            "account_name": account_name,
        }

        self.accounts.save()

        home_guild = await GetOrFetch.guild(
            self.bot, CONFIG["GENERAL"]["HOME_SERVER_ID"]
        )
        if home_guild:
            member = await GetOrFetch.member(home_guild, user_id)
            if member:
                return await self.assign_overwatch_roles(
                    member, platform, region, account_name
                )

        return False

    @tasks.loop(hours=12)
    async def update_overwatch_roles(self):
        home_guild = await GetOrFetch.guild(
            self.bot, CONFIG["GENERAL"]["HOME_SERVER_ID"]
        )
        if home_guild:
            for user_id, vals in self.accounts.items():
                member = await GetOrFetch.member(home_guild, user_id)
                if member:
                    await self.assign_overwatch_roles(
                        member, vals["platform"], vals["region"], vals["account_name"]
                    )
                    await asyncio.sleep(60)

    @update_overwatch_roles.error
    async def restart_update_overwatch_roles(self, *args):
        await asyncio.sleep(10)

        self.update_overwatch_roles.restart()

    @nextcord.user_command("Overwatch Profile", dm_permission=False)
    async def see_overwatch_profile(
        self, interaction: nextcord.Interaction, member: nextcord.Member
    ):
        if member.id not in self.accounts:
            await interaction.send(
                "That User has not connected their Profile with this Bot.",
                ephemeral=True,
            )
            return

        account_name = self.accounts[member.id]["account_name"]

        await interaction.send(
            f"{account_name}'s Profile with all Stats: https://overwatch.blizzard.com/en-us/career/{account_name.replace('#', '-')}/",
            ephemeral=True,
        )

    @nextcord.slash_command(
        "clean-overwatch-roles",
        default_member_permissions=nextcord.Permissions(administrator=True),
        dm_permission=False,
    )
    async def clean_overwatch_roles(self, interaction: nextcord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        if guild:
            name_list = [hero for hero in CONFIG["ACCOUNT_LINKER"]["HEROES"]]
            name_list.extend(CONFIG["ACCOUNT_LINKER"]["CLASS_ROLES"])
            name_list.extend(CONFIG["ACCOUNT_LINKER"]["SEPERATOR_ROLE_NAMES"].values())

            for r in guild.roles:
                if r.name.replace(" Main", "") in name_list:
                    await r.delete(reason="Cleaning Overwatch Roles")

            self.overwatch_roles.clear()
            self.overwatch_roles.save()

        await interaction.send("Done.", ephemeral=True)


async def setup(bot):
    bot.add_cog(AccountLinker(bot))
