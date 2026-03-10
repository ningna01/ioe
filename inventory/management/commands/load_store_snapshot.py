from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from inventory.utils.store_snapshot import get_local_view_db_path, get_store_snapshot_path


class Command(BaseCommand):
    help = 'Load the exported store snapshot JSON into the current database.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--snapshot',
            default=None,
            help='Snapshot JSON path. Defaults to settings.STORE_SNAPSHOT_PATH.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Allow loading into a database other than settings.LOCAL_VIEW_DB_PATH.',
        )

    def handle(self, *args, **options):
        snapshot_path = get_store_snapshot_path(options['snapshot'])
        if not snapshot_path.exists():
            raise CommandError(f'Snapshot file not found: {snapshot_path}')

        target_db_path = Path(settings.DATABASES['default']['NAME']).expanduser().resolve()
        local_view_db_path = get_local_view_db_path()
        if target_db_path != local_view_db_path and not options['force']:
            raise CommandError(
                'Refusing to load snapshot into a non-local view database. '
                f'Set IOE_DB_PATH={local_view_db_path} or rerun with --force.'
            )

        call_command('flush', '--noinput', verbosity=0)
        call_command('loaddata', str(snapshot_path), verbosity=0)

        self.stdout.write(self.style.SUCCESS(
            f'Loaded snapshot {snapshot_path} into {target_db_path}'
        ))
