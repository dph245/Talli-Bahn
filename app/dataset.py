"""Import generations, not cross-import station identities."""
import hashlib
from .database import connect


def gtfs_version(path):
    with connect(path) as db:
        row = db.execute("SELECT value FROM metadata WHERE key='import_id'").fetchone()
        if row:
            return 'gtfs:' + row[0]
    # Existing imports need no rewrite/reimport. Atomic replacement changes the
    # token; a mere catalog rebuild or server restart does not. This fallback
    # is deliberately conservative and is not a portable station identity.
    stat = path.stat()
    signature = f'{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}'
    return 'gtfs:legacy:' + hashlib.sha256(signature.encode()).hexdigest()
