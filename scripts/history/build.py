#!/usr/bin/env python3
"""Build an optional Linux/CPython 3.14 wheel bundle, outside the main package."""
import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import zipfile

COMMIT = 'dd6881ad7a3e8fce4ed4314711c99f6251ec06f8'
ROOT = Path(__file__).resolve().parents[2]


def run(*arguments, **kwargs):
    return subprocess.run(list(map(str, arguments)), check=True, **kwargs)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='可使用固定提交的本地干净 CNEquity checkout')
    parser.add_argument('--wheel-source', type=Path, help='离线构建时的预下载依赖目录')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 14) or (sys.platform, platform.machine()) != ('linux', 'x86_64'):
        parser.error('请使用 Linux x86_64 的 Python 3.14 构建历史组件。')
    import tomllib
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    with tempfile.TemporaryDirectory(prefix='strathive-history-build-') as temporary:
        work = Path(temporary)
        source = args.source
        if source is None:
            source = work / 'cnequity'
            run('git', 'clone', '--quiet', 'https://github.com/rootSunc/CNEquity.git', source)
            run('git', '-C', source, 'checkout', '--quiet', COMMIT)
        actual = run('git', '-C', source, 'rev-parse', 'HEAD', capture_output=True, text=True).stdout.strip()
        dirty = run('git', '-C', source, 'status', '--porcelain', capture_output=True, text=True).stdout.strip()
        if actual != COMMIT or dirty:
            parser.error('CNEquity 源码必须是固定提交的干净 checkout。')
        epoch = run('git', '-C', source, 'show', '-s', '--format=%ct', 'HEAD', capture_output=True, text=True).stdout.strip()
        environment = {**os.environ, 'SOURCE_DATE_EPOCH': epoch}
        build = work / 'build-env'
        run(sys.executable, '-E', '-m', 'venv', build)
        python = build / 'bin/python'
        pip = [python, '-E', '-m', 'pip', '--isolated', '--disable-pip-version-check']
        locations = ['--no-index', '--find-links', args.wheel_source] if args.wheel_source else []
        run(*pip, 'install', *locations, '--require-hashes', '-r', Path(__file__).with_name('build-requirements.lock'))
        bundle = work / 'bundle'
        wheels = bundle / 'wheels'
        wheels.mkdir(parents=True)
        run(*pip, 'wheel', *locations, '--no-deps', '--require-hashes', '--no-build-isolation',
            '-r', Path(__file__).with_name('requirements.lock'), '--wheel-dir', wheels, env=environment)
        run(*pip, 'wheel', '--no-deps', '--no-build-isolation', source, '--wheel-dir', wheels, env=environment)
        requirements, packages = [], {}
        for wheel in sorted(wheels.glob('*.whl')):
            with zipfile.ZipFile(wheel) as archive:
                metadata = next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))
                package = BytesParser().parsebytes(archive.read(metadata))
            name, release = package['Name'], package['Version']
            if name.lower() in packages:
                raise RuntimeError(f'duplicate wheel: {name}')
            packages[name.lower()] = release
            requirements.append(f'{name}=={release} --hash=sha256:{sha(wheel)}')
        if packages.get('cnequity') != '0.25.0':
            raise RuntimeError('CNEquity wheel absent or wrong version')
        (bundle / 'requirements.lock').write_text('\n'.join(requirements) + '\n')
        # Validate the dependency closure in a clean environment with no build tools.
        import shutil
        shutil.rmtree(build)
        run(sys.executable, '-E', '-m', 'venv', build)
        run(*pip, 'install', '--no-index', '--find-links', wheels, '--only-binary=:all:',
            '--require-hashes', '-r', bundle / 'requirements.lock')
        run(*pip, 'check')
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=True)
        archive_path = work / 'history.tar.gz'
        with tarfile.open(archive_path, 'w:gz') as archive:
            for path in sorted(bundle.rglob('*')):
                if path.is_file():
                    archive.add(path, arcname=path.relative_to(bundle).as_posix(), recursive=False)
        checksum = sha(archive_path)
        filename = f'strathive-history-cne0.25.0-py314-linux_x86_64-{checksum[:12]}.tar.gz'
        destination = output / filename
        import shutil
        shutil.copyfile(archive_path, destination)
        value = {'format': 1, 'component': 'history', 'cnequity_version': '0.25.0', 'commit': COMMIT,
                 'python': '3.14', 'platform': 'linux_x86_64',
                 'artifact': {'filename': filename, 'sha256': checksum,
                              'url': f'https://github.com/huguozhen/strat-hive/releases/download/v{version}/{filename}'}}
        (output / 'history-component.json').write_text(json.dumps(value, indent=2) + '\n')
        print(json.dumps({'archive': str(destination), 'manifest': str(output / 'history-component.json'), 'wheels': len(packages)}))


if __name__ == '__main__':
    main()
