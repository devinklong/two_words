# scripts/research

One-time investigation scripts. Nothing in the daily run calls them (checked 10/22/26 with a repo-wide search).
They import shared modules such as `db_connection`, so run them from the repo root with:

    PYTHONPATH=scripts python scripts/research/<name>.py
