import disnake
from disnake.ext import commands
from disnake import ButtonStyle
import aiohttp
from typing import Dict, Any, Optional

CRYPTOBOT_TOKEN = "TOKEN"
ORDERS_CHANNEL_ID = 1441116849483026440
ADMIN_ROLE_ID = 1440367647425560660
TEST_MODE = True # False - Выключить тестовый режим

orders_db: Dict[str, Any] = {}


class CryptoBot:
    PROD_URL = "https://pay.crypt.bot/api"
    TEST_URL = "https://testnet-pay.crypt.bot/api"
    
    def __init__(self, token: str, test_mode: bool = False):
        self.token = token
        self.base_url = self.TEST_URL if test_mode else self.PROD_URL
        self.headers = {"Crypto-Pay-API-Token": token}
    
    async def _request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict[str, Any]]:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method,
                f"{self.base_url}/{endpoint}",
                headers=self.headers,
                **kwargs
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        return data.get("result")
        return None
    
    async def create_invoice(self, amount: float, order_id: str, currency: str = "RUB", expires_in: int = 259200) -> Optional[str]:
        payload = {
            "currency_type": "fiat",
            "fiat": currency,
            "amount": str(amount),
            "description": f"Оплата заказа #{order_id[:8]}",
            "expires_in": expires_in,
            "payload": order_id
        }
        result = await self._request("POST", "createInvoice", json=payload)
        if result:
            return result.get("bot_invoice_url")
        return None
    
    async def check_invoice(self, order_id: str) -> bool:
        params = {"payload": order_id}
        result = await self._request("GET", "getInvoices", params=params)
        if result and result.get("items"):
            return result["items"][0].get("status") == "paid"
        return False


class OrderModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(
                label="Описание задачи",
                placeholder="Подробно опишите что нужно сделать...",
                custom_id="description",
                style=disnake.TextInputStyle.paragraph,
                min_length=10,
                max_length=3000,
                required=True
            )
        ]
        super().__init__(title="📝 Новый заказ", components=components)
    
    async def callback(self, inter: disnake.ModalInteraction):
        description = inter.text_values["description"]
        order_id = str(inter.id)
        
        orders_db[order_id] = {
            "user_id": inter.author.id,
            "user_name": str(inter.author),
            "description": description,
            "status": "pending",
            "guild_id": inter.guild.id
        }
        
        container = disnake.ui.Container(
            disnake.ui.TextDisplay("# ⭐ Новый заказ"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay("**Описание задачи:**"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"```md\n{description[:1500]}\n```"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"-# 👤 Заказчик: {inter.author.mention} (`{inter.author.id}`)"),
            disnake.ui.TextDisplay("-# 🟡 Статус: Ожидает рассмотрения"),
            disnake.ui.Separator(),
            disnake.ui.ActionRow(
                disnake.ui.Button(
                    label="Принять",
                    style=ButtonStyle.green,
                    custom_id=f"order_accept:{order_id}"
                ),
                disnake.ui.Button(
                    label="Отказать",
                    style=ButtonStyle.red,
                    custom_id=f"order_reject:{order_id}"
                )
            )
        )
        
        channel = inter.guild.get_channel(ORDERS_CHANNEL_ID)
        if channel:
            await channel.send(components=container)
            await inter.response.send_message("✅ Ваш заказ успешно отправлен на рассмотрение!", ephemeral=True)
        else:
            await inter.response.send_message("❌ Ошибка: канал для заказов не найден!", ephemeral=True)


class PriceModal(disnake.ui.Modal):
    def __init__(self, order_id: str, message: disnake.Message, crypto: CryptoBot):
        self.order_id = order_id
        self.message = message
        self.crypto = crypto
        components = [
            disnake.ui.TextInput(
                label="Сумма в рублях",
                placeholder="Например: 1500",
                custom_id="price",
                style=disnake.TextInputStyle.short,
                min_length=1,
                max_length=10,
                required=True
            )
        ]
        super().__init__(title="💰 Установить цену", components=components)
    
    async def callback(self, inter: disnake.ModalInteraction):
        await inter.response.defer(ephemeral=True)
        
        price_str = inter.text_values["price"]
        
        try:
            price = float(price_str.replace(",", "."))
            if price <= 0:
                raise ValueError
        except ValueError:
            await inter.followup.send("❌ Введите корректную сумму!", ephemeral=True)
            return
        
        order = orders_db.get(self.order_id)
        if not order:
            await inter.followup.send("❌ Заказ не найден!", ephemeral=True)
            return
        
        invoice_url = await self.crypto.create_invoice(price, self.order_id)
        if not invoice_url:
            await inter.followup.send("❌ Ошибка создания счёта CryptoBot!", ephemeral=True)
            return
        
        order["price"] = price
        order["status"] = "accepted"
        order["invoice_url"] = invoice_url
        
        updated = disnake.ui.Container(
            disnake.ui.TextDisplay("# ⭐ Заказ принят"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay("**Описание задачи:**"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"```md\n{order['description'][:1500]}\n```"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"-# 👤 Заказчик: <@{order['user_id']}>"),
            disnake.ui.TextDisplay(f"-# 🟢 Статус: Принят | Цена: {price:.2f} ₽")
        )
        await self.message.edit(components=updated)
        
        user = inter.guild.get_member(order["user_id"])
        if user:
            dm_container = disnake.ui.Container(
                disnake.ui.TextDisplay("# 🧾 Чек для оплаты"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay("**Статус:** Ваш заказ был принят в работу"),
                disnake.ui.Separator(),
                disnake.ui.Section(
                    disnake.ui.TextDisplay(
                        f"Оплатите чек в течение **трёх** дней\nСтоимость: **`{price:.2f} ₽`**"
                    ),
                    accessory=disnake.ui.Button(
                        label="Оплатить",
                        style=ButtonStyle.link,
                        url=invoice_url
                    )
                ),
                disnake.ui.Separator(),
                disnake.ui.ActionRow(
                    disnake.ui.Button(
                        label="Оплатил",
                        style=ButtonStyle.green,
                        custom_id=f"order_paid:{self.order_id}"
                    ),
                    disnake.ui.Button(
                        label="Отмена",
                        style=ButtonStyle.red,
                        custom_id=f"order_cancel:{self.order_id}"
                    )
                )
            )
            try:
                await user.send(components=dm_container)
                await inter.followup.send("✅ Заказ принят, счёт отправлен!", ephemeral=True)
            except disnake.Forbidden:
                await inter.followup.send("⚠️ Заказ принят, но не удалось отправить ЛС!", ephemeral=True)
        else:
            await inter.followup.send("✅ Заказ принят!", ephemeral=True)


class RejectModal(disnake.ui.Modal):
    def __init__(self, order_id: str, message: disnake.Message):
        self.order_id = order_id
        self.message = message
        components = [
            disnake.ui.TextInput(
                label="Причина отказа",
                placeholder="Укажите причину отказа...",
                custom_id="reason",
                style=disnake.TextInputStyle.paragraph,
                min_length=5,
                max_length=1000,
                required=True
            )
        ]
        super().__init__(title="❌ Отказ заказа", components=components)
    
    async def callback(self, inter: disnake.ModalInteraction):
        await inter.response.defer(ephemeral=True)
        
        reason = inter.text_values["reason"]
        order = orders_db.get(self.order_id)
        
        if not order:
            await inter.followup.send("❌ Заказ не найден!", ephemeral=True)
            return
        
        order["status"] = "rejected"
        order["reason"] = reason
        
        updated = disnake.ui.Container(
            disnake.ui.TextDisplay("# ⭐ Заказ отклонён"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay("**Описание задачи:**"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"```md\n{order['description'][:1500]}\n```"),
            disnake.ui.Separator(),
            disnake.ui.TextDisplay(f"-# 👤 Заказчик: <@{order['user_id']}>"),
            disnake.ui.TextDisplay("-# 🔴 Статус: Отклонён")
        )
        await self.message.edit(components=updated)
        
        user = inter.guild.get_member(order["user_id"])
        if user:
            dm_container = disnake.ui.Container(
                disnake.ui.TextDisplay("# 🗑️ Ваш заказ отменён"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay(f"Причина:\n```md\n{reason}\n```"),
                disnake.ui.Separator()
            )
            try:
                await user.send(components=dm_container)
            except disnake.Forbidden:
                pass
        
        await inter.followup.send("✅ Заказ отклонён!", ephemeral=True)


class OrdersCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.crypto = CryptoBot(CRYPTOBOT_TOKEN, test_mode=TEST_MODE)
    
    @commands.slash_command(name="order", description="Заказать услугу")
    async def order_cmd(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.send_modal(OrderModal())
    
    @commands.Cog.listener()
    async def on_button_click(self, inter: disnake.MessageInteraction):
        custom_id = inter.component.custom_id
        if not custom_id or ":" not in custom_id:
            return
        
        action, order_id = custom_id.split(":", 1)
        order = orders_db.get(order_id)
        
        if action == "order_accept":
            if not any(r.id == ADMIN_ROLE_ID for r in inter.author.roles):
                await inter.response.send_message("❌ Нет прав!", ephemeral=True)
                return
            await inter.response.send_modal(PriceModal(order_id, inter.message, self.crypto))
        
        elif action == "order_reject":
            if not any(r.id == ADMIN_ROLE_ID for r in inter.author.roles):
                await inter.response.send_message("❌ Нет прав!", ephemeral=True)
                return
            await inter.response.send_modal(RejectModal(order_id, inter.message))
        
        elif action == "order_paid":
            if not order or order["user_id"] != inter.author.id:
                await inter.response.send_message("❌ Это не ваш заказ!", ephemeral=True)
                return
            
            await inter.response.defer(ephemeral=True)
            
            if await self.crypto.check_invoice(order_id):
                order["status"] = "paid"
                success = disnake.ui.Container(
                    disnake.ui.TextDisplay("# ✅ Оплата подтверждена"),
                    disnake.ui.Separator(),
                    disnake.ui.TextDisplay("Спасибо за оплату!\nВаш заказ будет выполнен в ближайшее время."),
                    disnake.ui.Separator(),
                    disnake.ui.TextDisplay(f"-# ID заказа: `{order_id[:8]}`")
                )
                await inter.message.edit(components=success)
                await inter.followup.send("✅ Оплата подтверждена!", ephemeral=True)
                
                channel = self.bot.get_channel(ORDERS_CHANNEL_ID)
                if channel:
                    notify = disnake.ui.Container(
                        disnake.ui.TextDisplay("# 💳 Оплата получена"),
                        disnake.ui.Separator(),
                        disnake.ui.TextDisplay(f"Заказ `{order_id[:8]}` оплачен!"),
                        disnake.ui.TextDisplay(f"-# Заказчик: <@{order['user_id']}> | Сумма: {order['price']:.2f} ₽")
                    )
                    await channel.send(components=notify)
            else:
                await inter.followup.send("❌ Оплата не найдена! Попробуйте снова.", ephemeral=True)
        
        elif action == "order_cancel":
            if not order or order["user_id"] != inter.author.id:
                await inter.response.send_message("❌ Это не ваш заказ!", ephemeral=True)
                return
            
            order["status"] = "cancelled"
            cancelled = disnake.ui.Container(
                disnake.ui.TextDisplay("# ❌ Заказ отменён"),
                disnake.ui.Separator(),
                disnake.ui.TextDisplay("Вы отменили заказ.")
            )
            await inter.response.edit_message(components=cancelled)


def setup(bot: commands.Bot):
    bot.add_cog(OrdersCog(bot))
