"""Console account limits re-read under the owner's control fence before send."""
from decimal import Decimal, localcontext

from .errors import ApiError


def account_risk_reader(store, runtime, quote_snapshot):
    def check(request, command):
        # Bounded exits and protection maintenance retain their dispatcher
        # permission/ownership/quantity checks without unrelated quote demands.
        if command.operation not in ('SUBMIT', 'CLOSE') or command.canonical_payload.side != 'BUY':
            return
        # Caller holds the exclusive account owner fence, outside any business
        # UoW. Nested account reads and cause persistence must not wait on the
        # dispatch transaction's own collateral mutex.
        with localcontext() as arithmetic, store.engine.begin() as connection:
            arithmetic.prec = 80
            data = store._state(connection)
            market = quote_snapshot()
            account = runtime._account_view()
            price = store._price(market, request.symbol, request.maximum_age)
            equity = Decimal(account['cash'])
            previous = Decimal(0)
            for position in account['positions']:
                value = Decimal(position['quantity']) * store._price(
                    market, position['symbol'], request.maximum_age)
                equity += value
                if position['symbol'] == request.symbol:
                    previous += value
            notional = Decimal(command.canonical_payload.quantity) * price
            fee = notional * Decimal('.001')
            # Commit the observed cause to BOTH ledgers before raising. Raising
            # inside either denied-order transaction would roll the lock back.
            locks = store._daily_loss(connection, data, equity, prospective_fee=fee)
            post_equity = equity - fee
            cap_exceeded = (post_equity <= 0 or
                (previous + notional) * 100 > post_equity * Decimal(data['risk']['max_position_pct']))
        if locks:
            raise ApiError('DAILY_LOSS_LIMIT', 'PAPER 日亏损风险锁生效；价格回升不会解除。', 409)
        if cap_exceeded:
            raise ApiError('POSITION_LIMIT', '该订单将超过 PAPER 单品种仓位上限。', 409)
    return check
