from pathlib import Path
import os
import sqlite3
from zipfile import ZipFile

import pytest

from app.database import SCHEMA
from app.gtfs_quality import EFA_AGENCIES, QualityError, check_quality, evaluate
from app.import_gtfs import import_feed


def fixture_db(path, *, prefix='', stops=12, trips=24, shift=0, flat=0,
               neighbor=False, missing=False, per_trip=None, start='20260901', end='20261031'):
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute('INSERT INTO agencies VALUES (?,?)', (prefix+'a', EFA_AGENCIES[0]))
        db.execute('INSERT INTO routes VALUES (?,?,?,?,?)', (prefix+'r', 'Test 17', '', 3, prefix+'a'))
        db.execute('INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,?,?)', (prefix+'c', 1,1,1,1,1,1,1,start,end))
        for i in range(stops):
            # Both child and parent IDs change; names and memberships remain semantic.
            db.execute('INSERT INTO stops VALUES (?,?,?,?,?,?)', (prefix+f'g{i}', f'Town, Place {i}', '', '', '', 1))
            db.execute('INSERT INTO stops VALUES (?,?,?,?,?,?)', (prefix+f's{i}', f'Town, Place {i}', '', prefix+f'g{i}', '', 0))
        for t in range(trips):
            tid = prefix+f't{t}'
            db.execute('INSERT INTO trips VALUES (?,?,?,?,?)', (tid,prefix+'r',prefix+'c','',0))
            for seq in range(per_trip or stops):
                i = (t * (per_trip or stops) + seq) % stops
                seconds = 3600 + shift + t*1800 + (0 if t < flat or neighbor and seq == 1 else seq*120)
                if missing and seq == 0:
                    seconds = None
                db.execute('INSERT INTO stop_times VALUES (?,?,?,?,?,?,?,?)',
                           (tid,prefix+f's{i}',seq,seconds,seconds,'',0,0))
        db.execute("INSERT INTO metadata VALUES ('import_id', 'preserve-this')")
    return path


@pytest.mark.parametrize('options', [dict(prefix='new'), dict(stops=11), dict(shift=37),
                                     dict(neighbor=True), dict(missing=True)])
def test_normal_changes_are_accepted(tmp_path, options):
    old = fixture_db(tmp_path/'old.sqlite')
    new = fixture_db(tmp_path/'new.sqlite', **options)
    report = check_quality(new, old)
    assert report.accepted and not report.warnings


def test_isolated_pathological_trip_warns_but_mass_pattern_blocks(tmp_path):
    isolated = fixture_db(tmp_path/'isolated.sqlite', flat=1)
    report = check_quality(isolated)
    assert report.candidate['suspicious'] == 1 and report.warnings
    broken = fixture_db(tmp_path/'broken.sqlite', trips=60, flat=60)
    with pytest.raises(QualityError) as error:
        check_quality(broken)
    assert error.value.report.candidate['suspicious'] == 60
    assert len(error.value.report.candidate['examples']) == 5


def test_one_way_boarding_area_is_warning_not_hard_error(tmp_path):
    path = fixture_db(tmp_path/'demand.sqlite', flat=24)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE stop_times SET drop_off_type=1 WHERE stop_sequence<11')
    report = check_quality(path)
    assert report.candidate['suspicious'] == 24
    assert report.candidate['pathological'] == 0
    assert report.warnings


@pytest.mark.parametrize('trips,flat,accepted', [(49,49,True), (50,50,False), (501,50,True)])
def test_absolute_pattern_thresholds(tmp_path, trips, flat, accepted):
    path = fixture_db(tmp_path/'candidate.sqlite', trips=trips, flat=flat)
    assert evaluate(path).accepted is accepted


def test_shortened_route_warns_without_regional_loss(tmp_path):
    old = fixture_db(tmp_path/'old.sqlite', stops=24)
    new = fixture_db(tmp_path/'new.sqlite', stops=12)
    report = check_quality(new, old)
    assert any('stop events 576 -> 288' in w for w in report.warnings)


