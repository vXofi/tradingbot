"""
Legacy sandbox debug script.
Kept for quick API testing: python main.py --sandbox
"""

import os
import uuid
import warnings
from dotenv import load_dotenv
from deprecation import DeprecatedWarning
from t_tech.invest import Client, MoneyValue, Quotation, OrderDirection, OrderType
from t_tech.invest.services import SandboxService
from t_tech.invest.exceptions import RequestError

warnings.filterwarnings("ignore", category=DeprecatedWarning)

load_dotenv()

TOKEN_TINKOFF = os.getenv('TOKEN_TINKOFF')

def run_bot():
    with Client(TOKEN_TINKOFF) as client:
        sandbox: SandboxService = client.sandbox

        accounts = sandbox.open_sandbox_account()
        account_id = accounts.account_id
        print(f"Создан счет в песочнице: {account_id}")

        sandbox.sandbox_pay_in(
            account_id=account_id,
            amount=MoneyValue(units=100000, nano=0, currency='rub')
        )
        print("Счет пополнен")

        figi = 'BBG004730N88'

        schedule = client.instruments.trading_schedules(
            exchange='MOEX',
            from_=None,
            to=None
        )
        print(f"Расписание торгов получено: {len(schedule.exchanges)} бирж")

        print("Выставляем заявку на покупку...")
        try:
            order_response = sandbox.post_sandbox_order(
                account_id=account_id,
                figi=figi,
                quantity=1,
                price=Quotation(units=250, nano=0),
                direction=OrderDirection.ORDER_DIRECTION_BUY,
                order_type=OrderType.ORDER_TYPE_LIMIT,
                order_id=str(uuid.uuid4())
            )
            print(f"Заявка выставлена! Статус: {order_response.execution_report_status}")
        except RequestError as e:
            print(f"Ошибка при выставлении заявки: {e.metadata.message}")
            print("Возможно, биржа закрыта. Торги на MOEX: Пн-Пт 10:00-18:50 MSK")

        portfolio = sandbox.get_sandbox_portfolio(account_id=account_id)
        print(f"Баланс: {portfolio.total_amount_currencies}")


if __name__ == "__main__":
    run_bot()
