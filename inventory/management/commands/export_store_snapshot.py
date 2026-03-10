import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from inventory.utils.store_snapshot import SNAPSHOT_EXCLUDED_MODELS, get_store_snapshot_path


class Command(BaseCommand):
    help = 'Export a consistent JSON snapshot of the store database.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default=None,
            help='Output JSON path. Defaults to settings.STORE_SNAPSHOT_PATH.',
        )

    def handle(self, *args, **options):
        database_config = settings.DATABASES['default']
        engine = database_config['ENGINE']
        if engine != 'django.db.backends.sqlite3':
            raise CommandError(f'Only sqlite3 is supported, got: {engine}')

        source_db_path = Path(database_config['NAME']).expanduser().resolve()
        if not source_db_path.exists():
            raise CommandError(f'Source database not found: {source_db_path}')

        output_path = get_store_snapshot_path(options['output'])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output_path = output_path.with_name(f'{output_path.name}.tmp')

        with tempfile.TemporaryDirectory(prefix='ioe-store-snapshot-') as temp_dir:
            temp_db_path = Path(temp_dir) / source_db_path.name
            self._backup_sqlite_database(source_db_path, temp_db_path)
            self._dump_snapshot(temp_db_path, temp_output_path)

        changed = self._replace_if_changed(temp_output_path, output_path)
        size_bytes = output_path.stat().st_size if output_path.exists() else 0

        self.stdout.write(self.style.SUCCESS(
            f'snapshot_path={output_path} source_db={source_db_path} changed={str(changed).lower()} bytes={size_bytes}'
        ))

    def _backup_sqlite_database(self, source_db_path: Path, temp_db_path: Path):
        source_conn = sqlite3.connect(source_db_path)
        target_conn = sqlite3.connect(temp_db_path)
        try:
            source_conn.backup(target_conn)
        except sqlite3.Error as exc:
            raise CommandError(f'Failed to create sqlite snapshot: {exc}') from exc
        finally:
            target_conn.close()
            source_conn.close()

    def _dump_snapshot(self, snapshot_db_path: Path, temp_output_path: Path):
        command = [sys.executable, str(Path(settings.BASE_DIR) / 'manage.py'), 'dumpdata']
        for label in SNAPSHOT_EXCLUDED_MODELS:
            command.extend(['--exclude', label])
        command.extend(['--output', str(temp_output_path)])

        env = os.environ.copy()
        env['IOE_DB_PATH'] = str(snapshot_db_path)

        result = subprocess.run(
            command,
            cwd=settings.BASE_DIR,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            temp_output_path.unlink(missing_ok=True)
            raise CommandError(result.stderr.strip() or result.stdout.strip() or 'dumpdata failed')

    def _replace_if_changed(self, temp_output_path: Path, output_path: Path) -> bool:
        if output_path.exists() and output_path.read_bytes() == temp_output_path.read_bytes():
            temp_output_path.unlink(missing_ok=True)
            return False
        temp_output_path.replace(output_path)
        return True
