# model2_analytics.app.ingestion — the ingestion package.
#
# This used to be two things: a real implementation living in a hyphenated
# `model2-analytics/` directory (which can't be `import`ed under its own
# name — Python identifiers can't contain hyphens) plus a thin shim package
# at `model2_analytics/` that just re-exported these modules under an
# importable name. The two drifted in size (the shim's catalogue.py/
# supervisor.py/worker.py were roughly a tenth the length of their real
# counterparts) and had to be kept in sync by hand. AuditReport2.md
# finding 5.
#
# Fixed by renaming the real directory itself to `model2_analytics/` and
# deleting the shim — there is now exactly one copy of this code. It's
# still reachable under more than one import spelling depending on what's
# on sys.path when the caller runs (see supervisor.py's own try/except
# and model1-registry/app/main.py's PYTHONPATH comment for why that's
# still true), but every spelling now resolves to this same file instead
# of two files that could disagree.
