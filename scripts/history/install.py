#!/usr/bin/env python3
"""Install the pinned historical backend in its own Python 3.14 environment."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

COMMIT = 'dd6881ad7a3e8fce4ed4314711c99f6251ec06f8'


def run(*arguments, **kwargs):
    return subprocess.run(list(map(str, arguments)), check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', type=Path, help='默认 XDG_DATA_HOME/strathive/history-env')
    parser.add_argument('--python', default='3.14', help='独立环境使用的 Python；默认首先验证现有 3.14')
    options = parser.parse_args()
    data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'strathive'
    environment = (options.environment or data / 'history-env').expanduser().resolve()
    if environment.exists() and any(environment.iterdir()):
        parser.error('安装目标须为空目录；已有后端请指定新目录，验证后再切换配置。')
    uv = shutil.which('uv')
    if not uv:
        parser.error('请先安装 uv；不会修改系统 Python。')
    interpreter = run(uv, 'python', 'find', options.python, capture_output=True, text=True).stdout.strip()
    run(uv, 'venv', '--python', interpreter, environment)
    python = environment / 'bin/python'
    requirements = Path(__file__).with_name('requirements.lock')
    build = Path(__file__).with_name('build-requirements.lock')
    run(uv, 'pip', 'install', '--python', python, '--require-hashes', '-r', build)
    run(uv, 'pip', 'install', '--python', python, '--no-build-isolation', '--require-hashes', '-r', requirements)
    # A full commit prevents tag movement from changing the installed source.
    run(uv, 'pip', 'install', '--python', python, '--no-build-isolation', '--no-deps',
        f'git+https://github.com/rootSunc/CNEquity.git@{COMMIT}')
    run(uv, 'pip', 'check', '--python', python)
    # Installs and loads both DuckLake extensions now, so later reads reuse cache.
    run(python, '-c', 'import duckdb; from equitylake_core.extensions import ensure_extensions; '
        'c=duckdb.connect(); print(ensure_extensions(c)); c.close()')
    frozen = run(uv, 'pip', 'freeze', '--python', python, capture_output=True, text=True).stdout
    (environment / 'installed-requirements.txt').write_text(frozen)
    print(json.dumps({'python': str(python), 'commit': COMMIT, 'configured': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