def test_regional_loss_warns_without_shortened_trips(tmp_path):
    old = fixture_db(tmp_path/'old.sqlite', stops=240, per_trip=10)
    new = fixture_db(tmp_path/'new.sqlite', stops=120, per_trip=10)
    report = check_quality(new, old)
    assert any('groups: 240 -> 120' in w for w in report.warnings)


def test_combined_signals_block_and_calendar_nonoverlap_does_not(tmp_path):
    old = fixture_db(tmp_path/'old.sqlite', stops=240)
    new = fixture_db(tmp_path/'new.sqlite', stops=120)
    assert not evaluate(new, old).accepted
    with sqlite3.connect(new) as db:
        db.execute("UPDATE calendar SET start_date='20261101',end_date='20261130'")
    report = check_quality(new, old)
    assert any('Insufficient' in w for w in report.warnings)


def test_parent_merge_without_name_loss_is_not_hard_failure(tmp_path):
    old = fixture_db(tmp_path/'old.sqlite', stops=240)
    new = fixture_db(tmp_path/'new.sqlite', stops=120)
    # Emulate separate roots with identical names: group count can change with no
    # evidence of physical station loss. Shortening alone must not block.
    for path in (old, new):
        with sqlite3.connect(path) as db:
            db.execute("UPDATE stops SET stop_name='Town, Interchange' WHERE location_type=1")
    assert check_quality(new, old).accepted


def test_exception_dates_control_common_day_comparison(tmp_path):
    old = fixture_db(tmp_path/'old.sqlite', stops=240)
    new = fixture_db(tmp_path/'new.sqlite', stops=120)
    with sqlite3.connect(old) as db:
        for day in range(1,15):
            db.execute('INSERT INTO calendar_dates VALUES (?,?,2)', ('c', f'202609{day:02}'))
    report = check_quality(new, old)
    assert all(m['active_trips'] == 0 for m in report.reference['routes'].values())


def archive_from_database(path, output):
    """Small real import fixture; no quality mocking on the rejection path."""
    import csv
    import io
    def clock(s):
        return '' if s is None else f'{s//3600:02}:{s//60%60:02}:{s%60:02}'
    with sqlite3.connect(path) as db, ZipFile(output, 'w') as z:
        queries = {
            'agency.txt': "SELECT agency_id,agency_name,'Europe/Berlin' AS agency_timezone FROM agencies",
            'stops.txt': 'SELECT stop_id,stop_name,parent_station,platform_code,location_type FROM stops',
            'routes.txt': 'SELECT * FROM routes', 'trips.txt': 'SELECT * FROM trips',
            'calendar.txt': 'SELECT * FROM calendar',
            'stop_times.txt': 'SELECT trip_id,stop_id,stop_sequence,arrival AS arrival_time,departure AS departure_time,stop_headsign,pickup_type,drop_off_type FROM stop_times',
        }
        for name, query in queries.items():
            cursor = db.execute(query)
            buf = io.StringIO(); writer = csv.writer(buf)
            writer.writerow([col[0] for col in cursor.description])
            for row in cursor:
                row = list(row)
                if name == 'stop_times.txt':
                    row[3:5] = map(clock, row[3:5])
                writer.writerow(row)
            z.writestr(name, buf.getvalue())
    return output


@pytest.mark.parametrize('technical_error', [False, True])
def test_import_failure_preserves_active_and_artifacts(tmp_path, monkeypatch, technical_error):
    active = fixture_db(tmp_path/'active.sqlite')
    broken = fixture_db(tmp_path/'broken.sqlite', trips=60, flat=60)
    archive = archive_from_database(broken, tmp_path/'broken.zip')
    catalog = tmp_path/'vrb-stops.json'; catalog.write_bytes(b'not even valid JSON')
    before, catalog_before = active.read_bytes(), catalog.read_bytes()
    stat = active.stat()
    if technical_error:
        def fail(*args):
            raise RuntimeError('quality check failed')
        monkeypatch.setattr('app.import_gtfs.check_quality', fail)
    with pytest.raises(RuntimeError if technical_error else QualityError):
        import_feed(archive, active)
    assert active.read_bytes() == before and active.stat().st_mtime_ns == stat.st_mtime_ns
    assert catalog.read_bytes() == catalog_before
    assert not list(tmp_path.glob('gtfs-*.sqlite'))


