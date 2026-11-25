"""Historical commands work without initializing a ledger."""
from pathlib import Path
import typer

from strathive import settings
from strathive.core.rules import require
from strathive.data.history.bars import symbol, window
from strathive.data.history.provider import History, configuration
from strathive.data.models import utcnow
from strathive.data.service import configuration as data_configuration


def install(data, output):
    history = typer.Typer(help='可选历史组件的安装、卸载、配置和诊断。', no_args_is_help=True)
    data.add_typer(history, name='history')

    @history.command('install')
    def install_component(bundle: Path | None = None, manifest: Path | None = None, repair: bool = False):
        from strathive.data.history.component import install
        output(install(bundle=bundle, manifest_file=manifest, repair=repair,
                       progress=lambda message: typer.echo(message, err=True)))

    @history.command('uninstall')
    def uninstall_component():
        from strathive.data.history.component import uninstall
        output(uninstall())

    @history.command('configure')
    def configure(python: Path | None = None, root: Path | None = None,
                  minute_security: list[str] = typer.Option(None), minute_days: int = 60):
        values = data_configuration(settings.settings())
        config = configuration(values)
        if python is not None:
            config['python'] = str(python.expanduser().absolute())
        if root is not None:
            config['root'] = str(root.expanduser().resolve())
        if minute_security is not None:
            config['minute_symbols'] = [symbol(s) for s in minute_security]
        config['minute_days'] = minute_days
        config = configuration({'history': config})
        config['enabled'] = True
        values['history'] = config
        selection = values.setdefault('selection', {})
        for capability in ('close', 'calendar'):
            selection[capability] = list(dict.fromkeys([*selection.get(capability, []), 'cnequity']))
        settings.save(values)
        output({'configured': config, 'online_verified': False})

    @history.command('status')
    def status():
        output(History().status())

    @history.command('doctor')
    def doctor():
        output(History().call('doctor'))

    @history.command('init')
    def initialize():
        output(History().call('init'))

    @history.command('inspect')
    def inspect():
        output(History().call('inspect'))

    @history.command('maintain')
    def maintain():
        output(History().call('maintain'))

    @history.command('schedule')
    def schedule():
        from strathive.schedule import enable
        service = History()
        require(service.status()['initialized'], '请先初始化并完成样本验证。', 'history_not_initialized')
        service.call('doctor')
        tasks = {name: enable(f'strathive-history-{name}') for name in ('daily', 'minute', 'retry')}
        output({'timers': tasks, 'timezone': 'Asia/Shanghai'})
        if not all(tasks.values()):
            raise typer.Exit(1)

    @history.command('clear-minute-list')
    def clear_minute_list():
        values = settings.settings()
        config = configuration(values)
        config['minute_symbols'] = []
        values['history'] = config
        settings.save(values)
        output({'minute_symbols': [], 'local_history_retained': True})

    @data.command('bars')
    def bars(security: list[str] = typer.Option(...), start: str = typer.Option(...), end: str = typer.Option(...),
             period: str = '1d', adjustment: str = 'none', output_file: Path | None = typer.Option(None, '--output')):
        output(History().bars(security, start, end, period, adjustment, output_file))

    @data.command('sync')
    def synchronize(security: list[str] = typer.Option(None), period: str = 'all', start: str | None = None,
                    end: str | None = None, retry_only: bool = False, scheduled: bool = False):
        require(period in ('all', '1d', '1m', 'factors'), '同步周期为 all、1d、1m 或 factors；其他周期由查询聚合。')
        require(not retry_only or (not security and not start and not end and period == 'all'),
                '--retry-only 仅重试已记录未完成范围，不接受新范围。')
        if start or end:
            window(start or end, end or utcnow().date().isoformat(), utcnow())
        datasets = {'all': ['daily_bars', 'minute_bars', 'adj_factors'], '1d': ['daily_bars', 'adj_factors'],
                    '1m': ['minute_bars', 'adj_factors'], 'factors': ['adj_factors']}[period]
        result = History().call('sync', request={'symbols': [symbol(s) for s in security] if security else None,
                                               'datasets': datasets, 'start': start, 'end': end, 'retry_only': retry_only, 'scheduled': scheduled})
        output(result)
        if not result['complete']:
            raise typer.Exit(1)
