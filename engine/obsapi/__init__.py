"""obsapi — the backend observability read API for the Scalers operator console.

A strawberry GraphQL schema + SSE endpoint over Postgres, mounted into the engine
FastAPI app. Serves every operation the Next.js console sends
(``web/lib/data/queries.ts``) returning the shapes it expects
(``web/lib/data/models.ts``), tenant-scoped, read-only over PG except the action
mutations which delegate to the ``engine/actions`` seams.

Import is side-effect-free (no DB connection, no driver required) so
``python -c "import obsapi"`` is a clean smoke test.
"""

from __future__ import annotations

from .mount import mount_obsapi
from .schema import schema

__all__ = ["mount_obsapi", "schema"]
