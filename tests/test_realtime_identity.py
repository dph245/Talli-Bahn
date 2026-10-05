"""RB43 static evidence plus deliberately reconstructed (not captured) bad RT."""
import shutil
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timedelta

import pytest
from google.transit import gtfs_realtime_pb2 as gtfs

from app.database import SCHEMA
from app.models import Stop
from app.providers.gtfs import GTFSProvider
from app.providers.gtfs_realtime import RealtimeFeed, Snapshot, apply_update, apply_alerts, parse_snapshot
from app.providers.gtfs_static import BERLIN
from app.providers.realtime_identity import identity_conflict
from app.database import connect
from test_transit import journey, update, write_feed
from app.import_gtfs import import_feed

CASE = json.loads((Path(__file__).parent / 'fixtures/rb43-20261005.json').read_text())


@pytest.fixture
def rb43(tmp_path):
    path = tmp_path / 'rb43.sqlite'
    t = CASE['trip']
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute('INSERT INTO agencies VALUES (?,?)', (t['agency_id'], t['agency_name']))
        db.execute('INSERT INTO routes VALUES (?,?,?,?,?)', (t['route_id'], 'RB43', '', 2, t['agency_id']))
        db.execute('INSERT INTO trips VALUES (?,?,?,?,?)', tuple(t[k] for k in
                   ('trip_id', 'route_id', 'service_id', 'trip_headsign', 'direction_id')))
        db.execute("INSERT INTO calendar_dates VALUES (?, '20261005', 1)", (t['service_id'],))
        db.execute("INSERT INTO metadata VALUES ('max_time','86400')")
        for s in CASE['stops']:
            db.execute('INSERT INTO stops VALUES (?,?,?,?,?,?)',
                       (s['stop_id'], s['stop_name'], s['stop_name'].casefold(), '', '', 0))
            db.execute('INSERT INTO stop_times VALUES (?,?,?,?,?,?,?,?)', tuple(s[k] for k in
                       ('trip_id', 'stop_id', 'stop_sequence', 'arrival', 'departure', 'stop_headsign', 'pickup_type', 'drop_off_type')))
    return GTFSProvider(path, RealtimeFeed(None))


def rb_update(foreign=False):
    u = gtfs.TripUpdate()
    u.trip.trip_id = '1029743'
    u.trip.start_date = '20261005'
    for s in CASE['stops']:
        st = u.stop_time_update.add(stop_sequence=s['stop_sequence'],
                                   stop_id='foreign-' + s['stop_id'] if foreign else s['stop_id'])
        reported = CASE['reported_realtime'].get(str(s['stop_sequence']))
        when = (datetime.fromisoformat('2026-10-05T' + reported).replace(tzinfo=BERLIN)
                if foreign and reported else
                datetime(2026, 10, 5, tzinfo=BERLIN) + timedelta(seconds=s['departure'] + 120))
        st.departure.time = int(when.timestamp())
    return u


def alert(header, **scope):
    a = gtfs.Alert()
    a.header_text.translation.add(text=header, language='de')
    selector = a.informed_entity.add()
    for k, v in scope.items():
        if k == 'trip_id':
            selector.trip.trip_id = v
        else:
            setattr(selector, k, v)
    return header, a


@pytest.mark.parametrize('foreign', [True, False])
def test_rb43_whole_route_realtime_and_alerts(rb43, foreign):
    u = rb_update(foreign)
    entries = [alert('Stuttgart-West: Umleitung wegen Bauarbeiten' if foreign else 'RB43 Hinweis', trip_id='1029743'),
               alert('Gelenkbus' if foreign else 'erixx Hinweis', agency_id='211'),
               alert('Linienhinweis', route_id='18234'),
               alert('Fremde Agency', agency_id='stuttgart'),
               alert('Fremde Route', route_id='bus44')]
    snapshot = Snapshot(updates={('1029743', '20261005'): u}, alerts=entries)
    for s in CASE['stops'][1:]:
        now = datetime(2026, 10, 5, 10, 30, tzinfo=BERLIN)
        station, departures = rb43.scheduled(s['stop_id'], 'departures', now)
        board = rb43.enrich(station, departures, snapshot, 'available', s['stop_id'], 'departures', now)
        d, = board.journeys
        assert d.trip_id == '1029743'
        if foreign:
            assert d.realtime is None and not d.cancelled and not d.alerts
            assert board.alerts == [] and board.realtime_status == 'unavailable'
        else:
            assert d.delay_minutes == 2
            assert [a.header for a in d.alerts] == ['RB43 Hinweis', 'erixx Hinweis', 'Linienhinweis']


def test_stop_sequence_does_not_override_conflicting_stop_id():
    d, u = journey(), update()
    u.delay = 900
    u.stop_time_update.add(stop_sequence=2, stop_id='foreign').departure.delay = 300
    apply_update(d, 'departures', {('t', '20260929'): u})
    assert d.realtime is None


@pytest.mark.parametrize('scope', [{'agency_id': 'wrong'}, {'route_id': 'wrong'},
                                  {'route_type': 3}, {'direction_id': 1}, {'stop_id': 'wrong'}])