def test_successful_import_without_any_catalog(tmp_path):
    source = fixture_db(tmp_path/'source.sqlite')
    archive = archive_from_database(source, tmp_path/'good.zip')
    report = import_feed(archive, tmp_path/'active.sqlite')
    assert report.accepted and (tmp_path/'active.sqlite').exists()
    assert not (tmp_path/'vrb-stops.json').exists()


def test_cli_rejection_is_bounded_report_and_nonzero_exit(tmp_path, monkeypatch, capsys):
    from app.import_gtfs import main
    broken = fixture_db(tmp_path/'broken.sqlite', trips=60, flat=60)
    archive = archive_from_database(broken, tmp_path/'broken.zip')
    target = tmp_path/'active.sqlite'
    monkeypatch.setattr('sys.argv', ['import_gtfs', str(archive), '--database', str(target)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    output = capsys.readouterr().err
    assert 'REJECTED' in output and '60/60' in output
    assert output.count('Example:') == 5
    assert not target.exists()
    assert not list(tmp_path.glob('gtfs-*.sqlite'))


# Opt-in because this imports two national feeds and needs about 10 GB of disk.
# The active production database and its catalog are never used as destinations.
@pytest.mark.skipif(os.getenv('TALLI_GTFS_SNAPSHOT_TESTS') != '1', reason='opt-in national GTFS integration')
def test_real_snapshot_imports_preserve_accepted_generation():
    import hashlib
    import tempfile
    root = Path(__file__).resolve().parents[1]
    good_zip = root/'latest_2026-09-29.zip'
    bad_zip = root/'latest_2026-10-03.zip'
    assert good_zip.exists() and bad_zip.exists()

    def fingerprint(path):
        with path.open('rb') as f:
            return hashlib.file_digest(f, 'sha256').hexdigest()

    with tempfile.TemporaryDirectory(prefix='quality-validation-', dir=root/'data') as directory:
        active = Path(directory)/'active.sqlite'
        good = import_feed(good_zip, active)
        print('\nREFERENCE IMPORT\n' + good.render(), flush=True)
        assert good.accepted and good.candidate['efa']['groups'] == 4639
        before, stat = fingerprint(active), active.stat()
        # A deliberately unusable artifact proves there is no catalog dependency.
        catalog = Path(directory)/'vrb-stops.json'; catalog.write_bytes(b'leave unchanged')
        with pytest.raises(QualityError) as error:
            import_feed(bad_zip, active)
        bad = error.value.report
        print('\nCANDIDATE IMPORT\n' + bad.render(), flush=True)
        assert bad.candidate['efa']['groups'] == 4033
        expected = {'420': ((334,5666), (334,2982)),
                    '421': ((111,3132), (52,547)), '793': ((134,4007), (134,4007))}
        for line, (old_counts, new_counts) in expected.items():
            key = (EFA_AGENCIES[0].casefold(), line, 3)
            old_metric, new_metric = bad.reference['routes'][key], bad.candidate['routes'][key]
            assert (old_metric['trips'],old_metric['events']) == old_counts
            assert (new_metric['trips'],new_metric['events']) == new_counts
            print(line, old_metric, '->', new_metric, flush=True)
        assert fingerprint(active) == before
        after = active.stat()
        assert (after.st_ino, after.st_size, after.st_mtime_ns) == (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        assert catalog.read_bytes() == b'leave unchanged'
        assert not list(Path(directory).glob('gtfs-*.sqlite'))
        print('Accepted database: SHA-256, inode, size and mtime unchanged; catalog unchanged.', flush=True)
