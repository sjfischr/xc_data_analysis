"""Migrations, repositories, views, and the S3 publication client.

Public entry points for callers outside this package:

* :func:`xc_platform.db.migrator.bootstrap` -- open (creating if needed) a
  SQLite path and apply every migration.
* :mod:`xc_platform.db.connection` -- ``open_writer_connection`` (single
  local writer) and ``open_reader_connection`` (verified, immutable
  snapshot). Only ``db/`` and ``cli/`` code should call these directly.
* :mod:`xc_platform.db.repositories` -- the typed, transactional write and
  read surface every other package uses instead of a raw connection
  (Requirement 10.3).
"""