def test_trip_alert_requires_all_selector_fields(scope):
    d = journey().model_copy(update={'agency_id': 'erixx', 'route_id': 'rb43', 'route_type': 2, 'direction_id': 0})
    entry = alert('Scoped', trip_id='t', **scope)
    apply_alerts([d], [entry], 'p', d.scheduled)
    assert not d.alerts


def test_conflict_outside_current_board_also_invalidates_alert_namespace(rb43):
    # The conflicting trip need not appear on this board; agency/route-only
    # alerts still use the same now-untrustworthy dataset namespace.
    u = rb_update(True)
    snapshot = Snapshot(updates={('1029743', '20261005'): u},
                        alerts=[alert('Station', stop_id='unrelated')])
    board = rb43.enrich(Stop(id='unrelated', name='Other'), [], snapshot, 'available',
                       'unrelated', 'departures', datetime(2026, 10, 5, tzinfo=BERLIN))
    assert not board.alerts and board.realtime_status == 'unavailable'


def test_identity_validation_rechecks_after_snapshot_or_import_change(rb43):
    good = rb_update()
    snapshot = Snapshot(updates={('1029743', '20261005'): good})
    assert rb43.identity_guard.accepts(snapshot)
    replacement = rb43.path.with_name('replacement.sqlite')
    shutil.copyfile(rb43.path, replacement)
    with sqlite3.connect(replacement) as db:
        db.execute("UPDATE stop_times SET stop_id='changed' WHERE stop_sequence=1")
    replacement.replace(rb43.path)
    assert not rb43.identity_guard.accepts(snapshot)
    shutil.copyfile(rb43.path, replacement)
    with sqlite3.connect(replacement) as db:
        db.execute("UPDATE stop_times SET stop_id='612924' WHERE stop_sequence=1")
    replacement.replace(rb43.path)
    assert rb43.identity_guard.accepts(snapshot)
    assert not rb43.identity_guard.accepts(Snapshot(updates={('1029743', '20261005'): rb_update(True)}))


@pytest.mark.parametrize('case', ['unknown', 'sequence_only', 'stop_only', 'route_conflict', 'missing_sequence', 'foreign_stop_only'])
def test_identity_optional_fields_and_subsets(rb43, case):
    u = rb_update()
    if case == 'unknown':
        u.trip.trip_id = 'not-in-static-subset'
    elif case == 'route_conflict':
        u.trip.route_id = 'other'
    elif case == 'missing_sequence':
        u.stop_time_update[0].stop_sequence = 999
    else:
        for s in u.stop_time_update:
            s.ClearField('stop_id' if case == 'sequence_only' else 'stop_sequence')
        if case == 'foreign_stop_only':
            u.stop_time_update[0].stop_id = 'other'
    with connect(rb43.path) as db:
        conflict = identity_conflict(db, Snapshot(updates={('x', ''): u}))
    assert bool(conflict) == (case in ('route_conflict', 'missing_sequence', 'foreign_stop_only'))


def test_duplicate_trip_updates_are_not_last_writer_wins(monkeypatch):
    monkeypatch.setattr('app.providers.gtfs_realtime.time.time', lambda: 1000)
    f = gtfs.FeedMessage()
    f.header.gtfs_realtime_version = '2.0'
    f.header.timestamp = 1000
    for i in range(2):
        u = f.entity.add(id=str(i)).trip_update
        u.trip.trip_id = '1029743'
        u.delay = i * 60
    with pytest.raises(ValueError, match='Mehrdeutige'):
        parse_snapshot(f.SerializeToString())


def test_optional_train_number_import_and_legacy_database(tmp_path):
    archive, path = tmp_path / 'feed.zip', tmp_path / 'feed.sqlite'
    write_feed(archive, {'trips.txt': 'route_id,service_id,trip_id,trip_short_name\nr,weekday,t,81561\n'})
    import_feed(archive, path)
    p = GTFSProvider(path, RealtimeFeed(None))
    now = datetime(2026, 9, 30, tzinfo=BERLIN)
    _, ds = p.scheduled('s', 'departures', now)
    assert ds[0].train_number == '81561'
    assert 'train_number' not in ds[0].model_dump()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE trip_names')
    _, ds = p.scheduled('s', 'departures', now)
    assert ds[0].train_number is None


@pytest.mark.parametrize('delay', [-480, 0, 1320, 10800])
def test_valid_delays_not_filtered_but_other_departure_time_rejected(rb43, delay):
    u = rb_update()
    s = u.stop_time_update[1]
    scheduled = int(datetime(2026, 10, 5, 10, 33, tzinfo=BERLIN).timestamp())
    s.departure.time = scheduled + delay
    s.departure.delay = delay
    with connect(rb43.path) as db:
        assert identity_conflict(db, Snapshot(updates={('1029743', '20261005'): u})) is None
        s.departure.time += 3600  # Same stops, but source describes the next train.
        assert 'Sollzeit' in identity_conflict(db, Snapshot(updates={('1029743', '20261005'): u}))


def test_alert_only_snapshot_cannot_clear_detected_namespace_conflict(rb43):
    assert not rb43.identity_guard.accepts(Snapshot(updates={('1029743', ''): rb_update(True)}))
    assert not rb43.identity_guard.accepts(Snapshot(alerts=[alert('wrong', trip_id='1029743')]))
    assert rb43.identity_guard.accepts(Snapshot(updates={('1029743', ''): rb_update()}))
