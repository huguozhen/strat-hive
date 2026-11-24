"""Market data commands with no ledger initialization requirement."""
import typer
from strathive.data.service import Data, configuration
from strathive import settings
from strathive.core.rules import require


def install(app, output):
    data = typer.Typer(help='行情、来源及诊断。', no_args_is_help=True)
    valuation = typer.Typer(help='账户和运行的参考市值。', no_args_is_help=True)
    app.add_typer(data, name='data')
    from .history import install as install_history
    install_history(data, output)
    app.add_typer(valuation, name='valuation')

    @data.command('status')
    def status():
        output(Data().status())

    @data.command('doctor')
    def doctor(online: bool = False):
        service = Data()
        result = service.status()
        if online:
            result['calendar'] = service.calendar('SH')[-5:]
        output(result)

    @data.command('configure')
    def configure(provider: str = 'galaxy'):
        require(provider == 'galaxy', '当前可配置的行情来源为 galaxy。')
        values = configuration(settings.settings())
        service = Data(config=values)
        require(provider in service.providers, '请先通过 setup 配置银河连接。', 'source_not_configured')
        values['selection'] = {**values.get('selection', {}), 'quote': [provider], 'close': [provider], 'calendar': [provider]}
        settings.save(values)
        output({'configured': provider, 'online_verified': False})

    @data.command('quote')
    def quote(security: list[str] = typer.Option(...), refresh: bool = False, offline: bool = False):
        service = Data()
        symbols = [service.instruments(s)['symbol'] for s in security]
        output(service.quotes(symbols, refresh, offline))

    @valuation.command('account')
    def account(ctx: typer.Context, account_id: int = typer.Option(...), refresh: bool = False, offline: bool = False):
        output(ctx.obj.ledger.valuation(account_id=account_id, refresh=refresh, offline=offline))

    @valuation.command('run')
    def run(ctx: typer.Context, run_id: int = typer.Option(...), refresh: bool = False, offline: bool = False):
        output(ctx.obj.ledger.valuation(run_id=run_id, refresh=refresh, offline=offline))

    @valuation.command('complete')
    def complete(ctx: typer.Context, run_id: int = typer.Option(...)):
        output(ctx.obj.ledger.complete_valuation(run_id))
